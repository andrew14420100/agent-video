"""Server HTTP locale (FastAPI) che espone il motore all'interfaccia web."""
from __future__ import annotations

import shutil
import tempfile
import threading
import webbrowser
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, library
from . import project as pj
from . import service
from .media import ffmpeg as ff
from .orchestrator import manager
from .project import ExportSettings, LicenseInfo, Timeline
from .research import providers as rp

UI_DIR = Path(__file__).resolve().parent.parent / "ui"

app = FastAPI(title=config.APP_NAME, version=config.APP_VERSION)


def _load(pid: str) -> pj.Project:
    try:
        return pj.load(pid)
    except KeyError as e:
        raise HTTPException(404, str(e))


def _not_busy(pid: str) -> None:
    if service.is_busy(pid):
        raise HTTPException(409, "Elaborazione in corso su questo progetto: attendi o annulla")


def _project_json(p: pj.Project) -> dict:
    d = p.model_dump(exclude={"assets": {"__all__": {"analysis"}}})
    for a, ad in zip(p.assets, d["assets"]):
        s = (a.analysis or {}).get("summary")
        ad["analysis_summary"] = s
        if a.kind == "audio" and a.analysis:
            ad["analysis_summary"] = {"bpm": a.analysis.get("bpm"), "beats": len(a.analysis.get("beats", []))}
        ad["license_ok"] = a.license.code in ("user", "generated") or a.license.code in config.settings().allowed_licenses
    d["timeline_duration"] = p.timeline.duration()
    d["clip_starts"] = p.timeline.clip_starts()
    return d


# ------------------------------------------------------------------ sistema
@app.get("/api/system")
def system(gpu: bool = True):
    return service.system_status(verify_gpu=gpu)


@app.get("/api/settings")
def get_settings():
    return config.settings().model_dump()


@app.put("/api/settings")
def put_settings(body: dict):
    cur = config.settings().model_dump()
    cur.update({k: v for k, v in body.items() if k in cur})
    config.save_settings(config.Settings(**cur))
    return config.settings().model_dump()


# ------------------------------------------------------------------ progetti
class NewProject(BaseModel):
    name: str = "Nuovo progetto"


@app.get("/api/projects")
def projects():
    return pj.list_projects()


@app.post("/api/projects")
def new_project(body: NewProject):
    return _project_json(pj.create(body.name))


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    return _project_json(_load(pid))


@app.delete("/api/projects/{pid}")
def del_project(pid: str):
    _not_busy(pid)
    pj.delete(pid)
    return {"ok": True}


class ProjectPatch(BaseModel):
    name: Optional[str] = None
    timeline: Optional[Timeline] = None
    export: Optional[ExportSettings] = None


@app.put("/api/projects/{pid}")
def patch_project(pid: str, body: ProjectPatch):
    _not_busy(pid)
    p = _load(pid)
    if body.name is not None:
        p.name = body.name
    if body.export is not None:
        p.export = body.export
    if body.timeline is not None:
        ids = {a.id for a in p.assets}
        for c in body.timeline.video + body.timeline.audio:
            if c.asset_id not in ids:
                raise HTTPException(400, f"Asset inesistente: {c.asset_id}")
        p.timeline = body.timeline
        from .render.engine import validate_timeline
        errs = validate_timeline(p) if p.timeline.video else []
        if errs:
            raise HTTPException(400, "; ".join(errs))
    pj.save(p)
    return _project_json(p)


# ------------------------------------------------------------------ asset
@app.post("/api/projects/{pid}/assets")
async def upload_asset(pid: str, file: UploadFile = File(...), role: str = Form(""),
                       analyze: bool = Form(True)):
    _not_busy(pid)
    p = _load(pid)
    if role not in ("", "logo", "music"):
        raise HTTPException(400, "Ruolo non valido")
    p.ensure_dirs()
    tmpdir = Path(tempfile.mkdtemp(dir=p.cache_dir))
    try:
        dest = tmpdir / library.safe_name(file.filename or "file")
        with open(dest, "wb") as f:
            shutil.copyfileobj(file.file, f, 1 << 20)
        try:
            a = library.import_file(p, dest, role=role, analyze=analyze and role != "logo",
                                    name=file.filename)
        except ff.FFmpegError as e:
            raise HTTPException(400, f"File non leggibile: {e}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    pj.save(p)
    return _project_json(p)


class ImportPath(BaseModel):
    path: str
    role: str = ""
    copy_file: bool = False


@app.post("/api/projects/{pid}/assets/import-path")
def import_path(pid: str, body: ImportPath):
    """Importa un file locale per percorso (utile per file grandi: nessun upload)."""
    _not_busy(pid)
    p = _load(pid)
    src = Path(body.path).expanduser()
    if not src.is_file():
        raise HTTPException(404, "File non trovato")
    try:
        library.import_file(p, src, copy=body.copy_file, role=body.role, analyze=body.role != "logo")
    except ff.FFmpegError as e:
        raise HTTPException(400, str(e))
    pj.save(p)
    return _project_json(p)


@app.delete("/api/projects/{pid}/assets/{aid}")
def delete_asset(pid: str, aid: str):
    _not_busy(pid)
    p = _load(pid)
    try:
        library.remove_asset(p, aid)
    except KeyError as e:
        raise HTTPException(404, str(e))
    pj.save(p)
    return _project_json(p)


@app.post("/api/projects/{pid}/assets/{aid}/analyze")
def analyze(pid: str, aid: str):
    _not_busy(pid)
    p = _load(pid)
    library.analyze_asset(p, p.asset(aid))
    pj.save(p)
    return _project_json(p)


@app.get("/api/projects/{pid}/files/{rel:path}")
def project_file(pid: str, rel: str):
    p = _load(pid)
    f = (p.dir / rel).resolve()
    if not str(f).startswith(str(p.dir.resolve())) or not f.is_file():
        raise HTTPException(404, "File non trovato")
    return FileResponse(f)


@app.get("/api/projects/{pid}/assets/{aid}/media")
def asset_media(pid: str, aid: str):
    p = _load(pid)
    try:
        f = p.asset_path(aid)
    except KeyError as e:
        raise HTTPException(404, str(e))
    if not f.is_file():
        raise HTTPException(404, "File mancante")
    return FileResponse(f)


# ------------------------------------------------------------------ ricerca
@app.get("/api/research")
def research(provider: str, q: str, kind: str = "video"):
    try:
        return [r.to_dict() for r in rp.search(provider, q, kind, 16)]
    except rp.ResearchError as e:
        raise HTTPException(502, str(e))


@app.post("/api/projects/{pid}/research/import")
def research_import(pid: str, item: dict):
    _not_busy(pid)
    p = _load(pid)
    res = rp.SearchResult.from_dict(item)
    try:
        f = rp.fetch(res, p.media_dir, want_height=p.export.height)
        library.import_file(p, f, copy=False, license=res.license, source=res.provider,
                            origin_url=res.landing_url, description=res.description[:500],
                            tags=res.tags, name=res.title, analyze=True)
    except (rp.ResearchError, ff.FFmpegError) as e:
        raise HTTPException(400, str(e))
    pj.save(p)
    return _project_json(p)


# ------------------------------------------------------------------ run
class GenerateBody(BaseModel):
    prompt: str


class RenderBody(BaseModel):
    mode: str = "final"


@app.post("/api/projects/{pid}/generate")
def generate(pid: str, body: GenerateBody):
    _load(pid)
    try:
        run = service.start_generate(pid, body.prompt)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return run.to_dict()


@app.post("/api/projects/{pid}/render")
def render(pid: str, body: RenderBody):
    p = _load(pid)
    if not p.timeline.video:
        raise HTTPException(400, "Timeline vuota")
    try:
        run = service.start_render(pid, "preview" if body.mode == "preview" else "final")
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return run.to_dict()


@app.get("/api/projects/{pid}/runs/{rid}")
def get_run(pid: str, rid: str):
    r = manager.get(rid)
    if r:
        return r.to_dict()
    d = manager.load_from_disk(_load(pid), rid)
    if not d:
        raise HTTPException(404, "Run non trovata")
    return d


@app.post("/api/runs/{rid}/cancel")
def cancel(rid: str):
    return {"ok": manager.cancel(rid)}


@app.get("/api/active")
def active():
    r = manager.active()
    return r.to_dict() if r else None


@app.exception_handler(Exception)
async def errors(_req, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})


if UI_DIR.exists():
    app.mount("/", StaticFiles(directory=str(UI_DIR), html=True), name="ui")


def serve(port: Optional[int] = None, open_browser: bool = True) -> None:
    import uvicorn

    s = config.settings()
    port = port or s.port
    url = f"http://{s.host}:{port}/"
    print(f"{config.APP_NAME} in esecuzione su {url}")
    if open_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=s.host, port=port, log_level="warning")


def run_app(port: Optional[int] = None) -> None:
    """Finestra desktop nativa (pywebview/WebView2 su Windows) se disponibile,
    altrimenti apre il browser predefinito."""
    try:
        import webview  # type: ignore
    except ImportError:
        serve(port=port, open_browser=True)
        return
    import uvicorn

    s = config.settings()
    port = port or s.port
    server = uvicorn.Server(uvicorn.Config(app, host=s.host, port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    webview.create_window(config.APP_NAME, f"http://{s.host}:{port}/", width=1600, height=960, min_size=(1200, 760))
    webview.start()
    server.should_exit = True

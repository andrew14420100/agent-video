"""Riga di comando: focusstudio <comando> ...

  focusstudio serve                       avvia l'interfaccia (http://127.0.0.1:8765)
  focusstudio doctor                      verifica FFmpeg, NVENC, voce, trascrizione, LLM
  focusstudio new "Nome"                  crea un progetto
  focusstudio import <id> file... [--logo] [--music]
  focusstudio generate <id> "richiesta"   pipeline completa multi-agente
  focusstudio render <id> [--preview]     rendering della timeline + QA + export
  focusstudio show <id>                   riepilogo del progetto
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config, library
from . import project as pj
from . import service


def _print_run(run) -> int:
    icons = {"completed": "OK ", "skipped": "-- ", "blocked": "BLK", "failed": "ERR", "cancelled": "ANN"}
    for t in run.tasks:
        print(f"[{icons.get(t.status, t.status)}] {t.title}: {t.summary} ({t.seconds:.1f}s)")
        for w in t.warnings:
            print(f"       ! {w}")
        if t.status == "failed" and t.error:
            print("       " + t.error.strip().splitlines()[-1])
    print(f"\nEsito: {run.status}")
    if run.result:
        print(json.dumps(run.result, indent=2, ensure_ascii=False))
    return 0 if run.status in ("completed", "completed_with_issues") else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="focusstudio", description=config.APP_NAME)
    ap.add_argument("--data-dir", help="cartella dati (default: %%LOCALAPPDATA%%/SteamFocusStudio)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve"); s.add_argument("--port", type=int); s.add_argument("--no-browser", action="store_true")
    sub.add_parser("doctor")
    s = sub.add_parser("new"); s.add_argument("name")
    s = sub.add_parser("import"); s.add_argument("project"); s.add_argument("files", nargs="+")
    s.add_argument("--logo", action="store_true"); s.add_argument("--music", action="store_true")
    s.add_argument("--analyze", action="store_true")
    s = sub.add_parser("generate"); s.add_argument("project"); s.add_argument("prompt")
    s = sub.add_parser("render"); s.add_argument("project"); s.add_argument("--preview", action="store_true")
    s = sub.add_parser("show"); s.add_argument("project")
    sub.add_parser("list")
    a = ap.parse_args(argv)
    if a.data_dir:
        config.set_data_dir(Path(a.data_dir))

    if a.cmd == "serve":
        from .server import serve
        serve(port=a.port, open_browser=not a.no_browser)
        return 0
    if a.cmd == "doctor":
        print(json.dumps(service.system_status(), indent=2, ensure_ascii=False))
        return 0
    if a.cmd == "new":
        p = pj.create(a.name)
        print(p.id)
        return 0
    if a.cmd == "list":
        for p in pj.list_projects():
            print(f"{p['id']}  {p['name']}  ({p['assets']} asset, {p['clips']} clip)")
        return 0
    p = pj.load(a.project)
    if a.cmd == "import":
        role = "logo" if a.logo else ("music" if a.music else "")
        for f in a.files:
            asset = library.import_file(p, Path(f), role=role, analyze=a.analyze)
            print(f"{asset.id}  {asset.kind:5s}  {asset.name}")
        pj.save(p)
        return 0
    if a.cmd == "generate":
        return _print_run(service.start_generate(p.id, a.prompt, wait=True))
    if a.cmd == "render":
        return _print_run(service.start_render(p.id, "preview" if a.preview else "final", wait=True))
    if a.cmd == "show":
        print(p.model_dump_json(indent=2, exclude={"assets": {"__all__": {"analysis"}}}))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())

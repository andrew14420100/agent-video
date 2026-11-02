"""Modello dati del progetto: asset, timeline, storyboard, impostazioni di esportazione.

Il progetto è salvato come ``project.json`` in una cartella dedicata, insieme a
``media/`` (file importati), ``cache/`` (segmenti renderizzati, analisi) e
``renders/`` (output). Tutti i tempi sono in secondi.
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

from . import config

SCHEMA_VERSION = 1


def new_id(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex[:10]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class LicenseInfo(BaseModel):
    code: str = "unknown"  # user | cc0 | pdm | public-domain-us-gov | by | by-sa | generated | unknown
    name: str = "Sconosciuta"
    url: str = ""
    attribution: str = ""
    requires_attribution: bool = False
    notes: str = ""


class Asset(BaseModel):
    id: str = Field(default_factory=lambda: new_id("a_"))
    kind: Literal["video", "image", "audio"]
    name: str
    path: str  # relativo alla cartella del progetto se dentro, altrimenti assoluto
    source: str = "local"  # local | nasa | openverse | wikimedia | tts | generated
    origin_url: str = ""
    license: LicenseInfo = Field(default_factory=LicenseInfo)
    sha256: str = ""
    info: dict = Field(default_factory=dict)  # dati ffprobe normalizzati
    analysis: Optional[dict] = None  # risultato dell'agente di analisi
    tags: list[str] = Field(default_factory=list)
    description: str = ""
    thumb: str = ""  # relativo al progetto
    role: str = ""  # "logo" | "music" | "voice" | ""
    added_at: str = Field(default_factory=now_iso)


class Transition(BaseModel):
    type: str = "cut"  # cut oppure un nome xfade (fade, dissolve, wipeleft, slideleft, ...)
    duration: float = 0.0


class VideoClip(BaseModel):
    id: str = Field(default_factory=lambda: new_id("c_"))
    asset_id: str
    src_in: float = 0.0
    duration: float
    transition: Transition = Field(default_factory=Transition)  # transizione IN ENTRATA
    fit: Literal["cover", "contain"] = "cover"
    motion: Literal["none", "zoom_in", "zoom_out", "pan_left", "pan_right"] = "none"
    volume: float = 1.0
    mute: bool = False
    speed: float = 1.0
    look: str = "none"  # none | cinematic | vivid | mono
    note: str = ""  # motivazione dell'agente che ha scelto la clip


class TextOverlay(BaseModel):
    id: str = Field(default_factory=lambda: new_id("t_"))
    kind: Literal["title", "lower_third", "caption", "end_card"] = "title"
    text: str
    subtext: str = ""
    start: float
    duration: float


class AudioClip(BaseModel):
    id: str = Field(default_factory=lambda: new_id("au_"))
    asset_id: str
    role: Literal["music", "voice", "sfx"] = "music"
    start: float = 0.0
    src_in: float = 0.0
    duration: float
    volume_db: float = 0.0
    fade_in: float = 0.0
    fade_out: float = 0.0
    loop: bool = False
    text: str = ""  # per le voci: testo pronunciato


class SubtitleCue(BaseModel):
    start: float
    end: float
    text: str


class Logo(BaseModel):
    asset_id: str
    position: Literal["top-left", "top-right", "bottom-left", "bottom-right"] = "top-right"
    scale: float = 0.09  # larghezza logo / larghezza video
    opacity: float = 0.9


class Timeline(BaseModel):
    video: list[VideoClip] = Field(default_factory=list)
    overlays: list[TextOverlay] = Field(default_factory=list)
    audio: list[AudioClip] = Field(default_factory=list)
    subtitles: list[SubtitleCue] = Field(default_factory=list)
    logo: Optional[Logo] = None
    fade_in: float = 0.4
    fade_out: float = 0.8
    keep_clip_audio: bool = True
    clip_audio_under_voice_db: float = -14.0

    def clip_starts(self) -> list[float]:
        """Inizio di ogni clip sulla timeline di uscita (le transizioni sovrappongono)."""
        starts, t = [], 0.0
        for i, c in enumerate(self.video):
            tr = c.transition.duration if (i > 0 and c.transition.type != "cut") else 0.0
            t -= tr
            starts.append(max(0.0, t))
            t += c.duration
        return starts

    def duration(self) -> float:
        t = 0.0
        for i, c in enumerate(self.video):
            tr = c.transition.duration if (i > 0 and c.transition.type != "cut") else 0.0
            t += c.duration - tr
        return max(0.0, t)


class ExportSettings(BaseModel):
    width: int = 1920
    height: int = 1080
    fps: int = 30
    codec: Literal["h264", "hevc"] = "h264"
    encoder: Literal["auto", "gpu", "cpu"] = "auto"
    burn_subtitles: bool = True
    soft_subtitles: bool = True
    loudness_target: float = -14.0
    audio_bitrate: str = "320k"


class Brief(BaseModel):
    prompt: str = ""
    duration: float = 30.0
    title: str = ""
    subtitle: str = ""
    pace: Literal["calm", "normal", "dynamic"] = "normal"
    look: str = "none"
    transition_style: str = "auto"
    narration: str = ""  # testo della voce narrante (vuoto = nessuna voce)
    want_voice: bool = False
    want_music: bool = True
    want_subtitles: bool = True
    topics: list[str] = Field(default_factory=list)
    search_online: bool = False
    language: str = "it"
    planner: str = "rules"  # rules | llm:<modello>
    notes: list[str] = Field(default_factory=list)


class Shot(BaseModel):
    index: int
    purpose: str  # intro | body | outro
    target_duration: float
    keywords: list[str] = Field(default_factory=list)
    narration: str = ""
    asset_id: str = ""
    src_in: float = 0.0
    reason: str = ""


class Storyboard(BaseModel):
    created_at: str = Field(default_factory=now_iso)
    total_duration: float = 0.0
    shots: list[Shot] = Field(default_factory=list)
    narration_sentences: list[str] = Field(default_factory=list)
    music_asset_id: str = ""
    beat_times: list[float] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class Project(BaseModel):
    schema_version: int = SCHEMA_VERSION
    id: str = Field(default_factory=lambda: new_id("p_"))
    name: str = "Nuovo progetto"
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    brief: Brief = Field(default_factory=Brief)
    assets: list[Asset] = Field(default_factory=list)
    timeline: Timeline = Field(default_factory=Timeline)
    storyboard: Optional[Storyboard] = None
    export: ExportSettings = Field(default_factory=ExportSettings)
    renders: list[dict] = Field(default_factory=list)
    runs: list[str] = Field(default_factory=list)

    # ---- percorsi -------------------------------------------------------
    @property
    def dir(self) -> Path:
        return config.paths().projects / self.id

    @property
    def media_dir(self) -> Path:
        return self.dir / "media"

    @property
    def cache_dir(self) -> Path:
        return self.dir / "cache"

    @property
    def renders_dir(self) -> Path:
        return self.dir / "renders"

    @property
    def runs_dir(self) -> Path:
        return self.dir / "runs"

    def ensure_dirs(self) -> None:
        for d in (self.dir, self.media_dir, self.cache_dir, self.renders_dir, self.runs_dir):
            d.mkdir(parents=True, exist_ok=True)

    def asset(self, asset_id: str) -> Asset:
        for a in self.assets:
            if a.id == asset_id:
                return a
        raise KeyError(f"Asset non trovato: {asset_id}")

    def asset_path(self, a: Asset | str) -> Path:
        if isinstance(a, str):
            a = self.asset(a)
        p = Path(a.path)
        return p if p.is_absolute() else self.dir / p

    def rel(self, p: Path) -> str:
        try:
            return Path(p).resolve().relative_to(self.dir.resolve()).as_posix()
        except ValueError:
            return str(Path(p).resolve())


_lock = threading.RLock()


def save(project: Project) -> None:
    with _lock:
        project.ensure_dirs()
        project.updated_at = now_iso()
        tmp = project.dir / "project.json.tmp"
        tmp.write_text(project.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(project.dir / "project.json")


def load(project_id: str) -> Project:
    if not re.fullmatch(r"p_[0-9a-f]{10}", project_id):
        raise KeyError("ID progetto non valido")
    f = config.paths().projects / project_id / "project.json"
    if not f.exists():
        raise KeyError(f"Progetto non trovato: {project_id}")
    with _lock:
        return Project.model_validate_json(f.read_text(encoding="utf-8"))


def create(name: str) -> Project:
    p = Project(name=name or "Nuovo progetto")
    save(p)
    return p


def list_projects() -> list[dict]:
    out = []
    for f in sorted(config.paths().projects.glob("p_*/project.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            out.append({"id": d["id"], "name": d["name"], "updated_at": d.get("updated_at", ""),
                        "assets": len(d.get("assets", [])), "clips": len(d.get("timeline", {}).get("video", []))})
        except Exception:
            continue
    return sorted(out, key=lambda x: x["updated_at"], reverse=True)


def delete(project_id: str) -> None:
    p = load(project_id)
    shutil.rmtree(p.dir, ignore_errors=True)

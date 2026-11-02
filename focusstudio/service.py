"""Funzioni di servizio condivise da CLI e server HTTP."""
from __future__ import annotations

import platform
import sys
from typing import Optional

from . import config, llm
from . import project as pj
from .agents.pipeline import generate_agents, render_agents
from .audio import asr, tts
from .media import ffmpeg as ff
from .orchestrator import Run, manager


def system_status(verify_gpu: bool = True) -> dict:
    out: dict = {"app": config.APP_NAME, "version": config.APP_VERSION, "python": sys.version.split()[0],
                 "os": f"{platform.system()} {platform.release()}", "data_dir": str(config.paths().data_dir)}
    try:
        out["ffmpeg"] = {"ok": True, "version": ff.version(), "path": ff.ffmpeg_bin()}
        enc = {}
        for name in ("libx264", "libx265", "h264_nvenc", "hevc_nvenc"):
            if verify_gpu or not name.endswith("nvenc"):
                ok, why = ff.verify_encoder(name)
                enc[name] = {"ok": ok, "detail": why}
        out["encoders"] = enc
        filters = ff.available_filters()
        out["filters"] = {k: k in filters for k in ("xfade", "subtitles", "loudnorm", "sidechaincompress",
                                                    "scdet", "ebur128", "blackdetect", "freezedetect")}
    except ff.FFmpegError as e:
        out["ffmpeg"] = {"ok": False, "error": str(e)}
    out["gpu"] = ff.nvidia_gpu_name() or None
    out["tts"] = tts.status()
    out["asr"] = asr.status()
    out["llm"] = llm.status()
    return out


def start_generate(project_id: str, prompt: str, wait: bool = False) -> Run:
    return manager.start(project_id, "generate", generate_agents(), prompt=prompt,
                         shared={"mode": "final"}, wait=wait)


def start_render(project_id: str, mode: str = "final", wait: bool = False) -> Run:
    p = pj.load(project_id)
    return manager.start(project_id, "render", render_agents(), prompt=f"render {mode}",
                         shared={"mode": mode, "expected_duration": p.timeline.duration()}, wait=wait)


def is_busy(project_id: Optional[str] = None) -> bool:
    r = manager.active()
    return bool(r and (project_id is None or r.project_id == project_id))

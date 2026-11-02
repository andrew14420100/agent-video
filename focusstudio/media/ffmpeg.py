"""Wrapper sicuro per FFmpeg/ffprobe.

- Gli argomenti sono sempre liste (nessuna interpolazione nella shell).
- Avanzamento letto da ``-progress pipe:1``.
- Cancellazione cooperativa tramite ``threading.Event``.
- Gli encoder hardware (NVENC) vengono considerati disponibili solo se una
  codifica di prova reale termina con successo.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

from .. import config


class FFmpegError(RuntimeError):
    def __init__(self, message: str, stderr: str = ""):
        super().__init__(message)
        self.stderr = stderr


class Cancelled(RuntimeError):
    pass


_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _find(name: str, configured: str) -> str:
    if configured and Path(configured).exists():
        return configured
    env = os.environ.get(f"SFS_{name.upper()}")
    if env and Path(env).exists():
        return env
    exe = name + (".exe" if sys.platform == "win32" else "")
    local = config.paths().bin / exe
    if local.exists():
        return str(local)
    found = shutil.which(name)
    if found:
        return found
    raise FFmpegError(
        f"{name} non trovato. Installa FFmpeg (Windows: 'winget install Gyan.FFmpeg') "
        f"oppure imposta il percorso nelle Impostazioni."
    )


def ffmpeg_bin() -> str:
    return _find("ffmpeg", config.settings().ffmpeg_path)


def ffprobe_bin() -> str:
    return _find("ffprobe", config.settings().ffprobe_path)


def run_ffmpeg(
    args: list[str],
    *,
    cwd: Optional[Path] = None,
    duration: Optional[float] = None,
    progress: Optional[Callable[[float], None]] = None,
    cancel: Optional[threading.Event] = None,
    timeout: Optional[float] = None,
) -> str:
    """Esegue ffmpeg e restituisce stderr. Solleva FFmpegError in caso di errore."""
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1", "-nostats", *args]
    with tempfile.TemporaryFile(mode="w+b") as errf:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=errf,
            creationflags=_CREATE_NO_WINDOW,
        )
        timer = None
        if timeout:
            timer = threading.Timer(timeout, proc.kill)
            timer.start()
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                if cancel is not None and cancel.is_set():
                    proc.kill()
                    proc.wait()
                    raise Cancelled("Operazione annullata")
                line = raw.decode("utf-8", "replace").strip()
                if progress and duration and line.startswith("out_time_us="):
                    try:
                        us = int(line.split("=", 1)[1])
                        progress(max(0.0, min(1.0, us / 1e6 / duration)))
                    except ValueError:
                        pass
            rc = proc.wait()
        finally:
            if timer:
                timer.cancel()
        errf.seek(0)
        stderr = errf.read().decode("utf-8", "replace")
    if rc != 0:
        tail = "\n".join(stderr.strip().splitlines()[-25:])
        raise FFmpegError(f"ffmpeg terminato con codice {rc}:\n{tail}", stderr)
    return stderr


def run_capture(args: list[str], *, timeout: Optional[float] = None) -> tuple[bytes, str]:
    """Esegue ffmpeg catturando stdout binario (per analisi PCM/frame)."""
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", *args]
    p = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=_CREATE_NO_WINDOW)
    err = p.stderr.decode("utf-8", "replace")
    if p.returncode != 0:
        raise FFmpegError(f"ffmpeg errore {p.returncode}: {err[-1500:]}", err)
    return p.stdout, err


def probe(path: Path | str) -> dict:
    p = Path(path)
    if not p.exists():
        raise FFmpegError(f"File non trovato: {p}")
    cmd = [ffprobe_bin(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(p)]
    r = subprocess.run(cmd, capture_output=True, timeout=60, creationflags=_CREATE_NO_WINDOW)
    if r.returncode != 0:
        raise FFmpegError(f"ffprobe non riesce a leggere {p.name}: {r.stderr.decode('utf-8','replace')[-500:]}")
    return json.loads(r.stdout.decode("utf-8", "replace") or "{}")


def version() -> str:
    r = subprocess.run([ffmpeg_bin(), "-version"], capture_output=True, creationflags=_CREATE_NO_WINDOW)
    first = r.stdout.decode("utf-8", "replace").splitlines()[0] if r.stdout else ""
    m = re.search(r"ffmpeg version (\S+)", first)
    return m.group(1) if m else first


@lru_cache(maxsize=1)
def available_encoders() -> frozenset[str]:
    r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-encoders"], capture_output=True, creationflags=_CREATE_NO_WINDOW)
    names = set()
    for line in r.stdout.decode("utf-8", "replace").splitlines():
        m = re.match(r"\s*[VAS][\.FSXBD]{5}\s+(\S+)", line)
        if m:
            names.add(m.group(1))
    return frozenset(names)


@lru_cache(maxsize=1)
def available_filters() -> frozenset[str]:
    r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-filters"], capture_output=True, creationflags=_CREATE_NO_WINDOW)
    names = set()
    for line in r.stdout.decode("utf-8", "replace").splitlines():
        m = re.match(r"\s*[TSC\.]{2,3}\s+(\S+)\s+\S+->\S+", line)
        if m:
            names.add(m.group(1))
    return frozenset(names)


_verified: dict[str, tuple[bool, str]] = {}


def verify_encoder(name: str) -> tuple[bool, str]:
    """Codifica di prova reale (0.5 s, 640x360). Restituisce (ok, motivo)."""
    if name in _verified:
        return _verified[name]
    if name not in available_encoders():
        res = (False, "encoder non compilato in questo FFmpeg")
    else:
        try:
            run_ffmpeg(
                ["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30", "-t", "0.5",
                 "-pix_fmt", "yuv420p", "-c:v", name, "-f", "null", "-"],
                timeout=60,
            )
            res = (True, "codifica di prova riuscita")
        except Exception as e:  # driver/GPU mancanti
            msg = str(e).strip().splitlines()
            res = (False, (msg[-1] if msg else "errore")[:200])
    _verified[name] = res
    return res


@dataclass
class EncoderChoice:
    codec: str  # h264 | hevc
    encoder: str
    hardware: bool
    reason: str


def pick_encoder(codec: str = "h264", prefer: str = "auto") -> EncoderChoice:
    codec = "hevc" if codec in ("hevc", "h265") else "h264"
    gpu_name = "h264_nvenc" if codec == "h264" else "hevc_nvenc"
    cpu_name = "libx264" if codec == "h264" else "libx265"
    gpu_setting = config.settings().gpu_encoding
    if prefer in ("auto", "gpu") and gpu_setting != "off":
        ok, why = verify_encoder(gpu_name)
        if ok:
            return EncoderChoice(codec, gpu_name, True, "NVENC verificato con codifica di prova")
        gpu_reason = f"NVENC non utilizzabile ({why})"
    else:
        gpu_reason = "GPU disattivata nelle impostazioni"
    ok, why = verify_encoder(cpu_name)
    if not ok:
        raise FFmpegError(f"Nessun encoder {codec} disponibile: {gpu_reason}; {cpu_name}: {why}")
    return EncoderChoice(codec, cpu_name, False, f"{gpu_reason}; uso {cpu_name}")


def video_encode_args(choice: EncoderChoice, *, quality: str, height: int) -> list[str]:
    """Parametri di codifica. quality: final | high | preview | intermediate"""
    enc = choice.encoder
    big = height >= 2000
    color = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]
    if enc == "libx264":
        if quality == "preview":
            q = ["-preset", "veryfast", "-crf", "23"]
        elif quality == "intermediate":
            q = ["-preset", "veryfast", "-crf", "12"]
        else:
            q = ["-preset", "slow" if not big else "medium", "-crf", "18"]
        return ["-c:v", "libx264", *q, "-profile:v", "high", "-pix_fmt", "yuv420p", *color]
    if enc == "libx265":
        crf = "26" if quality == "preview" else ("14" if quality == "intermediate" else "20")
        preset = "fast" if quality in ("preview", "intermediate") else "medium"
        return ["-c:v", "libx265", "-preset", preset, "-crf", crf, "-tag:v", "hvc1",
                "-pix_fmt", "yuv420p", "-x265-params", "log-level=error", *color]
    if enc in ("h264_nvenc", "hevc_nvenc"):
        cq = {"preview": "26", "intermediate": "15"}.get(quality, "19" if enc == "h264_nvenc" else "21")
        preset = "p4" if quality in ("preview", "intermediate") else "p6"
        args = ["-c:v", enc, "-preset", preset, "-tune", "hq", "-rc", "vbr", "-cq", cq, "-b:v", "0",
                "-spatial_aq", "1", "-pix_fmt", "yuv420p", *color]
        if enc == "hevc_nvenc":
            args += ["-tag:v", "hvc1", "-profile:v", "main"]
        else:
            args += ["-profile:v", "high"]
        return args
    raise FFmpegError(f"Encoder non gestito: {enc}")


def parse_fps(s: str | None) -> float:
    if not s or s in ("0/0",):
        return 0.0
    if "/" in s:
        a, b = s.split("/", 1)
        try:
            return float(a) / float(b) if float(b) else 0.0
        except ValueError:
            return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def nvidia_gpu_name() -> str:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return ""
    try:
        r = subprocess.run([exe, "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
                           capture_output=True, timeout=10, creationflags=_CREATE_NO_WINDOW)
        return r.stdout.decode("utf-8", "replace").strip().splitlines()[0] if r.returncode == 0 else ""
    except Exception:
        return ""

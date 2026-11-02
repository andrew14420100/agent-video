"""Trascrizione locale con faster-whisper (MIT). GPU CUDA se disponibile,
altrimenti CPU int8. L'audio viene decodificato con FFmpeg (non con PyAV)."""
from __future__ import annotations

import importlib.util
import threading
from pathlib import Path
from typing import Optional

from .. import config
from ..media.analysis import decode_mono

_model = None
_model_desc = ""
_lock = threading.Lock()


class ASRUnavailable(RuntimeError):
    pass


def installed() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


def status() -> dict:
    return {"engine": "faster-whisper", "installed": installed(), "model": config.settings().whisper_model,
            "loaded": _model_desc or None}


def _load():
    global _model, _model_desc
    if _model is not None:
        return _model
    if not installed():
        raise ASRUnavailable("faster-whisper non installato (pip install faster-whisper)")
    from faster_whisper import WhisperModel  # type: ignore

    s = config.settings()
    attempts = []
    if s.whisper_device in ("auto", "cuda"):
        attempts.append(("cuda", "float16"))
    if s.whisper_device in ("auto", "cpu"):
        attempts.append(("cpu", "int8"))
    last = None
    for dev, ct in attempts:
        try:
            m = WhisperModel(s.whisper_model, device=dev, compute_type=ct,
                             download_root=str(config.paths().data_dir / "models" / "whisper"))
            # prova reale: la CUDA può fallire solo all'inferenza
            import numpy as np
            list(m.transcribe(np.zeros(16000, np.float32), language="it")[0])
            _model, _model_desc = m, f"{s.whisper_model} su {dev} ({ct})"
            return _model
        except Exception as e:  # noqa: BLE001
            last = e
    raise ASRUnavailable(f"Impossibile caricare Whisper: {last}")


def transcribe(path: Path, language: Optional[str] = "it", start: float = 0.0,
               duration: Optional[float] = None) -> list[dict]:
    """Restituisce segmenti [{start,end,text,words:[(s,e,w)], no_speech_prob}] in secondi
    relativi a ``start`` del file."""
    with _lock:
        model = _load()
        audio = decode_mono(path, 16000, start, duration)
        if audio.size < 1600:
            return []
        segs, _info = model.transcribe(audio, language=language, word_timestamps=True,
                                       vad_filter=True, beam_size=5)
        out = []
        for s in segs:
            out.append({
                "start": float(s.start), "end": float(s.end), "text": s.text.strip(),
                "no_speech_prob": float(getattr(s, "no_speech_prob", 0.0)),
                "words": [(float(w.start), float(w.end), w.word.strip()) for w in (s.words or [])],
            })
        return out


def model_description() -> str:
    return _model_desc

"""Sintesi vocale locale con Piper (https://github.com/OHF-Voice/piper1-gpl).

Piper è un processo/libreria esterna opzionale; le voci (.onnx + .onnx.json)
vanno messe nella cartella ``voices`` dei dati dell'applicazione. Ogni voce ha
una propria licenza (vedi la MODEL_CARD della voce) che va verificata dall'utente.
"""
from __future__ import annotations

import importlib.util
import re
import wave
from pathlib import Path
from typing import Optional

from .. import config


class TTSUnavailable(RuntimeError):
    pass


def piper_installed() -> bool:
    return importlib.util.find_spec("piper") is not None


def list_voices() -> list[Path]:
    vs = sorted(config.paths().voices.glob("*.onnx"))
    s = config.settings().piper_voice
    if s and Path(s).exists() and Path(s) not in vs:
        vs.insert(0, Path(s))
    return [v for v in vs if Path(str(v) + ".json").exists()]


def pick_voice(language: str = "it") -> Optional[Path]:
    s = config.settings().piper_voice
    if s and Path(s).exists():
        return Path(s)
    vs = list_voices()
    pref = [v for v in vs if v.name.lower().startswith(language.lower())]
    return (pref or vs or [None])[0]


def status(language: str = "it") -> dict:
    v = pick_voice(language)
    return {"engine": "piper", "installed": piper_installed(), "voice": v.name if v else None,
            "voices": [x.name for x in list_voices()],
            "ready": piper_installed() and v is not None}


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text.strip())
    if not text:
        return []
    parts = re.split(r"(?<=[.!?…])\s+(?=[A-ZÀ-Ý0-9\"«])", text)
    out = []
    for p in parts:
        p = p.strip()
        if len(p) > 220:  # frasi molto lunghe: spezza su punto e virgola/due punti
            out += [x.strip() for x in re.split(r"(?<=[;:])\s+", p) if x.strip()]
        elif p:
            out.append(p)
    return out


_voice_cache: dict[str, object] = {}


def synthesize(sentences: list[str], out_dir: Path, *, length_scale: float = 1.0,
               language: str = "it", prefix: str = "voice") -> list[tuple[Path, float, str]]:
    """Sintetizza ogni frase in un WAV. Restituisce [(file, durata, testo)]."""
    if not piper_installed():
        raise TTSUnavailable("Piper non installato (pip install piper-tts)")
    voice_path = pick_voice(language)
    if voice_path is None:
        raise TTSUnavailable("Nessuna voce Piper trovata nella cartella voices")
    from piper import PiperVoice  # type: ignore

    try:
        from piper import SynthesisConfig  # type: ignore
    except ImportError:  # versioni precedenti
        SynthesisConfig = None  # type: ignore
    key = str(voice_path)
    if key not in _voice_cache:
        _voice_cache[key] = PiperVoice.load(str(voice_path))
    voice = _voice_cache[key]
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for i, s in enumerate(sentences):
        f = out_dir / f"{prefix}_{i:03d}.wav"
        with wave.open(str(f), "wb") as wf:
            if SynthesisConfig is not None:
                voice.synthesize_wav(s, wf, syn_config=SynthesisConfig(length_scale=length_scale))  # type: ignore
            else:  # pragma: no cover
                voice.synthesize(s, wf, length_scale=length_scale)  # type: ignore
        with wave.open(str(f), "rb") as rf:
            dur = rf.getnframes() / float(rf.getframerate())
        results.append((f, dur, s))
    return results

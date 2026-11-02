"""Client minimale per un LLM locale tramite Ollama (opzionale).

Usato solo dall'agente di pianificazione per interpretare la richiesta e
scrivere un testo di narrazione. Se Ollama non è in esecuzione la pipeline usa
il pianificatore a regole e lo dichiara nel report.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Optional

from . import config


class LLMUnavailable(RuntimeError):
    pass


def _url(path: str) -> str:
    return config.settings().ollama_url.rstrip("/") + path


def status() -> dict:
    s = config.settings()
    if not s.llm_enabled:
        return {"enabled": False, "reachable": False, "model": s.ollama_model, "models": []}
    try:
        with urllib.request.urlopen(_url("/api/tags"), timeout=2) as r:
            models = [m.get("name") for m in json.loads(r.read()).get("models", [])]
        return {"enabled": True, "reachable": True, "model": s.ollama_model, "models": models,
                "model_present": s.ollama_model in models}
    except Exception:
        return {"enabled": True, "reachable": False, "model": s.ollama_model, "models": []}


def chat_json(system: str, user: str, schema: dict, timeout: float = 180) -> dict:
    s = config.settings()
    st = status()
    if not st["reachable"]:
        raise LLMUnavailable("Ollama non raggiungibile")
    if not st.get("model_present"):
        raise LLMUnavailable(f"Modello '{s.ollama_model}' non installato in Ollama (ollama pull {s.ollama_model})")
    body = json.dumps({
        "model": s.ollama_model, "stream": False, "format": schema,
        "options": {"temperature": 0.4},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(_url("/api/chat"), data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
    except (urllib.error.URLError, TimeoutError) as e:
        raise LLMUnavailable(f"Errore Ollama: {e}") from e
    content = (data.get("message") or {}).get("content", "")
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise LLMUnavailable("Risposta LLM non in JSON valido") from e


def model_name() -> Optional[str]:
    return config.settings().ollama_model

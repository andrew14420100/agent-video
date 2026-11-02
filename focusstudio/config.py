"""Impostazioni applicative persistenti (JSON) e percorsi di lavoro."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from pydantic import BaseModel, Field

APP_NAME = "Steam Focus Studio"
APP_VERSION = "0.1.0"


def default_data_dir() -> Path:
    env = os.environ.get("SFS_DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(base) / "SteamFocusStudio"
    return Path.home() / ".steam-focus-studio"


class Settings(BaseModel):
    # Binari esterni (vuoto = ricerca automatica nel PATH / data_dir/bin)
    ffmpeg_path: str = ""
    ffprobe_path: str = ""
    # Accelerazione GPU: "auto" usa NVENC solo se una codifica di prova riesce davvero
    gpu_encoding: str = "auto"  # auto | off
    # LLM locale opzionale (Ollama). Se non raggiungibile si usa il pianificatore a regole.
    llm_enabled: bool = True
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:14b-instruct"
    # Voce (Piper, locale). Percorso del modello .onnx; vuoto = primo modello in data_dir/voices
    piper_voice: str = ""
    # Trascrizione (faster-whisper, locale)
    whisper_model: str = "small"
    whisper_device: str = "auto"  # auto | cuda | cpu
    # Ricerca online di materiale con licenza libera
    online_research: bool = True
    allowed_licenses: list[str] = Field(
        default_factory=lambda: ["cc0", "pdm", "public-domain", "public-domain-us-gov", "by", "by-sa"]
    )
    max_download_mb: int = 400
    # Font per titoli e sottotitoli (nome famiglia; libass lo cerca tra i font di sistema)
    font_family: str = "Segoe UI" if sys.platform == "win32" else "Inter"
    # Server UI
    host: str = "127.0.0.1"
    port: int = 8765


class Paths:
    def __init__(self, data_dir: Path | None = None):
        self.data_dir = Path(data_dir or default_data_dir())
        self.projects = self.data_dir / "projects"
        self.voices = self.data_dir / "voices"
        self.bin = self.data_dir / "bin"
        self.cache = self.data_dir / "cache"
        self.settings_file = self.data_dir / "settings.json"

    def ensure(self) -> None:
        for p in (self.data_dir, self.projects, self.voices, self.bin, self.cache):
            p.mkdir(parents=True, exist_ok=True)


_paths: Paths | None = None
_settings: Settings | None = None


def paths() -> Paths:
    global _paths
    if _paths is None:
        _paths = Paths()
        _paths.ensure()
    return _paths


def set_data_dir(path: Path) -> None:
    """Usato da test e CLI per isolare i dati."""
    global _paths, _settings
    _paths = Paths(path)
    _paths.ensure()
    _settings = None


def settings() -> Settings:
    global _settings
    if _settings is None:
        f = paths().settings_file
        if f.exists():
            try:
                _settings = Settings.model_validate_json(f.read_text(encoding="utf-8"))
            except Exception:
                _settings = Settings()
        else:
            _settings = Settings()
    return _settings


def save_settings(new: Settings) -> None:
    global _settings
    _settings = new
    paths().settings_file.write_text(json.dumps(new.model_dump(), indent=2), encoding="utf-8")

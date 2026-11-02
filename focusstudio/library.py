"""Importazione e catalogazione degli asset di un progetto."""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Optional

from .media import analysis as an
from .media import ffmpeg as ff
from .project import Asset, LicenseInfo, Project

USER_LICENSE = LicenseInfo(code="user", name="Materiale dell'utente",
                           notes="Importato dall'utente, che dichiara di avere i diritti d'uso.")


def safe_name(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).stem).strip("._")[:60] or "file"
    return stem + Path(name).suffix.lower()


def import_file(project: Project, src: Path, *, copy: bool = True, license: Optional[LicenseInfo] = None,
                source: str = "local", origin_url: str = "", description: str = "", tags: Optional[list[str]] = None,
                role: str = "", analyze: bool = False, name: Optional[str] = None) -> Asset:
    src = Path(src)
    if not src.exists():
        raise FileNotFoundError(str(src))
    info = an.media_info(src)
    digest = an.sha256_file(src, limit_mb=256)
    for a in project.assets:
        if a.sha256 == digest and a.role == role:
            return a  # già importato
    project.ensure_dirs()
    if copy and not str(src.resolve()).startswith(str(project.dir.resolve())):
        dest = project.media_dir / safe_name(src.name)
        i = 1
        while dest.exists():
            dest = project.media_dir / f"{Path(safe_name(src.name)).stem}_{i}{src.suffix.lower()}"
            i += 1
        shutil.copy2(src, dest)
    else:
        dest = src
    asset = Asset(kind=info["kind"], name=name or src.name, path=project.rel(dest), source=source,
                  origin_url=origin_url, license=license or USER_LICENSE.model_copy(), sha256=digest,
                  info=info, description=description, tags=tags or _tags_from_name(src.name), role=role)
    thumb = project.dir / "thumbs" / f"{asset.id}.jpg"
    if an.make_thumbnail(project.asset_path(asset), thumb, asset.kind, float(info.get("duration") or 0)):
        asset.thumb = project.rel(thumb)
    if analyze:
        analyze_asset(project, asset)
    project.assets.append(asset)
    return asset


def _tags_from_name(name: str) -> list[str]:
    words = re.split(r"[^A-Za-zÀ-ÿ0-9]+", Path(name).stem.lower())
    return [w for w in words if len(w) > 2 and not w.isdigit()][:12]


def analyze_asset(project: Project, asset: Asset) -> dict:
    p = project.asset_path(asset)
    if asset.kind == "video":
        asset.analysis = an.analyze_video(p, asset.info)
    elif asset.kind == "audio":
        asset.analysis = an.analyze_audio(p, asset.info)
    else:
        asset.analysis = an.analyze_image(p)
    return asset.analysis


def remove_asset(project: Project, asset_id: str) -> None:
    a = project.asset(asset_id)
    tl = project.timeline
    tl.video = [c for c in tl.video if c.asset_id != asset_id]
    tl.audio = [c for c in tl.audio if c.asset_id != asset_id]
    if tl.logo and tl.logo.asset_id == asset_id:
        tl.logo = None
    project.assets = [x for x in project.assets if x.id != asset_id]
    p = project.asset_path(a)
    if str(p.resolve()).startswith(str(project.media_dir.resolve())):
        p.unlink(missing_ok=True)


__all__ = ["import_file", "analyze_asset", "remove_asset", "ff"]

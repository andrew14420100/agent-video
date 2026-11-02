"""Ricerca di materiale riutilizzabile legalmente da fonti con licenza esplicita.

Fonti (gratuite, senza chiavi API):
- **NASA Image and Video Library** (images-api.nasa.gov): media prodotti da NASA,
  in genere non protetti da copyright negli USA. Gli elementi che citano terzi o
  copyright vengono marcati come licenza sconosciuta e quindi esclusi.
- **Openverse** (api.openverse.org): immagini e audio con licenze Creative Commons;
  la ricerca è filtrata su licenze che consentono uso commerciale e modifiche.
- **Wikimedia Commons**: video/immagini con licenza letta dai metadati del file.

Nessun contenuto viene scaricato se la licenza non è tra quelle consentite.
"""
from __future__ import annotations

import json
import re
import ssl
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .. import config
from ..project import LicenseInfo

UA = "SteamFocusStudio/0.1 (local video editor; https://github.com/andrew14420100/agent-video)"
TIMEOUT = 25


class ResearchError(RuntimeError):
    pass


@dataclass
class SearchResult:
    provider: str
    id: str
    kind: str  # video | image | audio
    title: str
    description: str
    thumb: str
    landing_url: str
    download_url: str = ""  # può essere risolto in seguito (NASA)
    width: int = 0
    height: int = 0
    duration: float = 0.0
    license: LicenseInfo = field(default_factory=LicenseInfo)
    tags: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["license"] = self.license.model_dump()
        return d

    @staticmethod
    def from_dict(d: dict) -> "SearchResult":
        d = dict(d)
        d["license"] = LicenseInfo(**d.get("license", {}))
        return SearchResult(**d)


def _ctx() -> ssl.SSLContext:
    return ssl.create_default_context()


def http_json(url: str, params: Optional[dict] = None) -> dict | list:
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ctx()) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise ResearchError(f"HTTP {e.code} da {urllib.parse.urlparse(url).netloc}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ResearchError(f"Rete non raggiungibile ({urllib.parse.urlparse(url).netloc}): {e}") from e


def _quote_url(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    path = urllib.parse.quote(urllib.parse.unquote(p.path), safe="/~")
    scheme = "https" if p.scheme in ("http", "https") else p.scheme
    return urllib.parse.urlunsplit((scheme, p.netloc, path, p.query, p.fragment))


def head_size(url: str) -> int:
    req = urllib.request.Request(_quote_url(url), method="HEAD", headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ctx()) as r:
            return int(r.headers.get("Content-Length") or 0)
    except Exception:
        return 0


def download(url: str, dest: Path, max_mb: int, progress=None, cancel=None) -> Path:
    req = urllib.request.Request(_quote_url(url), headers={"User-Agent": UA})
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ctx()) as r:
            total = int(r.headers.get("Content-Length") or 0)
            if total and total > max_mb * 1024 * 1024:
                raise ResearchError(f"File troppo grande ({total / 1e6:.0f} MB > {max_mb} MB)")
            read = 0
            with open(tmp, "wb") as f:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise ResearchError("Download annullato")
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    read += len(chunk)
                    if read > max_mb * 1024 * 1024:
                        raise ResearchError(f"Download oltre il limite di {max_mb} MB")
                    if progress and total:
                        progress(read / total)
    except urllib.error.HTTPError as e:
        tmp.unlink(missing_ok=True)
        raise ResearchError(f"Download fallito: HTTP {e.code}") from e
    except (urllib.error.URLError, OSError) as e:
        tmp.unlink(missing_ok=True)
        if isinstance(e, ResearchError):
            raise
        raise ResearchError(f"Download fallito: {e}") from e
    except ResearchError:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(dest)
    return dest


def license_allowed(lic: LicenseInfo) -> bool:
    return lic.code in config.settings().allowed_licenses


# --------------------------------------------------------------------------- NASA

_THIRD_PARTY = re.compile(r"©|\(c\)|copyright|courtesy of|all rights reserved|used with permission", re.I)


def nasa_search(query: str, kind: str = "video", limit: int = 12) -> list[SearchResult]:
    data = http_json("https://images-api.nasa.gov/search", {"q": query, "media_type": kind, "page_size": limit})
    out = []
    for it in data.get("collection", {}).get("items", [])[:limit]:
        d = (it.get("data") or [{}])[0]
        links = it.get("links") or []
        thumb = next((lnk["href"] for lnk in links if lnk.get("render") == "image"), "")
        nasa_id = d.get("nasa_id", "")
        desc = (d.get("description") or "")[:1200]
        credit = " ".join(str(d.get(k, "")) for k in ("photographer", "secondary_creator", "center"))
        lic = LicenseInfo(
            code="public-domain-us-gov",
            name="NASA – media generalmente non protetti da copyright (USA)",
            url="https://www.nasa.gov/nasa-brand-center/images-and-media/",
            attribution=f"NASA{(' / ' + d['secondary_creator']) if d.get('secondary_creator') else ''}",
            requires_attribution=True,
            notes="Linee guida NASA: citare NASA, non usare loghi/insegne per implicare approvazione.",
        )
        if _THIRD_PARTY.search(desc + " " + credit) or d.get("photographer", "").lower().startswith("courtesy"):
            lic = LicenseInfo(code="unknown", name="Possibile materiale di terzi",
                              notes="La descrizione cita copyright/terze parti: escluso automaticamente.")
        out.append(SearchResult(
            provider="nasa", id=nasa_id, kind=kind, title=d.get("title", nasa_id), description=desc,
            thumb=_quote_url(thumb) if thumb else "",
            landing_url=f"https://images.nasa.gov/details/{urllib.parse.quote(nasa_id)}",
            license=lic, tags=[k.lower() for k in (d.get("keywords") or [])][:15],
            extra={"collection": it.get("href", ""), "date": d.get("date_created", "")},
        ))
    return out


def nasa_resolve(res: SearchResult, want_height: int, max_mb: int) -> SearchResult:
    """Sceglie la variante di file più adatta (risoluzione/peso) dal manifest NASA."""
    files = http_json(_quote_url(res.extra["collection"]))
    if not isinstance(files, list):
        raise ResearchError("Manifest NASA non valido")
    if res.kind == "video":
        order = ["~orig.mp4", "~large.mp4", "~medium.mp4", "~small.mp4"] if want_height >= 2000 \
            else ["~large.mp4", "~orig.mp4", "~medium.mp4"]
    else:
        order = ["~orig.jpg", "~large.jpg", "~orig.png", "~medium.jpg"]
    for suffix in order:
        url = next((f for f in files if f.endswith(suffix)), None)
        if not url:
            continue
        size = head_size(url)
        if size and size > max_mb * 1024 * 1024:
            continue
        res.download_url = _quote_url(url)
        res.extra["size"] = size
        return res
    raise ResearchError("Nessuna variante entro il limite di dimensione")


# --------------------------------------------------------------------------- Openverse

_OV_CODES = {"cc0": "cc0", "pdm": "pdm", "by": "by", "by-sa": "by-sa"}


def openverse_search(query: str, kind: str = "image", limit: int = 12) -> list[SearchResult]:
    endpoint = "images" if kind == "image" else "audio"
    data = http_json(f"https://api.openverse.org/v1/{endpoint}/", {
        "q": query, "license": "cc0,pdm,by,by-sa", "page_size": limit, "mature": "false",
    })
    out = []
    for r in data.get("results", [])[:limit]:
        code = _OV_CODES.get((r.get("license") or "").lower(), "unknown")
        ver = r.get("license_version") or ""
        lic = LicenseInfo(
            code=code, name=f"CC {r.get('license', '').upper()} {ver}".strip() if code not in ("pdm",) else "Public Domain Mark",
            url=r.get("license_url") or "", attribution=r.get("attribution") or
            f"\"{r.get('title', '')}\" di {r.get('creator', 'autore sconosciuto')}",
            requires_attribution=code in ("by", "by-sa"),
            notes=f"Fonte originale: {r.get('source', '')}",
        )
        out.append(SearchResult(
            provider="openverse", id=r.get("id", ""), kind=kind, title=r.get("title") or "",
            description=(r.get("description") or "")[:600] if isinstance(r.get("description"), str) else "",
            thumb=r.get("thumbnail") or "", landing_url=r.get("foreign_landing_url") or "",
            download_url=r.get("url") or "", width=int(r.get("width") or 0), height=int(r.get("height") or 0),
            duration=float(r.get("duration") or 0) / 1000.0, license=lic,
            tags=[t.get("name", "").lower() for t in (r.get("tags") or []) if isinstance(t, dict)][:15],
            extra={"filetype": r.get("filetype") or "", "creator": r.get("creator") or ""},
        ))
    return out


# --------------------------------------------------------------------------- Wikimedia Commons

def _wm_license(meta: dict) -> LicenseInfo:
    short = (meta.get("LicenseShortName", {}).get("value") or "").strip()
    url = meta.get("LicenseUrl", {}).get("value") or ""
    artist = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value") or "").strip()
    s = short.lower()
    if s in ("cc0", "cc0 1.0") or "cc0" in s:
        code = "cc0"
    elif "public domain" in s or s == "pd" or s.startswith("pd-"):
        code = "public-domain"
    elif s.startswith("cc by-sa"):
        code = "by-sa"
    elif s.startswith("cc by") and "nc" not in s and "nd" not in s:
        code = "by"
    else:
        code = "unknown"
    return LicenseInfo(code=code, name=short or "Sconosciuta", url=url,
                       attribution=f"{artist or 'Autore sconosciuto'}, {short} (Wikimedia Commons)",
                       requires_attribution=code in ("by", "by-sa"))


def wikimedia_search(query: str, kind: str = "video", limit: int = 10) -> list[SearchResult]:
    ft = "filetype:video" if kind == "video" else "filetype:bitmap"
    data = http_json("https://commons.wikimedia.org/w/api.php", {
        "action": "query", "format": "json", "generator": "search", "gsrsearch": f"{query} {ft}",
        "gsrnamespace": 6, "gsrlimit": limit, "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata|mediatype", "iiurlwidth": 480,
    })
    out = []
    for page in (data.get("query", {}).get("pages", {}) or {}).values():
        ii = (page.get("imageinfo") or [{}])[0]
        meta = ii.get("extmetadata") or {}
        title = page.get("title", "").replace("File:", "")
        desc = re.sub(r"<[^>]+>", "", (meta.get("ImageDescription", {}).get("value") or ""))[:600]
        out.append(SearchResult(
            provider="wikimedia", id=str(page.get("pageid")), kind=kind, title=title, description=desc,
            thumb=ii.get("thumburl", ""), landing_url=ii.get("descriptionurl", ""),
            download_url=ii.get("url", ""), width=int(ii.get("width") or 0), height=int(ii.get("height") or 0),
            duration=float(ii.get("duration") or 0), license=_wm_license(meta),
            tags=[w.lower() for w in re.split(r"\W+", title) if len(w) > 2][:10],
            extra={"size": int(ii.get("size") or 0), "mime": ii.get("mime", "")},
        ))
    return out


PROVIDERS = {
    "nasa": {"search": nasa_search, "kinds": ["video", "image"], "label": "NASA Image & Video Library"},
    "openverse": {"search": openverse_search, "kinds": ["image", "audio"], "label": "Openverse (Creative Commons)"},
    "wikimedia": {"search": wikimedia_search, "kinds": ["video", "image"], "label": "Wikimedia Commons"},
}


def search(provider: str, query: str, kind: str, limit: int = 12) -> list[SearchResult]:
    if not config.settings().online_research:
        raise ResearchError("Ricerca online disattivata nelle impostazioni")
    p = PROVIDERS.get(provider)
    if not p:
        raise ResearchError(f"Provider sconosciuto: {provider}")
    if kind not in p["kinds"]:
        raise ResearchError(f"{p['label']} non fornisce '{kind}'")
    return p["search"](query, kind, limit)


def fetch(res: SearchResult, dest_dir: Path, want_height: int = 1080, cancel=None) -> Path:
    """Scarica un risultato (solo se la licenza è consentita)."""
    if not license_allowed(res.license):
        raise ResearchError(f"Licenza non consentita ({res.license.code}): download rifiutato")
    max_mb = config.settings().max_download_mb
    if res.provider == "nasa" and not res.download_url:
        nasa_resolve(res, want_height, max_mb)
    if not res.download_url:
        raise ResearchError("URL di download mancante")
    ext = Path(urllib.parse.urlsplit(res.download_url).path).suffix.lower() or (
        ".mp4" if res.kind == "video" else ".jpg" if res.kind == "image" else ".mp3")
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", f"{res.provider}_{res.title}")[:70].strip("_")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{stem}{ext}"
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    return download(res.download_url, dest, max_mb, cancel=cancel)

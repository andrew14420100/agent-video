"""Analisi reale dei media: metadati ffprobe, tagli di scena, esposizione,
nitidezza, movimento, fotogrammi neri/congelati, livelli audio, beat musicali.

Tutte le misure derivano dalla decodifica dei file (FFmpeg + NumPy); nessuna
valutazione "semantica" del contenuto viene dichiarata.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import numpy as np

from . import ffmpeg as ff

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
AUDIO_EXT = {".mp3", ".wav", ".flac", ".ogg", ".oga", ".m4a", ".aac", ".opus"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mts", ".m2ts", ".ogv", ".wmv"}

AW, AH = 160, 90  # risoluzione di analisi
AFPS = 4.0  # campioni al secondo


def sha256_file(path: Path, limit_mb: int = 0) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        read = 0
        while chunk := f.read(1 << 20):
            h.update(chunk)
            read += len(chunk)
            if limit_mb and read >= limit_mb << 20:
                break
    return h.hexdigest()


def media_info(path: Path) -> dict:
    """Metadati normalizzati e tipo di media (video/image/audio)."""
    d = ff.probe(path)
    streams = d.get("streams", [])
    fmt = d.get("format", {})
    v = next((s for s in streams if s.get("codec_type") == "video"
              and not s.get("disposition", {}).get("attached_pic")), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    ext = path.suffix.lower()
    dur = float(fmt.get("duration") or 0) or float((v or a or {}).get("duration") or 0)
    info: dict = {"format": fmt.get("format_name", ""), "size": int(fmt.get("size") or 0), "duration": round(dur, 3)}
    if v is not None:
        rot = 0
        for sd in v.get("side_data_list", []) or []:
            if "rotation" in sd:
                rot = int(float(sd["rotation"]))
        if "rotate" in (v.get("tags") or {}):
            rot = int(float(v["tags"]["rotate"]))
        w, h = int(v.get("width") or 0), int(v.get("height") or 0)
        if abs(rot) in (90, 270):
            w, h = h, w
        fps = ff.parse_fps(v.get("avg_frame_rate")) or ff.parse_fps(v.get("r_frame_rate"))
        info.update({"width": w, "height": h, "fps": round(fps, 3), "vcodec": v.get("codec_name", ""),
                     "pix_fmt": v.get("pix_fmt", ""), "rotation": rot})
    if a is not None:
        info.update({"has_audio": True, "acodec": a.get("codec_name", ""),
                     "sample_rate": int(a.get("sample_rate") or 0), "channels": int(a.get("channels") or 0)})
    else:
        info["has_audio"] = False
    if v is not None and (ext in IMAGE_EXT or v.get("codec_name") in ("mjpeg", "png", "webp", "bmp", "tiff")) \
            and dur < 0.5 and ext not in VIDEO_EXT:
        kind = "image"
    elif v is not None and ext not in AUDIO_EXT:
        kind = "video"
    elif a is not None:
        kind = "audio"
    else:
        raise ff.FFmpegError(f"Nessuno stream utilizzabile in {path.name}")
    info["kind"] = kind
    return info


# --------------------------------------------------------------------------- video

def _decode_and_cuts(path: Path, duration: float) -> tuple[np.ndarray, list[float]]:
    """Un solo passaggio di decodifica: fotogrammi in scala di grigi (4 fps,
    160x90) su stdout e tagli di scena (filtro scdet) su stderr."""
    use_scdet = "scdet" in ff.available_filters()
    if use_scdet:
        fc = (f"[0:v]scale=320:-2:flags=fast_bilinear,split[a][b];[a]scdet=threshold=12,nullsink;"
              f"[b]fps={AFPS},scale={AW}:{AH}:flags=area,format=gray[g]")
    else:
        fc = f"[0:v]fps={AFPS},scale={AW}:{AH}:flags=area,format=gray[g]"
    raw, err = ff.run_capture([
        "-v", "info", "-hwaccel", "auto", "-i", str(path), "-an", "-sn",
        "-filter_complex", fc, "-map", "[g]", "-f", "rawvideo", "-",
    ], timeout=max(180, duration * 4))
    n = len(raw) // (AW * AH)
    frames = np.frombuffer(raw[: n * AW * AH], np.uint8).reshape(n, AH, AW).astype(np.float32) / 255.0
    cuts = [float(m.group(1)) for m in re.finditer(r"lavfi\.scd\.time:\s*([0-9.]+)", err)]
    out: list[float] = []
    for c in sorted(cuts):
        if c > 0.05 and (not out or c - out[-1] > 0.3):
            out.append(round(c, 3))
    return frames, out


def _runs(mask: np.ndarray, min_len: int) -> list[tuple[int, int]]:
    out, start = [], None
    for i, m in enumerate(list(mask) + [False]):
        if m and start is None:
            start = i
        elif not m and start is not None:
            if i - start >= min_len:
                out.append((start, i))
            start = None
    return out


def analyze_video(path: Path, info: dict) -> dict:
    dur = float(info.get("duration") or 0)
    frames, cuts = _decode_and_cuts(path, dur)
    n = len(frames)
    if n == 0:
        raise ff.FFmpegError("Decodifica video fallita (0 fotogrammi)")
    brightness = frames.mean(axis=(1, 2))
    contrast = frames.std(axis=(1, 2))
    # nitidezza: varianza del laplaciano
    lap = (-4 * frames[:, 1:-1, 1:-1] + frames[:, :-2, 1:-1] + frames[:, 2:, 1:-1]
           + frames[:, 1:-1, :-2] + frames[:, 1:-1, 2:])
    sharp = lap.reshape(n, -1).var(axis=1)
    motion = np.zeros(n, np.float32)
    if n > 1:
        motion[1:] = np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2))
        motion[0] = motion[1]
    black = (brightness < 0.06) & (contrast < 0.03)
    frozen = motion < 0.0015
    black_runs = [(round(a / AFPS, 2), round(b / AFPS, 2)) for a, b in _runs(black, 2)]
    frozen_runs = [(round(a / AFPS, 2), round(b / AFPS, 2)) for a, b in _runs(frozen, int(AFPS * 2))]

    # punteggio qualità per campione (0..1): esposizione, contrasto, nitidezza, movimento moderato
    sharp_n = np.clip(sharp / (np.percentile(sharp, 90) + 1e-6), 0, 1)
    expo = 1.0 - np.clip(np.abs(brightness - 0.45) / 0.45, 0, 1)
    contr = np.clip(contrast / 0.22, 0, 1)
    mot = np.clip(motion / 0.04, 0, 1)
    mot_score = 1.0 - np.abs(mot - 0.45) / 0.55  # preferisce movimento presente ma non caotico
    score = 0.3 * sharp_n + 0.25 * expo + 0.2 * contr + 0.25 * np.clip(mot_score, 0, 1)
    score[black] = 0.0
    score[frozen] *= 0.4

    def r(x):
        return [round(float(v), 4) for v in x]

    return {
        "version": 1,
        "sample_rate": AFPS,
        "samples": n,
        "brightness": r(brightness),
        "contrast": r(contrast),
        "sharpness": r(sharp_n),
        "motion": r(motion),
        "score": r(score),
        "scene_cuts": cuts,
        "black": black_runs,
        "frozen": frozen_runs,
        "summary": {
            "mean_score": round(float(score.mean()), 3),
            "mean_brightness": round(float(brightness.mean()), 3),
            "mean_motion": round(float(motion.mean()), 4),
            "scene_count": len(cuts) + 1,
            "black_seconds": round(sum(b - a for a, b in black_runs), 2),
            "frozen_seconds": round(sum(b - a for a, b in frozen_runs), 2),
        },
    }


def best_window(analysis: dict | None, src_duration: float, want: float,
                avoid: list[tuple[float, float]] | None = None, step: float = 0.25) -> tuple[float, float]:
    """Sceglie l'intervallo [in, in+want] migliore: punteggio alto, nessun taglio di
    scena interno, niente nero/freeze, nessuna sovrapposizione con parti già usate.
    Restituisce (src_in, punteggio)."""
    want = min(want, src_duration)
    if src_duration - want < 0.05 or not analysis:
        return 0.0, 0.0
    sr = analysis["sample_rate"]
    score = np.asarray(analysis["score"], np.float32)
    cuts = analysis.get("scene_cuts", [])
    bad = list(analysis.get("black", [])) + list(analysis.get("frozen", []))
    avoid = avoid or []
    best, best_s = 0.0, -1e9
    t = 0.0
    margin = 0.15
    candidates = [t0 * step for t0 in range(int((src_duration - want) / step) + 1)]
    # i punti subito dopo un taglio sono ottimi punti d'ingresso
    candidates += [c + 0.08 for c in cuts if c + 0.08 + want <= src_duration]
    for t in candidates:
        a, b = t, t + want
        i0, i1 = int(a * sr), max(int(a * sr) + 1, int(b * sr))
        s = float(score[i0:i1].mean()) if i0 < len(score) else 0.0
        inner = [c for c in cuts if a + margin < c < b - margin]
        s -= 0.35 * len(inner)
        for x, y in bad:
            if x < b and y > a:
                s -= 0.6
        for x, y in avoid:
            ov = min(b, y) - max(a, x)
            if ov > 0:
                s -= 1.0 * ov / want
        if a < 0.3:
            s -= 0.05  # gli inizi dei file spesso contengono loghi/fade
        if s > best_s:
            best, best_s = t, s
    return round(best, 3), round(best_s, 3)


def analyze_image(path: Path) -> dict:
    raw, _ = ff.run_capture(["-v", "error", "-i", str(path), "-frames:v", "1",
                             "-vf", f"scale={AW}:{AH}:flags=area,format=gray", "-f", "rawvideo", "-"])
    f = np.frombuffer(raw[: AW * AH], np.uint8).reshape(AH, AW).astype(np.float32) / 255
    lap = (-4 * f[1:-1, 1:-1] + f[:-2, 1:-1] + f[2:, 1:-1] + f[1:-1, :-2] + f[1:-1, 2:])
    return {"version": 1, "brightness": round(float(f.mean()), 3), "contrast": round(float(f.std()), 3),
            "sharpness_raw": round(float(lap.var()), 5),
            "summary": {"mean_score": round(float(0.5 * min(1, f.std() / 0.22) + 0.5 * (1 - abs(f.mean() - 0.45) / 0.45)), 3)}}


# --------------------------------------------------------------------------- audio

def decode_mono(path: Path, sr: int = 22050, start: float = 0.0, duration: float | None = None) -> np.ndarray:
    args = ["-v", "error"]
    if start:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", str(path)]
    if duration:
        args += ["-t", f"{duration:.3f}"]
    args += ["-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    raw, _ = ff.run_capture(args, timeout=600)
    return np.frombuffer(raw, np.float32)


def analyze_audio(path: Path, info: dict) -> dict:
    """RMS, onset envelope, tempo e posizioni dei beat (spectral flux + autocorrelazione)."""
    sr = 22050
    y = decode_mono(path, sr)
    if y.size < sr:
        return {"version": 1, "duration": y.size / sr, "bpm": 0, "beats": [], "rms": []}
    hop, win = 512, 2048
    n = 1 + (y.size - win) // hop
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    frames = y[idx] * np.hanning(win)[None, :].astype(np.float32)
    mag = np.abs(np.fft.rfft(frames, axis=1))
    # bande logaritmiche con peso uguale: evita che hi-hat/rumore dominino sulla cassa
    freqs = np.fft.rfftfreq(win, 1 / sr)
    edges = np.geomspace(40, sr / 2, 9)
    bands = np.stack([mag[:, (freqs >= lo_) & (freqs < hi_)].sum(axis=1)
                      for lo_, hi_ in zip(edges[:-1], edges[1:])], axis=1)
    logm = np.log1p(100 * bands / (bands.max(axis=0, keepdims=True) + 1e-9))
    bflux = np.maximum(0, np.diff(logm, axis=0))
    w = np.linspace(1.6, 0.6, bflux.shape[1])  # leggera enfasi sulle basse
    flux = (bflux * w).sum(axis=1)
    flux = np.concatenate([[0], flux])
    flux = flux - np.convolve(flux, np.ones(16) / 16, mode="same")
    flux = np.maximum(flux, 0)
    env_rate = sr / hop
    # tempo: autocorrelazione dell'envelope tra 70 e 180 BPM
    ac = np.correlate(flux, flux, mode="full")[len(flux) - 1:]
    lo, hi = int(env_rate * 60 / 180), int(env_rate * 60 / 70)
    bpm, beats = 0.0, []
    if hi < len(ac) and flux.max() > 0:
        lags = np.arange(lo, hi)
        bpms = 60.0 * env_rate / lags
        prior = np.exp(-0.5 * (np.log2(bpms / 120.0) / 0.6) ** 2)  # preferenza per tempi comuni
        lag = lo + int(np.argmax(ac[lo:hi] * prior))
        # interpolazione parabolica per un periodo frazionario
        if 0 < lag < len(ac) - 1:
            a_, b_, c_ = ac[lag - 1], ac[lag], ac[lag + 1]
            den = a_ - 2 * b_ + c_
            period = lag + (0.5 * (a_ - c_) / den if den else 0.0)
        else:
            period = float(lag)
        bpm = 60.0 * env_rate / period
        # fase: offset che massimizza l'energia degli onset sulla griglia
        grid = np.arange(0, len(flux) - 1, period)
        best_phase, best_val = 0.0, -1.0
        for ph in np.arange(0, period, 0.5):
            idxs = np.clip(np.round(grid + ph).astype(int), 0, len(flux) - 1)
            v = float(flux[idxs].sum())
            if v > best_val:
                best_phase, best_val = ph, v
        tol = max(1, int(period * 0.08))
        offset = (win / 2) / sr  # centro della finestra di analisi
        for k in np.arange(best_phase, len(flux), period):
            k = int(round(k))
            a, b = max(0, k - tol), min(len(flux), k + tol + 1)
            if a >= b:
                break
            j = a + int(np.argmax(flux[a:b]))
            beats.append(round(j / env_rate + offset, 3))
        if beats:
            pb = period / env_rate
            while beats[0] - pb > -0.02:
                beats.insert(0, round(max(0.0, beats[0] - pb), 3))
    rms_hop = sr // 10
    m = y.size // rms_hop
    rms = np.sqrt((y[: m * rms_hop].reshape(m, rms_hop) ** 2).mean(axis=1))
    return {"version": 1, "duration": round(y.size / sr, 3), "bpm": round(bpm, 1), "beats": beats,
            "rms": [round(float(v), 4) for v in rms], "peak": round(float(np.abs(y).max()), 4)}


# --------------------------------------------------------------------------- miniature

def make_thumbnail(path: Path, out: Path, kind: str, duration: float) -> bool:
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        if kind == "audio":
            ff.run_ffmpeg(["-i", str(path), "-filter_complex",
                           "aformat=channel_layouts=mono,showwavespic=s=480x120:colors=0x3d8bff",
                           "-frames:v", "1", str(out)], timeout=120)
        else:
            ss = ["-ss", f"{min(duration * 0.3, 5):.2f}"] if kind == "video" and duration > 1 else []
            ff.run_ffmpeg([*ss, "-i", str(path), "-frames:v", "1",
                           "-vf", "scale=480:270:force_original_aspect_ratio=increase,crop=480:270",
                           "-q:v", "4", str(out)], timeout=120)
        return out.exists()
    except ff.FFmpegError:
        return False

"""Controllo qualità del file finale: misure reali su ciò che è stato prodotto.

- ffprobe: codec, risoluzione, fps, durata audio/video, flussi presenti.
- Decodifica completa con blackdetect, freezedetect, silencedetect, ebur128
  (loudness integrata e true peak) in un solo passaggio.
- Sincronia voce: energia dell'audio finale dentro e fuori gli intervalli
  in cui la timeline prevede la voce.
- Conformità al piano: numero di clip, licenze degli asset usati, sottotitoli.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import numpy as np

from . import subtitles as subs
from .media import ffmpeg as ff
from .media.analysis import decode_mono
from .project import Project

ALWAYS_OK_LICENSES = {"user", "generated"}


def _check(name: str, status: str, measured, expected, note: str = "") -> dict:
    return {"name": name, "status": status, "measured": measured, "expected": expected, "note": note}


def analyze_output(path: Path, timeout: float = 3600) -> dict:
    stderr = ff.run_ffmpeg([
        "-i", str(path), "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", "blackdetect=d=0.4:pix_th=0.08,freezedetect=n=-60dB:d=1.5",
        "-af", "silencedetect=n=-50dB:d=1.5,ebur128=peak=true",
        "-f", "null", "-",
    ], timeout=timeout)
    black = [(float(a), float(b)) for a, b in re.findall(r"black_start:([0-9.]+) black_end:([0-9.]+)", stderr)]
    fstarts = [float(x) for x in re.findall(r"freeze_start: ([0-9.]+)", stderr)]
    fends = [float(x) for x in re.findall(r"freeze_end: ([0-9.]+)", stderr)]
    sstarts = [float(x) for x in re.findall(r"silence_start: (-?[0-9.]+)", stderr)]
    sends = [float(x) for x in re.findall(r"silence_end: ([0-9.]+)", stderr)]
    summary = stderr[stderr.rfind("Summary:"):] if "Summary:" in stderr else ""
    mi = re.search(r"I:\s+(-?[0-9.]+|-inf) LUFS", summary)
    mlra = re.search(r"LRA:\s+([0-9.]+) LU", summary)
    mtp = re.search(r"True peak:\s+Peak:\s+(-?[0-9.]+|-inf) dBFS", summary, re.S)
    errors = [ln for ln in stderr.splitlines() if re.search(r"error|corrupt|invalid", ln, re.I)
              and "Error while" not in ln and "concealing" not in ln]
    return {
        "black": black,
        "freeze": list(zip(fstarts, fends + [None] * (len(fstarts) - len(fends)))),
        "silence": list(zip(sstarts, sends + [None] * (len(sstarts) - len(sends)))),
        "integrated_lufs": float(mi.group(1)) if mi and mi.group(1) != "-inf" else None,
        "lra": float(mlra.group(1)) if mlra else None,
        "true_peak_dbfs": float(mtp.group(1)) if mtp and mtp.group(1) != "-inf" else None,
        "decode_errors": errors[:10],
    }


def voice_sync_check(project: Project, path: Path, total: float) -> Optional[dict]:
    voices = [c for c in project.timeline.audio if c.role == "voice"]
    if not voices:
        return None
    sr = 8000
    y = decode_mono(path, sr)
    if y.size == 0:
        return {"ok": False, "note": "audio non decodificabile"}
    hop = sr // 20
    n = y.size // hop
    rms = np.sqrt((y[: n * hop].reshape(n, hop) ** 2).mean(axis=1) + 1e-12)
    db = 20 * np.log10(rms)
    inside = np.zeros(n, bool)
    for c in voices:
        a, b = int(c.start * 20), int(min(total, c.start + c.duration) * 20)
        inside[a:b] = True
    per_clip = []
    for c in voices:
        a, b = int(c.start * 20), int(min(total, c.start + c.duration) * 20)
        seg = db[a:b]
        per_clip.append(float(np.percentile(seg, 75)) if seg.size else -120.0)
    audible = sum(1 for v in per_clip if v > -40)
    return {"ok": audible == len(voices), "voice_segments": len(voices), "audible_segments": audible,
            "voice_p75_db": [round(v, 1) for v in per_clip]}


def run_qa(project: Project, video: Path, expected_duration: Optional[float] = None,
           expected_size: Optional[tuple[int, int]] = None, expected_fps: Optional[int] = None,
           expected_codec: Optional[str] = None) -> dict:
    checks: list[dict] = []
    info = ff.probe(video)
    streams = info.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    s_sub = next((s for s in streams if s.get("codec_type") == "subtitle"), None)
    fmt_dur = float(info.get("format", {}).get("duration") or 0)
    if v is None:
        checks.append(_check("Flusso video", "fail", "assente", "presente"))
        return {"status": "fail", "checks": checks}
    fps = ff.parse_fps(v.get("avg_frame_rate"))
    W, H = int(v.get("width", 0)), int(v.get("height", 0))
    if expected_size:
        ok = (W, H) == tuple(expected_size)
        checks.append(_check("Risoluzione", "pass" if ok else "fail", f"{W}x{H}", f"{expected_size[0]}x{expected_size[1]}"))
    if expected_fps:
        ok = abs(fps - expected_fps) < 0.01
        checks.append(_check("Frame rate", "pass" if ok else "fail", round(fps, 3), expected_fps))
    if expected_codec:
        want = "hevc" if expected_codec == "hevc" else "h264"
        checks.append(_check("Codec video", "pass" if v.get("codec_name") == want else "fail",
                             v.get("codec_name"), want))
    vdur = float(v.get("duration") or fmt_dur)
    if expected_duration:
        frame = 1.0 / (fps or 30)
        diff = abs(vdur - expected_duration)
        st = "pass" if diff <= frame + 1e-3 else ("warn" if diff <= 0.25 else "fail")
        checks.append(_check("Durata", st, round(vdur, 3), round(expected_duration, 3), f"differenza {diff * 1000:.0f} ms"))
    if a is None:
        checks.append(_check("Flusso audio", "fail", "assente", "presente"))
    else:
        adur = float(a.get("duration") or fmt_dur)
        diff = abs(adur - vdur)
        checks.append(_check("Allineamento audio/video", "pass" if diff <= 0.05 else ("warn" if diff <= 0.15 else "fail"),
                             f"audio {adur:.3f}s / video {vdur:.3f}s", "differenza ≤ 50 ms"))
        checks.append(_check("Formato audio", "pass" if int(a.get("sample_rate", 0)) == 48000 and int(a.get("channels", 0)) == 2 else "warn",
                             f"{a.get('codec_name')} {a.get('sample_rate')} Hz {a.get('channels')} ch", "AAC 48 kHz stereo"))

    m = analyze_output(video)
    checks.append(_check("Decodifica completa", "pass" if not m["decode_errors"] else "fail",
                         f"{len(m['decode_errors'])} errori", "0 errori", "; ".join(m["decode_errors"][:3])))
    tl = project.timeline
    total = vdur
    fade_in, fade_out = tl.fade_in, tl.fade_out
    blacks = [(s, e) for s, e in m["black"] if not (s <= fade_in + 0.15 or e >= total - fade_out - 0.15)]
    checks.append(_check("Fotogrammi neri", "pass" if not blacks else "warn",
                         [f"{s:.2f}-{e:.2f}s" for s, e in blacks] or "nessuno", "nessun nero oltre le dissolvenze"))
    starts = tl.clip_starts()
    image_ranges = []
    for c, st in zip(tl.video, starts):
        try:
            if project.asset(c.asset_id).kind == "image" and c.motion == "none":
                image_ranges.append((st, st + c.duration))
        except KeyError:
            pass
    freezes = []
    for s, e in m["freeze"]:
        e = e if e is not None else total
        if not any(s >= a_ - 0.2 and e <= b_ + 0.2 for a_, b_ in image_ranges):
            freezes.append((s, e))
    checks.append(_check("Immagine bloccata (freeze)", "pass" if not freezes else "warn",
                         [f"{s:.2f}-{e:.2f}s" for s, e in freezes] or "nessuno", "nessun freeze > 1.5 s non voluto"))
    has_audio_content = bool(tl.audio) or any(project.asset(c.asset_id).info.get("has_audio") and not c.mute
                                               for c in tl.video if c.asset_id in {x.id for x in project.assets})
    sil = [(s, e if e is not None else total) for s, e in m["silence"]]
    sil = [(s, e) for s, e in sil if (e - s) > 1.5 and not (e >= total - 1.0 and s >= total - 3.0)]
    if has_audio_content:
        checks.append(_check("Silenzi", "pass" if not sil else "warn",
                             [f"{s:.2f}-{e:.2f}s" for s, e in sil] or "nessuno", "nessun silenzio > 1.5 s"))
    target = project.export.loudness_target
    I, tp = m["integrated_lufs"], m["true_peak_dbfs"]
    if I is None:
        checks.append(_check("Loudness integrata", "warn" if not has_audio_content else "fail", "silenzio", f"{target} LUFS"))
    else:
        d = abs(I - target)
        checks.append(_check("Loudness integrata", "pass" if d <= 1.0 else ("warn" if d <= 2.0 else "fail"),
                             f"{I:.1f} LUFS", f"{target:.0f} ±1 LUFS"))
    if tp is not None:
        checks.append(_check("True peak", "pass" if tp <= -1.0 else ("warn" if tp <= 0 else "fail"),
                             f"{tp:.1f} dBTP", "≤ -1.0 dBTP"))
    vs = voice_sync_check(project, video, total)
    if vs is not None:
        checks.append(_check("Voce udibile nei punti previsti", "pass" if vs["ok"] else "fail",
                             f"{vs['audible_segments']}/{vs['voice_segments']} segmenti", "tutti i segmenti", str(vs["voice_p75_db"])))
    if tl.subtitles:
        probs = subs.validate(subs.normalize(tl.subtitles, total), total)
        checks.append(_check("Sottotitoli", "pass" if not probs else "warn", f"{len(tl.subtitles)} cue",
                             "cue valide e monotone", "; ".join(probs[:4])))
        if project.export.soft_subtitles:
            checks.append(_check("Traccia sottotitoli nel file", "pass" if s_sub else "warn",
                                 "presente" if s_sub else "assente", "mov_text"))
    used = {c.asset_id for c in tl.video} | {c.asset_id for c in tl.audio}
    from . import config
    allowed = set(config.settings().allowed_licenses) | ALWAYS_OK_LICENSES
    bad = [x.name for x in project.assets if x.id in used and x.license.code not in allowed]
    checks.append(_check("Licenze degli asset usati", "pass" if not bad else "fail",
                         f"{len(used)} asset" + (f", non consentiti: {bad}" if bad else ""), "tutte documentate e consentite"))
    if project.storyboard and project.storyboard.shots:
        n_sb, n_tl = len(project.storyboard.shots), len(tl.video)
        checks.append(_check("Conformità allo storyboard", "pass" if n_sb == n_tl else "warn",
                             f"{n_tl} clip in timeline", f"{n_sb} inquadrature pianificate",
                             "la timeline può essere stata modificata manualmente" if n_sb != n_tl else ""))
    statuses = [c["status"] for c in checks]
    status = "fail" if "fail" in statuses else ("warn" if "warn" in statuses else "pass")
    return {"status": status, "checks": checks, "measurements": m}

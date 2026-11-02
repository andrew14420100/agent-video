"""Motore di rendering: dalla timeline del progetto a un MP4 reale.

Fasi:
1. **Segmenti** – ogni clip della timeline diventa un file intermedio con
   risoluzione/fps/formato uniformi (ritaglio, adattamento, movimento, look,
   audio della clip). I segmenti sono in cache: dopo una modifica della
   timeline si ricalcolano solo le clip cambiate.
2. **Composizione** – transizioni (xfade/acrossfade o tagli netti), dissolvenze,
   logo, titoli/lower third/sottotitoli (ASS via libass) e codifica finale.
3. **Mix audio** – audio delle clip + musica + voce, ducking con sidechain,
   normalizzazione EBU R128 in due passaggi (loudnorm) e true-peak.
4. **Mux** – video + audio AAC + sottotitoli soft (mov_text) in MP4 faststart.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .. import config
from .. import subtitles as subs
from ..media import ffmpeg as ff
from ..project import AudioClip, Project, VideoClip
from .ass import build_ass

ENGINE_VERSION = "1"
ProgressFn = Callable[[str, float], None]

XFADE_TYPES = {
    "fade", "dissolve", "fadeblack", "fadewhite", "wipeleft", "wiperight", "wipeup", "wipedown",
    "slideleft", "slideright", "slideup", "slidedown", "smoothleft", "smoothright", "smoothup", "smoothdown",
    "circleopen", "circleclose", "circlecrop", "rectcrop", "radial", "vertopen", "vertclose", "horzopen",
    "horzclose", "diagtl", "diagtr", "diagbl", "diagbr", "hlslice", "hrslice", "pixelize", "distance",
    "hblur", "zoomin", "squeezeh", "squeezev", "fadegrays",
}

LOOKS = {
    "none": "",
    "cinematic": "eq=contrast=1.07:saturation=0.9:gamma=0.98,vignette=angle=PI/5",
    "vivid": "eq=contrast=1.05:saturation=1.22",
    "mono": "hue=s=0,eq=contrast=1.1",
    "warm": "colorbalance=rs=0.06:gs=0.01:bs=-0.06,eq=saturation=1.05",
}


@dataclass
class RenderResult:
    path: Path
    mode: str
    width: int
    height: int
    fps: int
    duration: float
    encoder: str
    encoder_reason: str
    srt: Optional[Path] = None
    vtt: Optional[Path] = None
    segments_total: int = 0
    segments_cached: int = 0
    loudness: dict = field(default_factory=dict)
    timings: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        for k in ("path", "srt", "vtt"):
            d[k] = str(d[k]) if d[k] else None
        return d


def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def output_size(project: Project, mode: str) -> tuple[int, int, int]:
    W, H, fps = project.export.width, project.export.height, project.export.fps
    if mode == "preview":
        scale = 540 / min(W, H)
        W, H = _even(W * scale), _even(H * scale)
        fps = min(fps, 30)
    return W, H, fps


def frames(t: float, fps: int) -> int:
    return max(1, int(round(t * fps)))


# --------------------------------------------------------------------------- segmenti

def _segment_key(project: Project, clip: VideoClip, W: int, H: int, fps: int, quality: str, enc: str) -> str:
    a = project.asset(clip.asset_id)
    p = project.asset_path(a)
    st = p.stat()
    payload = {
        "v": ENGINE_VERSION, "clip": clip.model_dump(exclude={"id", "transition", "note"}),
        "src": str(p), "mtime": st.st_mtime_ns, "size": st.st_size, "W": W, "H": H, "fps": fps,
        "q": quality, "enc": enc, "kind": a.kind,
    }
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:20]


def _video_filter(clip: VideoClip, kind: str, W: int, H: int, fps: int, dur: float) -> str:
    Z = 1.12 if clip.motion != "none" else 1.0
    CW, CH = _even(W * Z), _even(H * Z)
    chain = []
    if clip.speed != 1.0 and kind == "video":
        chain.append(f"setpts=(PTS-STARTPTS)/{clip.speed:.4f}")
    else:
        chain.append("setpts=PTS-STARTPTS")
    chain.append(f"fps={fps}")
    if clip.fit == "contain":
        fg = f"scale={CW}:{CH}:force_original_aspect_ratio=decrease:flags=lanczos"
        bg = (f"scale={_even(CW / 4)}:{_even(CH / 4)}:force_original_aspect_ratio=increase,"
              f"crop={_even(CW / 4)}:{_even(CH / 4)},boxblur=12:2,eq=brightness=-0.08,scale={CW}:{CH}")
        base = ",".join(chain)
        graph = (f"{base},split[fg0][bg0];[bg0]{bg}[bg1];[fg0]{fg}[fg1];"
                 f"[bg1][fg1]overlay=(W-w)/2:(H-h)/2,setsar=1")
    else:
        chain.append(f"scale={CW}:{CH}:force_original_aspect_ratio=increase:flags=lanczos,crop={CW}:{CH},setsar=1")
        graph = ",".join(chain)
    D = max(dur, 0.1)
    P = f"min(t/{D:.4f}\\,1)"
    if clip.motion == "zoom_in":
        f = f"(1+{Z - 1:.3f}*{P})"
        graph += (f",scale=w='ceil({W}*{f}/2)*2':h='ceil({H}*{f}/2)*2':eval=frame:flags=bicubic,"
                  f"crop={W}:{H}")
    elif clip.motion == "zoom_out":
        f = f"(1+{Z - 1:.3f}*(1-{P}))"
        graph += (f",scale=w='ceil({W}*{f}/2)*2':h='ceil({H}*{f}/2)*2':eval=frame:flags=bicubic,"
                  f"crop={W}:{H}")
    elif clip.motion == "pan_left":
        graph += f",crop={W}:{H}:x='(iw-ow)*(1-{P})':y='(ih-oh)/2'"
    elif clip.motion == "pan_right":
        graph += f",crop={W}:{H}:x='(iw-ow)*{P}':y='(ih-oh)/2'"
    look = LOOKS.get(clip.look, "")
    if look:
        graph += "," + look
    graph += ",format=yuv420p"
    return graph


def render_segment(project: Project, clip: VideoClip, out: Path, W: int, H: int, fps: int,
                   enc_args: list[str], cancel: Optional[threading.Event]) -> None:
    a = project.asset(clip.asset_id)
    src = project.asset_path(a)
    dur = frames(clip.duration, fps) / fps
    nfr = frames(clip.duration, fps)
    args: list[str] = []
    if a.kind == "image":
        args += ["-loop", "1", "-framerate", str(fps), "-t", f"{dur + 0.5:.3f}", "-i", str(src)]
        has_audio = False
    elif a.kind == "video":
        src_dur = float(a.info.get("duration") or 0)
        need = dur * clip.speed
        start = max(0.0, min(clip.src_in, max(0.0, src_dur - 0.05)))
        args += ["-ss", f"{start:.3f}", "-t", f"{need + 0.5:.3f}", "-i", str(src)]
        has_audio = bool(a.info.get("has_audio")) and not clip.mute and clip.volume > 0
    else:
        raise ff.FFmpegError(f"L'asset {a.name} non è un video o un'immagine")
    args += ["-f", "lavfi", "-t", f"{dur + 0.5:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    vf = _video_filter(clip, a.kind, W, H, fps, dur)
    if has_audio:
        tempo = ""
        if clip.speed != 1.0:
            s = max(0.5, min(2.0, clip.speed))
            tempo = f"atempo={s:.4f},"
        af = (f"[0:a:0]asetpts=PTS-STARTPTS,{tempo}aresample=48000,"
              f"aformat=sample_fmts=fltp:channel_layouts=stereo,volume={clip.volume:.3f},"
              f"apad,atrim=0:{dur:.4f}[aout]")
    else:
        af = f"[1:a]atrim=0:{dur:.4f},aformat=sample_fmts=fltp:channel_layouts=stereo[aout]"
    fc = f"[0:v]{vf}[vout];{af}"
    tmp = out.with_suffix(".tmp.mkv")
    ff.run_ffmpeg([*args, "-filter_complex", fc, "-map", "[vout]", "-map", "[aout]",
                   "-frames:v", str(nfr), *enc_args, "-c:a", "pcm_s16le", "-ar", "48000", str(tmp)],
                  cancel=cancel, timeout=max(600, dur * 60))
    tmp.replace(out)


# --------------------------------------------------------------------------- audio

def _loudnorm_measure(stderr: str) -> dict:
    m = re.search(r"\{\s*\"input_i\"[^}]*\}", stderr, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def mix_audio(project: Project, bed: Path, out: Path, total: float, work: Path,
              cancel: Optional[threading.Event]) -> dict:
    """Mixa audio delle clip, musica e voce; normalizza a target LUFS (2 passaggi)."""
    tl = project.timeline
    inputs = ["-i", str(bed)]
    parts: list[str] = []
    music, voice, sfx = [], [], []
    idx = 1
    for c in tl.audio:
        try:
            a = project.asset(c.asset_id)
        except KeyError:
            continue
        p = project.asset_path(a)
        if not p.exists():
            continue
        if c.start >= total:
            continue
        dur = min(c.duration, total - c.start)
        if c.loop:
            inputs += ["-stream_loop", "-1"]
        inputs += ["-i", str(p)]
        fo = min(c.fade_out, dur / 2)
        fi = min(c.fade_in, dur / 2)
        ms = int(round(c.start * 1000))
        chain = (f"[{idx}:a:0]atrim=start={c.src_in:.3f}:duration={dur:.3f},asetpts=PTS-STARTPTS,"
                 f"aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,volume={c.volume_db:.2f}dB")
        if fi > 0:
            chain += f",afade=t=in:st=0:d={fi:.3f}"
        if fo > 0:
            chain += f",afade=t=out:st={max(0.0, dur - fo):.3f}:d={fo:.3f}"
        if ms > 0:
            chain += f",adelay={ms}:all=1"
        label = f"x{idx}"
        parts.append(chain + f"[{label}]")
        {"music": music, "voice": voice, "sfx": sfx}[c.role].append(label)
        idx += 1

    def bus(labels: list[str], name: str) -> Optional[str]:
        if not labels:
            return None
        if len(labels) == 1:
            parts.append(f"[{labels[0]}]anull[{name}]")
        else:
            parts.append("".join(f"[{x}]" for x in labels) +
                         f"amix=inputs={len(labels)}:normalize=0:duration=longest[{name}]")
        return name

    vbus, mbus, sbus = bus(voice, "voice"), bus(music, "music"), bus(sfx, "sfx")
    bed_label = "0:a:0"
    final_inputs: list[str] = []
    if vbus:
        n_sc = 1 + (1 if mbus else 0) + (1 if tl.keep_clip_audio else 0)
        outs = ["vmain"] + [f"vsc{i}" for i in range(n_sc - 1)]
        parts.append(f"[{vbus}]asplit={n_sc}" + "".join(f"[{o}]" for o in outs))
        final_inputs.append("vmain")
        k = 0
        if mbus:
            parts.append(f"[{mbus}][vsc{k}]sidechaincompress=threshold=0.015:ratio=10:attack=25:release=450:makeup=1[mduck]")
            final_inputs.append("mduck")
            k += 1
        if tl.keep_clip_audio:
            parts.append(f"[{bed_label}]volume={tl.clip_audio_under_voice_db:.1f}dB[bedq]")
            parts.append(f"[bedq][vsc{k}]sidechaincompress=threshold=0.02:ratio=4:attack=25:release=450[bedd]")
            final_inputs.append("bedd")
    else:
        if mbus:
            final_inputs.append(mbus)
        if tl.keep_clip_audio:
            vol = "-10" if mbus else "0"
            parts.append(f"[{bed_label}]volume={vol}dB[bedq]")
            final_inputs.append("bedq")
    if sbus:
        final_inputs.append(sbus)
    if not final_inputs:
        parts.append(f"[{bed_label}]volume=0[silent]")
        final_inputs.append("silent")
    if len(final_inputs) == 1:
        parts.append(f"[{final_inputs[0]}]anull[mixraw]")
    else:
        parts.append("".join(f"[{x}]" for x in final_inputs) +
                     f"amix=inputs={len(final_inputs)}:normalize=0:duration=longest[mixraw]")
    parts.append(f"[mixraw]apad,atrim=0:{total:.4f},asetpts=PTS-STARTPTS[mix]")
    graph = ";".join(parts)

    target = project.export.loudness_target
    tp = -1.5
    # 1° passaggio: misura
    stderr = ff.run_ffmpeg([*inputs, "-filter_complex",
                            graph + f";[mix]loudnorm=I={target}:TP={tp}:LRA=11:print_format=json[m]",
                            "-map", "[m]", "-f", "null", "-"], cancel=cancel, timeout=1800)
    meas = _loudnorm_measure(stderr)
    try:
        in_i = float(meas.get("input_i", "-inf"))
    except ValueError:
        in_i = float("-inf")
    if meas and in_i > -70:
        ln = (f"loudnorm=I={target}:TP={tp}:LRA=11:measured_I={meas['input_i']}:measured_TP={meas['input_tp']}:"
              f"measured_LRA={meas['input_lra']}:measured_thresh={meas['input_thresh']}:"
              f"offset={meas['target_offset']}:linear=true,aresample=48000,alimiter=limit={10 ** (tp / 20):.4f}:level=false")
        normalized = True
    else:
        ln = "anull"
        normalized = False
    ff.run_ffmpeg([*inputs, "-filter_complex", graph + f";[mix]{ln},atrim=0:{total:.4f}[m]",
                   "-map", "[m]", "-c:a", "pcm_s24le", "-ar", "48000", "-ac", "2", str(out)],
                  cancel=cancel, timeout=1800)
    return {"measured_input": meas, "normalized": normalized, "target_lufs": target, "true_peak_limit": tp}


# --------------------------------------------------------------------------- composizione

def _logo_xy(pos: str, margin: int) -> str:
    return {
        "top-left": f"{margin}:{margin}",
        "top-right": f"W-w-{margin}:{margin}",
        "bottom-left": f"{margin}:H-h-{margin}",
        "bottom-right": f"W-w-{margin}:H-h-{margin}",
    }.get(pos, f"W-w-{margin}:{margin}")


def validate_timeline(project: Project) -> list[str]:
    errs = []
    tl = project.timeline
    if not tl.video:
        errs.append("La timeline video è vuota")
    for i, c in enumerate(tl.video):
        try:
            a = project.asset(c.asset_id)
        except KeyError:
            errs.append(f"Clip {i + 1}: asset mancante")
            continue
        if not project.asset_path(a).exists():
            errs.append(f"Clip {i + 1}: file non trovato ({a.name})")
        if c.duration <= 0.05:
            errs.append(f"Clip {i + 1}: durata troppo breve")
        if a.kind == "video":
            sd = float(a.info.get("duration") or 0)
            if sd and c.src_in + c.duration * c.speed > sd + 0.05:
                errs.append(f"Clip {i + 1}: oltre la fine del file sorgente ({c.src_in:.2f}+{c.duration:.2f} > {sd:.2f})")
        if i > 0 and c.transition.type != "cut":
            if c.transition.type not in XFADE_TYPES:
                errs.append(f"Clip {i + 1}: transizione sconosciuta '{c.transition.type}'")
            prev = tl.video[i - 1]
            if c.transition.duration >= min(c.duration, prev.duration) - 0.05:
                errs.append(f"Clip {i + 1}: transizione più lunga delle clip adiacenti")
    return errs


def render_project(project: Project, mode: str = "final", progress: Optional[ProgressFn] = None,
                   cancel: Optional[threading.Event] = None, out_path: Optional[Path] = None) -> RenderResult:
    t0 = time.time()
    prog = progress or (lambda s, f: None)
    errs = validate_timeline(project)
    if errs:
        raise ValueError("Timeline non valida:\n- " + "\n- ".join(errs))
    project.ensure_dirs()
    tl = project.timeline
    W, H, fps = output_size(project, mode)
    choice = ff.pick_encoder(project.export.codec if mode == "final" else "h264",
                             prefer={"gpu": "gpu", "cpu": "cpu"}.get(project.export.encoder, "auto"))
    inter_choice = ff.pick_encoder("h264", prefer={"cpu": "cpu"}.get(project.export.encoder, "auto"))
    inter_args = ff.video_encode_args(inter_choice, quality="preview" if mode == "preview" else "intermediate", height=H)
    final_args = ff.video_encode_args(choice, quality="preview" if mode == "preview" else "final", height=H)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    work = project.cache_dir / f"work-{mode}"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    segdir = project.cache_dir / "segments"
    segdir.mkdir(parents=True, exist_ok=True)
    timings: dict[str, float] = {}

    # 1) segmenti -------------------------------------------------------------
    t = time.time()
    segs: list[Path] = []
    cached = 0
    n = len(tl.video)
    for i, clip in enumerate(tl.video):
        if cancel is not None and cancel.is_set():
            raise ff.Cancelled("Rendering annullato")
        key = _segment_key(project, clip, W, H, fps, mode, inter_choice.encoder)
        seg = segdir / f"{key}.mkv"
        if seg.exists() and seg.stat().st_size > 0:
            cached += 1
        else:
            prog("segments", i / n)
            render_segment(project, clip, seg, W, H, fps, inter_args, cancel)
        segs.append(seg)
    prog("segments", 1.0)
    timings["segments"] = round(time.time() - t, 2)

    # 2) composizione -----------------------------------------------------------
    t = time.time()
    total_frames = 0
    durs = [frames(c.duration, fps) / fps for c in tl.video]
    trans = []
    for i, c in enumerate(tl.video):
        if i == 0 or c.transition.type == "cut" or c.transition.duration <= 0:
            trans.append(0.0)
        else:
            trans.append(frames(c.transition.duration, fps) / fps)
    total = sum(durs) - sum(trans)
    total_frames = int(round(total * fps))
    total = total_frames / fps

    inputs: list[str] = []
    for s in segs:
        inputs += ["-i", str(s)]
    parts = []
    for i in range(n):
        parts.append(f"[{i}:v]setpts=PTS-STARTPTS,fps={fps},settb=AVTB[sv{i}]")
        parts.append(f"[{i}:a]asetpts=PTS-STARTPTS[sa{i}]")
    vcur, acur = "sv0", "sa0"
    L = durs[0]
    for i in range(1, n):
        vo, ao = f"v{i}", f"a{i}"
        if trans[i] == 0:
            parts.append(f"[{vcur}][sv{i}]concat=n=2:v=1:a=0,settb=AVTB[{vo}]")
            parts.append(f"[{acur}][sa{i}]concat=n=2:v=0:a=1[{ao}]")
        else:
            ttype = tl.video[i].transition.type
            off = L - trans[i]
            parts.append(f"[{vcur}][sv{i}]xfade=transition={ttype}:duration={trans[i]:.4f}:offset={off:.4f},settb=AVTB[{vo}]")
            parts.append(f"[{acur}][sa{i}]acrossfade=d={trans[i]:.4f}:c1=tri:c2=tri[{ao}]")
        L = L + durs[i] - trans[i]
        vcur, acur = vo, ao

    vchain = f"[{vcur}]settb=AVTB,fps={fps}"
    if tl.fade_in > 0:
        vchain += f",fade=t=in:st=0:d={min(tl.fade_in, total / 4):.3f}"
    if tl.fade_out > 0:
        fo = min(tl.fade_out, total / 4)
        vchain += f",fade=t=out:st={total - fo:.3f}:d={fo:.3f}"
    vchain += "[vbase]"
    parts.append(vchain)
    vlast = "vbase"
    next_in = n
    if tl.logo:
        try:
            la = project.asset(tl.logo.asset_id)
            lp = project.asset_path(la)
            if lp.exists():
                inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{total + 1:.3f}", "-i", str(lp)]
                lw = _even(W * tl.logo.scale)
                margin = int(round(min(W, H) * 0.035))
                parts.append(f"[{next_in}:v]scale={lw}:-2:flags=lanczos,format=rgba,"
                             f"colorchannelmixer=aa={tl.logo.opacity:.2f}[lg]")
                parts.append(f"[{vlast}][lg]overlay={_logo_xy(tl.logo.position, margin)}:eof_action=pass:format=auto[vlogo]")
                vlast = "vlogo"
                next_in += 1
        except KeyError:
            pass
    cues = subs.normalize(tl.subtitles, total)
    burn = project.export.burn_subtitles and bool(cues)
    overlays = [o for o in tl.overlays if o.start < total]
    if overlays or burn:
        ass = build_ass(W, H, overlays, cues if burn else [], config.settings().font_family, burn)
        (work / "graphics.ass").write_text(ass, encoding="utf-8")
        parts.append(f"[{vlast}]subtitles=filename=graphics.ass[vtxt]")
        vlast = "vtxt"
    parts.append(f"[{vlast}]format=yuv420p[vout]")
    parts.append(f"[{acur}]atrim=0:{total:.4f}[aout]")
    video_only = work / ("video.mp4")
    bed = work / "bed.wav"
    ff.run_ffmpeg([*inputs, "-filter_complex", ";".join(parts),
                   "-map", "[vout]", "-frames:v", str(total_frames), "-r", str(fps), *final_args,
                   "-an", str(video_only),
                   "-map", "[aout]", "-c:a", "pcm_s16le", "-ar", "48000", str(bed)],
                  cwd=work, duration=total, progress=lambda f: prog("compose", f), cancel=cancel,
                  timeout=max(1800, total * 120))
    timings["compose"] = round(time.time() - t, 2)

    # 3) mix audio --------------------------------------------------------------
    t = time.time()
    prog("audio", 0.0)
    mixed = work / "mix.wav"
    loud = mix_audio(project, bed, mixed, total, work, cancel)
    prog("audio", 1.0)
    timings["audio"] = round(time.time() - t, 2)

    # 4) mux --------------------------------------------------------------------
    t = time.time()
    project.renders_dir.mkdir(parents=True, exist_ok=True)
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", project.name).strip("_")[:40] or "video"
    suffix = f"{H}p" if mode == "final" else "preview"
    out = out_path or (project.renders_dir / f"{base}_{stamp}_{suffix}_{choice.codec}.mp4")
    srt = vtt = None
    mux = ["-i", str(video_only), "-i", str(mixed)]
    maps = ["-map", "0:v:0", "-map", "1:a:0"]
    sub_codec: list[str] = []
    if cues:
        srt = out.with_suffix(".srt")
        vtt = out.with_suffix(".vtt")
        srt.write_text(subs.to_srt(cues), encoding="utf-8")
        vtt.write_text(subs.to_vtt(cues), encoding="utf-8")
        if project.export.soft_subtitles:
            mux += ["-i", str(srt)]
            maps += ["-map", "2:s:0"]
            sub_codec = ["-c:s", "mov_text", "-metadata:s:s:0", f"language={_iso639_2(project.brief.language)}"]
    ff.run_ffmpeg([*mux, *maps, "-c:v", "copy", "-c:a", "aac", "-b:a", project.export.audio_bitrate,
                   *sub_codec, "-movflags", "+faststart",
                   "-metadata", f"title={project.name}", "-metadata", "comment=Steam Focus Studio",
                   str(out)], cancel=cancel, timeout=1800)
    timings["mux"] = round(time.time() - t, 2)
    timings["total"] = round(time.time() - t0, 2)
    prog("done", 1.0)
    return RenderResult(path=out, mode=mode, width=W, height=H, fps=fps, duration=total,
                        encoder=choice.encoder, encoder_reason=choice.reason, srt=srt, vtt=vtt,
                        segments_total=n, segments_cached=cached, loudness=loud, timings=timings)


def _iso639_2(lang: str) -> str:
    return {"it": "ita", "en": "eng", "es": "spa", "fr": "fra", "de": "deu"}.get(lang, "und")

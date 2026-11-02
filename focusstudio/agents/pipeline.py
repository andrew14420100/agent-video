"""Agenti della pipeline di produzione.

Ogni agente lavora sul progetto condiviso e restituisce un risultato verificabile
(cosa ha fatto, con quali dati, cosa non ha potuto fare). Ordine:

  Regia (richiesta) → Ricerca → Analisi → Storyboard → Montaggio → Audio →
  Sottotitoli → Grafica → Rendering → Controllo qualità → Esportazione
"""
from __future__ import annotations

import json
import math
import re
import shutil
from pathlib import Path

from .. import config, library, qa, subtitles as subs
from ..audio import asr, tts
from ..media import analysis as an
from ..media import ffmpeg as ff
from ..orchestrator import Agent, AgentResult, Blocked, Context
from ..project import (AudioClip, LicenseInfo, Logo, Shot, Storyboard, SubtitleCue, TextOverlay,
                       Transition, VideoClip)
from ..render.engine import render_project
from ..research import providers as rp
from . import brief as brief_mod

OK_LICENSES = {"user", "generated"}
NON_BROLL = re.compile(r"this week|briefing|conference|interview|podcast|panel|talk|news|update|q&a|"
                       r"press|lecture|webinar|town hall|launch coverage|replay", re.I)


def license_ok(code: str) -> bool:
    return code in OK_LICENSES or code in config.settings().allowed_licenses


def visual_assets(project, only_allowed=True):
    return [a for a in project.assets if a.kind in ("video", "image") and a.role in ("", "broll")
            and (not only_allowed or license_ok(a.license.code))]


def music_assets(project):
    return [a for a in project.assets if a.kind == "audio" and a.role in ("", "music")
            and license_ok(a.license.code)]


def tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-zà-ÿ0-9]{3,}", text.lower())}


# --------------------------------------------------------------------------- 0. regia

class BriefAgent(Agent):
    name, title = "director", "Regia: interpretazione della richiesta"

    def run(self, ctx: Context) -> AgentResult:
        p = ctx.project
        prompt = ctx.run.prompt.strip()
        if not prompt:
            raise Blocked("Richiesta vuota")
        b, exp, notes = brief_mod.interpret(prompt, use_llm=config.settings().llm_enabled)
        p.brief = b
        p.export = brief_mod.apply_export(p.export, exp)
        if b.title and p.name in ("Nuovo progetto", ""):
            p.name = b.title
        warnings = [n for n in notes if n.startswith("Voce richiesta")]
        summary = (f"{b.duration:.0f} s, {p.export.width}x{p.export.height} {p.export.fps} fps {p.export.codec.upper()}, "
                   f"ritmo {b.pace}" + (f", titolo «{b.title}»" if b.title else "")
                   + (", voce" if b.want_voice else "") + (", ricerca online" if b.search_online else ""))
        return AgentResult("completed", summary, {"brief": b.model_dump(), "export": p.export.model_dump(),
                                                   "planner_notes": notes}, warnings=warnings)


# --------------------------------------------------------------------------- 1. ricerca

class ResearchAgent(Agent):
    name, title = "research", "Ricerca e selezione del materiale"

    def run(self, ctx: Context) -> AgentResult:
        p, b = ctx.project, ctx.project.brief
        candidates = [a for a in p.assets if a.kind in ("video", "image") and a.role in ("", "broll")]
        excluded = [{"asset": a.name, "licenza": a.license.code} for a in candidates if not license_ok(a.license.code)]
        local = visual_assets(p)
        ctx.log(f"Materiale già nel progetto: {len(local)} utilizzabili, {len(excluded)} esclusi per licenza")
        downloaded, rejected, errors = [], [], []
        warnings: list[str] = []
        if b.search_online:
            if not config.settings().online_research:
                warnings.append("Ricerca online richiesta ma disattivata nelle impostazioni")
            elif not b.topics:
                warnings.append("Ricerca online richiesta ma nessun argomento riconosciuto nella richiesta")
            else:
                want = min(8, max(3, math.ceil(b.duration / 8)))
                query = " ".join(b.topics[:4])
                qtok = set(b.topics)
                ctx.log(f"Ricerca online: «{query}» (obiettivo {want} clip)")
                results = []
                for prov, kind in (("nasa", "video"), ("wikimedia", "video")):
                    try:
                        rs = rp.search(prov, query, kind, 15)
                        if not rs and len(b.topics) > 1:
                            rs = rp.search(prov, " ".join(b.topics[:2]), kind, 15)
                        ctx.log(f"{prov}: {len(rs)} risultati")
                        results += rs
                    except rp.ResearchError as e:
                        errors.append(f"{prov}: {e}")
                        ctx.log(f"{prov}: errore {e}")
                scored = []
                for r in results:
                    rel = len(qtok & (tokens(r.title + " " + r.description) | set(r.tags))) / max(1, len(qtok))
                    # notiziari, conferenze e interviste sono poco adatti come B-roll
                    if NON_BROLL.search(r.title):
                        rel -= 0.5
                    scored.append((rel, r))
                scored.sort(key=lambda x: -x[0])
                for rel, r in scored:
                    if len(downloaded) >= want:
                        break
                    ctx.check_cancel()
                    if rel < 0.34:
                        rejected.append({"titolo": r.title, "motivo": f"pertinenza testuale bassa ({rel:.2f})"})
                        continue
                    if not license_ok(r.license.code):
                        rejected.append({"titolo": r.title, "motivo": f"licenza non consentita: {r.license.name}"})
                        continue
                    if r.duration and r.duration > 900:
                        rejected.append({"titolo": r.title, "motivo": "video troppo lungo (> 15 min)"})
                        continue
                    try:
                        ctx.log(f"Scarico «{r.title}» ({r.provider}, {r.license.name})")
                        f = rp.fetch(r, p.media_dir, want_height=p.export.height, cancel=ctx.cancel)
                        a = library.import_file(p, f, copy=False, license=r.license, source=r.provider,
                                                origin_url=r.landing_url, description=r.description[:500],
                                                tags=list(dict.fromkeys(r.tags + list(qtok))), name=r.title)
                        downloaded.append({"titolo": r.title, "provider": r.provider, "licenza": r.license.name,
                                           "url": r.landing_url, "pertinenza_testuale": round(rel, 2),
                                           "risoluzione": f"{a.info.get('width')}x{a.info.get('height')}",
                                           "durata": a.info.get("duration")})
                        ctx.progress(len(downloaded) / want)
                    except (rp.ResearchError, ff.FFmpegError, OSError) as e:
                        rejected.append({"titolo": r.title, "motivo": f"download/lettura fallita: {e}"})
                if not downloaded:
                    warnings.append("La ricerca online non ha prodotto materiale utilizzabile")
        if b.want_music and not music_assets(p):
            warnings.append("Musica richiesta ma nessuna traccia con licenza valida nel progetto: "
                            "importa una traccia (il video userà solo l'audio delle clip/voce)")
        usable = visual_assets(p)
        if not usable:
            raise Blocked("Nessun materiale visivo utilizzabile: importa clip/immagini o chiedi una ricerca online "
                          "(es. 'cerca filmati di ...')")
        native = [a for a in usable if a.kind == "video" and min(a.info.get("width", 0), a.info.get("height", 0)) >= 2160]
        if p.export.height >= 2160 and len(native) < len([a for a in usable if a.kind == "video"]):
            warnings.append("Output 4K richiesto ma alcune sorgenti video hanno risoluzione inferiore: verranno ingrandite")
        summary = (f"{len(usable)} asset visivi utilizzabili ({len(downloaded)} scaricati con licenza libera)"
                   + (f", {len(rejected)} risultati scartati" if rejected else ""))
        return AgentResult("completed", summary, {"scaricati": downloaded, "scartati": rejected[:30],
                                                   "esclusi_per_licenza": excluded, "errori_rete": errors},
                           warnings=warnings)


# --------------------------------------------------------------------------- 2. analisi

class AnalysisAgent(Agent):
    name, title = "analysis", "Analisi tecnica delle clip"

    def run(self, ctx: Context) -> AgentResult:
        p = ctx.project
        todo = [a for a in p.assets if a.analysis is None and a.role in ("", "broll", "music")
                and (a.kind != "audio" or license_ok(a.license.code))]
        rows = []
        for i, a in enumerate(todo):
            ctx.check_cancel()
            ctx.log(f"Analizzo {a.name}")
            try:
                library.analyze_asset(p, a)
            except ff.FFmpegError as e:
                ctx.log(f"Analisi fallita per {a.name}: {e}")
                a.analysis = {"version": 1, "error": str(e)[:300], "summary": {"mean_score": 0}}
            ctx.progress((i + 1) / max(1, len(todo)))
        for a in p.assets:
            if a.analysis and a.kind == "video":
                s = a.analysis.get("summary", {})
                rows.append({"asset": a.name, "durata": a.info.get("duration"), "scene": s.get("scene_count"),
                             "qualità": s.get("mean_score"), "nero_s": s.get("black_seconds"),
                             "freeze_s": s.get("frozen_seconds")})
            elif a.analysis and a.kind == "audio":
                rows.append({"asset": a.name, "bpm": a.analysis.get("bpm"), "beat": len(a.analysis.get("beats", []))})
        return AgentResult("completed", f"{len(todo)} file analizzati (scene, esposizione, nitidezza, movimento, nero, freeze, beat)",
                           {"assets": rows})


# --------------------------------------------------------------------------- 3. storyboard

PACE = {"calm": (4.8, 0.9), "normal": (3.4, 0.5), "dynamic": (2.0, 0.0)}  # durata tipica, transizione


class StoryboardAgent(Agent):
    name, title = "storyboard", "Pianificazione narrativa e storyboard"

    def run(self, ctx: Context) -> AgentResult:
        p, b = ctx.project, ctx.project.brief
        fps = p.export.fps
        T = round(b.duration * fps) / fps
        base, _tr = PACE[b.pace]
        music = music_assets(p)
        beats: list[float] = []
        bpm = 0.0
        sb = Storyboard(total_duration=T)
        if b.want_music and music:
            m = music[0]
            sb.music_asset_id = m.id
            an_ = m.analysis or {}
            bpm = float(an_.get("bpm") or 0)
            mdur = float(m.info.get("duration") or 0)
            bt = list(an_.get("beats") or [])
            if bt and mdur > 0:
                k = 0
                while k * mdur < T:  # musica in loop
                    beats += [x + k * mdur for x in bt if x + k * mdur < T]
                    k += 1
        sb.beat_times = [round(x, 3) for x in beats]
        # confini delle inquadrature
        pattern = [1.0, 0.85, 1.2, 0.95, 1.1, 0.8, 1.15, 1.0]
        boundaries = [0.0]
        t, i = 0.0, 0
        first = max(base, 3.2) if b.title else base
        while True:
            step = first if i == 0 else base * pattern[i % len(pattern)]
            nxt = t + step
            if beats and 60.0 / max(bpm, 1) < step:  # aggancio al beat più vicino
                nxt = min(beats, key=lambda x: abs(x - nxt))
                if nxt - t < step * 0.6:
                    nxt = t + step
            if T - nxt < base * 0.7:
                break
            boundaries.append(round(nxt * fps) / fps)
            t, i = nxt, i + 1
        boundaries.append(T)
        # limite: inquadrature non più lunghe della clip più lunga disponibile
        vis = visual_assets(p)
        has_image = any(a.kind == "image" for a in vis)
        max_src = max([float(a.info.get("duration") or 0) for a in vis if a.kind == "video"] or [0])
        if not has_image and max_src > 0:
            limit = max_src - 0.3
            nb = [boundaries[0]]
            for x in boundaries[1:]:
                while x - nb[-1] > limit:
                    nb.append(round((nb[-1] + limit * 0.9) * fps) / fps)
                nb.append(x)
            boundaries = nb
        sections = b.notes or []
        topics = b.topics or []
        sentences = tts.split_sentences(b.narration) if b.narration else []
        sb.narration_sentences = sentences
        n = len(boundaries) - 1
        for k in range(n):
            purpose = "intro" if k == 0 else ("outro" if k == n - 1 else "body")
            kws = list(topics)
            if sections:
                kws = list(tokens(sections[min(len(sections) - 1, k * len(sections) // n)])) + kws
            sb.shots.append(Shot(index=k, purpose=purpose, target_duration=round(boundaries[k + 1] - boundaries[k], 3),
                                 keywords=kws[:6]))
        self._assign(p, sb, vis, ctx)
        sb.notes.append(f"{n} inquadrature, durata tipica {base:.1f} s (ritmo {b.pace})"
                        + (f", tagli agganciati ai beat ({bpm:.0f} BPM)" if beats else ""))
        sb.notes.append("La pertinenza è calcolata su nomi, tag e descrizioni dei file, non sul contenuto visivo")
        p.storyboard = sb
        return AgentResult("completed", f"{n} inquadrature pianificate su {T:.2f} s"
                           + (f", tagli a tempo di musica ({bpm:.0f} BPM)" if beats else ""),
                           {"shots": [s.model_dump() for s in sb.shots], "notes": sb.notes})

    def _assign(self, p, sb: Storyboard, vis, ctx: Context) -> None:
        _, tr = PACE[p.brief.pace]
        used: dict[str, int] = {}
        ranges: dict[str, list] = {}
        prev = ""
        for s in sb.shots:
            need = s.target_duration + tr  # margine per le transizioni
            kw = set(s.keywords)
            best = None
            for a in vis:
                ad = float(a.info.get("duration") or 0)
                q = float((a.analysis or {}).get("summary", {}).get("mean_score") or 0.4)
                text = tokens(a.name + " " + a.description) | set(a.tags)
                rel = len(kw & text) / max(1, len(kw)) if kw else 0.0
                novelty = -0.45 * used.get(a.id, 0) - (0.9 if a.id == prev and len(vis) > 1 else 0)
                if a.kind == "video":
                    if ad < min(need, s.target_duration) + 0.05:
                        continue
                    src_in, ws = an.best_window(a.analysis, ad, min(need, ad), ranges.get(a.id))
                else:
                    src_in, ws = 0.0, q
                score = 0.4 * rel + 0.25 * q + 0.35 * ws + novelty
                if best is None or score > best[0]:
                    best = (score, a, src_in, rel, q, ws)
            if best is None:
                raise Blocked(f"Nessuna clip abbastanza lunga per l'inquadratura {s.index + 1} "
                              f"({s.target_duration:.1f} s)")
            score, a, src_in, rel, q, ws = best
            s.asset_id, s.src_in = a.id, src_in
            s.reason = (f"{a.name}: pertinenza {rel:.2f}, qualità {q:.2f}"
                        + (f", finestra {src_in:.2f}-{src_in + s.target_duration:.2f} s (punteggio {ws:.2f})" if a.kind == "video" else ", immagine"))
            used[a.id] = used.get(a.id, 0) + 1
            ranges.setdefault(a.id, []).append((src_in, src_in + need))
            prev = a.id


# --------------------------------------------------------------------------- 4. montaggio

class EditorAgent(Agent):
    name, title = "editor", "Montaggio delle clip"

    def run(self, ctx: Context) -> AgentResult:
        p = ctx.project
        sb = p.storyboard
        if not sb or not sb.shots:
            raise Blocked("Storyboard assente")
        fps = p.export.fps
        _, tr = PACE[p.brief.pace]
        n = len(sb.shots)
        bounds = [0.0]
        for s in sb.shots:
            bounds.append(bounds[-1] + s.target_duration)
        # transizioni (in frame pari, così metà cade su un frame intero)
        trans = [0]
        for k in range(1, n):
            if tr <= 0:
                trans.append(0)
            else:
                lim = min(sb.shots[k - 1].target_duration, sb.shots[k].target_duration) * 0.4
                f = int(min(tr, lim) * fps) // 2 * 2
                trans.append(f if p.brief.pace != "normal" or k % 2 == 1 else 0)
        trans.append(0)
        out_w, out_h = p.export.width, p.export.height
        out_ar = out_w / out_h
        clips = []
        motions = ["zoom_in", "pan_right", "zoom_out", "pan_left"]
        img_i = 0
        Bf = [round(x * fps) for x in bounds]
        for k, s in enumerate(sb.shots):
            a = p.asset(s.asset_id)
            dfr = (Bf[k + 1] - Bf[k]) + trans[k] // 2 + trans[k + 1] // 2
            src_in = max(0.0, s.src_in - (trans[k] / 2) / fps) if a.kind == "video" else 0.0
            ad = float(a.info.get("duration") or 0)
            if a.kind == "video" and src_in + dfr / fps > ad - 0.02:
                src_in = max(0.0, ad - dfr / fps - 0.05)
            sw, sh = a.info.get("width") or out_w, a.info.get("height") or out_h
            src_ar = sw / max(1, sh)
            fit = "contain" if abs(math.log(src_ar / out_ar)) > 0.35 else "cover"
            motion = "none"
            if a.kind == "image":
                motion = motions[img_i % len(motions)]
                img_i += 1
            elif p.brief.pace == "calm" and float((a.analysis or {}).get("summary", {}).get("mean_motion", 1)) < 0.004:
                motion = "zoom_in"  # clip quasi statica: lieve push-in
            clips.append(VideoClip(
                asset_id=a.id, src_in=round(src_in, 3), duration=dfr / fps, fit=fit, motion=motion,
                transition=Transition(type="fade" if trans[k] else "cut", duration=trans[k] / fps),
                note=s.reason,
            ))
        p.timeline.video = clips
        T = p.timeline.duration()
        expected = Bf[-1] / fps
        ctx.shared["expected_duration"] = expected
        if abs(T - expected) > 0.5 / fps:
            raise RuntimeError(f"Durata timeline {T:.3f} s diversa dal piano {expected:.3f} s")
        return AgentResult("completed", f"Timeline di {n} clip, durata {T:.3f} s (esatta al frame)",
                           {"clips": [{"asset": p.asset(c.asset_id).name, "in": c.src_in, "durata": round(c.duration, 3),
                                       "adattamento": c.fit, "movimento": c.motion,
                                       "transizione": c.transition.duration} for c in clips],
                            "durata_timeline": round(T, 3)})


# --------------------------------------------------------------------------- 5. audio

class AudioAgent(Agent):
    name, title = "audio", "Creazione e mixaggio dell'audio"
    critical = False

    def run(self, ctx: Context) -> AgentResult:
        p, b = ctx.project, ctx.project.brief
        T = p.timeline.duration()
        tl = p.timeline
        tl.audio = [c for c in tl.audio if c.role == "sfx"]
        p.assets = [a for a in p.assets if not (a.role == "voice" and a.source == "tts")]
        details: dict = {}
        warnings: list[str] = []
        status = "completed"
        parts = []
        # musica
        sb = p.storyboard
        if b.want_music and sb and sb.music_asset_id:
            m = p.asset(sb.music_asset_id)
            mdur = float(m.info.get("duration") or 0)
            tl.audio.append(AudioClip(asset_id=m.id, role="music", start=0.0, duration=T, loop=mdur < T,
                                      fade_in=0.0 if sb.beat_times else 1.0, fade_out=min(2.5, T * 0.12),
                                      volume_db=-1.0))
            parts.append(f"musica «{m.name}»" + (" in loop" if mdur < T else ""))
            details["musica"] = {"asset": m.name, "licenza": m.license.name, "bpm": (m.analysis or {}).get("bpm")}
        # voce
        sentences = sb.narration_sentences if sb else []
        if b.want_voice and sentences:
            st = tts.status(b.language)
            if not st["ready"]:
                status = "blocked"
                warnings.append("Voce richiesta ma Piper o una voce non sono installati: video senza voce")
            else:
                start, tail = 0.9, 1.2
                avail = T - start - tail
                gap = 0.3
                scale = 1.0
                out_dir = p.media_dir / "voice"
                shutil.rmtree(out_dir, ignore_errors=True)
                res = tts.synthesize(sentences, out_dir, length_scale=scale, language=b.language)
                total = sum(d for _, d, _ in res) + gap * (len(res) - 1)
                if total > avail:
                    scale = max(0.82, avail / total * 0.98)
                    ctx.log(f"Narrazione troppo lunga ({total:.1f}s > {avail:.1f}s): risintetizzo a velocità {1 / scale:.2f}x")
                    res = tts.synthesize(sentences, out_dir, length_scale=scale, language=b.language)
                    total = sum(d for _, d, _ in res) + gap * (len(res) - 1)
                dropped = []
                while res and total > avail:
                    f, d, s = res.pop()
                    dropped.append(s)
                    total -= d + gap
                if dropped:
                    warnings.append(f"Narrazione troppo lunga per {T:.0f} s: omesse {len(dropped)} frasi finali")
                if res:
                    spare = avail - total
                    gap2 = gap + min(1.0, spare / max(1, len(res)))
                    t = start
                    voice_lic = LicenseInfo(code="generated", name="Voce sintetica locale (Piper)",
                                            notes=f"Modello voce: {st['voice']} – verificarne la licenza nella MODEL_CARD")
                    timing = []
                    for f, d, s in res:
                        a = library.import_file(p, f, copy=False, license=voice_lic, source="tts", role="voice",
                                                name=f.name)
                        tl.audio.append(AudioClip(asset_id=a.id, role="voice", start=round(t, 3), duration=round(d, 3),
                                                  text=s, volume_db=0.0))
                        timing.append({"start": round(t, 2), "end": round(t + d, 2), "testo": s})
                        t += d + gap2
                    parts.append(f"voce narrante ({len(res)} frasi, {total:.1f} s)")
                    details["voce"] = {"motore": "Piper", "voce": st["voice"], "velocità": round(1 / scale, 2),
                                       "frasi": timing}
        elif b.want_voice:
            status = "blocked"
            warnings.append("Voce richiesta ma nessun testo di narrazione disponibile")
        has_clip_audio = any(p.asset(c.asset_id).info.get("has_audio") for c in tl.video)
        tl.keep_clip_audio = True
        details["mix"] = {"audio_clip_originale": has_clip_audio, "ducking_sotto_voce": any(c.role == "voice" for c in tl.audio),
                          "target_lufs": p.export.loudness_target, "true_peak": -1.5}
        if not parts:
            parts.append("solo audio originale delle clip" if has_clip_audio else "nessuna sorgente audio")
        summary = ", ".join(parts) + f"; mix con ducking e normalizzazione a {p.export.loudness_target:.0f} LUFS (in rendering)"
        if status == "blocked":
            summary = "Parziale: " + summary
        return AgentResult(status, summary, details, warnings=warnings)


# --------------------------------------------------------------------------- 6. sottotitoli

class SubtitleAgent(Agent):
    name, title = "subtitles", "Sottotitoli automatici"
    critical = False

    def run(self, ctx: Context) -> AgentResult:
        p, b = ctx.project, ctx.project.brief
        tl = p.timeline
        T = tl.duration()
        if not b.want_subtitles:
            tl.subtitles = []
            return AgentResult("skipped", "Sottotitoli non richiesti")
        voices = [c for c in tl.audio if c.role == "voice"]
        cues: list[SubtitleCue] = []
        method = ""
        warnings = []
        if voices:
            words: list[tuple[float, float, str]] = []
            aligned = 0
            for c in voices:
                ctx.check_cancel()
                path = p.asset_path(c.asset_id)
                script = c.text.split()
                ww = []
                if asr.installed():
                    try:
                        segs = asr.transcribe(path, b.language)
                        ww = [w for s in segs for w in s["words"]]
                    except asr.ASRUnavailable as e:
                        warnings.append(str(e))
                if ww and len(ww) == len(script):
                    words += [(c.start + s_, c.start + e_, tok) for (s_, e_, _), tok in zip(ww, script)]
                    aligned += 1
                else:
                    a0 = c.start + (ww[0][0] if ww else 0.0)
                    a1 = c.start + (ww[-1][1] if ww else c.duration)
                    total = sum(len(x) + 1 for x in script)
                    t = a0
                    for tok in script:
                        d = (a1 - a0) * (len(tok) + 1) / total
                        words.append((t, t + d, tok))
                        t += d
            cues = subs.from_words(words)
            method = (f"testo della narrazione allineato con Whisper ({asr.model_description()}) su {aligned}/{len(voices)} frasi"
                      if aligned else "testo della narrazione con tempi dei segmenti vocali (Whisper non disponibile o non allineato)")
        else:
            if not asr.installed():
                return AgentResult("skipped", "Nessuna voce narrante e faster-whisper non installato: nessun sottotitolo",
                                   warnings=["Installa faster-whisper per trascrivere il parlato delle clip"])
            starts = tl.clip_starts()
            words = []
            for c, st in zip(tl.video, starts):
                a = p.asset(c.asset_id)
                if a.kind != "video" or not a.info.get("has_audio") or c.mute:
                    continue
                ctx.check_cancel()
                segs = asr.transcribe(p.asset_path(a), b.language if b.language else None, c.src_in, c.duration)
                for s in segs:
                    if s["no_speech_prob"] > 0.6:
                        continue
                    for ws, we, w in s["words"]:
                        if ws < c.duration:
                            words.append((st + ws, st + min(we, c.duration), w))
            cues = subs.from_words(sorted(words))
            method = f"trascrizione del parlato delle clip con Whisper ({asr.model_description()})"
        cues = subs.normalize(cues, T)
        tl.subtitles = cues
        probs = subs.validate(cues, T)
        if not cues:
            return AgentResult("completed", "Nessun parlato rilevato: nessun sottotitolo generato", {"metodo": method})
        return AgentResult("completed", f"{len(cues)} sottotitoli ({method})",
                           {"metodo": method, "problemi": probs,
                            "cue": [c.model_dump() for c in cues]}, warnings=probs[:3])


# --------------------------------------------------------------------------- 7. grafica

TRANSITIONS = {
    "calm": ["dissolve", "fade", "fadeblack", "fade"],
    "normal": ["fade", "smoothleft", "dissolve", "circleopen", "wipeleft"],
    "dynamic": ["slideleft", "smoothright", "zoomin", "fadewhite", "slideup", "hblur"],
}


class GraphicsAgent(Agent):
    name, title = "graphics", "Grafiche, transizioni e animazioni"
    critical = False

    def run(self, ctx: Context) -> AgentResult:
        p, b = ctx.project, ctx.project.brief
        tl = p.timeline
        T = tl.duration()
        done = []
        pal = TRANSITIONS[b.pace]
        k = 0
        for c in tl.video[1:]:
            if c.transition.duration > 0:
                c.transition.type = pal[k % len(pal)]
                k += 1
        if k:
            done.append(f"{k} transizioni ({', '.join(sorted(set(pal[:k])))})")
        for c in tl.video:
            c.look = b.look
        if b.look != "none":
            done.append(f"look colore «{b.look}»")
        tl.overlays = []
        if b.title:
            tl.overlays.append(TextOverlay(kind="title", text=b.title, subtext=b.subtitle,
                                           start=0.35, duration=round(min(3.6, T * 0.3), 2)))
            done.append("titolo animato")
        if b.notes and p.storyboard:
            starts = tl.clip_starts()
            n = len(tl.video)
            for i, label in enumerate(b.notes):
                idx = min(n - 1, max(1, round(i * n / len(b.notes))))
                st = starts[idx] + 0.4
                if st + 3 < T - 1:
                    tl.overlays.append(TextOverlay(kind="lower_third", text=label, start=round(st, 2), duration=3.0))
            done.append(f"{len(b.notes)} lower third di sezione")
        if re.search(r"chiusura|end card|outro|cartello finale", b.prompt, re.I) and b.title:
            tl.overlays.append(TextOverlay(kind="end_card", text=b.title, start=round(T - 3.2, 2), duration=3.0))
            done.append("cartello finale")
        logos = [a for a in p.assets if a.role == "logo"]
        if logos:
            if not tl.logo or tl.logo.asset_id not in {a.id for a in logos}:
                tl.logo = Logo(asset_id=logos[-1].id)
            done.append(f"logo «{logos[-1].name}» ({tl.logo.position})")
        tl.fade_in, tl.fade_out = (0.6, 1.0) if b.pace == "calm" else (0.3, 0.8)
        done.append("dissolvenza in apertura/chiusura")
        return AgentResult("completed", ", ".join(done),
                           {"overlays": [o.model_dump() for o in tl.overlays],
                            "transizioni": [c.transition.model_dump() for c in tl.video[1:]]})


# --------------------------------------------------------------------------- 8. rendering

class RenderAgent(Agent):
    name, title = "render", "Rendering del video"

    def run(self, ctx: Context) -> AgentResult:
        p = ctx.project
        weights = {"segments": (0.0, 0.45), "compose": (0.45, 0.4), "audio": (0.85, 0.1), "done": (0.95, 0.05)}

        def prog(stage, f):
            a, w = weights.get(stage, (0.95, 0.05))
            ctx.progress(a + w * f)

        mode = ctx.shared.get("mode", "final")
        r = render_project(p, mode, progress=prog, cancel=ctx.cancel)
        ctx.shared["render"] = r
        p.renders.append({"path": p.rel(r.path), "mode": mode, "created_at": r.path.stem[-20:],
                          "duration": r.duration, "size": f"{r.width}x{r.height}", "encoder": r.encoder})
        return AgentResult("completed",
                           f"{r.path.name}: {r.width}x{r.height} {r.fps} fps, {r.duration:.2f} s, encoder {r.encoder} "
                           f"({r.segments_cached}/{r.segments_total} segmenti da cache), {r.timings['total']:.0f} s",
                           r.to_dict(), outputs=[p.rel(r.path)] + [p.rel(x) for x in (r.srt, r.vtt) if x])


# --------------------------------------------------------------------------- 9. QA

class QAAgent(Agent):
    name, title = "qa", "Controllo qualità audio e video"

    def run(self, ctx: Context) -> AgentResult:
        p = ctx.project
        r = ctx.shared.get("render")
        if r is None:
            raise Blocked("Nessun file renderizzato da controllare")
        expected = ctx.shared.get("expected_duration") or p.timeline.duration()
        if ctx.shared.get("mode") == "final":
            size = (p.export.width, p.export.height)
        else:
            size = (r.width, r.height)
        report = qa.run_qa(p, r.path, expected_duration=expected, expected_size=size,
                           expected_fps=r.fps, expected_codec=p.export.codec if r.mode == "final" else "h264")
        ctx.shared["qa"] = report
        fails = [c for c in report["checks"] if c["status"] == "fail"]
        warns = [c for c in report["checks"] if c["status"] == "warn"]
        summary = (f"Esito {report['status'].upper()}: {len(report['checks']) - len(fails) - len(warns)} superati, "
                   f"{len(warns)} avvisi, {len(fails)} falliti")
        status = "failed" if fails else "completed"
        return AgentResult(status, summary, report,
                           warnings=[f"{c['name']}: {c['measured']} (atteso {c['expected']})" for c in warns])


# --------------------------------------------------------------------------- 10. export

class ExportAgent(Agent):
    name, title = "export", "Esportazione del risultato finale"

    def run(self, ctx: Context) -> AgentResult:
        p = ctx.project
        r = ctx.shared.get("render")
        rep = ctx.shared.get("qa")
        if r is None or rep is None:
            raise Blocked("Rendering o controllo qualità mancanti")
        base = r.path.with_suffix("")
        used = {c.asset_id for c in p.timeline.video} | {c.asset_id for c in p.timeline.audio}
        if p.timeline.logo:
            used.add(p.timeline.logo.asset_id)
        credits = []
        for a in p.assets:
            if a.id not in used:
                continue
            credits.append({"asset": a.name, "fonte": a.source, "url": a.origin_url, "licenza": a.license.name,
                            "codice": a.license.code, "attribuzione": a.license.attribution,
                            "note": a.license.notes, "sha256": a.sha256[:16]})
        lines = ["CREDITI E LICENZE", "=" * 17, ""]
        for c in credits:
            lines.append(f"- {c['asset']} — {c['licenza']}" + (f" — {c['attribuzione']}" if c["attribuzione"] else "")
                         + (f" — {c['url']}" if c["url"] else ""))
        credits_f = base.parent / (base.name + "_crediti.txt")
        credits_f.write_text("\n".join(lines) + "\n", encoding="utf-8")
        poster = base.parent / (base.name + "_poster.jpg")
        try:
            ff.run_ffmpeg(["-ss", f"{min(r.duration / 3, 4):.2f}", "-i", str(r.path), "-frames:v", "1",
                           "-q:v", "3", str(poster)], timeout=120)
        except ff.FFmpegError:
            poster = None
        run_tasks = [{"agente": t.title, "stato": t.status, "sintesi": t.summary, "secondi": t.seconds,
                      "avvisi": t.warnings} for t in ctx.run.tasks if t is not ctx.task]
        report = {
            "progetto": p.name, "file": r.path.name, "durata": r.duration, "risoluzione": f"{r.width}x{r.height}",
            "fps": r.fps, "codec": p.export.codec if r.mode == "final" else "h264", "encoder": r.encoder,
            "encoder_motivo": r.encoder_reason, "loudness": r.loudness, "qa": rep, "crediti": credits,
            "brief": p.brief.model_dump(), "storyboard": p.storyboard.model_dump() if p.storyboard else None,
            "agenti": run_tasks,
        }
        rep_f = base.parent / (base.name + "_report.json")
        rep_f.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        md = [f"# Report di produzione — {p.name}", "", f"- File: `{r.path.name}`",
              f"- {r.width}x{r.height}, {r.fps} fps, {r.duration:.3f} s, encoder `{r.encoder}`",
              f"- Esito controllo qualità: **{rep['status'].upper()}**", "", "## Controlli", "",
              "| Controllo | Esito | Misurato | Atteso |", "|---|---|---|---|"]
        for c in rep["checks"]:
            md.append(f"| {c['name']} | {c['status']} | {c['measured']} | {c['expected']} |")
        md += ["", "## Agenti", ""] + [f"- **{t['agente']}** — {t['stato']}: {t['sintesi']}" for t in run_tasks]
        md += ["", "## Crediti", ""] + [ln for ln in lines[3:]]
        md_f = base.parent / (base.name + "_report.md")
        md_f.write_text("\n".join(md) + "\n", encoding="utf-8")
        outs = [r.path, r.srt, r.vtt, credits_f, rep_f, md_f, poster]
        ctx.shared["result"] = {"video": p.rel(r.path), "qa": rep["status"],
                                "files": [p.rel(x) for x in outs if x]}
        return AgentResult("completed", f"Consegnati {len([x for x in outs if x])} file (MP4, sottotitoli, crediti, report)",
                           ctx.shared["result"], outputs=[p.rel(x) for x in outs if x])


def generate_agents() -> list[Agent]:
    return [BriefAgent(), ResearchAgent(), AnalysisAgent(), StoryboardAgent(), EditorAgent(), AudioAgent(),
            SubtitleAgent(), GraphicsAgent(), RenderAgent(), QAAgent(), ExportAgent()]


def render_agents() -> list[Agent]:
    return [RenderAgent(), QAAgent(), ExportAgent()]

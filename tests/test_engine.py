"""Test reali: generano media sintetici con FFmpeg, eseguono analisi, montaggio,
rendering e QA, e verificano il file prodotto con ffprobe."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from focusstudio import config, library, qa
from focusstudio import project as pj
from focusstudio import subtitles as subs
from focusstudio.agents.brief import parse_duration, parse_rules
from focusstudio.media import analysis as an
from focusstudio.project import AudioClip, Logo, SubtitleCue, TextOverlay, Transition, VideoClip
from focusstudio.render.engine import render_project, validate_timeline


def ff(*a):
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", *a], check=True)


@pytest.fixture(scope="session")
def media(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("media")
    ff("-f", "lavfi", "-i", "testsrc2=s=640x360:r=30", "-f", "lavfi", "-i", "sine=f=440:r=48000",
       "-t", "6", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
       str(d / "a.mp4"))
    # due scene con un taglio netto a 3 s e nero nel primo secondo
    ff("-f", "lavfi", "-i", "color=black:s=640x360:r=30:d=1", "-f", "lavfi", "-i", "mandelbrot=s=640x360:r=30",
       "-f", "lavfi", "-i", "cellauto=s=640x360:r=30:rule=110",
       "-filter_complex", "[1:v]trim=0:2,setpts=PTS-STARTPTS[m];[2:v]trim=0:3,setpts=PTS-STARTPTS[c];"
       "[0:v][m][c]concat=n=3:v=1:a=0,format=yuv420p[v]", "-map", "[v]",
       "-c:v", "libx264", "-preset", "ultrafast", str(d / "b.mp4"))
    ff("-f", "lavfi", "-i", "testsrc2=s=360x640:r=30", "-t", "4", "-c:v", "libx264", "-preset", "ultrafast",
       "-pix_fmt", "yuv420p", str(d / "vertical.mp4"))
    ff("-f", "lavfi", "-i", "gradients=s=1200x800", "-frames:v", "1", str(d / "img.png"))
    ff("-f", "lavfi", "-i", "aevalsrc='0.6*sin(2*PI*55*t)*exp(-12*mod(t,0.5))':s=48000:d=20", "-ac", "2",
       str(d / "music.wav"))
    ff("-f", "lavfi", "-i", "color=c=red:s=100x100,format=rgba", "-frames:v", "1", str(d / "logo.png"))
    return d


@pytest.fixture()
def data_dir(tmp_path):
    config.set_data_dir(tmp_path / "data")
    s = config.settings()
    s.gpu_encoding = "off"
    return tmp_path


def test_media_info_and_analysis(media, data_dir):
    i = an.media_info(media / "b.mp4")
    assert i["kind"] == "video" and i["width"] == 640 and abs(i["duration"] - 6) < 0.1
    a = an.analyze_video(media / "b.mp4", i)
    assert any(abs(c - 3.0) < 0.15 for c in a["scene_cuts"]), a["scene_cuts"]
    assert a["black"] and a["black"][0][0] == 0.0 and abs(a["black"][0][1] - 1.0) < 0.3
    start, _ = an.best_window(a, i["duration"], 1.5)
    assert start >= 0.9  # evita il nero iniziale
    assert an.media_info(media / "img.png")["kind"] == "image"
    assert an.media_info(media / "music.wav")["kind"] == "audio"


def test_beat_detection(media, data_dir):
    a = an.analyze_audio(media / "music.wav", {})
    assert abs(a["bpm"] - 120) < 3, a["bpm"]
    diffs = [b - a_ for a_, b in zip(a["beats"], a["beats"][1:])]
    assert abs(sum(diffs) / len(diffs) - 0.5) < 0.03


def test_corrupt_file_rejected(tmp_path, data_dir):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(Exception):
        an.media_info(bad)


def test_brief_rules():
    assert parse_duration("video di 1 minuto e 30 secondi") == 90
    assert parse_duration("clip da 45s") == 45
    b, exp, _ = parse_rules('Video verticale dinamico di 20 secondi in 4K HEVC titolo "Ciao". Testo: dura dieci minuti.')
    assert b.duration == 20 and b.pace == "dynamic" and b.title == "Ciao" and b.want_voice
    assert exp == {"width": 2160, "height": 3840, "codec": "hevc"}


def test_subtitles_format_and_validation():
    words = [(i * 0.4, i * 0.4 + 0.35, w) for i, w in enumerate("Questa è una frase. E questa è un'altra frase più lunga".split())]
    cues = subs.from_words(words)
    assert len(cues) >= 2 and cues[0].text.endswith("frase.")
    srt = subs.to_srt(cues)
    assert srt.startswith("1\n00:00:00,000 --> ")
    assert subs.to_vtt(cues).startswith("WEBVTT")
    assert subs.validate(cues, 10) == []
    assert subs.validate([SubtitleCue(start=2, end=1, text="x")], 10)


def _project(media):
    p = pj.create("Test")
    A = {Path(f).stem: library.import_file(p, media / f, analyze=True)
         for f in ("a.mp4", "b.mp4", "vertical.mp4", "img.png")}
    music = library.import_file(p, media / "music.wav", role="music", analyze=True)
    logo = library.import_file(p, media / "logo.png", role="logo")
    return p, A, music, logo


def test_validate_timeline_errors(media, data_dir):
    p, A, *_ = _project(media)
    p.timeline.video = [VideoClip(asset_id=A["a"].id, src_in=5.5, duration=2)]
    assert any("oltre la fine" in e for e in validate_timeline(p))


def test_render_full_pipeline_exact_duration(media, data_dir):
    p, A, music, logo = _project(media)
    tl = p.timeline
    tl.video = [
        VideoClip(asset_id=A["a"].id, src_in=0.5, duration=2.0),
        VideoClip(asset_id=A["b"].id, src_in=1.2, duration=2.0, transition=Transition(type="fade", duration=0.4)),
        VideoClip(asset_id=A["img"].id, duration=2.0, motion="zoom_in", transition=Transition(type="slideleft", duration=0.4)),
        VideoClip(asset_id=A["vertical"].id, duration=1.5, fit="contain"),
    ]
    tl.overlays = [TextOverlay(kind="title", text="Titolo", start=0.2, duration=1.5)]
    tl.audio = [AudioClip(asset_id=music.id, role="music", duration=10, fade_out=1)]
    tl.subtitles = [SubtitleCue(start=0.5, end=2.0, text="Ciao")]
    tl.logo = Logo(asset_id=logo.id)
    p.export.width, p.export.height = 640, 360
    expected = tl.duration()
    assert abs(expected - 6.7) < 1e-6
    r = render_project(p, "final")
    info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
                                      "-show_format", str(r.path)], capture_output=True, check=True).stdout)
    v = [s for s in info["streams"] if s["codec_type"] == "video"][0]
    assert (v["width"], v["height"]) == (640, 360)
    assert abs(float(v["duration"]) - expected) <= 1 / 30 + 1e-3
    assert any(s["codec_type"] == "audio" for s in info["streams"])
    assert any(s["codec_type"] == "subtitle" for s in info["streams"])
    assert r.srt and r.srt.exists()
    rep = qa.run_qa(p, r.path, expected_duration=expected, expected_size=(640, 360), expected_fps=30,
                    expected_codec="h264")
    statuses = {c["name"]: c["status"] for c in rep["checks"]}
    assert statuses["Durata"] == "pass"
    assert statuses["Loudness integrata"] == "pass", rep
    assert statuses["Decodifica completa"] == "pass"
    assert statuses["Licenze degli asset usati"] == "pass"
    # seconda esecuzione: tutti i segmenti dalla cache
    r2 = render_project(p, "final")
    assert r2.segments_cached == 4


def test_hevc_export(media, data_dir):
    p, A, *_ = _project(media)
    p.timeline.video = [VideoClip(asset_id=A["a"].id, duration=1.5)]
    p.export.codec = "hevc"
    p.export.width, p.export.height = 640, 360
    r = render_project(p, "final")
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name,codec_tag_string",
                          "-of", "csv=p=0", str(r.path)], capture_output=True, text=True).stdout
    assert "hevc" in out and "hvc1" in out


def test_unknown_license_fails_qa(media, data_dir):
    p, A, *_ = _project(media)
    A["a"].license.code = "unknown"
    p.timeline.video = [VideoClip(asset_id=A["a"].id, duration=1.0)]
    p.export.width, p.export.height = 320, 180
    r = render_project(p, "preview")
    rep = qa.run_qa(p, r.path)
    lic = [c for c in rep["checks"] if c["name"] == "Licenze degli asset usati"][0]
    assert lic["status"] == "fail"


def test_generate_pipeline_rules_only(media, data_dir):
    from focusstudio import service
    config.settings().llm_enabled = False
    p, *_ = _project(media)
    pj.save(p)
    run = service.start_generate(p.id, 'Video dinamico di 8 secondi in 720p con titolo "Prova", senza sottotitoli', wait=True)
    st = {t.agent: t.status for t in run.tasks}
    assert st["render"] == "completed" and st["qa"] == "completed" and st["export"] == "completed", \
        [(t.agent, t.status, t.summary) for t in run.tasks]
    assert st["subtitles"] == "skipped"
    p = pj.load(p.id)
    assert abs(p.timeline.duration() - 8.0) < 1 / 30
    qa_task = [t for t in run.tasks if t.agent == "qa"][0]
    dur = [c for c in qa_task.details["checks"] if c["name"] == "Durata"][0]
    assert dur["status"] == "pass"
    assert len(p.storyboard.shots) == len(p.timeline.video) >= 3

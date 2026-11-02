"""Genera media di prova sintetici (nessun materiale protetto da copyright).

Uso:  python scripts/make_test_media.py <cartella_output>
Produce clip video con tagli di scena, nero iniziale, formato verticale,
un'immagine ad alta risoluzione, una traccia musicale a 120 BPM e un logo PNG
con trasparenza. Serve per test automatici e prove del motore di montaggio.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", *args], check=True)


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    # 1) zoom frattale 12 s, con audio tono
    ff("-f", "lavfi", "-i", "mandelbrot=s=1920x1080:rate=30",
       "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000",
       "-t", "12", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
       "-c:a", "aac", "-shortest", str(out / "fractal_zoom.mp4"))
    # 2) due scene (taglio a 6 s)
    ff("-f", "lavfi", "-i", "life=s=480x270:rate=30:mold=10:ratio=0.2:death_color=#101830:life_color=#40c0ff,scale=1920:1080:flags=neighbor",
       "-f", "lavfi", "-i", "cellauto=s=480x270:rate=30:rule=110,scale=1920:1080:flags=neighbor",
       "-filter_complex", "[0:v]trim=0:6,setpts=PTS-STARTPTS[a];[1:v]trim=0:4,setpts=PTS-STARTPTS[b];[a][b]concat=n=2:v=1:a=0[v]",
       "-map", "[v]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
       str(out / "two_scenes.mp4"))
    # 3) gradienti animati con 1 s di nero all'inizio
    ff("-f", "lavfi", "-i", "gradients=s=1920x1080:rate=30:speed=0.03:n=4",
       "-vf", "fade=t=in:st=1:d=0.01", "-t", "9",
       "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
       str(out / "gradients_black_start.mp4"))
    # 4) clip verticale 9:16
    ff("-f", "lavfi", "-i", "testsrc2=s=1080x1920:rate=30", "-t", "6",
       "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
       str(out / "vertical_test.mp4"))
    # 5) immagine 3000x2000
    ff("-f", "lavfi", "-i", "gradients=s=3000x2000:c0=#0b1020:c1=#2a6cff:c2=#ff7a2a:n=3", "-frames:v", "1",
       str(out / "still_gradient.png"))
    # 6) musica sintetica 120 BPM, 40 s (cassa + hi-hat + basso)
    expr = ("0.6*sin(2*PI*55*t)*exp(-12*mod(t,0.5))"
            "+0.25*sin(2*PI*110*t)*lt(mod(floor(t*2),4),2)"
            "+0.08*(random(0)-0.5)*exp(-40*mod(t+0.25,0.5))")
    ff("-f", "lavfi", "-i", f"aevalsrc='{expr}':s=48000:d=40", "-ac", "2", "-c:a", "pcm_s16le",
       str(out / "music_120bpm.wav"))
    # 7) logo con trasparenza
    ff("-f", "lavfi", "-i", "color=c=#2a6cff@1.0:s=400x400,format=rgba,"
       "geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='if(lt(hypot(X-200,Y-200),180),255,0)'",
       "-frames:v", "1", str(out / "logo.png"))
    print("Media di prova creati in", out)


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "test_media"))

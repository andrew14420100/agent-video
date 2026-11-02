# Steam Focus Studio

Studio di montaggio video **automatizzato da più agenti**, locale, gratuito e open source.
Descrivi il video in italiano; gli agenti cercano/analizzano il materiale, pianificano lo storyboard,
montano le clip, creano voce e mix audio, sottotitolano, aggiungono grafiche e transizioni, controllano
la qualità ed esportano un **vero MP4** (H.264 o HEVC, fino a 4K) — con un report verificabile di ciò
che ogni agente ha fatto davvero.

> Questo progetto riprende le specifiche di [Steam-Focus-Studio](https://github.com/andrew14420100/Steam-Focus-Studio)
> (che non conteneva codice, vedi [docs/AUDIT.md](docs/AUDIT.md)) e le trasforma in un motore funzionante.

## Esempio

```
Video calmo di 30 secondi in 4K HEVC con titolo "La Terra dall'orbita",
cerca online filmati della Terra vista dalla stazione spaziale, con musica e voce narrante.
Testo: La Terra vista dall'orbita sembra immobile. ...
```

Risultato: MP4 3840×2160 di 30,000 s, clip NASA scaricate con licenza registrata, tagli a tempo di
musica, voce italiana locale (Piper), sottotitoli allineati con Whisper, titolo animato, logo, mix a
−14 LUFS, report QA + crediti.

## Requisiti

- Windows 11 (sviluppato e testato anche su Linux), Python 3.10–3.13
- FFmpeg ≥ 6 con libx264, libx265, libass (su Windows: `winget install Gyan.FFmpeg`, include NVENC)
- Facoltativi: GPU NVIDIA (NVENC per codifica, CUDA per Whisper), Piper + voce italiana,
  faster-whisper, Ollama con un modello (es. `qwen2.5:14b-instruct`, entra in 16 GB di VRAM)

## Installazione (Windows 11)

```powershell
git clone https://github.com/andrew14420100/agent-video.git
cd agent-video
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

Lo script (chiede conferma a ogni passo): verifica/installa Python e FFmpeg via winget, crea `.venv`,
installa le dipendenze (`.[voice,asr,desktop]`), propone il download della voce italiana Piper, crea un
collegamento sul desktop e lancia `focusstudio doctor`. Avvio: `start.bat` (finestra desktop
WebView2; se pywebview manca si apre il browser su http://127.0.0.1:8765).

Installazione manuale:

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -e ".[voice,asr,desktop]"
python -m focusstudio app
```

LLM locale facoltativo: installa [Ollama](https://ollama.com) e `ollama pull qwen2.5:14b-instruct`.
Senza LLM la richiesta viene interpretata con regole deterministiche (dichiarato nel report);
in quel caso il testo della voce va scritto dopo `Testo:`.

## Uso

**Interfaccia**: importa clip/immagini/audio (trascina o percorso locale per file grandi), carica il
logo, scrivi la richiesta e premi *Genera con gli agenti*. Il pannello Agenti mostra stato, sintesi,
avvisi e dettagli verificabili di ciascuno. Poi modifica la timeline (trascina per riordinare, inspector
per ingresso/durata/transizione/movimento/look/volume, dividi al cursore, testi, sottotitoli, audio),
*Anteprima rapida* (540p, segmenti in cache) ed *Esporta finale*.

**Riga di comando**:

```bash
python -m focusstudio new "Il mio video"                 # stampa l'ID progetto
python -m focusstudio import <ID> clip1.mp4 foto.jpg
python -m focusstudio import <ID> --music brano.mp3
python -m focusstudio import <ID> --logo logo.png
python -m focusstudio generate <ID> 'Video dinamico di 45 secondi in 4K con titolo "Estate"...'
python -m focusstudio render <ID> [--preview]            # dopo modifiche alla timeline
python -m focusstudio doctor                             # verifica FFmpeg, NVENC, voce, Whisper, LLM
```

Output in `%LOCALAPPDATA%\SteamFocusStudio\projects\<ID>\renders\`: `.mp4`, `.srt`, `.vtt`,
`_crediti.txt`, `_report.json`, `_report.md`, `_poster.jpg`.

## Cosa interpreta la richiesta (regole)

Durata (`45 secondi`, `1 minuto e 30`, `1:30`), risoluzione (`4K`, `1080p`, `720p`), formato
(`verticale`/`9:16`, `quadrato`), codec (`HEVC`/`H.265`), fps (`60 fps`), ritmo (`dinamico`, `calmo`…),
look (`cinematografico`, `bianco e nero`, `vivace`, `caldo`), titolo (`titolo "..."`), sottotitolo,
voce (`Testo: ...`), `senza musica`, `senza sottotitoli`, ricerca online (`cerca filmati di ...`),
cartello finale (`chiusura`).

## Stato

Vedi [docs/STATUS.md](docs/STATUS.md) per ciò che funziona (con prove), ciò che è parziale e ciò che manca.
Architettura: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Licenze: [docs/LICENSING.md](docs/LICENSING.md).

## Test

```bash
pip install -e ".[dev]"
python -m pytest -q
```

I test generano media sintetici con FFmpeg ed eseguono analisi, rendering reale, QA e la pipeline
multi-agente completa, verificando i file con ffprobe.

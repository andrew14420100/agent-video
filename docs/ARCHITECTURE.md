# Architettura

```
 UI web (ui/: HTML+JS, nessun build)  ──HTTP──▶  server.py (FastAPI, 127.0.0.1:8765)
                                                    │
 CLI (cli.py) ─────────────────────────────────────▶ service.py
                                                    │
                                         orchestrator.py (RunManager)
                                                    │  esegue in sequenza, salva runs/<id>.json
     ┌────────────┬───────────┬──────────┬──────────┴─┬──────────┬───────────┬──────────┬──────────┬────────┬────────┐
  Regia      Ricerca     Analisi   Storyboard   Montaggio   Audio   Sottotitoli  Grafica  Rendering   QA    Export
 (brief.py) (providers) (analysis) (pipeline)   (pipeline) (tts)    (asr)       (ass.py) (engine)   (qa.py)
     │           │            │                                 │        │                    │
   Ollama*   NASA/Wikimedia  FFmpeg+NumPy                     Piper*  faster-whisper*       FFmpeg (x264/x265/NVENC, libass)
             /Openverse
                                * opzionali: se assenti l'agente lo dichiara (skipped/blocked) e la pipeline continua
```

## Modello dati (`project.py`)

Ogni progetto è una cartella (`%LOCALAPPDATA%\SteamFocusStudio\projects\<id>`):

- `project.json` – brief, asset (con licenza, sha256, ffprobe, analisi), timeline, storyboard, esportazione
- `media/` – file importati/scaricati · `thumbs/` · `cache/segments/` – segmenti renderizzati (cache)
- `renders/` – MP4, SRT, VTT, crediti, report JSON/MD, poster · `runs/` – log verificabile di ogni esecuzione

La **timeline** ha: traccia video magnetica (clip con ingresso, durata, transizione in entrata,
adattamento cover/contain, movimento, look, velocità, volume), testi animati, audio (musica/voce/sfx),
sottotitoli, logo. La durata è calcolata come `Σ durate − Σ transizioni` ed è esatta al frame.

## Motore di rendering (`render/engine.py`)

1. **Segmenti**: ogni clip → file intermedio uniforme (risoluzione, fps, SAR, yuv420p, audio 48 kHz).
   Chiave di cache = hash di parametri clip + file sorgente (mtime/size) + formato. Dopo una modifica
   si ricalcolano solo le clip cambiate.
2. **Composizione**: `xfade`/`acrossfade` (o `concat` per i tagli netti), dissolvenze, logo (`overlay`),
   titoli/lower third/sottotitoli via ASS (`libass`), codifica finale con l'encoder scelto.
3. **Mix audio**: audio clip + musica + voce; ducking con `sidechaincompress`; normalizzazione con
   guadagno lineare + limitatore true-peak sovracampionato, misura EBU R128 e correzione (≤ 3 passaggi).
4. **Mux**: MP4 `+faststart`, AAC, sottotitoli `mov_text`, SRT/VTT a parte.

**Encoder**: NVENC (h264/hevc) è usato solo se una **codifica di prova reale** riesce; altrimenti
libx264/libx265. La scelta e il motivo finiscono nel report.

## Agenti (`agents/pipeline.py`)

| # | Agente | Cosa fa realmente | Se manca qualcosa |
|---|---|---|---|
| 0 | Regia | Interpreta la richiesta (regole IT/EN; LLM Ollama se attivo) | regole sole, dichiarato |
| 1 | Ricerca | Conta il materiale utilizzabile; se richiesto cerca su NASA/Wikimedia, filtra licenze, scarica, registra provenienza | `blocked` se non c'è materiale visivo |
| 2 | Analisi | Tagli di scena, esposizione, contrasto, nitidezza, movimento, nero, freeze; BPM e beat della musica | file illeggibile → annotato |
| 3 | Storyboard | Inquadrature con durate per ritmo, tagli agganciati ai beat, scelta clip+finestra con motivazione | `blocked` se le clip sono troppo corte |
| 4 | Montaggio | Timeline esatta al frame, transizioni, adattamento verticale/orizzontale, Ken Burns | errore se la durata non torna |
| 5 | Audio | Voce Piper per frase (adatta la velocità alla durata), musica in loop, piano di mix | `blocked` (non critico) se manca la voce |
| 6 | Sottotitoli | Testo della narrazione allineato con Whisper, oppure trascrizione del parlato delle clip | `skipped` se Whisper manca |
| 7 | Grafica | Tipi di transizione, titolo animato, lower third, cartello finale, logo, look | — |
| 8 | Rendering | Motore descritto sopra | errore FFmpeg riportato |
| 9 | QA | ffprobe, decodifica completa, durata, A/V, loudness, true peak, nero, freeze, silenzi, voce udibile, sottotitoli, licenze, storyboard | `failed` se un controllo fallisce |
| 10 | Export | Crediti/licenze, report JSON+Markdown, poster | — |

Il flusso "Anteprima/Esporta" dopo modifiche manuali esegue solo Rendering → QA → Export.

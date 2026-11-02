# Stato del progetto (v0.1.0, 2026-11-02)

Legenda: ✅ implementato e verificato con test reali · 🟡 implementato, verificato solo in parte · ❌ mancante

## Funziona (con prove)

| Funzione | Stato | Prova |
|---|---|---|
| Import di video/immagini/audio, ffprobe, SHA-256, miniature | ✅ | test `test_media_info_and_analysis` |
| Analisi clip: tagli di scena, nero, freeze, esposizione, nitidezza, movimento | ✅ | taglio a 3,0 s e nero 0–1 s rilevati nel test |
| Beat/BPM della musica | ✅ | 119,9 BPM su traccia a 120 (test ±3) |
| Interpretazione della richiesta (regole IT/EN) | ✅ | `test_brief_rules` |
| Storyboard con tagli a tempo di musica e motivazione per ogni scelta | ✅ | pipeline completa |
| Montaggio con durata esatta al frame | ✅ | QA durata 25,000 s / 30,000 s |
| Transizioni (37 tipi xfade), dissolvenze, Ken Burns, verticale→orizzontale | ✅ | rendering di prova e controllo dei fotogrammi |
| Titoli animati, lower third, cartello finale, logo | ✅ | fotogrammi estratti e controllati a vista |
| Voce italiana locale (Piper), adattata alla durata | ✅ | 3 frasi sintetizzate, voce udibile 3/3 nel QA |
| Sottotitoli: testo allineato con Whisper, SRT/VTT/mov_text/impressi | ✅ | allineamento 3/3 frasi, validazione cue |
| Mix: ducking, normalizzazione −14 LUFS, true peak ≤ −1,5 dBTP | ✅ | QA misurato −14,0 LUFS / −1,5 dBTP |
| Export MP4 H.264 e HEVC (hvc1), 1080p e 4K | ✅ | `test_hevc_export`, prova 4K NASA |
| Controllo qualità su file reale (17 controlli) | ✅ | ha trovato 2 difetti reali durante lo sviluppo (musica troncata, loudness) |
| Blocco delle licenze non consentite | ✅ | `test_unknown_license_fails_qa`, download rifiutato |
| Ricerca NASA e Wikimedia con licenza registrata | ✅ | 4 clip NASA scaricate e montate |
| Cache dei segmenti (ri-render veloce dopo le modifiche) | ✅ | 4/4 segmenti dalla cache nel test |
| Salvataggio e ripresa dei progetti, log verificabile delle esecuzioni | ✅ | `project.json`, `runs/*.json` |
| Interfaccia: libreria, ricerca, prompt, timeline modificabile, inspector, anteprima, agenti | ✅ | test Playwright: modifica → salva → anteprima → QA PASS |

## Parziale o non verificato qui

| Funzione | Stato | Note |
|---|---|---|
| NVENC (RTX 5070 Ti) | 🟡 | Codice pronto, ma attivato solo dopo una codifica di prova riuscita. Qui non c'è una GPU: si usa libx264/libx265. Su Blackwell serve un FFmpeg recente (build Gyan 7.1+) e il driver ≥ 570. |
| Windows 11 (install.ps1, start.bat, WebView2) | 🟡 | Scritti per Windows ma **non eseguiti** su Windows in questo ambiente. |
| LLM locale (Ollama) per scrivere la narrazione e tradurre gli argomenti | 🟡 | Client e schema JSON implementati, ma non provati con un Ollama reale. Senza LLM la voce richiede `Testo: ...`. |
| Whisper su GPU | 🟡 | Prova CUDA e ricade automaticamente su CPU int8. Su Windows servono le librerie cuBLAS/cuDNN 12. |
| Openverse (immagini/audio CC) | 🟡 | Implementato, ma durante i test il servizio andava in timeout. |
| Pertinenza delle clip | 🟡 | Si basa su titoli, tag e descrizioni, **non** sul contenuto visivo; il report lo dichiara. |
| Musica automatica | 🟡 | Usa solo tracce importate o con licenza valida; nessuna generazione musicale. |

## Mancante (prossime fasi)

- ❌ Comprensione semantica delle immagini (es. CLIP locale su GPU) per scegliere le inquadrature in base al contenuto
- ❌ Timeline con trascinamento dei bordi per il trim (oggi il trim si fa dall'inspector), annulla/ripeti, più tracce video sovrapposte
- ❌ Anteprima in tempo reale senza render (oggi l'anteprima rapida è un render 540p con cache)
- ❌ Libreria di effetti sonori e musica a licenza libera integrata
- ❌ Correzione automatica guidata dal QA (oggi il QA segnala; la loudness si corregge da sola nel mix)
- ❌ Installer `.exe` firmato (oggi script PowerShell + venv)
- ❌ Moduli newsroom della specifica originale (RSS, fact-checking, packaging YouTube)

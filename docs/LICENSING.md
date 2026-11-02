# Licenze: software, modelli, media

Verifica: 2026-11-02. Questa tabella è una misura operativa, non un parere legale.

## Dipendenze software

| Componente | Uso | Licenza | Obbligatorio |
|---|---|---|---|
| Python, FastAPI, Uvicorn, Pydantic, NumPy, python-multipart | applicazione | PSF / MIT / BSD | sì |
| FFmpeg (build Gyan per Windows, "full" GPL) | decodifica, rendering, analisi, QA | GPL-3 (build con x264/x265) | sì, **processo esterno** |
| Piper TTS (`piper-tts`, OHF-Voice/piper1-gpl) | voce sintetica locale | GPL-3 | no |
| faster-whisper (+ CTranslate2) | trascrizione/allineamento | MIT | no |
| Modelli Whisper (OpenAI) | ASR | MIT | no |
| pywebview | finestra desktop | BSD-3 | no |
| Ollama + modello LLM a scelta | interpretazione richiesta | MIT (Ollama); modello: licenza propria | no |

FFmpeg e Piper sono invocati come programmi/librerie esterne installate dall'utente: non sono
ridistribuiti in questo repository.

## Voci Piper

Ogni voce ha una propria MODEL_CARD. `it_IT-paola-medium` rimanda al dataset
`paolapersico1/Voice-Dataset-Italian` ("License: See URL"): **l'utente deve verificarne le condizioni
prima di un uso commerciale**. L'app registra nel report quale voce è stata usata.

## Media cercati online

| Fonte | Licenze accettate | Note |
|---|---|---|
| NASA Image and Video Library | `public-domain-us-gov` | Media NASA generalmente non protetti da copyright negli USA; citare NASA; vietato usare loghi per implicare approvazione. Se la descrizione cita copyright/terzi l'elemento è marcato `unknown` ed escluso. |
| Wikimedia Commons | `cc0`, `public-domain`, `by`, `by-sa` (letto da `extmetadata`) | NC/ND esclusi |
| Openverse | `cc0`, `pdm`, `by`, `by-sa` | servizio a volte lento/irraggiungibile |

Regole applicate dal codice:

- download rifiutato se la licenza non è nell'elenco consentito (`research/providers.fetch`);
- il QA fallisce se nella timeline finisce un asset con licenza non consentita;
- ogni asset ha URL di origine, licenza, attribuzione e hash SHA-256 nel progetto e nel report;
- il materiale importato dall'utente è marcato "Materiale dell'utente": è responsabilità dell'utente
  averne i diritti. Nessun download da YouTube o siti che lo vietano.

**Attenzione (CC BY-SA)**: un video che include materiale BY-SA va distribuito con licenza compatibile.
I crediti generati vanno inseriti nella descrizione del video quando richiesto dalla licenza.

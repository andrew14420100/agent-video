# Audit di Steam Focus Studio (andrew14420100/Steam-Focus-Studio)

Data: 2026-11-02 · commit esaminato: `8dce262` ("Specify from-zero newsroom and MCP editor workflow")

## Contenuto del repository

| File | Tipo | Note |
|---|---|---|
| `README.md` | documentazione | Dichiara esplicitamente: *"Stato iniziale: specifica e scaffolding. Non è ancora una pipeline funzionante."* |
| `CLAUDE.md` | specifica | 13 agenti per un notiziario gaming, vincoli su licenze, 4K, no servizi a pagamento |
| `docs/ARCHITECTURE.md` | specifica | Orchestratore deterministico, contratti task, stati `blocked` |
| `docs/ROADMAP.md` | specifica | Fasi 0–8 |
| `docs/ACCEPTANCE_TESTS.md` | specifica | Test attesi (durata reale, SRT/VTT validi, licenze sconosciute bloccate…) |
| `docs/LICENSING.md` | checklist | Provenienza e licenza di ogni asset |
| `docs/CLAUDE_CODE_PROMPT.md` | prompt | Istruzioni operative |
| `.env.example`, `.gitignore` | configurazione | Nessun segreto presente |

## Cosa funzionava davvero

**Nessun codice eseguibile**: il repository contiene solo specifiche. Non c'erano
funzioni simulate da rimuovere, ma nemmeno componenti da riutilizzare come codice.

## Cosa è stato riutilizzato

Le specifiche erano valide e sono state adottate nel nuovo motore:

- orchestratore deterministico con stati espliciti (`completed`, `skipped`, `blocked`, `failed`) e registro per task;
- nessun asset con licenza sconosciuta nel video finale (verificato dal QA, test `test_unknown_license_fails_qa`);
- provenienza e crediti di ogni asset (`*_crediti.txt`, `*_report.json`);
- distinzione tra risoluzione sorgente e di uscita (avviso quando si ingrandisce a 4K);
- verifica del file prodotto con ffprobe e misure reali (non dichiarazioni);
- elaborazione locale, nessun servizio a pagamento, upload/pubblicazione assenti.

## Cosa è cambiato rispetto alla specifica

La nuova richiesta è uno **studio di montaggio generale** (non solo notiziario gaming): l'utente
descrive un video e fornisce o fa cercare il materiale. Le parti "newsroom" (RSS, fact-checking,
packaging YouTube) non sono state implementate in questa prima versione; il motore di montaggio,
che è il prerequisito di tutto, sì.

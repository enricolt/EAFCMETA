# HANDOFF — EA FC Meta

Documento di passaggio per chi continua il lavoro **in locale** (stato al 3 ottobre 2026, config `patch.json` 1.6).
Chi legge: una persona o una sessione di Claude Code che non ha visto la conversazione. Qui c'è tutto quello che serve.

- **Repository:** `enricolt/EAFCMETA` — branch di lavoro `claude/nifty-babbage-h8l35m` (non esiste `main`, nessuna PR).
- **Lingua:** l'utente scrive e legge in **italiano**. Testi dell'app, messaggi, commit e documenti in italiano.
- **Uso:** app personale per l'utente e qualche amico. L'utente ha **pochi token**: passi piccoli e verificabili.
- **Pronomi:** non assumere il genere dell'utente; formule neutre.

---

## 1. Cosa vuole l'utente

1. Un'app **autonoma** (finestra desktop, non nel browser) che valuta le carte di EA FC e dice se una carta **è meta** e
   **su quali basi va presa o no**, con una parte descrittiva.
2. **Il giudizio lo genera l'app**, con regole nell'engine i cui parametri derivano da ciò che dicono i pro.
3. Quando i pro **non concordano**, spiegare perché («secondo X sì, secondo Y no») con i motivi.
4. **Nessun inserimento a mano**: raccolta dei pareri e delle carte **totalmente automatica** (scelte dell'utente: sottotitoli YouTube non
   ufficiali ammessi; **niente X, niente modello linguistico a pagamento**; salvataggio automatico sopra una soglia di sicurezza;
   TikTok e Instagram esclusi perché non c'è una via automatica lecita).
5. Dati da FUT.GG e FUTBIN (prezzi indicativi). FUTBIN **blocca** l'utente: lì solo pagine salvate dal browser.
6. Grafica moderna e curata (fatta).

---

## 2. Stato: cosa funziona e cosa no

### Funziona ed è verificato (424 test con `python -m pytest -q`; `tests/ui_smoke.py` 76 controlli con Playwright, senza errori JS)
- **Punteggio:** stats per ruolo + bonus (PlayStyle per tier e ruolo, body type, 5★) → regole dichiarative (delta con tetto) → soft cap →
  fusione con i pareri (quota crescente col numero di creator, vedi §5). Portieri inclusi.
- **Verdetto sul prezzo:** curva robusta (Theil-Sen) per posizione, soglia adattiva, ignorato fuori dal range di mercato; veloce (500 carte in 0,17 s).
- **Analisi scritta** per carta, **pareri dei creator** con spiegazione delle discordanze, peso di ogni parere visibile.
- **Motore di regole** (`rules.json`, `rules.py`): 13 regole manuali, apprendimento dai criteri citati dai pro (proposte da approvare), API e UI.
- **Calibrazione** dai pareri (soglie, pesi per ruolo, regole) con limiti anti-deriva e anti-overfitting; applica/ripristina.
- **Import:** CSV/TSV/JSON, pagine salvate di FUT.GG/FUTBIN (carte e liste), aggiornamento prezzi, **pareri da foglio di calcolo**.
- **Letture dei siti** verificate su pagine reali salvate (FUT.GG e FUTBIN: movimento, portiere, lista): stats, prezzo, PlayStyle(+), altezza, peso,
  AcceleRATE, ruoli con rating, segnali (GG Rating, tier community, FUTBIN Rating).
- **Ricerca pareri** (`eafcmeta/research/`): testo incollato → proposte (carta, sì/no, voto, motivo, criteri) con revisione; YouTube (metadati), X (spento).
- **Estrazione offline** riscritta (segmentazione senza punteggiatura, attribuzione alla carta giusta, negazioni, confidenza conservativa).
  Valutata su un corpus **sintetico** di 169 trascrizioni: precisione 1.000 a confidenza ≥ 0,8 (solo 14 casi) e 0,99 a ≥ 0,6; richiamo basso (8% a ≥ 0,8).
- **Raccolta automatica** (`eafcmeta/auto/`): scheduler, scoperta canali, video con trascrizioni (yt-dlp), salvataggio sopra soglia, undo, API e UI.
- **UI** rifatta: sidebar, carte FUT per rarità, confronto, drawer di dettaglio, import a 4 schede, Calibra, Regole, Ricerca pareri, Automatico, temi.
- **Launcher** `start.py`: aggiornamento da GitHub, finestra a 3 livelli (pywebview → Edge/Chrome in app mode → browser), `--diagnosi`.

### NON verificato — controllare per primo (nessuna rete dal cloud, nessun Windows)
- **yt-dlp contro YouTube vero:** la forma del suo JSON (`channel_follower_count`, `automatic_captions`, capitoli) è scritta a memoria e testata solo con finti.
- **FUT.GG vero:** `fetch.py` e il crawl dell'automazione non hanno mai parlato col sito; l'URL elenco di default e il parametro `page` sono ipotesi.
- **Qualità dell'estrazione su trascrizioni vere:** il corpus l'ha scritto lo stesso agente che ha scritto l'estrattore. Prima di fidarsi del
  salvataggio automatico, **misurare su una decina di trascrizioni reali etichettate a mano** e regolare `min_confidence` (oggi 0,6).
- **Finestra nativa sul PC dell'utente:** nessun livello della catena è mai stato visto su Windows. Usare `python start.py --diagnosi`.
- **FUTBIN:** l'utente è bloccato; le pagine FUTBIN arrivano solo se le salva dal browser. La pagina portiere FUTBIN non ha il blocco del rating.
- **Lista dei pro** (`config/pros.json`, 42 voci da tornei 2026 e ricerche): handle e follower quasi tutti da verificare; per nessun pro dei tornei risulta
  che dia opinioni sulle carte (i creator come Hollywood285, ilSantu, Flokox sono più adatti).
- **Soglie meta e pesi** sono valori di partenza: l'app li calibra solo con pareri veri (servono ≥ 12 carte con parere di un creator).
- `.exe` Windows (PyInstaller + workflow GitHub): scritto, non provato; yt-dlp non è incluso.

### Non fatto
- Nessuna PR, nessun CI sui test. Nessuna lettura di TikTok/Instagram/X (scelta dell'utente / vincoli dei servizi).
- Voci mancanti nel backend segnalate dalla UI: titolo leggibile delle regole, etichette dei criteri esposte dall'API, peso massimo dei pareri
  e soglie di `learn` nell'API, avanzamento reale di "Esegui ora" (oggi solo in corso sì/no), `automatic` e `confidence` dentro `analysis.opinions.groups`.

---

## 3. Come si avvia e si prova

```
git clone -b claude/nifty-babbage-h8l35m https://github.com/enricolt/EAFCMETA
cd EAFCMETA
pip install -r requirements-dev.txt     # include fastapi, uvicorn, bs4, lxml, pytest, httpx
python start.py                         # controlla gli aggiornamenti, apre la finestra (o il browser)
python start.py --window app|pywebview|browser   # forza un livello (--browser = browser)
python start.py --diagnosi              # rapporto da copiare/incollare (Python, git, pywebview, browser, porta, commit)
python build_windows.py                 # .exe con PyInstaller (Windows, NON provato)
python start.py --lan                   # accesso in rete locale con chiave in .token
python -m pytest -q                     # tutta la suite (424 test)
python -m eafcmeta.auto run|status|undo # raccolta automatica a mano / stato / annulla i pareri automatici (NON provata dal vivo)
python -m eafcmeta.importer data/esempio.csv        # 30 carte FITTIZIE di prova
python -m eafcmeta.collect cartella_con_pagine/     # importa pagine HTML salvate
python -m eafcmeta.fetch "https://www.fut.gg/players/?nation_id=[54]" --max 30   # NON TESTATO sul sito vero
```
Variabili d'ambiente: `EAFCMETA_DB` (file SQLite, default `eafcmeta.db` nella cartella), `EAFCMETA_TOKEN` (chiave API; la imposta `--lan`),
`EAFCMETA_LOCAL_CONFIG` (percorso di `local.json` e dei file personali accanto, usato nei test), `EAFCMETA_AUTO=0` (spegne lo scheduler, impostato nei test).

Per guardare la UI da script: Playwright con Chromium già presente (`/opt/pw-browsers` nel cloud; in locale `pip install playwright`
e `playwright install chromium`). Le prove visive fatte finora usavano un server temporaneo con `EAFCMETA_DB=/tmp/x.db`.

---

## 4. Architettura e mappa dei file

Monolite Python: **FastAPI + SQLite**; frontend statico a moduli ES (nessun build step), servito da `/static`.

| Parte | File | Ruolo |
|---|---|---|
| Avvio | `start.py`, `EAFCMETA.pyw`, `eafcmeta/desktop.py`, `build_windows.py` | Launcher, finestra a 3 livelli, build `.exe`. |
| API | `api.py`, `api_rules.py`, `api_research.py`, `api_auto.py` | Rotte REST sotto `/api/v1`, auth opzionale `X-Token`. |
| Motore | `scoring.py`, `rules.py`, `opinion_model.py`, `analysis.py`, `calibration.py` | Punteggio, regole, peso dei pareri, testo, calibrazione. |
| Dati siti | `sources.py`, `collect.py`, `fetch.py`, `importer.py`, `opinions_import.py` | Lettura pagine FUT.GG/FUTBIN, import, crawl gentile. |
| Ricerca | `research/` (pipeline, sources, resolver, extract, criteria, store, text, llm) | Testo → proposte di parere; criteri. |
| Automatico | `auto/` (runner, scheduler, ytdlp, transcript, discovery, videos, opinions, futgg, schema, config) | Ciclo giornaliero di raccolta. |
| Dati | `db.py`, `models.py` | SQLite (cards, price_history, opinions, proposals, criteria, channels, processed_items, auto_runs), validazioni Pydantic. |
| Config | `config/patch.json`, `rules.json`, `criteria.json`, `research.json`, `pros.json`, `auto.json` | Tutti i parametri. Personali (ignorati da git): `local.json`, `rules.local.json`, `auto.local.json`. |
| UI | `static/index.html`, `css/`, `js/` (main, router, sections, state, api, ui, card, charts, detail, form, labels, `views/*`) | Interfaccia. |
| Test | `tests/` | 424 test; `ui_smoke.py` e `research_eval.py` non girano in pytest di default. |

### Dati
- `cards`: chiave naturale nome + versione + posizione. Una carta letta da due siti si **fonde** (`db.find_card`: nome contenuto nell'altro e stats ≥ 90% entro ±2,
  oppure stesso club/nazione). `data` JSON: stats, PlayStyle, body type, stelle, `signals`, altezza, peso, AcceleRATE, club, lega, nazione, ruoli.
- `opinions(card_id, creator, stance, score, reason, url, ts, auto, confidence)`: un parere per creator e carta. `auto=1` = raccolto dall'automazione (rimovibile in blocco;
  un parere manuale non viene mai sovrascritto).

## 5. Come funziona il motore oggi

1. **Score base** = media pesata delle stats del ruolo + bonus (tetto 12) + `delta` delle regole **attive** (tetto per regola e totale) → *soft cap* sopra 90.
2. **Parere dei creator** (`opinion_model.py`): valore = voto, o sì 85 / dipende 70 / no 50; peso = peso del creator × freschezza (metà dopo 60 giorni, minimo 30%)
   × affidabilità (community FUT.GG 0,3 e **zero sotto 30 voti**). Parere = media pesata. **Quota** sullo score finale = 0,30 · n/(n+2), con n = somma dei pesi
   (un creator ≈ 10%, tre ≈ 18%, otto ≈ 24%). Senza pareri vale solo il base. Il voto deve essere coerente con la scelta (sì ≥ 70, no ≤ 60).
3. **Verdetto sul prezzo:** score finale contro la curva delle carte della stessa posizione; neutro con pochi dati o prezzo fuori range.
4. **Etichetta meta** (Meta di vertice / Meta / Giocabile / Sotto il meta): soglie su `meta` (90/84/76) applicate allo score **base**, scelta fatta perché la
   calibrazione confronta il base con i pro (altrimenti sarebbe circolare). Il verdetto di convenienza usa invece lo score finale. **Da riconsiderare** se l'utente
   preferisce che l'etichetta tenga conto dei pareri.
5. **Analisi** (`analysis.describe` + `rules.py`): punti di forza e deboli, scatto, PlayStyle utili/fuori ruolo, body type, stelle, prezzo, pareri con discordanze,
   discordanze sui criteri, "cosa ha contato", consiglio finale (matrice in `rules.json`).
6. **Apprendimento** (`rules.learn`): dai criteri citati nei pareri (tabella `criteria`), propone regole se ≥ 5 menzioni da ≥ 2 creator e consenso ≥ 0,6; mai attive senza approvazione.
7. **Calibrazione:** con ≥ 12 carte con parere di un creator (escluso il voto automatico della community) propone soglie (accuratezza bilanciata, ≥ 4 carte per classe, ±3 punti),
   pesi (±30% da `patch.json`) e delta delle regole; applica e ripristina; riparte sempre dai valori originali (nessuna deriva).

## 6. Motore di regole — stato (era il "Lavoro A")

**Fatto:** regole dichiarative, valutatore unico, apprendimento, calibrazione, API e UI (vedi §2, §5). **Resta:** titolo leggibile per le regole nell'API; regole su condizioni
nuove (ruolo alternativo, AcceleRATE, altezza/peso ora disponibili sulle carte); dopo i primi pareri veri, rivedere i template di `rules.json` per i criteri `height`, `body_type`,
`animations`, `price`; normalizzare i nomi di club e lega tra i due siti (scrivono "Liga F" e "Liga F Moeve").

## 7. Ricerca e raccolta — stato (era il "Lavoro B")

**Fatto:** pipeline a stadi con revisione (`research/`) e raccolta automatica (§7b). **Vincoli da non aggirare:** X ha API a pagamento (spenta per scelta); TikTok e Instagram non hanno API
utilizzabili da un privato; le trascrizioni YouTube arrivano solo da yt-dlp (non ufficiale, zona grigia dei termini, basso ritmo). **Resta:** provarla in locale (vedi §11),
misurare la qualità su trascrizioni vere, eventuale modello linguistico se l'utente cambia idea (`LLMExtractor` esiste e resta spento), ampliare `pros.json` con canali verificati.

## 7b. Raccolta automatica (fatta — `eafcmeta/auto/`, `eafcmeta/api_auto.py`)

Richiesta dell'utente: processo **totalmente automatico**, nessun inserimento a mano di carte né pareri. Decisioni vincolanti dell'utente:
trascrizioni YouTube con metodo **non ufficiale** (yt-dlp; zona grigia dei termini, accettata, uso personale, basso ritmo); **niente X/Twitter**
(codice esistente lasciato spento); **niente modello a pagamento** (solo parole chiave, `research/extract.py` non toccato); salvataggio
automatico **sopra soglia**, marcato "automatico", rimovibile in blocco; TikTok/Instagram esclusi.

| File | Ruolo |
|---|---|
| `auto/ytdlp.py` | Provider yt-dlp (processo `python -m yt_dlp`, **runner iniettabile**): ricerca, elenco video, sottotitoli it/en senza scaricare il video; pausa tra richieste, tetto richieste, blocco (429/anti-bot) => `YtBlocked`. |
| `auto/transcript.py` | json3/vtt -> righe di ~14 parole (le trascrizioni automatiche non hanno punteggiatura) -> sezioni per capitolo. |
| `auto/discovery.py` | Scoperta canali (similarità nome, iscritti, video FC); tabelle `channels`, `channel_misses`. |
| `auto/videos.py` | Elabora i video nuovi (`processed_items`), resolver + `OfflineExtractor`, decisione di sicurezza, tetti. |
| `auto/opinions.py` | `decide`, `save_auto` (protegge i manuali), scarti in `proposals` (`rejected`, nota `scartata_auto: ...`), `undo`. |
| `auto/futgg.py` | Carte/prezzi: riusa `fetch.crawl` una pagina elenco alla volta, robots.txt letto una volta. |
| `auto/runner.py` | Un'esecuzione completa in `auto_runs` (lock nel DB, passi indipendenti). |
| `auto/scheduler.py` | `Scheduler.tick()` (orologio/lavoro iniettabili) + `AutoService` (thread demone, avvio manuale). |
| `auto/config.py`, `config/auto.json`, `config/pros.json` | Parametri (default nel repo; personali in `auto.local.json`, ignorato dal controllo versioni), elenco pro. |
| `auto/schema.py` | Migrazione idempotente: colonne `opinions.auto/confidence/src_date` + nuove tabelle. Richiamata da `db.connect`. |
| `api_auto.py` | `/api/v1/auto/{status,run,config,opinions}`. CLI: `python -m eafcmeta.auto run|status|undo`. |

Punti da sapere:
- **Hunk nei file condivisi** (piccoli e additivi): `db.py` (una riga in `connect`, `upsert_opinion` azzera `auto/confidence/src_date`
  così un parere corretto a mano diventa manuale, `opinions_by_card` aggiunge `automatic` e `confidence`), `api.py` (avvio/arresto
  del servizio nel `lifespan` + una riga per il router), `tests/conftest.py` (variabili d'ambiente che spengono la raccolta nei test),
  `requirements.txt` (yt-dlp), `.gitignore` (`auto.local.json`).
- Lo scheduler parte solo se `enabled` **e** `EAFCMETA_AUTO != "0"`; `PUT /auto/config` con `enabled: true` lo avvia senza riavviare.
- `undo` non rimette i video già letti in coda (restano in `processed_items`): l'annullamento è duraturo.
- Un video senza trascrizione si riprova (per `retry_no_transcript_days`) perché i sottotitoli automatici compaiono dopo ore.
- **NON verificato**: yt-dlp contro YouTube reale (il cloud blocca la rete: provati solo la sintassi delle opzioni e il comportamento
  offline); la forma esatta del JSON di yt-dlp (`channel_follower_count`, `uploader_id`, `automatic_captions` con chiavi `xx-orig`,
  `chapters`) è scritta dalla documentazione/memoria e testata con risposte finte; FUT.GG reale (URL elenco di default
  `https://www.fut.gg/players/` e parametro `page` non verificati); calibrazione delle soglie (0,6 / 0,75) su dati veri; thread demone
  dentro la finestra desktop su Windows. Primo passo in locale: `python -m eafcmeta.auto run`, guardare `status` e `GET /auto/opinions`.
- Rischio noto: `crawl` (fetch.py) non intercetta errori HTTP diversi da 401/403/429/503: il passo li registra e passa alla pagina dopo.

---

## 8. Cose che vanno sapute sulle pagine dei siti (struttura verificata a ottobre 2026)

**FUT.GG** (pagina carta): JSON-LD `BreadcrumbList` (nome, «Versione NN OVR») e `WebPage` (descrizione con `NN OVR POS (Club)`, `url`). Il resto si legge a **token di testo**
ancorati alle etichette (`Attributes`/`Chemistry Style`, `Skill Moves`, `Weak Foot`, `Body Type`, `Current price`, `Tier vote`, `View All`). **PlayStyle:** `div[title]` con `svg height=42`;
il **PlayStyle+ ha una forma dell'icona diversa dal rombo** (`M128,12.808L243.192,128…` = normale). Stat dei portieri: etichette semplici (`Diving`, `Reflexes`…); fine blocco `GK Basic`.
**Lista:** `a[href*=/players/]` con `.fc-card`, `img[alt="Nome - NN - Versione"]`, token `[POS, GGrating, prezzo|EXTINCT]`; prezzi abbreviati (`6.8M`, `783K`).

**FUTBIN** (pagina carta): token `… NN POS ++ … Nome … Add to Compare`; prezzo console in `.price-box.platform-ps-only .lowest-price-1`; **PlayStyle** `a.playStyle-table-icon.active`,
**PlayStyle+ = classe `psplus`**; stats tra `Player Stats` e `Total Chem. style added:`; rarità dal JSON-LD `Product` («Nome Rarità EA FC 27 Player Card», es. `All Icons`).
**Lista:** `tr.player-row` con `td.table-rating/pos/price`, nome dall'`img[alt]` non «Nation/League/Club», revisione in `.table-player-revision`.

**Normalizzazioni** (`sources.normalize_version`): «Base Icon»/«Icons»/«All Icons» → `Icon`; «Rare/Common/Normal» → `Gold`; così la stessa carta da due siti coincide.
**Limite noto:** due carte dello stesso giocatore con stessa etichetta di versione e stesso valore (es. due «TOTW 89») vengono scambiate per una sola; l'ultimo prezzo vince.

I file HTML usati per scrivere i parser **non sono nel repository** (pesano 0,6–1,4 MB ciascuno): i test usano pagine ricostruite in `tests/test_collect.py`.
Se cambiano i siti, chiedere all'utente di risalvare una pagina e adeguare `sources.py` guardando i token reali (come fatto finora).

---

## 9. Regole di lavoro

- Mantenere lo stile esistente: moduli piccoli, funzioni documentate in italiano, test accanto al codice, parametri in `patch.json` (non costanti nel codice).
- **Verificare davvero** prima di dire «funziona»: eseguire `pytest`, e per la UI provare nel browser (Playwright) con dati veri o realistici.
- Non pubblicare segreti. Non committare `.token`, `local.json`, `*.db`, `app.log` (sono già in `.gitignore`).
- Commit chiari in italiano; **non aprire PR** se l'utente non lo chiede (finora si lavora direttamente sul branch).
- Dire con onestà ciò che **non** si è potuto provare (siti reali, Windows, finestra nativa).

## 10. Decisioni aperte (da chiedere all'utente, non assumere)

1. L'etichetta **Meta** deve usare lo score base (oggi) o quello finale con i pareri?
2. **Peso di ogni creator:** oggi uguale per tutti (`opinion_weighting.creator_weights` in `patch.json`); dare più fiducia a chi l'utente ritiene più affidabile?
3. **Modello linguistico** (chiave API a pagamento) per estrarre i pareri meglio, o restare sulle parole chiave? **X** a pagamento con tetto di spesa?
4. **PR verso `main`** (non esiste) o continuare sul branch di lavoro.
5. **Pacchetto `.exe`** per Windows quando la finestra funziona.

## 11. Primi passi consigliati a chi parte ora

1. Clonare, `pip install -r requirements-dev.txt`, `python -m pytest -q` (424 test), `python start.py --diagnosi`, poi `python start.py` e guardare cosa succede (finestra o browser).
2. Caricare carte vere: salvare dal browser 5–10 pagine FUT.GG/FUTBIN e usare **Importa → Pagine salvate**; oppure provare `python -m eafcmeta.fetch` (mai provato dal vivo).
3. Provare l'automazione **dal vivo, a mano e con pochi dati:** `python -m eafcmeta.auto run` con `max_channels` e `videos_per_channel` bassi in `auto.local.json`; controllare le righe di
   `auto_runs`, i canali scoperti (accettati solo se nome e iscritti combaciano) e i pareri salvati. Correggere la forma dei dati di yt-dlp se non coincide.
4. Misurare l'estrazione su ~10 trascrizioni reali etichettate a mano (usare `tests/research_eval.py` come modello) e decidere la soglia `min_confidence`.
5. Quando ci sono ≥ 12 carte con pareri: **Calibra**, rivedere le proposte delle regole apprese e approvarle una per una.

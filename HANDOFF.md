# HANDOFF — EA FC Meta

Documento di passaggio per chi continua il lavoro **in locale** (stato al 2 ottobre 2026, patch config 1.5).
Chi legge: una persona o una sessione di Claude Code che non ha visto la conversazione. Qui c'è tutto quello che serve.

- **Repository:** `enricolt/EAFCMETA` — branch di lavoro `claude/nifty-babbage-h8l35m` (non esiste ancora `main`, nessuna PR).
- **Lingua:** l'utente scrive e legge in **italiano**. Testi dell'app, messaggi, commit e documenti in italiano.
- **Uso:** app personale per l'utente e qualche amico. Non è un prodotto pubblico. L'utente ha **pochi token**: lavorare per
  passi piccoli e verificabili, non rifare cose già fatte.
- **Pronomi:** non assumere il genere dell'utente; usare formule neutre.

---

## 1. Cosa vuole l'utente (requisiti, nelle sue parole riassunte)

1. Un'app **autonoma** (finestra desktop, non nel browser) che valuta le carte di EA FC e dice se una carta
   **è meta** e **su quali basi va presa o no**, con una parte descrittiva.
2. **L'analisi la fa l'app**, con le **regole scritte nell'engine**, secondo **parametri che decidiamo capendo il meta
   dai pro**. L'app deve **generare i giudizi autonomamente**, non limitarsi a mostrare testo inserito a mano.
3. Quando i pro **non sono d'accordo**, spiegare perché: «secondo Team Gullit sì, secondo Exeed e Nassada no», con i motivi.
4. Dati da **FUTBIN e FUT.GG** per iniziare (prezzi indicativi, non in tempo reale).
5. Poi i **pareri dei pro più famosi da X, TikTok e Instagram**, con una **ricerca automatica**.
6. Grafica moderna e curata (fatta: carte in stile FUT, tema chiaro/scuro).

**Priorità attuale dell'utente:** i punti 2 e 5 — *motore di regole che genera i giudizi da solo* e *ricerca automatica*.
Sono descritti nelle sezioni 6 e 7. Tutto il resto è già funzionante.

---

## 2. Stato: cosa funziona e cosa no

### Funziona ed è verificato (58 test, `pytest`; UI controllata con Playwright su telefono/desktop, chiaro/scuro)
- Punteggio per carta: stats per ruolo + bonus (PlayStyle per tier e ruolo, body type, 5★) → soft cap → fusione 70/30 col "pro".
- Verdetto sul prezzo: curva robusta (Theil-Sen) score–ln(prezzo) per posizione, soglia adattiva. Servono ≥ 8 carte con ≥ 4 prezzi diversi.
- Analisi scritta per carta (`analysis.py`): etichetta meta, perché sì / perché no, consiglio.
- Pareri dei creator (sì/dipende/no, voto, motivo, link) e **spiegazione delle discordanze**.
- Import: CSV/TSV/JSON incollati, **pagine salvate** di FUT.GG e FUTBIN (carte singole e **liste**), aggiornamento prezzi.
- Lettura verificata su **pagine reali salvate dall'utente** (non sul sito live): FUT.GG (carta di movimento, portiere, lista),
  FUTBIN (carta di movimento, portiere, lista).
- Segnali dei siti (GG Rating, tier community, FUTBIN Rating) e «parere» automatico "FUT.GG (community)".
- Calibrazione 🎯 dai pareri dei creator (soglie e pesi), con applica/ripristina.
- Launcher `start.py` con aggiornamento da GitHub a ogni avvio; finestra nativa con pywebview; `EAFCMETA.pyw` per Windows.

### NON verificato — controllare per primo
- **Finestra nativa (pywebview) sul PC dell'utente:** l'utente ha detto che si apriva nel browser. `start.py` ora stampa il
  motivo se pywebview non parte (righe `[app]`), ma **non è mai stato visto l'esito reale su Windows**.
- **`eafcmeta/fetch.py`** (scarico automatico da FUT.GG): scritto e testato **solo con un sito finto**. Mai provato contro il sito vero
  (dal cloud la rete è bloccata). Non sappiamo se FUT.GG accetta richieste da Python (Cloudflare).
- **FUTBIN blocca l'utente** («continua a bloccarsi»). Le pagine FUTBIN arrivano solo se l'utente le salva dal browser.
- **Pagina portiere FUTBIN**: letta, ma il blocco "FUTBIN Rating" non c'è (nessun segnale FUTBIN per i portieri).
- **Soglie "meta" (90/84/76) e pesi per ruolo**: sono valori **miei di partenza, non calibrati**. Esempio: una carta da 12.000 crediti
  (Chloe Kelly Gold 86) esce "Meta di vertice" senza pareri. Servono i pareri dei pro per tararli (vedi calibrazione).
- Nessun dato reale di pareri dei pro è ancora inserito: la calibrazione è pronta ma ha **0 carte** su 12 necessarie.

### Non fatto
- **Ricerca automatica dei pareri dei pro** (X, TikTok, Instagram, YouTube): non esiste niente. Vedi sezione 7.
- **Motore di regole dichiarativo**: oggi molte regole dell'analisi sono costanti scritte nel codice. Vedi sezione 6.
- Nessuna PR, nessun CI. Nessun pacchetto `.exe` (si avvia con `python start.py`).

---

## 3. Come si avvia e si prova

```
git clone -b claude/nifty-babbage-h8l35m https://github.com/enricolt/EAFCMETA
cd EAFCMETA
pip install -r requirements-dev.txt     # include fastapi, uvicorn, bs4, lxml, pytest, httpx
python start.py                         # controlla gli aggiornamenti, apre la finestra (o il browser)
python start.py --browser               # forza il browser
python start.py --lan                   # accesso in rete locale con chiave in .token
pytest -q                               # 58 test
python -m eafcmeta.importer data/esempio.csv        # 30 carte FITTIZIE di prova
python -m eafcmeta.collect cartella_con_pagine/     # importa pagine HTML salvate
python -m eafcmeta.fetch "https://www.fut.gg/players/?nation_id=[54]" --max 30   # NON TESTATO sul sito vero
```
Variabili d'ambiente: `EAFCMETA_DB` (file SQLite, default `eafcmeta.db` nella cartella), `EAFCMETA_TOKEN` (chiave API; la imposta `--lan`),
`EAFCMETA_LOCAL_CONFIG` (percorso di `local.json`, usato nei test).

Per guardare la UI da script: Playwright con Chromium già presente (`/opt/pw-browsers` nel cloud; in locale `pip install playwright`
e `playwright install chromium`). Le prove visive fatte finora usavano un server temporaneo con `EAFCMETA_DB=/tmp/x.db`.

---

## 4. Architettura e mappa dei file

Monolite Python: **FastAPI + SQLite**, un solo file HTML statico come frontend, nessun build step.

| File | Ruolo |
|---|---|
| `start.py` | Launcher: `git fetch/pull --ff-only` (solo se non ci sono modifiche locali), installa le dipendenze, apre finestra o browser. |
| `EAFCMETA.pyw` | Doppio clic su Windows senza console; log in `app.log`. |
| `eafcmeta/api.py` | Rotte REST sotto `/api/v1`, auth opzionale via `X-Token`, calcolo e assemblaggio della valutazione (`_scored`, `_eval`). |
| `eafcmeta/scoring.py` | Punteggio, curva e verdetto, **caricamento e validazione della config** (`patch.json` + `local.json`). |
| `eafcmeta/analysis.py` | **Analisi scritta** (`describe`), riepilogo pareri (`summarize_opinions`), segnali dei siti, etichetta meta. |
| `eafcmeta/calibration.py` | Calibrazione da pareri: accordo, soglie e pesi suggeriti, apply/reset. |
| `eafcmeta/sources.py` | **Lettura pagine FUT.GG/FUTBIN** (carta e lista), segnali dei siti, normalizzazione versioni. |
| `eafcmeta/collect.py` | Importa lotti di pagine; applica le liste (prezzi) *dopo* le carte; crea il parere "FUT.GG (community)". |
| `eafcmeta/fetch.py` | Scarico automatico gentile da FUT.GG (non testato dal vivo). |
| `eafcmeta/importer.py` | Import CSV/TSV/JSON e aggiornamento prezzi. |
| `eafcmeta/db.py` | SQLite: tabelle `cards`, `price_history`, `opinions`; upsert con riconoscimento della stessa carta. |
| `eafcmeta/models.py` | Modelli Pydantic con tutte le validazioni (`CardIn`, `OpinionIn`, …). |
| `eafcmeta/desktop.py` | Finestra nativa pywebview con ripiego sul browser. |
| `eafcmeta/config/patch.json` | **Tutti i parametri del motore** (pesi, bonus, soglie, PlayStyle, meta, calibrazione). Validato all'avvio. |
| `eafcmeta/config/local.json` | Calibrazione personale (creata dall'app, **ignorata da git**), sopra `patch.json`. |
| `eafcmeta/static/index.html` | Intera UI (CSS+JS inline): griglia di carte FUT, dettaglio, form, import, calibrazione. |
| `tests/` | `test_scoring`, `test_api`, `test_importer`, `test_collect`, `test_analysis`, `test_calibration`, `test_start`. |

### Dati (SQLite)
- `cards(id, name, version, position, price, is_sbc, data JSON, pro_score, pro_notes)`; `data` = stats, playstyles, body_type, weak_foot, skill_moves, **signals**.
  Chiave naturale: nome + versione + posizione (senza distinzione di maiuscole). Una carta letta da due siti si **fonde** (vedi `db.find_card`: nome
  contenuto nell'altro, stessa versione e posizione, candidato unico).
- `price_history(card_id, price, ts)`. `opinions(card_id, creator NOCASE, stance yes|maybe|no, score, reason, url, ts)`, unica per (carta, creator).
- Il punteggio "pro" di una carta è la **media dei suoi pareri** (voto, oppure sì 85 / dipende 70 / no 50); senza pareri vale il vecchio `pro_score` manuale.

---

## 5. Come funziona il motore oggi

1. **Score base** = media pesata delle stats del ruolo (`role_weights`) + bonus (PlayStyle+ per tier S/A/B e ruolo, body type, 5★ piede debole / skill; tetto `max_bonus` 12),
   poi *soft cap* sopra 90 (tanh verso 100).
2. **Score finale** = 70% base + 30% pro (se non ci sono pareri vale solo il base; l'API segnala `pro_missing`).
3. **Verdetto sul prezzo**: confronto con la curva delle carte della stessa posizione (Theil-Sen), soglia = `max(min_gap, k·σ residui)`.
4. **Etichetta meta** (`analysis.meta_level`): soglie su `final` — `top` 90, `meta` 84, `playable` 76.
5. **Testo** (`analysis.describe`): punti di forza/debolezza per ruolo, scatto/velocità, PlayStyle utili o fuori ruolo, body type, skill/piede debole, prezzo, pareri.
6. **Calibrazione** (`calibration.py`): con ≥ 12 carte con parere di un creator (esclusa la community automatica), confronta il nostro `base` col voto medio dei pro
   (approvata se ≥ 80), propone soglia "meta" che massimizza l'accordo e pesi per ruolo = `peso·(1 + shrink·corr)` con `shrink = 0.5·n/(n+20)`, minimo 0.2.

### Dove le regole sono ancora scritte nel codice (da portare nell'engine, vedi §6)
In `analysis.py`: forza se stat ≥ 88; debolezza se stat < 70 con peso ≥ 2; scatto+velocità media ≥ 88 «da vertice», < 72 «basso» (ST/W/FB/WB/CF), < 66 per i CB;
bonus testuali per body type Lean/Unique/Custom; skill ≥ 5 / ≤ 2 e piede debole ≥ 5 / ≤ 2 per i ruoli offensivi; `_advice` (matrice livello × verdetto);
`MOVERS`, `ROLE_IT`, `NAMES`. Nessuna di queste è in `patch.json`, quindi **la calibrazione non le può toccare**.

---

## 6. LAVORO A — Motore di regole che genera i giudizi da solo (priorità)

**Obiettivo:** il giudizio («è meta? perché? prendila o no») deve uscire da **regole dichiarative nell'engine**, i cui parametri derivano da ciò che dicono i pro.
Niente testo scritto a mano per carta.

### Design proposto
1. **Regole dichiarative** in un file (es. `eafcmeta/config/rules.json`, validato come `patch.json`). Ogni regola:
   ```json
   {"id": "st-pace-elite", "roles": ["ST","CF"], "when": {"avg": ["acceleration","sprint_speed"], "gte": 90},
    "effect": {"score": 2.0, "tag": "pace"}, "pro_text": "Scatto e velocità da vertice…", "con_text": null,
    "source": "learned|manual", "evidence": {"mentions": 7, "creators": ["Team Gullit","Exeed"]}}
   ```
   Tipi di condizione da supportare: soglia su una stat o media di stat, PlayStyle (+) presente, body type, stelle skill/piede debole, altezza/peso (non ancora letti dalle pagine),
   ruolo alternativo, prezzo. Effetti: variazione di score (limitata), tag di spiegazione, testo pro/contro.
2. **Un solo valutatore** (`rules.evaluate(card, cfg) -> {score_delta, reasons_pro, reasons_con, tags}`) che sostituisce le costanti di `analysis.describe`
   e dà un contributo allo score in `scoring` (con tetto, come `max_bonus`).
3. **I parametri vengono dai pro.** Estrarre dai *motivi* dei pareri (campo `reason`, e in futuro trascrizioni) dei **criteri strutturati**:
   `{ruolo, criterio (pace, body type, animazioni, PlayStyle X, …), polarità (+/−), peso}`. Aggregare per ruolo e creator → proporre/aggiornare regole
   con `evidence`. La calibrazione esistente va estesa a queste regole (soglie e delta), con le stesse protezioni (minimo di dati, cambi limitati, applica/ripristina).
4. **Discordanze** (già fatte per i pareri): estendere a livello di criterio («Team Gullit premia lo scatto, Exeed lo ritiene meno importante per via di…»).
5. **Revisione umana:** le regole *learned* entrano in stato `proposta` e diventano attive solo se l'utente le approva (UI semplice: lista, evidenza, accetta/rifiuta).

### Compiti concreti (in ordine)
1. Estrarre le costanti di §5 da `analysis.py` in `rules.json` (stesso comportamento, test invariati) + `rules.py` con valutatore e validazione all'avvio.
2. Far entrare `score_delta` nel calcolo (con tetto) e mostrarlo nel dettaglio («cosa ha contato»).
3. Tabella `criteria` (o campo in `opinions`) e modulo di estrazione dei criteri dai motivi. Prima versione **a parole chiave** (offline, deterministica);
   poi, se l'utente fornisce una chiave API, versione con modello linguistico con output strutturato.
4. Estendere `calibration.py` alle regole; aggiornare la finestra "Calibra".
5. Leggere altezza, peso, AcceleRATE e chimica dalle pagine (ci sono in entrambe le pagine, oggi ignorati): sono criteri che i pro citano spesso.

### Criteri di accettazione
- Cambiare un valore in `rules.json` cambia il giudizio senza toccare il codice; config non valida → errore chiaro all'avvio.
- Dato un insieme di pareri sintetici, l'estrazione produce le regole attese (test deterministici); nessuna regola *learned* è attiva senza approvazione.
- Con meno dati del minimo, nessun cambio automatico (come oggi per la calibrazione).
- I test esistenti passano; ogni regola nuova ha almeno un test positivo e uno negativo.

---

## 7. LAVORO B — Ricerca automatica (dati e pareri)

### Vincoli reali (importante, non aggirare)
- **Rete bloccata dal cloud** di Claude Code (403 dal proxy su fut.gg, futbin.com, futdb.app). In locale invece si può provare.
- **FUTBIN** blocca l'utente. **FUT.GG** non è mai stato provato da Python. Entrambi violano i ToS se letti in automatico: l'utente ha scelto
  consapevolmente di procedere per uso personale. Mantenere **basso ritmo, robots.txt, tetto di richieste, nessuna elusione** di blocchi.
- **X / TikTok / Instagram:** non esistono API libere per leggere i post altrui. X ha API ufficiali a pagamento (e soglie); TikTok e Instagram non offrono
  un accesso pratico ai contenuti di terzi; lo scraping viola i loro termini e viene bloccato. **Non costruire scraper di questi social.**
- I pareri nei video sono nell'audio: servono **trascrizioni** e poi un **modello linguistico** per estrarre «carta, sì/no, motivo».

### Strada consigliata (a fasi)
1. **Fonte più praticabile: YouTube** (i creator citati — Team Gullit, Exeed, Nassada — fanno video; **da verificare** i canali reali). YouTube Data API v3 (chiave gratuita con quota)
   per cercare i video per canale e carta; trascrizioni dalle sottotitoli disponibili. Controllare i termini d'uso delle trascrizioni.
2. **Pipeline a stadi**, ognuno testabile: `scoperta → testo (trascrizione/post) → estrazione (LLM, JSON strutturato) → risoluzione della carta
   (nome/versione ↔ `cards`) → proposta → revisione dell'utente → salvataggio in `opinions`**.
   - Tabella `proposals` (pareri proposti, con link, data, estratto di testo, confidenza); **niente va in `opinions` senza conferma**.
   - Deduplica per (url, carta, creator); conservare sempre la fonte.
   - Testo esterno = dati non fidati: l'estrazione non deve eseguire istruzioni contenute nei testi (prompt injection); validare lo schema JSON in uscita.
3. **Ripiego manuale veloce** (anche prima del resto): campo «incolla post/trascrizione» → estrazione → proposta. Copre X, TikTok e Instagram **senza violare nulla**
   (il testo lo copia l'utente) e costa solo la chiamata al modello.
4. **Dati dei siti:** provare `fetch.py` in locale. Se FUT.GG risponde 403, un'idea **non provata** è usare la finestra dell'app (pywebview, motore Edge) per
   caricare le pagine come un browser vero ed estrarre l'HTML; da valutare con cautela e solo a basso ritmo.
5. Chiavi in **variabili d'ambiente** (`ANTHROPIC_API_KEY`, `YOUTUBE_API_KEY`), mai nel repository. Modulo con interfaccia comune
   (`Source.search(card) -> [Item]`, `extract(text, known_cards) -> [OpinionProposal]`) per poter aggiungere fonti.

### Criteri di accettazione
- `python -m eafcmeta.research --card "Nome" ` (o da UI) produce **proposte** con fonte e motivo; l'utente le approva e diventano pareri.
- Test con trascrizioni/testi di esempio (fixture) per estrazione e risoluzione carta; nessuna chiamata di rete nei test.
- Ogni fase fallisce in modo chiaro (blocco, chiave mancante, quota) senza salvare dati parziali o sbagliati.

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

1. **Modello linguistico per estrarre i pareri:** vuole usare una chiave API (costo a parte dai crediti di Claude Code)? Senza, resta solo l'estrazione a parole chiave.
2. **Quali creator e quali canali** considerare «pro» (oltre a Team Gullit, Exeed, Nassada) e con che peso.
3. **YouTube come fonte principale** al posto di X/TikTok/Instagram: accettabile?
4. Se aprire una **PR verso `main`** (oggi non c'è `main`) o continuare sul branch di lavoro.
5. Pacchetto **`.exe`** per Windows (PyInstaller) quando la finestra nativa funziona.

## 11. Primi passi consigliati a chi parte ora

1. Clonare, `pip install -r requirements-dev.txt`, `pytest -q` (devono passare 58 test), `python start.py` e **guardare cosa succede** (finestra o browser, righe `[app]`).
2. Importare le 30 carte di esempio e inserire qualche parere di prova per vedere analisi, discordanze e calibrazione.
3. Chiedere all'utente 3–4 pagine salvate vere (carta di movimento, portiere, lista) per FUT.GG e FUTBIN se i siti sono cambiati.
4. Iniziare dal **Lavoro A, compito 1** (estrarre le regole in `rules.json` senza cambiare i risultati): è sicuro, testabile e sblocca tutto il resto.
5. Poi il **ripiego manuale «incolla testo → proposte»** del Lavoro B (nessun vincolo di rete) e, in parallelo, provare `fetch.py` in locale.

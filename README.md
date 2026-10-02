# EA FC Meta

App per uso personale (e con gli amici): valuta le carte EA FC con uno score da stats in-game, PlayStyle, body type
e parere dei pro (inserito a mano), e dice se una carta conviene rispetto al prezzo di mercato.

## Avvio
    pip install -r requirements.txt
    python start.py            # controlla gli aggiornamenti, poi apre l'app in una finestra nativa
    pip install -r requirements-dev.txt && pytest

## App desktop (senza browser)
`python start.py` apre l'app in una **finestra nativa** (pywebview: su Windows usa Edge WebView2, già incluso in
Windows 10/11). Su Windows puoi anche fare **doppio clic su `EAFCMETA.pyw`**: parte senza finestra di console
(i messaggi vanno in `app.log`). Se la finestra nativa non è disponibile ripiega sul browser; `--browser` lo forza.

## Copia locale con aggiornamento automatico
Prima volta (serve git e Python 3.11+):

    git clone -b claude/nifty-babbage-h8l35m https://github.com/enricolt/EAFCMETA
    cd EAFCMETA
    python start.py

Ogni avvio fa `git fetch` e, se ci sono novità, un aggiornamento fast-forward (solo se non hai modifiche locali);
reinstalla le dipendenze se `requirements.txt` è cambiato. Offline usa la versione che hai. Il database
`eafcmeta.db` è ignorato da git: gli aggiornamenti non lo toccano. Opzioni: `--check`, `--no-update`, `--port`, `--lan`, `--browser`, `--retry-app`.

## Con gli amici (rete locale)
`python start.py --lan` ascolta su tutta la rete e **richiede una chiave d'accesso** (creata in `.token`, ignorata da git).
Stampa il link da mandare agli amici (contiene la chiave: `http://IP:8000/#token=…`). Per l'accesso da fuori casa
usa un tunnel con autenticazione (Tailscale, Cloudflare Tunnel): non aprire la porta direttamente su internet.

## Inserire i dati
Nessuno scraping (violerebbe i ToS dei siti): i dati si copiano a mano.
- **Importa → Carte**: incolla CSV/TSV/JSON (colonne: `name, version, position, price, is_sbc, body_type, weak_foot,
  skill_moves, playstyles` + una colonna per stat). “Modello CSV” genera le intestazioni. Se nome+versione+posizione
  esistono già la carta viene **aggiornata** (niente doppioni) e lo storico prezzi si allunga. Anteprima prima di salvare;
  se c'è un errore non viene salvato nulla e vedi le righe sbagliate.
- **Importa → Solo prezzi**: righe `nome;versione;prezzo` da incollare ogni tanto.
- **Importa → Pagine salvate** (carte vere): salva col browser (Ctrl+S → “Pagina web, solo HTML”) la pagina di un
  giocatore su **FUT.GG** o **FUTBIN** e scegli il file: leggo nome, versione, ruolo, prezzo (console), stats, body
  type, piede debole, skill e PlayStyle. Più file insieme; da terminale: `python -m eafcmeta.collect cartella/`.
  Limite noto: i PlayStyle+ non sono ancora riconosciuti con certezza (servono pagine di esempio con PlayStyle+).
- Da terminale: `python -m eafcmeta.importer data/esempio.csv` (30 carte fittizie di prova).
- Prezzi: `12.500`, `12500`, `12k`, `1.2m`.

## Come si calcola
- **Score base** = media pesata delle stats del ruolo + bonus (PlayStyle+ per tier e ruolo, body type, 5★ piede
  debole/skill), con bonus massimo 12; sopra 90 la scala si comprime dolcemente verso 100.
- **Score finale** = 70% base + 30% pro. Senza parere dei pro vale solo il base (l'app lo segnala).
- **Verdetto**: scarto dello score dalla curva score–ln(prezzo) delle carte della stessa posizione (regressione
  robusta Theil-Sen). Servono ≥ 8 carte con ≥ 4 prezzi diversi; la soglia si adatta alla dispersione (min ±1.5).
  “Conviene” / “In linea” / “Evita”, altrimenti “Pochi dati”.
- Pesi, bonus e soglie: `eafcmeta/config/patch.json` (validato all'avvio). Sono valori di partenza: da tarare sulle
  tier list che conosci.

## API
Documentazione interattiva su `/docs`. Principali: `GET/POST /api/v1/cards`, `GET/PUT/DELETE /api/v1/cards/{id}`,
`PUT /api/v1/cards/{id}/pro`, `POST /api/v1/import`, `POST /api/v1/prices`, `GET /api/v1/meta`.
Posizioni supportate: ST, CF, ali (RW/LW/RM/LM), CAM, CM, CDM, terzini (RB/LB), esterni (RWB/LWB), CB. Niente portieri.

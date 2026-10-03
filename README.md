# EA FC Meta

App per uso personale (e con gli amici): valuta le carte EA FC con uno score da stats in-game, PlayStyle, body type
e parere dei pro (inserito a mano), e dice se una carta conviene rispetto al prezzo di mercato.

> Per chi continua il lavoro: leggi **[HANDOFF.md](HANDOFF.md)** (stato, cosa non è verificato, prossimi compiti).

## Avvio
    pip install -r requirements.txt
    python start.py            # controlla gli aggiornamenti, poi apre l'app in una finestra propria (vedi sotto)
    pip install -r requirements-dev.txt && pytest

## App desktop (senza browser)
`python start.py` apre l'app in una **finestra propria** provando in ordine tre livelli (ognuno stampa il motivo se fallisce):
1. **pywebview** (finestra nativa; su Windows richiede pythonnet/WebView2, che su Python recenti può non installarsi);
2. **finestra "app" di Microsoft Edge / Chrome / Chromium** già installati: `--app=URL` con profilo dedicato
   (`.app_profile/`), senza barra degli indirizzi, sembra un'app nativa e **non richiede nessuna libreria**. Il server si
   chiude quando chiudi la finestra (la pagina invia un battito al server, così funziona anche se Edge/Chrome riusano
   un'istanza già aperta);
3. **browser predefinito** (ultimo ripiego; il server resta attivo finché non chiudi il programma).

Opzioni: `--window auto|pywebview|app|browser` forza un livello, `--browser` = `--window browser`, `--retry-app` riprova
l'installazione di pywebview (tentativo con timeout di 90 s, mai bloccante). Su Windows puoi fare **doppio clic su
`EAFCMETA.pyw`**: parte senza console (messaggi in `app.log`).

**Se qualcosa non va:** `python start.py --diagnosi` stampa (e salva in `diagnosi.txt`) versione di Python, sistema,
git, pywebview (e perché non si importa), browser trovati per l'app mode, porta, versione dell'app e ultimo commit:
copia e incolla il testo per chiedere aiuto.

> **Stato della verifica:** catena di ripiego, ricerca dei browser, riga di comando e diagnosi sono coperte da test
> automatici (Linux, con percorsi e livelli finti). **Mai provato su Windows reale**: non so ancora quale livello
> parta sul tuo PC; le righe `[app] ...` e `--diagnosi` lo dicono.

### Eseguibile Windows (.exe) con PyInstaller — NON PROVATO
`python build_windows.py` (su Windows) crea `dist/EAFCMETA.exe` partendo da `eafcmeta.spec` (include `eafcmeta/static` e
`eafcmeta/config`; usa la finestra app mode di Edge/Chrome, pywebview non è incluso). Database e `local.json` stanno
accanto all'.exe (o in `%LOCALAPPDATA%\EAFCMETA` se la cartella non è scrivibile). Il workflow GitHub Actions
`.github/workflows/build-windows.yml` (avvio manuale: Actions, build-windows, Run workflow) costruisce l'.exe su
`windows-latest` e lo allega come artefatto: nessuna release, nessun segreto. **Questo build non è mai stato eseguito**:
potrebbero servire correzioni (import nascosti nello spec). Windows SmartScreen può avvisare per un .exe non firmato.

## Copia locale con aggiornamento automatico
Prima volta (serve git e Python 3.11+):

    git clone -b claude/nifty-babbage-h8l35m https://github.com/enricolt/EAFCMETA
    cd EAFCMETA
    python start.py

Ogni avvio fa `git fetch` e, se ci sono novità, un aggiornamento fast-forward (solo se non hai modifiche locali);
reinstalla le dipendenze se `requirements.txt` è cambiato. Offline usa la versione che hai. Il database
`eafcmeta.db` è ignorato da git: gli aggiornamenti non lo toccano. Opzioni: `--check`, `--no-update`, `--port`, `--lan`, `--browser`, `--window`, `--retry-app`, `--diagnosi`.

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
  I PlayStyle+ sono riconosciuti da entrambi i siti; la stessa carta letta da FUT.GG e da FUTBIN non si duplica (“Kelly” = “Chloe Kelly”; Rare/Common = Gold).
- **Pagine elenco di FUT.GG** (es. `fut.gg/players/?nation_id=[54]`): salvate come le altre, aggiornano i prezzi delle
  carte già presenti e dicono quali giocatori mancano. Le statistiche stanno solo nelle pagine dei singoli giocatori.
- **Raccolta automatica da FUT.GG** (sul tuo PC):
  `python -m eafcmeta.fetch "https://www.fut.gg/players/?nation_id=[54]" --max 30` scarica da solo le pagine dei
  giocatori non ancora presenti, con pause di 2.5 s e nel rispetto di robots.txt. Se il sito blocca la richiesta
  si ferma e te lo dice (allora salva le pagine dal browser). Non testato contro il sito reale.
- Da terminale: `python -m eafcmeta.importer data/esempio.csv` (30 carte fittizie di prova).
- Prezzi: `12.500`, `12500`, `12k`, `1.2m`.

## Pareri dei creator e analisi di ogni carta
Nel dettaglio di una carta c'è un'**analisi scritta** (generata da regole fisse sui dati, senza LLM): se è meta, perché
sì, perché no/attenzione e un consiglio finale. Sotto, **“Cosa dicono i creator”**: per ogni carta puoi inserire il parere
di più creator (es. Team Gullit, Exeed, Nassada): sì / dipende / no, voto facoltativo, motivo a parole sue e link. Se
discordano l'app lo dice e riporta i motivi di ciascuno (“secondo Team Gullit sì; secondo Exeed e Nassada no”). Il
punteggio “pro” (30% dello score) è la media dei pareri; senza voto, sì = 85, dipende = 70, no = 50 (`stance_scores`).
I pareri e i motivi li inserisci tu: l'app non li legge dai video. Soglie “meta” in `patch.json` (`meta`).

## Calibrazione dal meta dei pro (🎯 Calibra)
I parametri dell'analisi (soglie meta e pesi delle statistiche) si ricavano da ciò che ritengono meta i pro: con almeno
12 carte che hanno il parere di un creator, “Calibra” mostra quanto il nostro punteggio è allineato (accordo e
correlazione), propone soglie nuove e, dove ci sono 8+ carte dello stesso ruolo, pesi nuovi (cambi prudenti: più
pochi sono i dati, meno si spostano). Applichi tu; “Ripristina” torna ai valori originali. Il risultato sta in
`eafcmeta/config/local.json` (personale, ignorato da git) sopra `patch.json`. Il voto automatico della community
di FUT.GG non conta come “pro”.

## Segnali dei siti
Leggendo le pagine di FUT.GG e FUTBIN l'app salva anche: GG Rating, ruolo in cui rende meglio e classifica,
FUTBIN Rating e classifica, e il tier votato dalla community di FUT.GG (che diventa un parere “FUT.GG (community)”,
segnalato come poco affidabile sotto i 30 voti).

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
Posizioni supportate: ST, CF, ali (RW/LW/RM/LM), CAM, CM, CDM, terzini (RB/LB), esterni (RWB/LWB), CB e GK (portieri: contano riflessi, tuffo, piazzamento, presa e rinvio; la lettura automatica delle loro pagine non è ancora verificata).

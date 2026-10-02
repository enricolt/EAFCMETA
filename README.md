# EA FC Meta

App per uso personale: valuta carte EA FC (score da stats + PlayStyle + body type, parere dei pro inserito a mano)
e dice se una carta conviene rispetto al prezzo di mercato.

## Avvio
    pip install -r requirements.txt
    uvicorn eafcmeta.api:app --reload      # docs su /docs
    pytest

Interfaccia web su `/` (classifica, aggiunta carte, pro score modificabile).
Import carte: `python -m eafcmeta.importer data/esempio.csv` (dati di esempio fittizi).
DB: file `eafcmeta.db` (o variabile `EAFCMETA_DB`).

## Decisioni iniziali
- Monolite FastAPI + SQLite, nessuno scraping: le carte si inseriscono via API (`POST /api/v1/cards`).
- Pro sentiment inserito a mano (`PUT /api/v1/cards/{id}/pro`); se manca, conta solo lo score base.
- Bonus (PlayStyle, body type, 5★) sommati e limitati a `max_bonus`, così lo score resta ≤ 100.
- Verdetto = scarto dello score dalla curva score-vs-ln(prezzo) delle carte nella stessa posizione
  (serve almeno `min_cards_for_curve` carte, altrimenti NEUTRAL).
- Pesi e soglie in `eafcmeta/config/patch.json`: si modificano senza toccare il codice.
- Posizioni supportate: ST/CF, ali, CAM, CM/CDM, terzini, CB (niente portieri per ora).

## Copia locale con aggiornamento automatico
Prima volta (serve git e Python 3.11+):

    git clone -b claude/nifty-babbage-h8l35m https://github.com/enricolt/EAFCMETA
    cd EAFCMETA
    python start.py

Ogni avvio di `python start.py` fa `git fetch`, aggiorna la copia se ci sono novità (solo fast-forward, e solo
se non hai modifiche locali), reinstalla le dipendenze se `requirements.txt` è cambiato e apre l'app su
http://localhost:8000. Se sei offline usa la versione che hai. Il database `eafcmeta.db` è ignorato da git,
quindi gli aggiornamenti non lo toccano. Opzioni: `--lan` (visibile in rete locale), `--check`, `--no-update`.

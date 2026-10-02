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

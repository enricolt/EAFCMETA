# Contratto API del catalogo (nuova impostazione dell'app)

**Principio:** l'app NON è la collezione dell'utente. È il **database delle carte del gioco**, che si **aggiorna a comando**
("Aggiorna database") e **valuta ogni carta**. La schermata principale è l'elenco delle carte **in ordine di uscita**
(più recenti prima, come su FUTBIN), con la valutazione su ognuna. Aggiungere carte a mano e importare file restano
funzioni secondarie ("Strumenti").

Tutte le rotte sono sotto `/api/v1`, protette da `require_token` come le altre. Testi in italiano.

## GET /catalog
Elenco paginato delle carte, valutazioni incluse (lette dalla cache delle valutazioni, mai ricalcolate a ogni richiesta).

Parametri (tutti facoltativi):
- `sort`: `released` (default) | `score` | `price` | `rating` | `name`; `order`: `desc` (default) | `asc`
- `limit` (default 60, max 200), `offset` (default 0)
- `q` (testo su nome/versione), `position` (lista separata da virgole, es. `ST,CF`), `version` (famiglie di versione, es. `TOTW,Icon`),
  `rating_min`, `rating_max`, `price_min`, `price_max`
- `verdict` (lista: `MUST_DO,NEUTRAL,AVOID`), `meta` (lista: `top,meta,playable,below`), `has_opinions` (`true`/`false`)
- `released_after`, `released_before` (date `YYYY-MM-DD`)

Risposta:
```json
{"total": 1234, "offset": 0, "limit": 60,
 "items": [{
   "id": 1, "name": "Chloe Kelly", "version": "Gold 86", "version_family": "Gold", "position": "RM", "rating": 86,
   "released_at": "2026-09-25T17:01:00Z",          // null se sconosciuta
   "first_seen": "2026-10-03T01:20:00Z",
   "cost_credits": 12000, "is_sbc": false,
   "scores": {"base_score": 91.49, "final_score": 88.6, "pro_sentiment_score": 75.9, "pro_share": 0.2},
   "verdict": "MUST_DO", "value_gap": 4.1, "verdict_reason": "...",
   "meta_level": "top", "meta_label": "Meta di vertice",
   "summary": "Scatto e velocità da vertice · rende più del prezzo (+4,1)",   // una riga, max ~120 caratteri
   "top_stats": [{"k": "acceleration", "v": 88}], "bonus_playstyles": ["Power Shot+"],
   "opinions_count": 3
 }]}
```
`released_at` e `rating` vengono dalle pagine dei siti (FUT.GG: «Added On», FUTBIN: «Release date»).
Carte senza data: in coda all'ordinamento per uscita, ordinate per `first_seen`.

## GET /catalog/facets
Valori per i filtri: `{"positions": [{"value","count"}], "versions": [{"value","count"}], "rating": {"min","max"},
"price": {"min","max"}, "released": {"min","max"}, "total": N}`.

## GET /catalog/status
```json
{"cards": 1234, "evaluated": 1234, "newest": "2026-10-02T...Z", "oldest": "2026-08-01T...Z",
 "running": false,
 "progress": {"phase": "elenco|pagine|prezzi|valutazione", "done": 12, "total": 40, "message": "Leggo le pagine dei giocatori nuovi…"},
 "last_update": {"started": "...", "finished": "...", "status": "ok|parziale|errore|annullato",
                 "summary": {"nuove": 14, "aggiornate": 3, "prezzi": 120, "valutate": 137, "errori": [], "fermato": null}},
 "config": {"list_urls": ["https://www.fut.gg/..."], "pages_per_update": 3, "lookback_days": 14,
            "needs_setup": false}}
```
`needs_setup = true` se nessun indirizzo di elenco "ultime uscite" è configurato o riconosciuto: l'interfaccia deve guidare l'utente a incollarlo.

## POST /catalog/update
Corpo: `{"mode": "new" | "backfill" | "prices", "pages": 3}` (tutti facoltativi; default `new`).
- `new`: legge le pagine elenco **dalla più recente**, scarica le pagine dei giocatori non ancora nel database e si **ferma** quando una pagina contiene solo carte già note (o al tetto di pagine); poi aggiorna i prezzi delle carte uscite negli ultimi `lookback_days` e rivaluta.
- `backfill`: continua all'indietro (più vecchie) per altre `pages` pagine (popolamento iniziale a tranche).
- `prices`: solo prezzi delle carte recenti.
Risposta `202 {"started": true}`; `409` se un aggiornamento è già in corso. Gira in background con avanzamento in `/catalog/status`.
Gentile col sito: pausa tra richieste, robots.txt, tetti. Un blocco o 3 errori di fila fermano il passo e lo registrano.

## POST /catalog/cancel
Ferma l'aggiornamento in corso: `{"ok": true}`.

## PUT /catalog/config
`{"list_urls"?: [..], "pages_per_update"?: int, "lookback_days"?: int}` — validati (solo https, host consentiti, limiti). Salva in un file personale.

## Rotte esistenti da mantenere
`GET /cards/{id}` (dettaglio completo con analisi), `PUT/DELETE /cards/{id}`, `POST /cards` (aggiunta manuale), `PUT/DELETE /cards/{id}/opinions…`,
import, calibrazione, regole, ricerca, automatico. `GET /cards` può restare ma l'elenco principale usa `/catalog`.

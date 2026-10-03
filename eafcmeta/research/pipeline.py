"""Pipeline a stadi: fonte -> testo -> risoluzione della carta -> estrazione -> proposta (pending) -> revisione umana.

Ogni stadio fallisce con un errore chiaro; se qualcosa va storto durante il salvataggio il chiamante fa rollback
(nessun dato parziale). Niente arriva in `opinions` da qui: serve store.accept().
"""
from . import config, resolver, store, text as T
from .extract import LLMExtractor, OfflineExtractor
from .llm import get_llm
from .resolver import Candidate
from .sources import Item, MissingKeyError


def make_extractor(mode: str = "auto", llm=None):
    """mode: 'offline' | 'llm' | 'auto' (llm se c'è un client/chiave, altrimenti offline)."""
    if mode not in ("auto", "offline", "llm"):
        raise ValueError("mode deve essere auto, offline o llm")
    if mode == "offline":
        return OfflineExtractor()
    llm = llm or get_llm()
    if llm is None:
        if mode == "llm":
            raise MissingKeyError("manca la chiave del modello: imposta la variabile d'ambiente ANTHROPIC_API_KEY "
                                  "(oppure usa la modalità offline)")
        return OfflineExtractor()
    return LLMExtractor(llm)


def _cards(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT id, name, version, position FROM cards")]


def process_items(conn, items: list[Item], extractor, card_id: int | None = None) -> dict:
    """Elabora i testi e salva le proposte. NON fa commit. Ritorna un riepilogo."""
    cards = _cards(conn)
    forced = None
    if card_id is not None:
        forced = next((c for c in cards if c["id"] == card_id), None)
        if forced is None:
            raise store.ProposalNotFound("carta non trovata")
    report = {"mode": extractor.mode, "created": [], "duplicates": [], "warnings": [], "unresolved": []}
    limit = config.limit("max_text_chars")
    for it in items:
        text = T.clean(it.text)
        if not text:
            report["warnings"].append(f"{it.url or 'testo'}: vuoto, saltato")
            continue
        if len(text) > limit:
            text = text[:limit]
            report["warnings"].append(f"{it.url or 'testo'}: troncato a {limit} caratteri")
        if forced:
            cands = [Candidate(card_id=forced["id"], name=forced["name"], version=forced["version"],
                               position=forced["position"], confidence=1.0, start=0, end=0, kind="forced")]
        else:
            cands = resolver.resolve(text, cards)
        if not cands:
            report["unresolved"].append(it.url or "(testo incollato)")
            report["warnings"].append(f"{it.url or 'testo'}: nessuna carta riconosciuta (indica card_id o aggiungi la carta)")
            continue
        ex = extractor.extract(text, cands, it.creator, it.url)
        report["warnings"].extend(ex.warnings)
        if it.low_confidence:  # es. video senza trascrizione: solo titolo/descrizione/capitoli
            for p in ex.proposals:
                p.confidence = round(p.confidence * 0.5, 2)
                p.note = (p.note + "; " if p.note else "") + "fonte senza trascrizione: solo titolo/descrizione/capitoli"
        new, dup = store.add_proposals(conn, ex.proposals, it.source, text)
        report["created"].extend(new)
        report["duplicates"].extend(dup)
        if not ex.proposals:
            report["warnings"].append(f"{it.url or 'testo'}: carte riconosciute ma nessun parere espresso")
    return report

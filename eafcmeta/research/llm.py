"""Client del modello linguistico (API Anthropic). Opzionale: senza ANTHROPIC_API_KEY si usa solo l'estrazione offline.

L'interfaccia che serve al resto del pacchetto è solo `complete(system, user, schema) -> str`, così nei test si
inietta un finto client. L'SDK `anthropic` è importato solo se serve (non è nei requirements).
"""
import os

from .sources import MissingKeyError, ResearchError

DEFAULT_MODEL = "claude-opus-5-5"  # sovrascrivibile con EAFCMETA_LLM_MODEL (es. un modello più economico)


class LLMCallError(ResearchError):
    pass


def have_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


class AnthropicLLM:
    def __init__(self, model: str | None = None, client=None, max_tokens: int = 4000):
        self.model = model or os.environ.get("EAFCMETA_LLM_MODEL") or DEFAULT_MODEL
        self.max_tokens = max_tokens
        self._client = client

    def _get_client(self):
        if self._client is None:
            if not have_key():
                raise MissingKeyError("manca la chiave del modello: imposta la variabile d'ambiente ANTHROPIC_API_KEY")
            try:
                import anthropic
            except ImportError:
                raise LLMCallError("libreria 'anthropic' non installata (pip install anthropic)") from None
            self._client = anthropic.Anthropic()
        return self._client

    def complete(self, system: str, user: str, schema: dict) -> str:
        client = self._get_client()
        try:
            resp = client.messages.create(
                model=self.model, max_tokens=self.max_tokens, system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}})
        except ResearchError:
            raise
        except Exception as e:  # errori dell'SDK (rete, 4xx/5xx): messaggio chiaro, senza dettagli della richiesta
            raise LLMCallError(f"chiamata al modello fallita ({type(e).__name__})") from None
        if getattr(resp, "stop_reason", None) == "refusal":
            raise LLMCallError("il modello ha rifiutato la richiesta")
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


def get_llm() -> AnthropicLLM | None:
    """Client reale se la chiave c'è, altrimenti None (=> solo modalità offline)."""
    return AnthropicLLM() if have_key() else None

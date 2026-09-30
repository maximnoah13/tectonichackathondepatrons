"""Optionele Claude-koppeling.

Alles in dit project werkt zonder LLM (deterministisch). Staat de `anthropic`-package
geïnstalleerd en zijn er credentials (ANTHROPIC_API_KEY of `ant auth login`), dan
gebruiken de Specialist Agent en de Test Agent Claude voor extractie, formulering en
de judge. Zet TRUST_LAYER_LLM=off om Claude expliciet uit te zetten.
"""
import json
import os

MODEL = "claude-opus-5-5"

_client = None
_status = None


def status():
    """Geeft (beschikbaar: bool, reden: str)."""
    global _client, _status
    if _status is not None:
        return _status
    if os.environ.get("TRUST_LAYER_LLM", "").lower() == "off":
        _status = (False, "uitgezet via TRUST_LAYER_LLM=off")
        return _status
    try:
        import anthropic
    except ImportError:
        _status = (False, "package 'anthropic' niet geïnstalleerd")
        return _status
    try:
        _client = anthropic.Anthropic()
        _status = (True, f"Claude ({MODEL})")
    except Exception as e:  # geen credentials gevonden
        _status = (False, f"geen credentials: {e.__class__.__name__}")
    return _status


def available():
    return status()[0]


def structured(system, prompt, schema, effort="low", max_tokens=16000):
    """Eén Claude-call met gegarandeerde JSON-output volgens `schema`.

    Geeft een dict terug, of None als Claude niet beschikbaar is of faalt:
    de aanroeper valt dan terug op de deterministische route.
    """
    if not available():
        return None
    import anthropic
    try:
        response = _client.beta.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=system,
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": schema},
            },
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.RateLimitError:
        return None
    except anthropic.APIStatusError:
        return None
    except anthropic.APIConnectionError:
        return None
    if response.stop_reason in ("refusal", "max_tokens"):
        return None
    text = next((b.text for b in response.content if b.type == "text"), None)
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None

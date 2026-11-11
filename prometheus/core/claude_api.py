"""Anthropic credential validation and model discovery (official SDK).

Validation uses GET /v1/models: it proves the key works and costs no tokens.
"""
from __future__ import annotations

from dataclasses import dataclass

import anthropic

PREFERRED_MODEL = "claude-opus-5-5"


class ClaudeApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelInfo:
    id: str
    display_name: str
    max_input_tokens: int | None


def list_models(api_key: str) -> list[ModelInfo]:
    """Return the models this key can use. Raises ClaudeApiError with a user-facing reason."""
    if not api_key.strip():
        raise ClaudeApiError("Enter an API key first.")
    client = anthropic.Anthropic(api_key=api_key.strip(), max_retries=1, timeout=20.0)
    try:
        models = [ModelInfo(m.id, m.display_name, getattr(m, "max_input_tokens", None))
                  for m in client.models.list(limit=100)]
    except anthropic.AuthenticationError:
        raise ClaudeApiError("Anthropic rejected this API key (401). Check it was copied in full "
                             "and has not been revoked.") from None
    except anthropic.PermissionDeniedError:
        raise ClaudeApiError("This key is valid but lacks permission to list models (403). "
                             "Check the key's workspace permissions.") from None
    except anthropic.RateLimitError:
        raise ClaudeApiError("Rate limited by Anthropic (429). Wait a moment and try again.") from None
    except anthropic.APIStatusError as exc:
        raise ClaudeApiError(f"Anthropic API returned HTTP {exc.status_code}.") from None
    except anthropic.APIConnectionError:
        raise ClaudeApiError("Could not reach api.anthropic.com. Check your network connection.") from None
    finally:
        client.close()
    if not models:
        raise ClaudeApiError("The key is valid but no models are available to it.")
    return models


def default_model(models: list[ModelInfo], current: str = "") -> str:
    ids = [m.id for m in models]
    for candidate in (current, PREFERRED_MODEL):
        if candidate in ids:
            return candidate
    return ids[0]

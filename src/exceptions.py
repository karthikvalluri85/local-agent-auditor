"""
Domain-specific exceptions for the audit pipeline.

Raised instead of generic RuntimeError/ValueError so callers (main.py) can
distinguish "Ollama isn't reachable" from "the model won't produce
parseable JSON" without inspecting error message text.
"""


class OllamaConnectionError(Exception):
    """Raised when the Ollama server at OLLAMA_HOST can't be reached, or the
    requested model call otherwise fails at the transport level. Check that
    `ollama serve` is running and the model has been pulled."""


class InvalidModelResponseError(Exception):
    """Raised when the model still hasn't produced parseable JSON after
    MAX_RETRIES corrective retries. The last raw (unparsed) response is
    included in the exception message for debugging."""

"""
All local-model access goes through this module, via the official `ollama`
Python SDK. No raw HTTP calls needed.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import ollama

from src.config import MAX_RETRIES, MODEL_NAME, OLLAMA_HOST, REQUEST_TIMEOUT_S
from src.exceptions import InvalidModelResponseError, OllamaConnectionError


def safe_json_parse(raw: str) -> dict[str, Any] | None:
    """
    Attempt to coerce a small model's imperfect output into valid JSON.
    Handles the most common failure modes we see at 0.5B-3B parameter sizes:
      - prose before/after the JSON object ("Sure! Here's the JSON: {...}")
      - markdown code fences (```json ... ```)
      - trailing commas
      - single quotes instead of double quotes
    Returns None if nothing salvageable is found.
    """
    if not raw:
        return None

    text = raw.strip()

    # Strip markdown code fences
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    # Isolate the outermost {...} block if the model wrapped it in prose
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)

    # Try straight parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Repair common issues: trailing commas, single-quoted keys/values
    repaired = re.sub(r",\s*([}\]])", r"\1", text)  # trailing commas
    repaired = re.sub(r"'", '"', repaired)           # naive quote swap

    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        return None


def call_model(system_prompt: str, user_prompt: str, label: str) -> dict[str, Any]:
    """
    Calls the local Ollama model with strict JSON-mode enforcement and
    retries with an increasingly stern corrective instruction if the
    model returns malformed JSON.
    """
    client = ollama.Client(host=OLLAMA_HOST, timeout=REQUEST_TIMEOUT_S)
    last_raw = ""

    for attempt in range(1, MAX_RETRIES + 2):  # initial try + retries
        try:
            response = client.chat(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                format="json",       # Ollama-level JSON-mode constraint
                options={
                    "temperature": 0.1,  # low temp = fewer format hallucinations
                    "num_predict": 1024,  # headroom so an 8-item findings array isn't truncated
                },
            )
        except Exception as exc:
            raise OllamaConnectionError(
                f"[{label}] Could not reach Ollama at {OLLAMA_HOST}. "
                f"Is `ollama serve` running and is `{MODEL_NAME}` pulled? "
                f"Original error: {exc}"
            ) from exc

        last_raw = response["message"]["content"]
        parsed = safe_json_parse(last_raw)

        if parsed is not None:
            return parsed

        # Correction round: tell the model exactly what went wrong
        user_prompt = (
            f"Your previous reply was not valid JSON. Reply with ONLY a single "
            f"valid JSON object, no prose, no markdown fences. "
            f"Previous invalid reply was:\n{last_raw[:500]}"
        )
        time.sleep(0.5)

    raise InvalidModelResponseError(
        f"[{label}] Failed to get valid JSON from {MODEL_NAME} after "
        f"{MAX_RETRIES + 1} attempts. Last raw output:\n{last_raw}"
    )

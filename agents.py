"""
agents.py
---------
Core multi-agent orchestration logic for the Local Adversarial Auditor.

Three agents, one local model (qwen2.5:0.5b, ~398MB, zero GPU required), zero cloud calls:

  1. AuditorAgent    -> scans raw source for cost leaks / security holes / anti-patterns
  2. SkepticAgent     -> cross-examines each finding against hard-coded rules,
                          challenges weak evidence, drops or downgrades false positives
  3. SynthesizerAgent -> writes the final executive markdown report

Design choices that matter for small local models:
  - Every agent call is constrained with Ollama's `format="json"` mode AND
    a strict schema described in the prompt. Small models drift from prose
    instructions but hold up much better when the *transport* itself
    enforces JSON.
  - We never trust the model's JSON blindly: `safe_json_parse()` repairs
    common small-model failure modes (trailing commas, single quotes,
    prose wrapped around the object, truncated braces).
  - Each agent gets a narrow, single-purpose system prompt. Small models
    lose coherence fast with multi-task prompts, so we never ask one call
    to "find issues AND format them AND critique them."
  - Retries with a stricter corrective prompt on parse failure, capped at
    MAX_RETRIES, so the pipeline fails loudly instead of hanging forever.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import ollama

MODEL_NAME = "qwen2.5:0.5b"
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
MAX_RETRIES = 3
REQUEST_TIMEOUT_S = 60


# --------------------------------------------------------------------------- #
# Small-model-safe JSON handling
# --------------------------------------------------------------------------- #

def safe_json_parse(raw: str) -> dict[str, Any] | None:
    """
    Attempt to coerce a small model's imperfect output into valid JSON.
    Handles the most common failure modes we see at 1.5B-3B parameter sizes:
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
            raise RuntimeError(
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

    raise ValueError(
        f"[{label}] Failed to get valid JSON from {MODEL_NAME} after "
        f"{MAX_RETRIES + 1} attempts. Last raw output:\n{last_raw}"
    )


# --------------------------------------------------------------------------- #
# Data contracts between agents (kept intentionally flat/simple for small models)
# --------------------------------------------------------------------------- #

@dataclass
class Finding:
    id: str
    category: str          # "cost_leak" | "security" | "anti_pattern"
    severity: str           # "low" | "medium" | "high" | "critical"
    description: str
    evidence: str
    verdict: str = "pending"     # set by SkepticAgent: "confirmed" | "downgraded" | "rejected"
    challenge_note: str = ""


@dataclass
class AuditState:
    source_path: str
    source_code: str
    findings: list[Finding] = field(default_factory=list)
    report_markdown: str = ""


# --------------------------------------------------------------------------- #
# Agent 1: Auditor — finds issues
# --------------------------------------------------------------------------- #

AUDITOR_SYSTEM_PROMPT = """You are a strict code auditor. You ONLY output JSON.
You inspect source code for exactly three issue types:
- "cost_leak": inefficient loops, oversized cloud resources, missing caching,
  N+1 API/network calls, resources never released, polling without backoff.
- "security": hardcoded secrets, SQL injection, logging of sensitive data,
  missing input validation, insecure defaults.
- "anti_pattern": code smells that aren't security or cost issues but hurt
  maintainability or reliability.

Respond with ONLY this JSON shape, nothing else:
{
  "findings": [
    {
      "id": "F1",
      "category": "cost_leak",
      "severity": "high",
      "description": "one sentence describing the issue",
      "evidence": "the specific function name or line snippet that proves it"
    }
  ]
}
"category" must be exactly one of: cost_leak, security, anti_pattern — pick ONE, never combine them.
"severity" must be exactly one of: low, medium, high, critical — pick ONE, never combine them.
Do not include markdown. Do not include explanations outside the JSON.
Scan the ENTIRE file top to bottom before answering. Most real files have MORE
THAN ONE issue — do not stop after the first one you notice. Report every
distinct issue you find, up to 8, prioritizing the most severe and concrete ones."""

VALID_CATEGORIES = {"cost_leak", "security", "anti_pattern"}
VALID_SEVERITIES = {"low", "medium", "high", "critical"}


class AuditorAgent:
    label = "AGENT-1-AUDITOR"

    def run(self, state: AuditState) -> AuditState:
        user_prompt = (
            f"Audit the following Python source file for cost leaks, security "
            f"vulnerabilities, and anti-patterns. File: {state.source_path}\n\n"
            f"```python\n{state.source_code}\n```"
        )
        result = call_model(AUDITOR_SYSTEM_PROMPT, user_prompt, self.label)

        raw_findings = result.get("findings", [])
        if not isinstance(raw_findings, list):
            raw_findings = []
        if not raw_findings and ("description" in result or "evidence" in result):
            # Small model collapsed the schema and returned a single finding
            # object at the top level instead of wrapping it in {"findings": [...]}.
            raw_findings = [result]

        findings = []
        for i, rf in enumerate(raw_findings, start=1):
            if not isinstance(rf, dict):
                continue
            # A 0.5B model will sometimes echo the "a|b|c" placeholder from the
            # schema example verbatim instead of picking one value — fall back
            # to a safe default rather than surface the raw placeholder.
            category = str(rf.get("category", "")).strip().lower()
            if category not in VALID_CATEGORIES:
                category = "anti_pattern"
            severity = str(rf.get("severity", "")).strip().lower()
            if severity not in VALID_SEVERITIES:
                severity = "medium"
            findings.append(
                Finding(
                    id=str(rf.get("id", f"F{i}")),
                    category=category,
                    severity=severity,
                    description=str(rf.get("description", "")).strip(),
                    evidence=str(rf.get("evidence", "")).strip(),
                )
            )

        state.findings = findings
        return state


# --------------------------------------------------------------------------- #
# Agent 2: Skeptic — adversarially cross-examines each finding
# --------------------------------------------------------------------------- #

SKEPTIC_SYSTEM_PROMPT = """You are an adversarial senior reviewer. Another agent
already produced a list of findings about a codebase. Your job is to challenge
each finding, not rubber-stamp it. For every finding, decide:
- "confirmed": the evidence genuinely supports the claim as stated
- "downgraded": real issue, but severity is overstated or context softens it
- "rejected": evidence is too weak, speculative, or the description doesn't
  match the evidence given

Be skeptical by default. A finding only stays "confirmed" if the evidence
field actually demonstrates the described problem.

Respond with ONLY this JSON shape, nothing else:
{
  "reviews": [
    {
      "id": "F1",
      "verdict": "confirmed",
      "challenge_note": "one sentence explaining your reasoning",
      "revised_severity": "low|medium|high|critical"
    }
  ]
}
The "verdict" field must be exactly one of: confirmed, downgraded, rejected — pick ONE, never combine them.
Do not include markdown. Do not include explanations outside the JSON."""

VALID_VERDICTS = {"confirmed", "downgraded", "rejected"}


class SkepticAgent:
    label = "AGENT-2-SKEPTIC"

    def run(self, state: AuditState) -> AuditState:
        if not state.findings:
            return state

        findings_payload = [
            {
                "id": f.id,
                "category": f.category,
                "severity": f.severity,
                "description": f.description,
                "evidence": f.evidence,
            }
            for f in state.findings
        ]

        user_prompt = (
            "Cross-examine these findings from Agent 1. Challenge weak or "
            "overstated claims. Here is the findings list:\n\n"
            f"{json.dumps(findings_payload, indent=2)}"
        )
        result = call_model(SKEPTIC_SYSTEM_PROMPT, user_prompt, self.label)

        reviews = result.get("reviews", [])
        if not isinstance(reviews, list):
            reviews = []
        if not reviews and "verdict" in result:
            # Small model collapsed the schema and returned a single review
            # object at the top level instead of wrapping it in {"reviews": [...]}.
            reviews = [result]

        review_map = {}
        for r in reviews:
            if isinstance(r, dict) and "id" in r:
                review_map[str(r["id"])] = r

        for finding in state.findings:
            review = review_map.get(finding.id)
            if review:
                verdict = str(review.get("verdict", "")).strip().lower()
                if verdict not in VALID_VERDICTS:
                    # Small model returned something other than one of the three
                    # allowed values (e.g. echoed the "confirmed|downgraded|rejected"
                    # placeholder verbatim). Don't silently treat garbage as a pass.
                    finding.verdict = "downgraded"
                    finding.challenge_note = (
                        f"(skeptic returned unparseable verdict {review.get('verdict')!r}; "
                        f"flagged for manual review)"
                    )
                else:
                    finding.verdict = verdict
                    finding.challenge_note = str(review.get("challenge_note", "")).strip()
                revised = str(review.get("revised_severity", "")).strip().lower()
                if revised in SEVERITY_EMOJI:
                    finding.severity = revised
            else:
                # Model dropped this id in its response — fail safe rather than
                # silently trusting an unreviewed finding.
                finding.verdict = "downgraded"
                finding.challenge_note = "(not explicitly reviewed by skeptic agent)"

        return state


# --------------------------------------------------------------------------- #
# Agent 3: Synthesizer — writes the executive report
# --------------------------------------------------------------------------- #

SYNTHESIZER_SYSTEM_PROMPT = """You are writing an executive summary for a
non-technical stakeholder based on a code audit that has already been
fact-checked by a second reviewer. Only report findings whose verdict is
"confirmed" or "downgraded" — never mention "rejected" findings.

Respond with ONLY this JSON shape, nothing else:
{
  "executive_summary": "2-3 sentence plain-language summary of overall risk",
  "top_priority": "the single most urgent finding id and why, one sentence",
  "recommendation": "one sentence on the recommended immediate next step"
}
Do not include markdown. Do not include explanations outside the JSON."""


class SynthesizerAgent:
    label = "AGENT-3-SYNTHESIZER"

    def run(self, state: AuditState) -> AuditState:
        actionable = [f for f in state.findings if f.verdict != "rejected"]

        payload = [
            {
                "id": f.id,
                "category": f.category,
                "severity": f.severity,
                "description": f.description,
                "verdict": f.verdict,
                "challenge_note": f.challenge_note,
            }
            for f in actionable
        ]

        user_prompt = (
            "Here are the fact-checked findings. Write the executive summary "
            f"fields.\n\n{json.dumps(payload, indent=2)}"
        )
        result = call_model(SYNTHESIZER_SYSTEM_PROMPT, user_prompt, self.label)

        state.report_markdown = render_markdown_report(state, result, actionable)
        return state


# --------------------------------------------------------------------------- #
# Markdown rendering (deterministic, not model-generated -> always well-formed)
# --------------------------------------------------------------------------- #

SEVERITY_EMOJI = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🟢",
}

CATEGORY_LABEL = {
    "cost_leak": "💸 Cost Leak",
    "security": "🛡️ Security",
    "anti_pattern": "🧩 Anti-Pattern",
}


def render_markdown_report(
    state: AuditState, synth_result: dict[str, Any], actionable: list[Finding]
) -> str:
    lines: list[str] = []
    lines.append(f"# Audit Report — `{state.source_path}`")
    lines.append("")
    lines.append(f"_Generated fully offline by a 3-agent adversarial pipeline running `{MODEL_NAME}` on Ollama._")
    lines.append("")
    lines.append("## Executive Summary")
    lines.append("")
    lines.append(synth_result.get("executive_summary", "No summary generated."))
    lines.append("")
    lines.append(f"**Top priority:** {synth_result.get('top_priority', 'N/A')}")
    lines.append("")
    lines.append(f"**Recommended next step:** {synth_result.get('recommendation', 'N/A')}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Findings")
    lines.append("")

    if not actionable:
        lines.append("No findings survived adversarial review.")
    else:
        # Sort by severity, most severe first
        order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        for f in sorted(actionable, key=lambda x: order.get(x.severity, 4)):
            emoji = SEVERITY_EMOJI.get(f.severity, "⚪")
            cat = CATEGORY_LABEL.get(f.category, f.category)
            lines.append(f"### {emoji} [{f.id}] {cat} — {f.severity.upper()}")
            lines.append("")
            lines.append(f"**Issue:** {f.description}")
            lines.append("")
            lines.append(f"**Evidence:** `{f.evidence}`")
            lines.append("")
            lines.append(f"**Skeptic verdict:** `{f.verdict}` — {f.challenge_note}")
            lines.append("")

    rejected = [f for f in state.findings if f.verdict == "rejected"]
    if rejected:
        lines.append("---")
        lines.append("")
        lines.append(f"## Discarded Claims ({len(rejected)})")
        lines.append("")
        lines.append("_These were raised by the auditor agent but rejected by the skeptic agent as unsupported._")
        lines.append("")
        for f in rejected:
            lines.append(f"- **[{f.id}]** {f.description} — _{f.challenge_note}_")
        lines.append("")

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Pipeline orchestration
# --------------------------------------------------------------------------- #

def run_pipeline(source_path: str) -> AuditState:
    with open(source_path, "r", encoding="utf-8") as f:
        source_code = f.read()

    state = AuditState(source_path=source_path, source_code=source_code)

    pipeline = [AuditorAgent(), SkepticAgent(), SynthesizerAgent()]

    for agent in pipeline:
        t0 = time.time()
        print(f"  -> {agent.label} running...", file=sys.stderr)
        state = agent.run(state)
        elapsed = time.time() - t0
        print(f"  -> {agent.label} done in {elapsed:.2f}s", file=sys.stderr)

    return state

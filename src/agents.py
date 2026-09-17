"""
The three pipeline agents:

  1. AuditorAgent    -> scans raw source for cost leaks / security holes / anti-patterns
  2. SkepticAgent     -> cross-examines each finding, challenges weak evidence,
                          drops or downgrades false positives
  3. SynthesizerAgent -> writes the final executive markdown report

Each agent has one job and one JSON contract. Small models lose coherence
fast with multi-task prompts, so we never ask a single call to "find issues
AND grade severity AND critique them."
"""

from __future__ import annotations

import json

from src.models import AuditState, Finding
from src.ollama_client import call_model
from src.report_builder import SEVERITY_EMOJI, render_markdown_report

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

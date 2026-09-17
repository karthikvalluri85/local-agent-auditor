"""
Deterministic (non-LLM) rendering of the final audit report.

Formatting must never depend on model output, so this module only accepts
already-validated data from the pipeline and does plain string templating —
report structure stays well-formed regardless of what the small model
outputs upstream.
"""

from __future__ import annotations

from typing import Any

from src.config import MODEL_NAME
from src.models import AuditState, Finding

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

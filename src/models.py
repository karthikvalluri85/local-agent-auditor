"""
Data contracts passed between pipeline stages.

Kept intentionally flat/simple: small local models stay more coherent
working against a shallow schema than a deeply nested one, and these
dataclasses mirror the JSON shape each agent is prompted to return.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Finding:
    id: str
    category: str           # "cost_leak" | "security" | "anti_pattern"
    severity: str            # "low" | "medium" | "high" | "critical"
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

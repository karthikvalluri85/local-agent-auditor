"""
Pipeline orchestration: runs the three agents in sequence over a shared
AuditState.
"""

from __future__ import annotations

import sys
import time

from src.agents import AuditorAgent, SkepticAgent, SynthesizerAgent
from src.models import AuditState


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

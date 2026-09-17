#!/usr/bin/env python3
"""
main.py
-------
Entry point for the Local Adversarial Auditor PoC.

Usage:
    python main.py [path/to/file.py]

If no path is given, audits the bundled sample_target/billing_service.py.

Everything runs against a local Ollama model. No API keys. No network calls
except localhost:11434. See README.md for setup.
"""

import argparse
import os
import sys
import time

from src.config import MODEL_NAME, OLLAMA_HOST
from src.exceptions import InvalidModelResponseError, OllamaConnectionError
from src.pipeline import run_pipeline

# Report content includes emoji (severity/category markers). Some terminals
# (notably Windows consoles using a legacy codepage like cp1252) can't encode
# them, which would otherwise crash the final print with UnicodeEncodeError.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def check_ollama_reachable() -> bool:
    try:
        import ollama
        client = ollama.Client(host=OLLAMA_HOST)
        client.list()
        return True
    except Exception:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a fully local, offline 3-agent adversarial code/FinOps audit."
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=os.path.join("sample_target", "billing_service.py"),
        help="Path to the Python file to audit (default: bundled sample).",
    )
    parser.add_argument(
        "--out",
        default="audit_report.md",
        help="Path to write the markdown report (default: audit_report.md).",
    )
    args = parser.parse_args()

    if not os.path.exists(args.target):
        print(f"ERROR: target file not found: {args.target}", file=sys.stderr)
        return 1

    print(f"Model:  {MODEL_NAME}")
    print(f"Host:   {OLLAMA_HOST}")
    print(f"Target: {args.target}")
    print()

    if not check_ollama_reachable():
        print(
            f"ERROR: Could not reach Ollama at {OLLAMA_HOST}.\n"
            f"  1. Make sure Ollama is installed: https://ollama.com\n"
            f"  2. Start it:      ollama serve\n"
            f"  3. Pull the model: ollama pull {MODEL_NAME}\n",
            file=sys.stderr,
        )
        return 1

    print("Running 3-agent adversarial audit pipeline (fully offline)...\n", file=sys.stderr)
    t0 = time.time()

    try:
        state = run_pipeline(args.target)
    except (OllamaConnectionError, InvalidModelResponseError) as exc:
        print(f"\nPipeline failed: {exc}", file=sys.stderr)
        return 1

    elapsed = time.time() - t0

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(state.report_markdown)

    confirmed = sum(1 for x in state.findings if x.verdict == "confirmed")
    downgraded = sum(1 for x in state.findings if x.verdict == "downgraded")
    rejected = sum(1 for x in state.findings if x.verdict == "rejected")

    print(f"\nDone in {elapsed:.2f}s total.")
    print(f"  Findings: {len(state.findings)} raised -> "
          f"{confirmed} confirmed, {downgraded} downgraded, {rejected} rejected by skeptic")
    print(f"  Report written to: {args.out}")
    print()
    print("=" * 70)
    print(state.report_markdown)
    print("=" * 70)

    return 0


if __name__ == "__main__":
    sys.exit(main())

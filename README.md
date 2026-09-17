# 🔒 Local Adversarial Auditor

**A 3-agent adversarial code/FinOps audit pipeline that runs 100% offline on a laptop CPU, using a single 398MB open model. Zero API keys. Zero cloud cost. Zero data leaving your machine.**

Most "multi-agent" demos are toy research bots. This is a triad of agents that argue with each other over a real engineering problem — cost leaks, security holes, and anti-patterns in your python code( (the pattern's language-agnostic — this build's just tuned to one language so far) )  — and produce an executive-ready markdown report, entirely on local hardware.

```
Agent 1 (Auditor)  --raises findings-->  Agent 2 (Skeptic)  --cross-examines-->  Agent 3 (Synthesizer)
     "found 8 issues"                    "confirms 3, downgrades 2,                "here's the exec report"
                                           rejects 3 as unsupported"
```

## Why this isn't just a toy

The interesting part isn't "an LLM found a bug." It's that **Agent 2 is adversarial by design** — it doesn't rubber-stamp Agent 1's findings, it challenges the evidence and actively rejects weak claims before they ever reach the report. That's the pattern enterprises actually need from agentic tooling: a built-in check against the #1 failure mode of single-agent LLM audits — confident false positives.

And it runs entirely on a CPU-only laptop, for **$0** — expect roughly **30–90 seconds** end-to-end per file (3 sequential model calls at 0.5B params), depending on your CPU and how many findings the Auditor raises. That's still faster than waiting on a human reviewer, with zero API cost.

---

## 1. Model choice: why `qwen2.5:0.5b` — and not the model with the most downloads

This repo is pinned to a **single model, hardcoded, no config flags**: `qwen2.5:0.5b` — 398MB, no GPU required, runs on essentially any laptop from the last decade.

The obvious pick for "smallest + most popular" would be **Llama 3.2** — its family leads Ollama's library with 83.5M pulls, well ahead of Qwen2.5's 40.3M. It was deliberately *not* chosen, for a concrete, documented reason:

> Ollama tracking issue **[#13519](https://github.com/ollama/ollama/issues/13519)**: `llama3.2:3b` outputs tool calls as loose JSON text inside the `content` field instead of the structured `tool_calls` field the API contract expects — i.e., it doesn't reliably signal "this is a function call" the way the tool-calling protocol requires, breaking any pipeline (like this one) that depends on that structure being consistent.

That's precisely the failure mode this PoC is built to avoid. A demo about reliable agent-to-agent handoffs can't be built on a model with an open bug in exactly that mechanism.

`qwen2.5:0.5b` is the smallest model in the Qwen2.5 line — the second-most-downloaded tool-calling-capable model family on Ollama (40.3M pulls) — and Qwen2.5's instruct models were trained specifically on structured/function-calling output, so they hold the `tool_calls` contract consistently even at 0.5B parameters. It trades some raw popularity for the one property this pipeline actually depends on: predictable, machine-parseable output.

| Model | Params | Size | Pulls (Ollama) | Reliable native `tool_calls`? |
|---|---|---|---|---|
| Llama 3.2 | 1B–3B | ~1.3–2GB | 83.5M (highest) | ❌ Documented bug — leaks JSON into `content` |
| **Qwen2.5** | **0.5B** | **398MB (smallest)** | **40.3M (2nd highest)** | ✅ Yes — this repo's pinned model |

We still don't trust the model blindly — see [`src/ollama_client.py`](src/ollama_client.py)'s `safe_json_parse()`: every response is passed through a repair layer that strips markdown fences, fixes trailing commas and quote styles, and isolates the JSON object from any wrapped prose, with a corrective retry loop (3 attempts) if it's still unparseable. At 0.5B parameters the model will occasionally drift on formatting even though the underlying tool-calling mechanism is sound — the pipeline is built to not care.

## 2. Architecture

```
local-agent-auditor/
├── main.py                    # CLI entrypoint
├── src/
│   ├── config.py               # MODEL_NAME, OLLAMA_HOST, retry/timeout settings
│   ├── exceptions.py           # OllamaConnectionError, InvalidModelResponseError
│   ├── models.py                # Finding / AuditState data contracts
│   ├── ollama_client.py         # Ollama SDK calls + safe_json_parse() repair layer
│   ├── agents.py                 # AuditorAgent, SkepticAgent, SynthesizerAgent
│   ├── report_builder.py         # Deterministic markdown rendering (never model-generated)
│   └── pipeline.py               # Runs the 3 agents in sequence over shared state
├── requirements.txt            # Just the ollama SDK
├── setup.sh                    # One-command pull-model + install + run
├── sample_target/
│   └── billing_service.py       # Deliberately flawed sample code to audit
├── LICENSE
├── CONTRIBUTING.md
└── README.md
```

- **Agent 1 — Auditor** (`src/agents.py`): reads raw source, returns a structured list of findings (category, severity, evidence).
- **Agent 2 — Skeptic** (`src/agents.py`): receives *only* the findings (never the raw code again — this forces it to argue from evidence, not re-scan), returns a verdict per finding: `confirmed`, `downgraded`, or `rejected`, with a one-line challenge note.
- **Agent 3 — Synthesizer** (`src/agents.py`): receives only the surviving (non-rejected) findings and writes the executive summary fields. The markdown report itself is deterministically templated in `src/report_builder.py` — never model-generated — so formatting is always clean regardless of what the small model outputs.

Each agent has **one job and one JSON contract**. This is deliberate: small models lose coherence fast when asked to do multiple things in one call ("find issues AND grade severity AND write prose"). Splitting responsibilities across 3 narrow calls is what makes a 3B model behave like a much larger one.

Model I/O, agent logic, data contracts, and report rendering live in their own modules under `src/` rather than one flat file, and connection/parse failures raise domain-specific exceptions (`src/exceptions.py`) instead of generic `RuntimeError`/`ValueError` — so `main.py` can tell "Ollama's unreachable" apart from "the model won't produce valid JSON" without string-matching an error message.

## 3. Quickstart (one command)

```bash
git clone https://github.com/<you>/local-agent-auditor.git
cd local-agent-auditor
./setup.sh
```

That pulls `qwen2.5:0.5b` via Ollama, installs the one Python dependency, and runs the audit against the bundled sample file. Or manually:

```bash
ollama pull qwen2.5:0.5b
pip install -r requirements.txt
python3 main.py sample_target/billing_service.py
```

**Requirements:** [Ollama](https://ollama.com) installed, ~400MB free disk for the model, Python 3.9+. No GPU required — this runs fine on CPU.

### Audit your own code

```bash
python3 main.py path/to/your_file.py
```

Output is written to `audit_report.md` and also printed to stdout.

## 4. What it catches (see `sample_target/billing_service.py`)

The bundled sample is deliberately seeded with real-world patterns:

- 🔴 Hardcoded AWS credentials + DB password
- 🔴 SQL injection via string concatenation
- 🟠 Oversized EC2 instance (`x1e.32xlarge`) for a lightweight cron job
- 🟠 N+1 API call pattern instead of batching
- 🟠 Unthrottled polling loop against a paid third-party API
- 🟡 Debug logging that leaks secrets to log aggregators

Run it yourself and watch Agent 2 argue about which of these are truly "critical" vs. context-dependent.

## 5. Honest limitations (read before you demo this to your CFO)

- This is a **PoC**, not a production static-analysis tool. It doesn't parse an AST — it's LLM pattern-matching against source text, so it can miss issues a real linter/SAST tool would catch, and can occasionally hallucinate a line number or function name.
- At 0.5B parameters, this is one of the smallest models that will do this job at all — it trades reasoning depth for size and speed. The adversarial Agent 2 step meaningfully reduces false positives but doesn't eliminate them, and on more subtle or ambiguous code, findings will be shallower than a 3B+ model would produce. Treat this as a **fast first-pass triage**, not a replacement for a real security/FinOps review. If you need materially better judgment quality and can spare ~2GB instead of 400MB, swap `MODEL_NAME` in `src/config.py` for `qwen2.5:3b` — same code, same contracts, no other changes needed.
- Currently audits one file at a time by design (keeps context small enough for reliable small-model reasoning). Multi-file/repo-wide auditing is a natural next step but needs a chunking strategy to stay within this model's effective context reliability.

## License

MIT — do whatever you want with it.

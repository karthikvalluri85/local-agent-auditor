# Contributing to Local Adversarial Auditor

Thank you for your interest in contributing!

## Local Development Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/karthikvalluri85/local-agent-auditor.git
   cd local-agent-auditor
   ```

2. **Create a Python virtual environment**
   ```bash
   python -m venv venv
   venv\Scripts\activate  # Windows
   source venv/bin/activate  # macOS/Linux
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Pull the model and start Ollama**
   ```bash
   ollama pull qwen2.5:0.5b
   ollama serve
   ```

5. **Run the auditor**
   ```bash
   python main.py sample_target/billing_service.py
   ```

## Development Guidelines

- **Code Style:** Follow PEP 8
- **Type Hints:** Encouraged but not required for this PoC
- **Module Boundaries:** Keep the `src/` separation of concerns — model I/O
  (`ollama_client.py`), agent logic (`agents.py`), data contracts
  (`models.py`), and report rendering (`report_builder.py`) stay in their own
  modules rather than growing back into one file.
- **Domain Errors:** Raise from `src/exceptions.py` (or add a new one there)
  instead of a bare `RuntimeError`/`ValueError`, so `main.py` can keep
  distinguishing failure modes without parsing error strings.
- **No Secrets:** Never commit credentials — `sample_target/billing_service.py`
  intentionally uses AWS's public example credentials for this exact reason.

## Submitting Changes

1. Create a feature branch from `main`
2. Make your changes and test locally (`python main.py <file>` against a
   running Ollama instance)
3. Submit a pull request with:
   - Clear description of changes
   - Any new dependencies added to `requirements.txt`
   - What you ran to verify it (this project has no automated test suite yet)

## Reporting Issues

Use GitHub Issues to report bugs or suggest features. Include:
- Ollama version and OS
- The model you ran against (`qwen2.5:0.5b` or a swapped-in alternative)
- Steps to reproduce and the raw pipeline output

## Questions?

Refer to the main [README.md](README.md) for project overview, architecture,
and the model's known limitations.

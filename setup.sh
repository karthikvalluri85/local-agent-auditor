#!/usr/bin/env bash
# One-command setup + run for the Local Adversarial Auditor PoC.
# Usage: ./setup.sh [path/to/file.py]

set -e

MODEL="qwen2.5:0.5b"

if ! command -v ollama &> /dev/null; then
    echo "Ollama is not installed. Install it from https://ollama.com and re-run this script."
    exit 1
fi

echo "Pulling $MODEL (skips automatically if already present)..."
ollama pull "$MODEL"

echo "Installing Python dependencies..."
pip install -q -r requirements.txt

echo "Running the audit..."
python3 main.py "$@"

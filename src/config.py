"""
Tunable settings for the audit pipeline.

`OLLAMA_HOST` can be overridden via environment variable for pointing at a
non-default Ollama instance. The rest are fixed by design (see README,
"Model choice") rather than exposed as runtime flags, to keep the pipeline's
behavior predictable and reproducible across runs.
"""

import os

MODEL_NAME = "qwen2.5:0.5b"
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
MAX_RETRIES = 3
REQUEST_TIMEOUT_S = 60

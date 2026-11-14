"""
Default RAG knobs shared with ``smarthris.config.Settings``.

Authoritative runtime values come from environment-backed Settings.
This module only holds safe development defaults for future RAG modules.
"""

DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "llama3.2"
DEFAULT_OLLAMA_REQUEST_TIMEOUT_SECONDS = 120.0
DEFAULT_EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_RAG_DOCUMENT_DIR = "data/documents"
DEFAULT_RAG_INDEX_DIR = "data/indexes"
DEFAULT_RAG_CHUNK_SIZE = 800
DEFAULT_RAG_CHUNK_OVERLAP = 120
DEFAULT_RAG_TOP_K = 5
DEFAULT_RAG_RETRIEVAL_CANDIDATES = 60
DEFAULT_RAG_RRF_CONSTANT = 60

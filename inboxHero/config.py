"""Central configuration for inboxHero.

All model-provider settings are read from environment variables (loaded from a
local .env if present). Nothing here is hardcoded to a secret. If no API key is
configured the system runs fully offline on deterministic heuristics, so every
command in the manifest runs on a clean checkout without credentials.
"""
import os
from pathlib import Path

# --- Load .env if python-dotenv is available (optional dependency) ----------
try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional
    pass

# --- Paths ------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
INBOX_PATH = Path(os.getenv("INBOXHERO_INBOX", ROOT / "inbox.json"))
OUTBOX_DIR = Path(os.getenv("INBOXHERO_OUTBOX", ROOT / "outbox"))
STATE_DIR = Path(os.getenv("INBOXHERO_STATE", ROOT / "state"))
TRACE_PATH = Path(os.getenv("INBOXHERO_TRACE", ROOT / "trace.jsonl"))
PREFS_PATH = STATE_DIR / "prefs.json"
ACTION_LOG_PATH = STATE_DIR / "action_log.json"
DASHBOARD_HTML = ROOT / "dashboard.html"
DASHBOARD_JSON = ROOT / "dashboard.json"

# --- Identity ---------------------------------------------------------------
# The inbox belongs to one person; that address is the trust anchor.
OWNER = os.getenv("INBOXHERO_OWNER", "sam@paperjet.io")
OWNER_DOMAIN = OWNER.split("@")[-1]

# --- Deterministic clock ----------------------------------------------------
# A fixed "run day" so demos (follow-up ages, commitment ordering) are
# reproducible regardless of the wall clock. Override with INBOXHERO_NOW.
REFERENCE_NOW = os.getenv("INBOXHERO_NOW", "2026-09-12T09:00:00")

# --- Model provider (all optional) ------------------------------------------
# Supported providers: "offline" (default, no network), "gemini", "openai-compatible".
LLM_PROVIDER = os.getenv("INBOXHERO_LLM_PROVIDER", "offline").lower()
LLM_MODEL = os.getenv("INBOXHERO_LLM_MODEL", "gemini-1.5-flash")
LLM_API_KEY = os.getenv("INBOXHERO_LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("INBOXHERO_LLM_BASE_URL", "")  # for local Ollama / openai-compatible
# Politeness for free-tier rate limits: seconds to sleep between model calls.
LLM_MIN_INTERVAL = float(os.getenv("INBOXHERO_LLM_MIN_INTERVAL", "4.0"))
LLM_MAX_RETRIES = int(os.getenv("INBOXHERO_LLM_MAX_RETRIES", "5"))


def ensure_dirs() -> None:
    OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)

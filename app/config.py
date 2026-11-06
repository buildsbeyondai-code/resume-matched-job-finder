"""Configuration: environment, allow-listed hosts, defaults. Secrets come only from env/.env."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"

HOST = "127.0.0.1"  # never bind to other interfaces (security requirement)
PORT = int(os.environ.get("RMJF_PORT", "5070"))
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
SOURCE_TIMEOUT_S = 10
MAX_RETRIES = 2
CACHE_TTL_S = 3600
PRUNE_DAYS = 60
PAGE_SIZE = 50
# Apply assistant: show the browser window (set RMJF_APPLY_HEADLESS=1 only for automated tests).
APPLY_HEADLESS = os.environ.get("RMJF_APPLY_HEADLESS", "") == "1"
# Tests only: accept http://127.0.0.1 / http://localhost job links (local fake career sites).
ALLOW_LOCAL_HTTP = os.environ.get("RMJF_ALLOW_LOCAL_HTTP", "") == "1"
APPLICATIONS_DIR = Path(os.environ.get("RMJF_APPLICATIONS_DIR", str(ROOT / "data" / "applications")))
BROWSER_PROFILE = Path(os.environ.get("RMJF_BROWSER_PROFILE", str(ROOT / "data" / "browser-profile")))

# Outbound requests are restricted to these HTTPS hosts (M3, security).
ALLOWED_HOSTS = {
    "boards-api.greenhouse.io",
    "api.lever.co",
    "api.smartrecruiters.com",
    "api.adzuna.com",
    # company careers boards, used to find an aggregator job's original posting (app/direct.py)
    "api.ashbyhq.com",
    "apply.workable.com",
}

# Job portals used by the apply browser only (never by sources.http_get): the last-resort application channel
# when a company has no application on its own website (app/portals.py). See docs/DECISIONS.md D29.
BROWSER_PORTAL_HOSTS = {"www.naukri.com", "www.linkedin.com"}
PORTAL_PAUSE_RANGE = (5, 600)  # seconds between portal applications (rate limit)

DEFAULTS = {
    "fallback_portals": ["naukri", "linkedin"],
    "portal_pause_s": 20,
    "ai_answers": True,  # Claude Opus 5.5 answers questions resume_details.md can't (app/ai.py, D54)
    "countries": ["India", "United States", "United Kingdom", "Germany"],
    "mode": "remote",
    "max_age_days": 30,
    "min_score": 40,
    "seniority": "mid",
    "target_companies": ["Microsoft", "Google", "Amazon", "IBM", "Accenture", "SAP",
                         "Deloitte", "Infosys", "TCS", "Oracle"],
}

# Default ATS boards. These are public, documented job-board APIs. Board tokens are editable in
# Settings; the listed MNCs mostly run private career sites, so they are reached through the
# licensed aggregator (Adzuna) with a company filter. See docs/DECISIONS.md D5.
DEFAULT_SOURCES = [
    {"kind": "adzuna", "token": "", "label": "Adzuna (licensed aggregator)", "enabled": 1},
    {"kind": "smartrecruiters", "token": "BoschGroup", "label": "Bosch (SmartRecruiters)", "enabled": 1},
    {"kind": "smartrecruiters", "token": "Visa", "label": "Visa (SmartRecruiters)", "enabled": 1},
    {"kind": "greenhouse", "token": "cloudflare", "label": "Cloudflare (Greenhouse)", "enabled": 1},
    {"kind": "lever", "token": "palantir", "label": "Palantir (Lever)", "enabled": 1},
]


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Minimal .env loader (KEY=VALUE lines). Existing env vars win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def db_path() -> Path:
    return Path(os.environ.get("RMJF_DB", str(ROOT / "data" / "jobfinder.sqlite3")))


def adzuna_keys():
    return os.environ.get("ADZUNA_APP_ID", ""), os.environ.get("ADZUNA_APP_KEY", "")

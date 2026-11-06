"""Job source adapters for official/public APIs only (M3). Each adapter builds request URLs and normalises
responses into a common posting dict. All network access goes through `http_get`, which enforces the
HTTPS allow-list, timeout, retries with exponential backoff and the 1-hour cache."""
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from . import config


class SourceError(Exception):
    pass


class NotAllowed(SourceError):
    pass


def check_allowed(url: str) -> None:
    u = urllib.parse.urlparse(url)
    if u.scheme != "https" or u.hostname not in config.ALLOWED_HOSTS:
        raise NotAllowed(f"Blocked request to non allow-listed host: {u.hostname}")


def _urllib_fetch(url: str, timeout: float) -> str:
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                               "User-Agent": "ResumeMatchedJobFinder/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (allow-listed https only)
        return r.read(20 * 1024 * 1024).decode("utf-8", "replace")


# Tests replace this with a fake (mocked network). Signature: (url, timeout) -> str body.
FETCH = _urllib_fetch


def http_get(url: str, db=None, cancel=None, sleep=None, fresh=False) -> str:
    """GET an allow-listed URL with retries. `fresh` skips the cached copy (the reply is still cached)."""
    sleep = sleep or time.sleep
    check_allowed(url)
    if db is not None and not fresh:
        cached = db.cache_get(url)
        if cached is not None:
            return cached
    last = None
    for attempt in range(config.MAX_RETRIES + 1):
        if cancel is not None and cancel.is_set():
            raise SourceError("Cancelled")
        try:
            body = FETCH(url, config.SOURCE_TIMEOUT_S)
            if db is not None:
                db.cache_put(url, body)
            return body
        except urllib.error.HTTPError as e:
            last = e
            if e.code < 500:
                raise SourceError(f"HTTP {e.code}") from e
        except (TimeoutError, urllib.error.URLError, OSError) as e:
            last = e
        if attempt < config.MAX_RETRIES:
            sleep(0.5 * (2 ** attempt))
    msg = "timed out" if isinstance(last, TimeoutError) or "timed out" in str(last) else str(last)
    raise SourceError(f"Unavailable after {config.MAX_RETRIES + 1} attempts ({msg})")


def strip_html(s: str) -> str:
    """Posting HTML is reduced to plain text; the UI never renders posting HTML (no script injection)."""
    s = html.unescape(s or "")
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def _iso(ts):
    if ts is None or ts == "":
        return ""
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts / 1000 if ts > 1e11 else ts, tz=timezone.utc).strftime("%Y-%m-%d")
    return str(ts)[:10]


def _posting(source, sid, title, company, location, url, posted="", desc="", country="", mode="",
             salary_min=None, salary_max=None, currency=""):
    return {"source": source, "source_id": str(sid), "title": (title or "").strip(),
            "company": (company or "").strip(), "location": (location or "").strip(), "url": url or "",
            "posted": _iso(posted), "description": strip_html(desc)[:6000], "country_hint": country or "",
            "mode_hint": mode or "", "salary_min": salary_min, "salary_max": salary_max, "currency": currency}


class Greenhouse:
    kind = "greenhouse"

    def urls(self, src, params):
        return [f"https://boards-api.greenhouse.io/v1/boards/{urllib.parse.quote(src['token'])}/jobs?content=true"]

    def parse(self, body, src):
        data = json.loads(body)
        company = src["label"].split(" (")[0]
        return [_posting("Greenhouse", j.get("id"), j.get("title"), company,
                         (j.get("location") or {}).get("name", ""), j.get("absolute_url"),
                         j.get("updated_at"), j.get("content", "")) for j in data.get("jobs", [])]


class Lever:
    kind = "lever"

    def urls(self, src, params):
        return [f"https://api.lever.co/v0/postings/{urllib.parse.quote(src['token'])}?mode=json"]

    def parse(self, body, src):
        company = src["label"].split(" (")[0]
        out = []
        for j in json.loads(body):
            cat = j.get("categories") or {}
            sal = j.get("salaryRange") or {}
            out.append(_posting("Lever", j.get("id"), j.get("text"), company, cat.get("location", ""),
                                j.get("hostedUrl"), j.get("createdAt"),
                                j.get("descriptionPlain") or j.get("description", ""), j.get("country", ""),
                                j.get("workplaceType", ""), sal.get("min"), sal.get("max"), sal.get("currency", "")))
        return out


class SmartRecruiters:
    kind = "smartrecruiters"

    def urls(self, src, params):
        q = urllib.parse.urlencode({"limit": 100, **({"q": params["keywords"]} if params.get("keywords") else {})})
        return [f"https://api.smartrecruiters.com/v1/companies/{urllib.parse.quote(src['token'])}/postings?{q}"]

    def parse(self, body, src):
        out = []
        for j in json.loads(body).get("content", []):
            loc = j.get("location") or {}
            mode = "remote" if loc.get("remote") else ("hybrid" if loc.get("hybrid") else "")
            where = ", ".join(x for x in [loc.get("city"), loc.get("region"), (loc.get("country") or "").upper()] if x)
            dept = (j.get("department") or {}).get("label", "")
            func = (j.get("function") or {}).get("label", "")
            out.append(_posting("SmartRecruiters", j.get("id"), j.get("name"),
                                (j.get("company") or {}).get("name") or src["label"].split(" (")[0], where,
                                f"https://jobs.smartrecruiters.com/{src['token']}/{j.get('id')}",
                                j.get("releasedDate"), f"{dept} {func}", loc.get("country", ""), mode))
        return out


ADZUNA_CC = {"in": "in", "us": "us", "gb": "gb", "de": "de", "fr": "fr", "nl": "nl", "ca": "ca", "au": "au",
             "sg": "sg", "pl": "pl", "it": "it", "es": "es", "at": "at", "ch": "ch", "br": "br", "mx": "mx"}


class Adzuna:
    """Licensed aggregator; credentials come only from ADZUNA_APP_ID / ADZUNA_APP_KEY env vars."""
    kind = "adzuna"

    def configured(self):
        a, k = config.adzuna_keys()
        return bool(a and k)

    def urls(self, src, params):
        app_id, key = config.adzuna_keys()
        if not (app_id and key):
            raise SourceError("Not configured: set ADZUNA_APP_ID and ADZUNA_APP_KEY in .env")
        from .matching import country_code
        ccs = [country_code(c) for c in (params.get("countries") or [])] or ["us"]
        # Adzuna's "what" requires every word, so a repeated or extra term shrinks results sharply
        # ("AI AI remote" -> 4 hits, "AI remote" -> 162). Use each term once; skills only widen the
        # search (what_or) when no keywords were typed.
        def uniq(words):
            seen, out = set(), []
            for w in words:
                if w and w.lower() not in seen:
                    seen.add(w.lower()); out.append(w)
            return out
        kw = uniq((params.get("keywords") or "").split())
        sk = [s for s in uniq((params.get("skills") or [])[:5]) if s.lower() not in {k.lower() for k in kw}]
        if params.get("mode") == "remote" and "remote" not in {k.lower() for k in kw}:
            kw.append("remote")
        urls = []
        for cc in ccs:
            if cc not in ADZUNA_CC:
                continue
            q = {"app_id": app_id, "app_key": key, "results_per_page": 50, "content-type": "application/json",
                 "max_days_old": params.get("max_age_days", 30)}
            if kw:
                q["what"] = " ".join(kw)
            if sk and not params.get("keywords"):
                q["what_or"] = " ".join(sk)
            if params.get("companies"):
                q["company"] = params["companies"][0] if len(params["companies"]) == 1 else None
                q = {k: v for k, v in q.items() if v is not None}
            for page in (1, 2):  # up to 100 postings per country
                urls.append(f"https://api.adzuna.com/v1/api/jobs/{cc}/search/{page}?{urllib.parse.urlencode(q)}")
        return urls

    def parse(self, body, src):
        out = []
        for j in json.loads(body).get("results", []):
            area = (j.get("location") or {}).get("area") or []
            out.append(_posting("Adzuna", j.get("id"), j.get("title"), (j.get("company") or {}).get("display_name"),
                                (j.get("location") or {}).get("display_name", ""), j.get("redirect_url"),
                                j.get("created"), j.get("description", ""), area[0] if area else "", "",
                                j.get("salary_min"), j.get("salary_max"), ""))
        return out


ADAPTERS = {a.kind: a for a in (Greenhouse(), Lever(), SmartRecruiters(), Adzuna())}


def redact(url: str) -> str:
    """Remove credentials from URLs before logging or returning to the UI."""
    return re.sub(r"(app_key|app_id)=[^&]+", r"\1=***", url)

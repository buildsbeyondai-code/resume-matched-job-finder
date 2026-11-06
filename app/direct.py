"""Find a job's original posting on the employer's own careers board, so applications go to the company
directly instead of through an aggregator (Adzuna).

The aggregator's API only gives its own details page. This module looks the company up on the public,
documented job-board APIs that most careers sites run on (Greenhouse, Lever, SmartRecruiters, Ashby,
Workable) and matches the job title. All requests go through sources.http_get (HTTPS allow-list).
"""
import json
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from . import sources

AGGREGATOR_HOSTS = ("adzuna.",)
SUFFIXES = r"\b(pvt|private|ltd|limited|llc|l\.l\.c|inc|incorporated|corp|corporation|co|company|gmbh|bv|b\.v|" \
           r"plc|sa|ag|group|holdings|technologies|technology|tech|solutions|services|consulting|consultancy|" \
           r"software|labs|india|global|international)\b\.?"
TITLE_NOISE = r"\(.*?\)|\[.*?\]|\b(100%|fully|remote|hybrid|on-?site|onsite|wfh|work from home|india|urgent|" \
              r"hiring|immediate joiner|contract|full[- ]time|part[- ]time)\b"


def is_aggregator(url: str) -> bool:
    host = urlparse(url or "").hostname or ""
    return any(a in host for a in AGGREGATOR_HOSTS)


def slugs(company: str):
    """Likely board names for a company: 'Allshore Talent Pvt Ltd' -> allshoretalent, allshore-talent, allshore."""
    base = re.sub(r"&", " and ", (company or "").lower())
    base = re.sub(SUFFIXES, " ", base)
    words = re.findall(r"[a-z0-9]+", base)
    if not words:
        return []
    out = ["".join(words), "-".join(words)]
    if len(words) > 1 and len(words[0]) > 3:
        out.append(words[0])
    return list(dict.fromkeys(s for s in out if len(s) >= 2))[:3]


def _title_words(t):
    t = re.sub(TITLE_NOISE, " ", (t or "").lower())
    return [w for w in re.findall(r"[a-z0-9+#]+", t) if w not in {"and", "the", "of", "for", "a", "an", "to", "in"}]


def title_match(a: str, b: str) -> float:
    wa, wb = set(_title_words(a)), set(_title_words(b))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


# Each board: (name, url builder, parser -> [(title, url)])
def _gh(slug, title):
    return f"https://boards-api.greenhouse.io/v1/boards/{urllib.parse.quote(slug)}/jobs"


def _gh_parse(body, slug):
    return [(j.get("title", ""), j.get("absolute_url", "")) for j in json.loads(body).get("jobs", [])]


def _lv(slug, title):
    return f"https://api.lever.co/v0/postings/{urllib.parse.quote(slug)}?mode=json"


def _lv_parse(body, slug):
    d = json.loads(body)
    return [(j.get("text", ""), j.get("hostedUrl", "")) for j in d] if isinstance(d, list) else []


def _sr(slug, title):
    q = urllib.parse.urlencode({"q": " ".join(_title_words(title)[:6]), "limit": 100})
    return f"https://api.smartrecruiters.com/v1/companies/{urllib.parse.quote(slug)}/postings?{q}"


def _sr_parse(body, slug):
    return [(j.get("name", ""), f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}")
            for j in json.loads(body).get("content", [])]


def _ash(slug, title):
    return f"https://api.ashbyhq.com/posting-api/job-board/{urllib.parse.quote(slug)}"


def _ash_parse(body, slug):
    return [(j.get("title", ""), j.get("jobUrl", "")) for j in json.loads(body).get("jobs", [])]


def _wk(slug, title):
    return f"https://apply.workable.com/api/v1/widget/accounts/{urllib.parse.quote(slug)}"


def _wk_parse(body, slug):
    return [(j.get("title", ""), j.get("url") or j.get("shortlink", "")) for j in json.loads(body).get("jobs", [])]


BOARDS = [("Greenhouse", _gh, _gh_parse), ("Lever", _lv, _lv_parse), ("SmartRecruiters", _sr, _sr_parse),
          ("Ashby", _ash, _ash_parse), ("Workable", _wk, _wk_parse)]


def find_on_company_board(company: str, title: str, min_score: float = 0.6):
    """Return {"url", "board", "title", "score"} for the best matching posting, or None."""
    tasks = [(b, s) for s in slugs(company) for b in BOARDS]
    if not tasks or not title:
        return None

    def run(task):
        (name, build, parse), slug = task
        try:
            items = parse(sources.http_get(build(slug, title)), slug)
        except Exception:  # noqa: BLE001 - unknown board names return 404; that's the common case
            return None
        best = max(((title_match(title, t), t, u) for t, u in items if u.startswith("https://")), default=None)
        if best and best[0] >= min_score:
            return {"score": round(best[0], 2), "title": best[1], "url": best[2], "board": name}
        return None

    with ThreadPoolExecutor(max_workers=8) as ex:
        hits = [h for h in ex.map(run, tasks) if h]
    return max(hits, key=lambda h: h["score"]) if hits else None


def careers_search_url(company: str, title: str) -> str:
    return "https://duckduckgo.com/?" + urllib.parse.urlencode(
        {"q": f"{company} careers {' '.join(_title_words(title)[:6])} apply"})

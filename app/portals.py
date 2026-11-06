"""Job portals used when a company has no application on its own website: Naukri and LinkedIn.

The job is looked up with the portal's own search, in the app's browser where the user is signed in. A result
counts only when the company matches and the title is close; the application then runs on that portal's job
page (Naukri "Apply" with its question drawer, LinkedIn "Easy Apply"). Both portals' terms restrict automated
use, so they are opt-in (Settings), used only as the last resort, one job at a time, with a pause in between.
The portals are reached only by the browser; their hosts are listed in config.BROWSER_PORTAL_HOSTS.
"""
import re
import urllib.parse
from urllib.parse import urlparse

from . import config, direct


def _naukri_search(base, title, company):
    words = " ".join(direct._title_words(title))
    slug = re.sub(r"[^a-z0-9]+", "-", words).strip("-") or "jobs"
    return f"{base}/{slug}-jobs?" + urllib.parse.urlencode({"k": f"{words}, {company}"})


def _linkedin_search(base, title, company):
    return f"{base}/jobs/search/?" + urllib.parse.urlencode(
        {"keywords": f"{' '.join(direct._title_words(title))} {company}"})


PORTALS = {
    "naukri": {"name": "Naukri", "host": "naukri.com", "base": "https://www.naukri.com", "link_re": r"/job-listings-",
               "search": _naukri_search, "signin": "/nlogin/login", "cookie": "nauk_at"},
    "linkedin": {"name": "LinkedIn", "host": "linkedin.com", "base": "https://www.linkedin.com", "link_re": r"/jobs/view/",
                 "search": _linkedin_search, "signin": "/login", "cookie": "li_at"},
}
DEFAULT_ORDER = ["naukri", "linkedin"]
MIN_TITLE = 0.6  # title similarity a portal result needs (company must match too)

RESULTS_JS = r"""
(pat) => {
  const re = new RegExp(pat); const seen = new Set(); const out = [];
  for (const a of document.querySelectorAll('a[href]')) {
    const base = a.href.split('?')[0];
    if (!re.test(a.href) || seen.has(base)) continue;
    seen.add(base);
    const card = a.closest('li, article, [class*="tuple"], [class*="card"]') || a.parentElement;
    out.push({ href: a.href, title: (a.innerText || '').replace(/\s+/g, ' ').trim(),
               card: (card.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 600) });
  }
  return out.slice(0, 60);
}
"""


def name(key):
    return PORTALS[key]["name"]


def search_url(key, title, company):
    p = PORTALS[key]
    return p["search"](p["base"], title or "", company or "")


def signin_url(key):
    p = PORTALS[key]
    return p["base"] + p["signin"]


def portal_of(url):
    """The portal key for a URL on a portal's host, else None."""
    host = urlparse(url or "").hostname or ""
    for key, p in PORTALS.items():
        if host == p["host"] or host.endswith("." + p["host"]):
            return key
    return None


def usable(key):
    """In test mode (config.ALLOW_LOCAL_HTTP) a portal is used only when it points at a local fake, so an
    automated test can never reach the real Naukri or LinkedIn."""
    return not config.ALLOW_LOCAL_HTTP or urlparse(PORTALS[key]["base"]).hostname in ("127.0.0.1", "localhost")


def enabled(setting):
    """Portal keys from the user's setting, in order, ignoring unknown names."""
    return [k for k in (setting if isinstance(setting, list) else DEFAULT_ORDER) if k in PORTALS and usable(k)]


def signed_in(cookies):
    """{portal: True/False} from the browser profile's cookies (the portal's session cookie is present)."""
    have = {(c.get("name"), portal_of("https://" + (c.get("domain") or "").lstrip("."))) for c in cookies}
    return {k: (p["cookie"], k) in have for k, p in PORTALS.items()}


def _norm(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _company_core(company):
    return " ".join(direct.slugs(company)[:1]) or _norm(company)


def age_days(card):
    """How old a result card says the posting is (days); unknown ages sort last."""
    t = (card or "").lower()
    if re.search(r"\b(just now|today|few hours|\d+\s*(hour|hr|minute|min)s?\s*ago)\b", t):
        return 0
    m = re.search(r"(\d+)\+?\s*(day|week|month)s?\s*ago", t)
    if not m:
        return 9999
    return int(m.group(1)) * {"day": 1, "week": 7, "month": 30}[m.group(2)]


def best_result(results, title, company, location="", min_title=MIN_TITLE):
    """The result whose card names the company and whose title is close enough (>= min_title). When several
    match, the one in the job's location wins, then the newest posting, then the closest title."""
    core = _company_core(company)
    city = _norm(re.split(r"[,/(]", location or "")[0])
    best = None
    for r in results:
        card = _norm(r.get("card"))
        if not core or core not in card:
            continue
        score = direct.title_match(title, r.get("title", ""))
        if score < min_title:
            continue
        rank = (bool(city) and city in card, -age_days(r.get("card")), score)
        if best is None or rank > best[0]:
            best = (rank, r)
    return best[1] if best else None

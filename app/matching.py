"""Country/work-mode normalisation, filtering, de-duplication and the 0-100 match score (all local)."""
import hashlib
import re

from .skills import find_skills

COUNTRIES = {
    "in": ["india", "bangalore", "bengaluru", "hyderabad", "pune", "chennai", "mumbai", "delhi", "gurgaon",
           "gurugram", "noida", "kolkata", "ind"],
    "us": ["united states", "usa", "u.s.", "us", "america", "new york", "california", "seattle", "austin",
           "san francisco", "texas", "boston", "chicago"],
    "gb": ["united kingdom", "uk", "gb", "england", "london", "scotland", "manchester", "edinburgh", "britain"],
    "de": ["germany", "deutschland", "de", "berlin", "munich", "münchen", "hamburg", "frankfurt", "stuttgart",
           "cologne", "köln"],
    "fr": ["france", "paris", "fr"], "nl": ["netherlands", "amsterdam", "nl"], "ca": ["canada", "toronto", "ca"],
    "au": ["australia", "sydney", "au"], "sg": ["singapore", "sg"], "pl": ["poland", "warsaw", "pl"],
    "ie": ["ireland", "dublin", "ie"], "es": ["spain", "madrid", "es"],
}
COUNTRY_NAMES = {"in": "India", "us": "United States", "gb": "United Kingdom", "de": "Germany", "fr": "France",
                 "nl": "Netherlands", "ca": "Canada", "au": "Australia", "sg": "Singapore", "pl": "Poland",
                 "ie": "Ireland", "es": "Spain"}
REGIONS = {  # remote-eligible regions per country
    "in": ["apac", "asia", "asia pacific"], "us": ["americas", "north america", "amer", "na"],
    "ca": ["americas", "north america"], "gb": ["emea", "europe", "eu"], "de": ["emea", "europe", "eu", "dach"],
    "fr": ["emea", "europe", "eu"], "nl": ["emea", "europe", "eu"], "pl": ["emea", "europe", "eu"],
    "ie": ["emea", "europe", "eu"], "es": ["emea", "europe", "eu"], "au": ["apac"], "sg": ["apac", "asia"],
}
GLOBAL = ["worldwide", "anywhere", "global", "remote - any"]


def country_code(name: str) -> str:
    n = (name or "").strip().lower()
    if n in COUNTRIES:
        return n
    for cc, names in COUNTRIES.items():
        if n in names:
            return cc
    return n[:2]


def _has(text, term):
    return re.search(r"(?<![a-z])" + re.escape(term) + r"(?![a-z])", text) is not None


def detect_countries(p) -> set:
    text = (p.get("location", "") + " | " + p.get("country_hint", "")).lower()
    found = set()
    for cc, names in COUNTRIES.items():
        for n in names:
            if len(n) <= 3:  # short codes only count as whole tokens in the location/country fields
                if re.search(r"(?:^|[\s,|/(-])" + re.escape(n) + r"(?:$|[\s,|/)-])", text):
                    found.add(cc)
            elif _has(text, n):
                found.add(cc)
    return found


def detect_mode(p) -> str:
    hint = (p.get("mode_hint") or "").lower().replace("-", "")
    if hint in ("remote", "hybrid", "onsite"):
        return "onsite" if hint == "onsite" else hint
    text = (p.get("title", "") + " " + p.get("location", "")).lower()
    if "hybrid" in text:
        return "hybrid"
    if "remote" in text or "work from home" in text:
        return "remote"
    desc = p.get("description", "").lower()
    if re.search(r"\bfully remote\b|\b100% remote\b|\bremote[- ]first\b", desc):
        return "remote"
    if re.search(r"\bhybrid\b", desc[:1500]):
        return "hybrid"
    # A remote / work-from-home mention with no negation or on-site requirement counts as remote
    # (aggregator snippets are short, so "fully remote" is often cut off).
    if (re.search(r"\bremote(ly)?\b|\bwork from home\b|\bwfh\b", desc[:1500])
            and not re.search(r"\bnot remote\b|\bno remote\b|\bon[- ]?site\b|\bin[- ]office\b", desc[:1500])):
        return "remote"
    return "onsite"


def country_ok(p, wanted_ccs) -> bool:
    if not wanted_ccs:
        return True
    ccs = p["countries"]
    if ccs & set(wanted_ccs):
        return True
    if p["mode"] == "remote":
        loc = (p.get("location", "") + " " + p.get("country_hint", "")).lower()
        if any(_has(loc, g) for g in GLOBAL):
            return True
        if not ccs:  # remote with a region instead of a country
            return any(any(_has(loc, r) for r in REGIONS.get(cc, [])) for cc in wanted_ccs)
    return False


def norm_key(p) -> str:
    def n(s):
        return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()
    loc = n(p.get("location", "")).split(" ")[0] if p.get("location") else ""
    base = f"{n(p.get('company'))}|{n(p.get('title'))}|{loc}"
    return hashlib.sha1(base.encode(), usedforsecurity=False).hexdigest()[:16]


def enrich(p) -> dict:
    p = dict(p)
    p["countries"] = detect_countries(p)
    p["mode"] = detect_mode(p)
    p["skills"] = find_skills(p.get("title", "") + "\n" + p.get("description", ""))
    p["key"] = norm_key(p)
    return p


def dedupe(postings):
    """Merge postings with the same company + title + location; keep every source and link (M5)."""
    merged = {}
    for p in postings:
        k = p["key"]
        if k not in merged:
            q = dict(p)
            q["sources"] = [{"name": p["source"], "url": p["url"]}]
            merged[k] = q
        else:
            m = merged[k]
            if all(s["name"] != p["source"] for s in m["sources"]):
                m["sources"].append({"name": p["source"], "url": p["url"]})
            m["skills"] = sorted(set(m["skills"]) | set(p["skills"]))
            if len(p.get("description", "")) > len(m.get("description", "")):
                m["description"] = p["description"]
            for f in ("salary_min", "salary_max", "posted"):
                m[f] = m.get(f) or p.get(f)
    return list(merged.values())


SENIOR = re.compile(r"\b(senior|sr\.?|lead|principal|staff|head|director|architect|manager)\b", re.I)
JUNIOR = re.compile(r"\b(junior|jr\.?|intern|internship|graduate|entry[- ]level|trainee|apprentice)\b", re.I)
LEVEL_YEARS = {"junior": (0, 2), "mid": (3, 7), "senior": (8, 40)}


def seniority_fit(title: str, years: float, level: str = "mid") -> float:
    lo, hi = LEVEL_YEARS.get(level, (3, 7))
    if years:
        lo, hi = min(lo, years), max(hi, years) if years > hi else hi
    if JUNIOR.search(title or ""):
        return 1.0 if lo <= 2 else 0.3
    if SENIOR.search(title or ""):
        return 1.0 if hi >= 8 or (years or 0) >= 6 else 0.6
    return 1.0 if lo >= 2 or (years or 0) >= 2 else 0.7


def search_profile(params) -> dict:
    """Stand-in profile built from the search terms, used when no resume has been uploaded,
    so results are ranked by relevance to what was searched instead of all scoring ~0."""
    terms = list(params.get("skills") or []) + re.findall(r"\w[\w+#.]*", params.get("keywords") or "")
    return {"skills": terms, "titles": [" ".join(terms)], "years": 0, "from_search": True}


def effective_profile(profile, params) -> dict:
    """The resume profile when one with skills exists, otherwise the search-terms profile."""
    return profile if (profile and profile.get("skills")) else search_profile(params or {})


def score(p, profile, level="mid") -> dict:
    """Score 0-100: 70 skill coverage + 15 title similarity + 15 seniority fit.
    With no resume, the score is relevance to the search terms instead (see search_profile)."""
    if profile.get("from_search"):
        terms = [t for t in dict.fromkeys(x.lower() for x in profile.get("skills") or []) if t]
        if not terms:
            return {"score": 50, "matched": [], "missing": []}
        title = (p.get("title") or "").lower()
        text = title + " " + (p.get("description") or "").lower()
        in_title = sum(1 for t in terms if _has(title, t))
        in_text = sum(1 for t in terms if _has(text, t))
        total = round(40 + 35 * in_title / len(terms) + 25 * in_text / len(terms))
        return {"score": max(0, min(100, total)), "matched": [t for t in terms if _has(text, t)],
                "missing": [t for t in terms if not _has(text, t)]}
    mine = {s.lower() for s in (profile.get("skills") or [])}
    # re-detect so postings stored before a vocabulary update are scored with the current skill list
    job = list(dict.fromkeys(list(p.get("skills") or []) +
                             find_skills((p.get("title") or "") + "\n" + (p.get("description") or ""))))
    matched = [s for s in job if s.lower() in mine]
    missing = [s for s in job if s.lower() not in mine]
    # Nobody has every skill a posting lists: matching ~60% of them counts as full coverage. At least 3
    # matches are needed for full marks, so a short snippet with one recognised skill can't score 100.
    coverage = min(1.0, len(matched) / max(3, round(len(job) * 0.6))) if job else 0.0
    title_low = (p.get("title") or "").lower()
    title_words = {w for t in (profile.get("titles") or []) for w in re.findall(r"[a-z]+", t.lower()) if len(w) > 3}
    tsim = 0.0
    if title_words:
        hits = sum(1 for w in title_words if w in title_low)
        tsim = min(1.0, hits / max(1, min(len(title_words), 2)))
    fit = seniority_fit(p.get("title", ""), profile.get("years") or 0, level)
    total = round(70 * coverage + 15 * tsim + 15 * fit) if (job or tsim) else round(15 * fit * 0.5)
    return {"score": max(0, min(100, total)), "matched": matched, "missing": missing}


def passes_search_filters(p, params) -> bool:
    if not country_ok(p, [country_code(c) for c in params.get("countries") or []]):
        return False
    modes = params.get("modes") or ([params["mode"]] if params.get("mode") and params["mode"] != "any" else [])
    if modes and p["mode"] not in modes:
        return False
    text = (p.get("title", "") + " " + p.get("description", "")).lower()
    if params.get("skills"):
        js = {s.lower() for s in p["skills"]}
        if not any(s.lower() in js or s.lower() in text for s in params["skills"]):
            return False
    for kw in re.findall(r"\w[\w+#.]*", (params.get("keywords") or "").lower()):
        if kw not in text:
            return False
    comps = [c.lower() for c in params.get("companies") or []]
    if comps and not any(c in (p.get("company") or "").lower() for c in comps):
        return False
    return True

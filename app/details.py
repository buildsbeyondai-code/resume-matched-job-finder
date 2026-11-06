"""resume_details.md: the applicant's details used to fill job applications.

Format: Markdown with `## Section` headings and `- Field: value` lines. Answers the user gives while applying
are appended under `## Saved answers` so the same question is never asked twice. Pure functions here; the
browser side lives in apply.py.
"""
import os
import re
import threading
from datetime import date
from pathlib import Path

DEFAULT_DIR = str(Path.home() / "JobSearch")  # set RMJF_JOBSEARCH_DIR in .env for your own folder
FILENAME = "resume_details.md"
ANSWERS = "Saved answers"
_LOCK = threading.Lock()


def folder() -> Path:
    return Path(os.environ.get("RMJF_JOBSEARCH_DIR", DEFAULT_DIR))


def path() -> Path:
    return folder() / FILENAME


def norm(s: str) -> str:
    """Lower-case, drop punctuation, '*' and '(required)' markers, collapse spaces."""
    s = re.sub(r"\(\s*(required|optional)\s*\)", " ", str(s or ""), flags=re.I)
    s = re.sub(r"[^\w]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


LINE_RE = re.compile(r"^\s*[-*]\s+(.+?):\s(.*)$")


def parse(text: str) -> dict:
    """Return {normalised field: (display field, value)}; later lines win."""
    out = {}
    for line in text.splitlines():
        m = LINE_RE.match(line)
        if m and m.group(2).strip():
            k = m.group(1).strip().strip("*").strip()
            out[norm(k)] = (k, m.group(2).strip())
    return out


def load():
    p = path()
    if not p.exists():
        return None
    return parse(p.read_text(encoding="utf-8"))


def resume_file():
    """Absolute path of the resume to upload, or None."""
    d = load() or {}
    name = d.get("resume file", (None, ""))[1]
    if name:
        p = Path(name) if name.startswith("/") else folder() / name
        if p.is_file():
            return p
    for p in sorted(folder().glob("*")):
        if p.suffix.lower() in (".pdf", ".docx") and "resume" in p.name.lower():
            return p
    return None


def _clean(v: str) -> str:
    return re.sub(r"[\r\n]+", " ", str(v)).strip()


def save_answers(pairs):
    """Add or replace `- field: value` lines. Known fields are replaced in place; new ones go under
    Saved answers. Returns the number of lines written."""
    pairs = [(_clean(k).replace(":", " -"), _clean(v)) for k, v in pairs if _clean(k) and _clean(v)]
    if not pairs:
        return 0
    with _LOCK:
        p = path()
        text = p.read_text(encoding="utf-8") if p.exists() else f"# Resume details\n\n## {ANSWERS}\n"
        lines = text.splitlines()
        for k, v in pairs:
            nk = norm(k)
            for i, line in enumerate(lines):
                m = LINE_RE.match(line) or re.match(r"^\s*[-*]\s+(.+?):\s*$", line)
                if m and norm(m.group(1)) == nk:
                    lines[i] = f"- {m.group(1).strip()}: {v}"
                    break
            else:
                idx = next((i for i, l in enumerate(lines) if l.strip().lower() == f"## {ANSWERS.lower()}"), None)
                if idx is None:
                    lines += ["", f"## {ANSWERS}"]
                    idx = len(lines) - 1
                end = idx + 1
                while end < len(lines) and not lines[end].startswith("## "):
                    end += 1
                while end > idx + 1 and not lines[end - 1].strip():
                    end -= 1
                lines.insert(end, f"- {k}: {v}")
        tmp = p.with_suffix(".md.tmp")
        tmp.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        os.replace(tmp, p)
    return len(pairs)


# ---------------- generation from resume text (used only when the file does not exist) ----------------
def generate(text: str, resume_name: str = "") -> str:
    email = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
    phone = re.search(r"\+?\d[\d\s().-]{8,}\d", text)
    linkedin = re.search(r"(?:https?://)?(?:www\.)?linkedin\.com/in/[\w-]+", text, re.I)
    github = re.search(r"(?:https?://)?(?:www\.)?github\.com/[\w-]+", text, re.I)
    name = ""
    for line in text.splitlines():
        s = line.strip()
        if 3 <= len(s) <= 40 and re.fullmatch(r"[A-Za-z][A-Za-z .'-]+", s) and len(s.split()) >= 2:
            name = s.title() if s.isupper() else s
            break
    parts = [p for p in name.replace(".", ". ").split() if len(p.strip(".")) > 1]
    rows = [("Personal", [("Full name", name), ("First name", parts[0] if parts else ""),
                          ("Last name", parts[-1] if len(parts) > 1 else ""),
                          ("Email", email.group(0) if email else ""),
                          ("Phone", re.sub(r"\s+", " ", phone.group(0)).strip() if phone else "")]),
            ("Links", [("LinkedIn", ("https://" + linkedin.group(0).split("://")[-1]) if linkedin else ""),
                       ("GitHub", ("https://" + github.group(0).split("://")[-1]) if github else "")]),
            ("Resume", [("Resume file", resume_name)])]
    out = ["# Resume details", "",
           "Used by Resume-Matched Job Finder to fill job applications. Edit any `- Field: value` line.",
           "Answers you give while applying are saved under Saved answers.", "",
           f"Created {date.today().isoformat()} from \"{resume_name or 'your resume'}\".", ""]
    for sec, items in rows:
        out.append(f"## {sec}")
        out += [f"- {k}: {v}" for k, v in items if v]
        out.append("")
    out.append(f"## {ANSWERS}")
    return "\n".join(out) + "\n"


def ensure(resume_text_fallback: str = "") -> Path:
    """Create resume_details.md from the resume in the folder (or the stored profile text) if missing."""
    p = path()
    if p.exists():
        return p
    from .resume import ResumeError, parse_resume
    text, name = resume_text_fallback, ""
    rf = resume_file()
    if rf:
        try:
            text, name = parse_resume(rf.read_bytes(), rf.name)["text"], rf.name
        except ResumeError:
            pass
    folder().mkdir(parents=True, exist_ok=True)
    with _LOCK:
        if not p.exists():
            p.write_text(generate(text or "", name), encoding="utf-8")
    return p


# ---------------- mapping form fields to details ----------------
# (canonical field, label pattern). Order matters: the first match wins.
CANON = [
    ("First name", r"\b(first|given|fore) ?name\b"),
    ("Last name", r"\b(last|family|sur) ?name\b|\bsurname\b"),
    ("Preferred name", r"\bpreferred (first )?name\b|\bnickname\b"),
    ("Full name", r"^(full |legal |your )?name$|\bfull (legal )?name\b|^name (as|on)"),
    ("Email", r"\be ?mail\b"),
    ("Phone country code", r"\b(country|dial(ing)?) code\b"),
    ("Phone", r"\b(phone|mobile|telephone|cell)\b"),
    ("LinkedIn", r"\blinked ?in\b"),
    ("GitHub", r"\bgithub\b"),
    ("Website", r"\b(website|portfolio|personal (site|url|page)|blog)\b"),
    ("Work authorization", r"\b(legally )?(authori[sz]ed|eligible|right) to work\b|\bwork (authori[sz]ation|permit)\b"),
    ("Visa sponsorship", r"\bsponsor(ship)?\b|\bvisa\b"),
    ("Willing to relocate", r"\breloca"),
    ("Notice period", r"\bnotice period\b|\bhow soon\b|\bearliest start\b|\bstart date\b|\bavailab(le|ility) to start\b"),
    ("Current salary", r"\bcurrent (salary|compensation|ctc|pay)\b"),
    ("Expected salary", r"\b(expected|desired|salary) (salary|compensation|ctc|expectation|requirement)s?\b|\bexpected ctc\b|\bsalary expectation"),
    ("How did you hear about us", r"\bhow did you (hear|find|learn)\b|\b(referral )?source\b"),
    ("Gender", r"\bgender\b"),
    ("Pronouns", r"\bpronoun"),
    ("Hispanic or Latino", r"\bhispanic|latin[oax]\b"),
    ("Race or ethnicity", r"\brace\b|\bethnic"),
    ("Veteran status", r"\bveteran\b"),
    ("Disability status", r"\bdisabilit"),
    ("Years of experience", r"\byears? of (professional |relevant |work )?experience\b|\bhow many years\b"),
    ("Current company", r"\b(current|present|most recent) (company|employer|organi[sz]ation)\b|^company$|^employer$"),
    ("Current title", r"\b(current|present|most recent) (job )?(title|role|position|designation)\b|^(job )?title$"),
    ("High school", r"\b(high|secondary|senior secondary) school\b"),
    ("Graduation year", r"\b(graduation|grad) (year|date)\b|\byear of graduation\b"),
    ("School", r"\b(school|university|college|institution)\b"),
    ("Degree", r"\bdegree\b|\bqualification\b"),
    ("Field of study", r"\b(discipline|field of study|major|speciali[sz]ation)\b"),
    ("City", r"^city$|\bcity\b(?! of)"),
    ("State", r"\b(state|province|region)\b"),
    ("Country", r"\bcountry\b"),
    ("Location", r"\blocation\b|\bwhere (are you|do you) (based|live|located)\b|\bcurrent(ly)? (location|based)\b"),
    ("Address", r"\baddress\b"),
    ("Cover letter", r"\bcover letter\b"),
    ("Summary", r"\b(summary|about (yourself|you)|tell us about)\b"),
]
_CANON_RE = [(k, re.compile(p)) for k, p in CANON]


REQ_MARK = re.compile(r"\s*[*\u2731\u273d\u2217]\s*")


def clean_label(label: str) -> str:
    """Drop required markers (*, ✱) and upload-button text that some boards put inside the label."""
    s = REQ_MARK.sub(" ", str(label or ""))
    s = re.sub(r"\b(ATTACH|UPLOAD) (RESUME|CV|FILE)(/CV)?\b.*$", "", s)
    return re.sub(r"\s+", " ", s).strip()


def canonical(label: str):
    n = norm(label)
    if not n:
        return None
    for k, rx in _CANON_RE:
        if rx.search(n):
            return k
    return None


def lookup(label: str, details: dict, name_attr: str = ""):
    """Value for a form field, preferring an exact saved answer to the same question.
    Returns (value, source key, canonical key)."""
    n = norm(label)
    canon = canonical(label) or canonical(name_attr.replace("_", " "))
    if n and n in details:
        return details[n][1], details[n][0], canon
    if canon:
        hit = details.get(norm(canon))
        if hit:
            return hit[1], hit[0], canon
    return None, None, canon


YES = {"yes", "y", "true", "i am", "i do", "i agree", "agree", "accept", "i accept"}
NO = {"no", "n", "false", "i am not", "i do not", "i don t"}


def choose(value: str, options):
    """Pick the option that best matches a stored value, or None."""
    v = norm(value)
    if not v or not options:
        return None
    opts = [(o, norm(o)) for o in options if norm(o)]
    for o, n in opts:
        if n == v:
            return o
    if v in YES or v in NO:
        want = YES if v in YES else NO
        for o, n in opts:
            if n in want:
                return o
        for o, n in opts:
            first = n.split(" ")[0]
            if (v in YES and first == "yes") or (v in NO and first == "no"):
                return o
    for o, n in opts:
        if n.startswith(v) or v.startswith(n):
            return o
    for o, n in opts:
        if re.search(r"\b" + re.escape(v) + r"\b", n) or re.search(r"\b" + re.escape(n) + r"\b", v):
            return o
    return None

"""Apply assistant: opens a job's application in a visible browser and fills it from resume_details.md.

The interactive Apply fills and leaves Submit to the user; auto apply (`_auto`) also steps through the form,
submits it and verifies the confirmation. The application is made on the company's own website, or, when the
company has none, on a job portal (Naukri, LinkedIn) the user has enabled. Required questions that
resume_details.md can't answer are returned to the UI; the answers are saved and filled in.

Playwright's sync API is bound to one thread, so a single worker thread owns the browser and runs every
browser call; HTTP handlers hand it work through a queue.
"""
import os
import queue
from urllib.parse import urlparse
import re
import secrets
import threading
from concurrent.futures import Future

from . import ai, config, details, direct, policy, portals

APPLY_RE = (r"^\s*(apply|apply now|apply for this (job|position|role)|apply online|apply to this job|"
            r"easy apply( to .+)?|i'?m interested|start (your )?application|submit (an )?application)\s*[›»>→]?\s*$")
# may end an application on some sites (a Naukri chat drawer's last "Save" sends it)
AMBIGUOUS_RE = re.compile(r"^\s*(continue|proceed|save)\s*[›»>→]?\s*$", re.I)
COMPANY_SITE_RE = r"^\s*apply (on|at|via) (the )?company('?s)? (site|website|career site)\s*[›»>→↗]?\s*$"
TEXTISH = {"text", "email", "tel", "url", "number", "textarea", "combobox", "date"}

SCAN_JS = r"""
(prefix) => {
  const vis = (el) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const txt = (el) => (el ? (el.innerText || el.textContent || '') : '').replace(/\s+/g, ' ').trim();
  const own = (el) => {
    const al = el.getAttribute('aria-labelledby');
    if (al) { const t = al.split(/\s+/).map((id) => txt(document.getElementById(id))).join(' ').trim(); if (t) return t; }
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l && txt(l)) return txt(l); }
    const w = el.closest('label'); if (w && txt(w)) return txt(w);
    return el.getAttribute('aria-label') || '';
  };
  const near = (el, skip) => {
    let p = el.parentElement;
    for (let i = 0; i < 5 && p; i++, p = p.parentElement) {
      for (const l of p.querySelectorAll('legend, label, [class*="label"], [class*="Label"], [class*="question"], [class*="title"]')) {
        if (l.contains(el) || (skip && skip(l)) || l.querySelector('input,select,textarea')) continue;
        const t = txt(l); if (t && t.length < 300) return t;
      }
    }
    return '';
  };
  const req = (el, label) => el.required || el.getAttribute('aria-required') === 'true' || /[*\u2731]/.test(label || '');
  window.__rmjfN = window.__rmjfN || 0;
  const tag = (el) => { if (!el.dataset.rmjf) el.dataset.rmjf = prefix + (++window.__rmjfN); return el.dataset.rmjf; };
  const out = []; const groups = {};
  // an open application dialog (LinkedIn Easy Apply, a Naukri question drawer) is the form; the page behind is not
  const dlg = [...document.querySelectorAll('[role=dialog], [aria-modal=true], [class*="chatbot_Drawer"], [class*="chatbot-drawer"]')]
    .filter((d) => vis(d) && d.querySelector('input, select, textarea, [contenteditable=true]'));
  const root = dlg.length ? dlg[dlg.length - 1] : document;
  const chrome = (el) => !!el.closest('header, nav, [role=search], [role=banner]') && !el.closest('[role=dialog]');
  // in a chat drawer every question waits for an answer, and the question is the bot's last message
  const chat = root !== document && /chatbot/i.test(root.className || '');
  const lastMsg = () => { const m = [...root.querySelectorAll('[class*="botMsg"], [class*="bot-msg"], [class*="botItem"]')];
    return m.length ? txt(m[m.length - 1]) : ''; };
  const els = root.querySelectorAll('input, select, textarea');
  for (const el of els) {
    if (chrome(el)) continue;
    const type = (el.type || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset', 'search', 'password'].includes(type)) continue;
    if (el.disabled || el.readOnly && type !== 'file') continue;
    if (type !== 'file' && type !== 'radio' && type !== 'checkbox' && !vis(el)) continue;
    if ((type === 'radio' || type === 'checkbox') && el.name && document.querySelectorAll(`input[name="${CSS.escape(el.name)}"]`).length > 1) {
      (groups[type + ':' + el.name] = groups[type + ':' + el.name] || []).push(el); continue;
    }
    if ((type === 'radio' || type === 'checkbox') && !vis(el) && !(el.closest('label') && vis(el.closest('label')))) continue;
    const id = tag(el);
    let kind = el.tagName === 'SELECT' ? 'select' : el.tagName === 'TEXTAREA' ? 'textarea' :
               el.getAttribute('role') === 'combobox' ? 'combobox' : type;
    if (!['select', 'textarea', 'combobox', 'file', 'checkbox', 'radio', 'email', 'tel', 'url', 'number', 'date'].includes(kind)) kind = 'text';
    let label = own(el); if (!label || kind === 'combobox' || kind === 'file') label = label || near(el);
    if (!label) label = el.placeholder || el.name || '';
    const options = kind === 'select' ? [...el.options].filter((o) => o.value !== '' && !/^\s*(select|choose|--|please)/i.test(o.text)).map((o) => o.text.trim()) : [];
    let filled = false;
    if (kind === 'select') filled = el.selectedIndex > 0 && el.value !== '';
    else if (kind === 'checkbox') filled = el.checked;
    else if (kind === 'file') filled = el.files && el.files.length > 0;
    else filled = (el.value || '').trim() !== '';
    out.push({ id, kind, label, name: el.name || el.id || '', required: req(el, label), options, filled });
  }
  for (const el of root.querySelectorAll('[contenteditable="true"]')) {  // chat-style answer boxes
    if (!vis(el) || chrome(el)) continue;
    const label = own(el) || lastMsg() || near(el) || 'Answer';
    out.push({ id: tag(el), kind: 'text', label, name: '', required: true, options: [], filled: txt(el) !== '' });
  }
  for (const [key, items] of Object.entries(groups)) {
    const kind = key.startsWith('radio') ? 'radio' : 'checkboxes';
    const optLabels = items.map((el) => own(el) || el.value);
    const id = tag(items[0]);
    items.forEach((el, i) => { el.dataset.rmjf = id; el.dataset.rmjfOpt = String(i); });
    const fs = items[0].closest('fieldset'); let label = fs ? txt(fs.querySelector('legend')) : '';
    const grp = items[0].closest('[role="radiogroup"],[role="group"]');
    if (!label && grp) label = own(grp);
    if (!label && chat) label = lastMsg();
    if (!label) { // the nearest text that is not one of the option labels
      const set = new Set(optLabels);
      label = near(items[0], (l) => set.has(txt(l)) || items.some((el) => l.contains(el)));
    }
    if (!label) label = items[0].name;
    out.push({ id, kind, label, name: items[0].name, required: chat || items.some((el) => req(el, label)) || /[*\u2731]/.test(label),
               options: optLabels, filled: items.some((el) => el.checked) });
  }
  return out;
}
"""


SIGNIN_URL_RE = re.compile(r"/(log-?in|sign-?in|sign-?up|register|authenticate|auth|account/create|users?/new|sso|authwall|checkpoint)\b", re.I)
PASSWORD_JS = """() => [...document.querySelectorAll('input[type=password]')].some((el) => {
  const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; })"""


def is_signin(url, has_password):
    """A sign-in or account sign-up wall rather than the application form."""
    from urllib.parse import urlparse
    return bool(has_password or SIGNIN_URL_RE.search(urlparse(url or "").path))


LAND_JS = """() => [...document.querySelectorAll('a[href]')].map((a) => a.href)
  .filter((h) => /\\/land\\/ad\\//.test(h) && /^https?:/.test(h))"""


BUTTONS_JS = r"""
(prefix) => {
  const vis = (el) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && !el.disabled; };
  const SUBMIT = /^(submit|submit( my)? application|submit & apply|send( my)? application|send|apply|apply now|complete( my)? application|finish|submit application now)$/i;
  const NEXT = /^(next|next step|continue|save (and|&) continue|review|review application|proceed|next page)$/i;
  const out = [];
  let n = 0;
  const dlg = [...document.querySelectorAll('[role=dialog], [aria-modal=true], [class*="chatbot_Drawer"], [class*="chatbot-drawer"]')]
    .filter((d) => vis(d) && d.querySelector('button, input[type=submit], [role=button]'));
  const root = dlg.length ? dlg[dlg.length - 1] : document;
  for (const el of root.querySelectorAll('button, input[type=submit], [role=button]')) {
    if (!vis(el)) continue;
    const t = (el.innerText || el.value || el.getAttribute('aria-label') || '').replace(/\s+/g, ' ').replace(/[›»>→]+$/, '').trim();
    const kind = SUBMIT.test(t) ? 'submit' : NEXT.test(t) ? 'next' :
                 (/^save$/i.test(t) && el.closest('[class*="chatbot"]')) ? 'next' :
                 (el.type === 'submit' && el.closest('form') && t.length < 40 && !/search|sign|log/i.test(t)) ? 'submit' : '';
    if (!kind) continue;
    el.dataset.rmjfBtn = prefix + (++n);
    out.push({ id: el.dataset.rmjfBtn, kind, text: t });
  }
  return out;
}
"""
CAPTCHA_JS = r"""() => [...document.querySelectorAll('iframe')].some((f) => {
  const r = f.getBoundingClientRect(); const src = f.src || '';
  if (r.width < 30 || r.height < 30 || getComputedStyle(f).visibility === 'hidden') return false;
  return (/recaptcha\/(api2|enterprise)\/(anchor|bframe)/.test(src) && !/size=invisible/.test(src))
      || /hcaptcha\.com\/captcha|challenges\.cloudflare\.com|\/captcha/i.test(src);
}) || !!document.querySelector('.h-captcha:not([data-size=invisible]) iframe, .cf-turnstile iframe')"""
CONFIRM_RE = re.compile(r"your application was sent|you have successfully applied|applied successfully|"
                        r"thank(s| you) for (applying|your application|submitting)|application (was |has been )?"
                        r"(successfully )?(submitted|received|sent)|we(?:'ve| have) received your application|"
                        r"successfully (applied|submitted)|application complete|you(?:'ve| have) applied", re.I)
CONFIRM_URL_RE = re.compile(r"(confirmation|thank-?you|thanks|success|submitted|application-complete)", re.I)
ERROR_JS = r"""() => [...document.querySelectorAll('[aria-invalid=true], .error, .errors, .field-error, [role=alert], .invalid-feedback')]
  .filter((e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; })
  .map((e) => (e.innerText || e.getAttribute('aria-label') || e.name || '').replace(/\s+/g, ' ').trim()).filter(Boolean).slice(0, 8)"""


def confirmed(before_text, after_text, before_url, after_url):
    """True when the page shows an application confirmation that was not there before Submit."""
    if CONFIRM_RE.search(after_text or "") and not CONFIRM_RE.search(before_text or ""):
        return True
    return after_url != before_url and bool(CONFIRM_URL_RE.search(urlparse(after_url or "").path))


class ApplyError(Exception):
    """User-facing reason the apply assistant could not continue."""


# ---------------- pure planning (unit-tested) ----------------
def plan(fields, det, resume_path):
    """Decide what to do with each scanned field.
    Returns (actions, missing, manual): actions = [(field, value)], missing = [question dicts]."""
    actions, missing, manual = [], [], []
    has_code_field = any(details.canonical(f["label"]) == "Phone country code" for f in fields)
    resume_used = False
    for f in fields:
        if f.get("filled"):
            continue
        label = details.clean_label(f["label"]) or f.get("name") or "Unlabelled field"
        kind = f["kind"]
        if kind == "file":
            n = details.norm(label + " " + f.get("name", ""))
            if resume_path and not resume_used and "cover" not in n and (
                    re.search(r"\b(resume|cv|curriculum)\b", n) or not any(
                        re.search(r"resume|cv", details.norm(g["label"] + " " + g.get("name", "")))
                        for g in fields if g["kind"] == "file")):
                actions.append((f, str(resume_path)))
                resume_used = True
            elif f["required"]:
                manual.append(f"{label}: attach the file in the browser window")
            continue
        value, _src, canon = details.lookup(label, det, f.get("name", ""))
        if canon == "Phone" and has_code_field:
            alt = det.get(details.norm("Phone number without country code"))
            value = alt[1] if alt else value
        q = {"id": f["id"], "label": label, "kind": kind, "canon": canon,
             "options": f["options"] if f["options"] else (["Yes", "No"] if kind == "checkbox" else [])}
        if value is None:
            if f["required"]:
                missing.append(q)
            continue
        how, value = convert(f, value)
        if how == "bad" and f["required"]:
            missing.append(q)
        if how == "fill":
            actions.append((f, value))
    return actions, missing, manual


def convert(f, value):
    """Map a text answer onto a field: ("fill", value) | ("skip", None) for a checkbox answered No |
    ("bad", None) when the answer matches none of the field's options (or a checkbox answer is neither yes nor no)."""
    kind = f["kind"]
    if kind in ("select", "radio"):
        opt = details.choose(value, f["options"])
        return ("bad", None) if opt is None else ("fill", opt)
    if kind == "checkboxes":
        picks = [p for p in (details.choose(v, f["options"]) for v in re.split(r"[;,]", value)) if p]
        return ("fill", picks) if picks else ("bad", None)
    if kind == "checkbox":
        v = details.norm(value)
        return ("fill", value) if v in details.YES else ("skip", None) if v in details.NO else ("bad", None)
    return "fill", value


def ai_actions(fields, missing, got):
    """Turn Claude's answers ({id: (value, basis, fact ids, confidence)}) into fill actions.
    Returns (actions, still_missing, used): used = [{label, value, basis}] shown to the user."""
    by_id = {f["id"]: f for f in fields}
    actions, rest, used = [], [], []
    for q in missing:
        a, f = got.get(q["id"]), by_id.get(q["id"])
        how, v = convert(f, a[0]) if a and f else ("bad", None)
        if how == "bad":
            rest.append(q)
            continue
        used.append({"label": q["label"], "value": a[0], "basis": a[1],
                     "fact_ids": list(a[2]) if len(a) > 2 else [], "confidence": a[3] if len(a) > 3 else None})
        if how == "fill":
            actions.append((f, v))
    return actions, rest, used


def answers_to_save(missing, answers, det):
    """Which `field: value` lines to save for the user's answers. A recognised question (for example
    "Are you authorized to work…") is saved under its canonical field the first time, so differently
    worded versions reuse it; otherwise under the question text itself."""
    out = []
    for q in missing:
        v = str(answers.get(q["id"], "")).strip()
        if not v:
            continue
        key = q["canon"] if q.get("canon") and details.norm(q["canon"]) not in det else q["label"]
        out.append((key.rstrip(" *").strip(), v))
    return out


# ---------------- browser worker ----------------
def browser_env(environ=None):
    """Environment for the browser. Under WSL, DISPLAY is sometimes set to a Windows X server address
    (for example 127.0.0.1:0.0) that does not exist; WSLg's own display is :0 (socket /tmp/.X11-unix/X0)."""
    env = dict(os.environ if environ is None else environ)
    d = env.get("DISPLAY", "")
    if os.path.exists("/tmp/.X11-unix/X0") and not d.startswith(":"):
        env["DISPLAY"] = ":0"
    return env


class Session:
    def __init__(self, job, page):
        self.id = secrets.token_hex(6)
        self.job, self.page, self.missing = job, page, []
        self.channel = "company"  # where the application is made: company | naukri | linkedin
        self.ai_asked, self.ai_got, self.ai_used, self.ai_error = set(), {}, [], ""


def _resume_text(path, _cache={}):  # noqa: B006 - cache by (path, mtime)
    if not path:
        return ""
    key = (str(path), os.path.getmtime(path))
    if key not in _cache:
        from .resume import ResumeError, parse_resume
        try:
            _cache[key] = parse_resume(path.read_bytes(), path.name)["text"]
        except ResumeError:
            _cache[key] = ""
    return _cache[key]


class Assistant:
    def __init__(self):
        self.q = queue.Queue()
        self.thread = None
        self.pw = self.ctx = None
        self.sessions = {}
        self.portal_state = {}  # portal -> signed in (True/False) as last seen; no entry = not checked yet

    # --- thread plumbing ---
    def call(self, fn, *args, timeout=240):
        if not self.thread or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._run, name="apply-browser", daemon=True)
            self.thread.start()
        fut = Future()
        self.q.put((fn, args, fut))
        return fut.result(timeout=timeout)

    def _run(self):
        while True:
            fn, args, fut = self.q.get()
            try:
                fut.set_result(fn(*args))
            except BaseException as e:  # noqa: BLE001 - surfaced to the HTTP handler
                fut.set_exception(e)

    def _context(self):
        if self.ctx is not None:
            try:
                self.ctx.pages  # raises if the browser was closed
                if self.ctx.browser is None or self.ctx.browser.is_connected():
                    return self.ctx
            except Exception:  # noqa: BLE001
                pass
            self.ctx = None
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise ApplyError("The browser automation library is not installed. Run: "
                             ".venv/bin/pip install playwright && .venv/bin/python -m playwright install chromium") from e
        if self.pw is None:
            self.pw = sync_playwright().start()
        profile = config.BROWSER_PROFILE
        profile.mkdir(parents=True, exist_ok=True)
        os.chmod(profile, 0o700)  # the portal sign-in sessions live here: owner-only
        try:
            self.ctx = self.pw.chromium.launch_persistent_context(
                str(profile), headless=config.APPLY_HEADLESS, no_viewport=True, env=browser_env(),
                args=["--start-maximized", "--disable-blink-features=AutomationControlled"])
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "shared libraries" in msg or "missing dependencies" in msg.lower():
                raise ApplyError("The browser could not start because system libraries are missing. In WSL run once: "
                                 "sudo ~/apps/resume-matched-job-finder/.venv/bin/python -m playwright install-deps chromium") from e
            raise ApplyError("The browser could not start: " + msg.splitlines()[0][:300]) from e
        self.ctx.on("close", lambda *_: setattr(self, "ctx", None))
        return self.ctx

    # --- operations (run on the worker thread) ---
    def _scan(self, page):
        fields = []
        for i, fr in enumerate(page.frames):
            try:
                fields += fr.evaluate(SCAN_JS, f"f{i}_")
            except Exception:  # noqa: BLE001 - cross-origin frames that are gone, about:blank
                continue
        return fields

    @staticmethod
    def _settle(page, ms=8000):
        try:
            page.wait_for_load_state("networkidle", timeout=ms)
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(800)

    def _apply_button(self, page):
        """The company-site apply button if there is one, else the page's Apply button."""
        for rx in (COMPANY_SITE_RE, APPLY_RE):
            for fr in page.frames:
                loc = fr.get_by_role("link", name=re.compile(rx, re.I)).or_(fr.get_by_role("button", name=re.compile(rx, re.I)))
                try:
                    if loc.count():
                        return loc.first
                except Exception:  # noqa: BLE001
                    continue
        return None

    def _find_form(self, page, force=False):
        """Return the page holding the application form, clicking an Apply button if needed. On a job portal
        (force) the Apply button is always clicked first: its page also has search boxes."""
        for attempt in range(2):
            fields = [f for f in self._scan(page) if f["kind"] != "file"]
            if len(fields) >= 3 and not (force and attempt == 0):
                return page
            btn = self._apply_button(page)
            if btn is None or direct.is_aggregator(page.url):
                return page
            before = set(page.context.pages)
            try:
                btn.click(timeout=5000)
            except Exception:  # noqa: BLE001
                return page
            page.wait_for_timeout(1500)
            new = [p for p in page.context.pages if p not in before]
            if new:
                page = new[-1]
            self._settle(page)
            if force:  # a portal's Apply opens its own dialog/drawer (or applies at once): never click it twice
                return page
        return page

    def _find(self, page, fid):
        for fr in page.frames:
            try:
                loc = fr.locator(f'[data-rmjf="{fid}"]')
                if loc.count():
                    return fr, loc
            except Exception:  # noqa: BLE001
                continue
        return None, None

    def _fill(self, page, f, value):
        fr, loc = self._find(page, f["id"])
        if loc is None:
            raise ApplyError("field disappeared")
        k = f["kind"]
        if k == "file":
            loc.first.set_input_files(value)
        elif k == "select":
            loc.first.select_option(label=value)
        elif k in ("radio", "checkboxes"):
            for v in ([value] if isinstance(value, str) else value):
                i = f["options"].index(v)
                opt = fr.locator(f'[data-rmjf="{f["id"]}"][data-rmjf-opt="{i}"]')
                try:
                    opt.check(force=True, timeout=3000)
                except Exception:  # noqa: BLE001 - custom-styled inputs: click through the DOM
                    opt.evaluate("el => el.click()")
        elif k == "checkbox":
            try:
                loc.first.check(force=True, timeout=3000)
            except Exception:  # noqa: BLE001
                loc.first.evaluate("el => el.click()")
        elif k == "combobox":
            loc.first.click(timeout=3000)
            loc.first.fill(value)
            page.wait_for_timeout(900)
            loc.first.press("Enter")
        else:
            loc.first.fill(value)

    def _pass(self, s):
        """Scan, fill everything known, report what is still missing. Repeats while new fields appear
        (conditional questions)."""
        det = details.load() or {}
        resume = details.resume_file()
        filled, manual, missing = [], [], []
        for _ in range(3):
            fields = self._scan(s.page)
            actions, missing, manual = plan(fields, det, resume)
            more, missing = self._ai(s, fields, missing)
            actions += more
            if not actions:
                break
            for f, v in actions:
                self._hook(s.job, "before_action")  # unattended mode: stop before the next form action when paused
                try:
                    self._fill(s.page, f, v)
                    filled.append(details.clean_label(f["label"]) or f["name"])
                except Exception:  # noqa: BLE001 - one odd widget must not stop the rest
                    manual.append(f"{details.clean_label(f['label']) or f['name']}: could not be filled automatically, check it in the browser")
            s.page.wait_for_timeout(600)
        signin = self._signin_page(s.page)
        if signin:  # never ask questions or save answers for a sign-in/sign-up form
            missing, manual = [], []
        s.missing = missing
        try:
            s.page.bring_to_front()
        except Exception:  # noqa: BLE001
            pass
        page_kind = ("sign-in" if signin else
                     "form" if any(f["kind"] != "file" for f in self._scan(s.page)) else "no-form")
        return {"session": s.id, "job": {"title": s.job.get("title"), "company": s.job.get("company")},
                "url": s.page.url, "filled": list(dict.fromkeys(filled)), "missing": missing, "manual": list(dict.fromkeys(manual)),
                "page": page_kind, "site": urlparse(s.page.url).hostname or "", "details_file": str(details.path()),
                "ai": list(s.ai_used), "ai_error": s.ai_error}

    @staticmethod
    def _signin_page(page):
        has_pw = False
        for fr in page.frames:
            try:
                has_pw = has_pw or bool(fr.evaluate(PASSWORD_JS))
            except Exception:  # noqa: BLE001
                continue
        return is_signin(page.url, has_pw)

    def _ai(self, s, fields, missing):
        """Ask Claude for required questions resume_details.md can't answer (once per question).
        Returns (fill actions, questions still missing). Never used on sign-in forms."""
        if not missing or not s.job.get("ai", True) or not ai.available() or self._signin_page(s.page):
            return [], missing
        new = [q for q in missing if q["id"] not in s.ai_asked]
        if new:
            s.ai_asked.update(q["id"] for q in new)
            self._hook(s.job, "step", f"Claude is answering {len(new)} question{'s' if len(new) > 1 else ''}")
            p = details.path()
            try:
                s.ai_got.update(ai.answer(new, s.job, p.read_text(encoding="utf-8") if p.exists() else "",
                                          _resume_text(details.resume_file())))
                s.ai_error = ""
            except ai.AIError as e:
                s.ai_error = str(e)
        actions, rest, used = ai_actions(fields, missing, s.ai_got)
        s.ai_used += [u for u in used if u not in s.ai_used]
        return actions, rest

    def _goto(self, page, url):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:  # noqa: BLE001
            raise ApplyError("The job page did not load: " + str(e).splitlines()[0][:200]) from e
        self._settle(page)

    def _company_site(self, page, job, search=True):
        """For an aggregator job, move `page` to the employer's own posting. Returns how it was found, or
        None when there is no company-site application (with `search`, the page is then left on a careers
        web search)."""
        self._goto(page, job["url"])
        hrefs = []
        for fr in page.frames:
            try:
                hrefs += fr.evaluate(LAND_JS)
            except Exception:  # noqa: BLE001
                continue
        for href in hrefs[:2]:  # the aggregator's "apply on company site" redirect
            try:
                self._goto(page, href)
            except ApplyError:
                continue
            if not direct.is_aggregator(page.url):
                return "Adzuna's link to the company website"
        hit = direct.find_on_company_board(job.get("company", ""), job.get("title", ""))
        if hit:
            self._goto(page, hit["url"])
            return f"the company's own {hit['board']} careers page"
        if search:
            self._goto(page, direct.careers_search_url(job.get("company", ""), job.get("title", "")))
        return None

    # --- job portals: the last resort when the company has no application of its own (Naukri, LinkedIn) ---
    @staticmethod
    def _hook(job, name, *args):
        """Progress and rate-limit callbacks from the auto-apply runner (job["hooks"]), if any."""
        fn = getattr(job.get("hooks"), name, None)
        if fn is not None:
            fn(*args)

    @staticmethod
    def _gate(job, what, channel):
        """Ask the application policy before a click that may send the application (Submit, a portal's Apply,
        an ambiguous "Continue"). Fails closed: without a gate (no runner hooks) nothing is ever sent."""
        fn = getattr(job.get("hooks"), "before_submit", None)
        if fn is None:
            raise policy.Blocked("Ready for your review: the form is filled. Press Submit in the browser window "
                                 "yourself.", "ready_for_review")
        fn(what, channel)

    @staticmethod
    def _after(job, outcome, **detail):
        """Report what a gated click did: submitted | not_submitted | unknown (the runner records it)."""
        fn = getattr(job.get("hooks"), "after_submit", None)
        if fn is not None:
            fn(outcome, detail)

    def _blocked(self, page):
        """'sign-in' or 'captcha' when the page is a wall the app must not touch, else ''."""
        has_pw = any(r for _fr, r in self._eval_all(page, PASSWORD_JS, False))
        if is_signin(page.url, has_pw):
            return "sign-in"
        return "captcha" if self._captcha(page) else ""

    def _wall(self, key, kind):
        name = portals.name(key)
        if kind == "sign-in":
            self.portal_state[key] = False
            return (f"{name} needs a sign-in. Open Settings, select Sign in to {name}, sign in once in the "
                    f"browser window, then press Retry.")
        return f"{name} showed a CAPTCHA. The app never solves CAPTCHAs: open the job in the browser to finish it."

    def _portal(self, page, job, key):
        """Find the job on one portal and start its application. Returns (outcome, detail, page):
        not_found | blocked (detail = reason) | company (moved to the company's own site) | applied (the Apply
        click itself applied, confirmation shown) | form (the portal's application form is open on `page`)."""
        name = portals.name(key)
        self._hook(job, "step", f"Searching {name} for the job")
        self._goto(page, portals.search_url(key, job.get("title", ""), job.get("company", "")))
        wall = self._blocked(page)
        if wall:
            return "blocked", self._wall(key, wall), page
        results = [r for _fr, rs in self._eval_all(page, portals.RESULTS_JS, portals.PORTALS[key]["link_re"]) for r in rs]
        hit = portals.best_result(results, job.get("title", ""), job.get("company", ""), job.get("location", ""))
        if hit is None:
            return "not_found", "", page
        self._hook(job, "step", f"Opening the matching job on {name}")
        self._goto(page, hit["href"])
        wall = self._blocked(page)
        if wall:
            return "blocked", self._wall(key, wall), page
        self.portal_state[key] = True
        job_url, before_text = page.url, self._text(page)
        self._hook(job, "step", f"Pressing Apply on {name}")
        btn = self._apply_button(page)
        gated = btn is not None and not re.match(COMPANY_SITE_RE, (btn.inner_text() or "").strip(), re.I)
        if gated:  # a portal's Apply can send the application at once (Naukri one-click Apply)
            self._gate(job, f"Apply on {name}", key)
        after = self._after if gated else (lambda *a, **k: None)  # outcomes are reported only for a gated click
        page = self._find_form(page, force=True)  # "Apply on company site" first, else Apply / Easy Apply
        if page.url.split("#")[0] != job_url.split("#")[0] and portals.portal_of(page.url) is None:
            after(job, "not_submitted", why="moved to the company website")
            return "company", f"{name}'s link to the company website", self._find_form(page)
        if confirmed(before_text, self._text(page), job_url, page.url):
            after(job, "submitted", why=f"{name} confirmed the one-click Apply")
            return "applied", name, page
        after(job, "not_submitted", why="Apply opened the next step")  # a form, dialog or sign-in: nothing sent
        wall = self._blocked(page)
        if wall:
            return "blocked", self._wall(key, wall), page
        return "form", name, page

    def _portals(self, page, job):
        """Try each enabled portal in order. Returns (outcome, detail, channel, page)."""
        keys = portals.enabled(job.get("portals"))
        self._hook(job, "before_portal")  # rate limit: waits out the pause since the last portal application
        for key in keys:
            outcome, detail, page = self._portal(page, job, key)
            if outcome != "not_found":
                return outcome, detail, ("company" if outcome == "company" else key), page
        names = " or ".join(portals.name(k) for k in keys)
        return "not_found", (f"Not found on portals: the company has no application on its own website, and no "
                             f"{names} job matched this company and title. Open the job to apply another way."), None, page

    def _register(self, job, page, channel, extra):
        s = Session(job, page)
        s.channel = channel
        self.sessions[s.id] = s
        return {"session": s.id, "job": {"title": job.get("title"), "company": job.get("company")},
                "url": page.url, "filled": [], "missing": [], "manual": [], "site": urlparse(page.url).hostname or "",
                "details_file": str(details.path()), "channel": channel, **extra}

    def _open(self, job):
        details.ensure()
        ctx = self._context()
        page = ctx.new_page()
        via, channel = None, "company"
        if direct.is_aggregator(job["url"]):
            use_portals = bool(portals.enabled(job.get("portals", [])))
            via = self._company_site(page, job, search=not use_portals)
            if via is None and use_portals:
                outcome, detail, channel, page = self._portals(page, job)
                if outcome in ("blocked", "not_found", "applied"):
                    if outcome == "not_found":
                        self._goto(page, direct.careers_search_url(job.get("company", ""), job.get("title", "")))
                    return self._register(job, page, channel, {"page": "portal-" + outcome, "reason": detail,
                                                               "via": detail if outcome == "applied" else None})
                via = detail  # "form": the portal's own application; "company": the company site via the portal
            elif via is None:
                return self._register(job, page, None, {"page": "no-company-site", "via": None})
            else:
                page = self._find_form(page)
        else:
            self._goto(page, job["url"])
            page = self._find_form(page)
        s = Session(job, page)
        s.channel = channel
        self.sessions[s.id] = s
        out = self._pass(s)
        out.update(via=via, channel=channel)
        return out

    def _session(self, sid):
        s = self.sessions.get(sid)
        if not s or s.page.is_closed():
            self.sessions.pop(sid, None)
            raise ApplyError("That application window was closed. Press Apply on the job again.")
        return s

    def _answer(self, sid, answers):
        s = self._session(sid)
        det = details.load() or {}
        saved = details.save_answers(answers_to_save(s.missing, answers, det))
        out = self._pass(s)
        out["saved"] = saved
        return out

    def _rescan(self, sid):
        s = self._session(sid)
        try:  # follow the user to the tab they opened last (for example the company's posting)
            pages = [p for p in s.page.context.pages if not p.is_closed()]
            if pages and pages[-1] is not s.page:
                s.page = pages[-1]
        except Exception:  # noqa: BLE001
            pass
        return self._pass(s)

    # --- full auto apply (fill, answer from the details file, step through, submit, verify) ---
    def _eval_all(self, page, js, arg=None):
        out = []
        for i, fr in enumerate(page.frames):
            try:
                r = fr.evaluate(js, arg if arg is not None else f"b{i}_") if arg is not False else fr.evaluate(js)
            except Exception:  # noqa: BLE001
                continue
            out.append((fr, r))
        return out

    def _buttons(self, page):
        return [(fr, b) for fr, bs in self._eval_all(page, BUTTONS_JS) for b in bs]

    def _captcha(self, page):
        return any(r for _fr, r in self._eval_all(page, CAPTCHA_JS, False))

    def _errors(self, page):
        return [e for _fr, r in self._eval_all(page, ERROR_JS, False) for e in r]

    @staticmethod
    def _text(page):
        try:
            return page.inner_text("body", timeout=5000)
        except Exception:  # noqa: BLE001
            return ""

    def _proof(self, page, key):
        folder = config.APPLICATIONS_DIR
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{re.sub(r'[^0-9a-f]', '', key)[:16] or 'job'}.png"
        try:
            page.screenshot(path=str(path), full_page=True, timeout=15000)
        except Exception:  # noqa: BLE001 - a proof screenshot must never undo a submitted application
            return None
        return str(path)

    def _auto(self, job, max_steps=8):
        """Apply to one job end to end. Returns a status dict: submitted | needs_answers | needs_you |
        no_company_site | failed | ready_for_review | policy_blocked | outcome_unknown."""
        try:
            r = self._open(job)
        except policy.Blocked as e:  # stopped before a portal's Apply click
            return {"session": None, "status": e.status, "reason": str(e), "channel": None, "via": None, "url": "",
                    "site": "", "title": job.get("title"), "company": job.get("company"), "ai": [], "ai_error": ""}
        s = self.sessions[r["session"]]
        channel = r.get("channel", "company")
        portal = portals.PORTALS.get(channel, {}).get("name")  # None on the company's own site
        base = {"session": s.id, "via": r.get("via"), "channel": channel, "title": job.get("title"),
                "company": job.get("company")}

        def done(status, **kw):
            out = {**base, "status": status, "url": s.page.url, "site": urlparse(s.page.url).hostname or "",
                   "ai": list(s.ai_used), "ai_error": s.ai_error}
            out.update(kw)
            return out

        def submitted():
            return done("submitted", proof=self._proof(s.page, job.get("key", "")),
                        confirmation=next((ln.strip() for ln in self._text(s.page).splitlines()
                                           if CONFIRM_RE.search(ln)), "Confirmation page shown")[:300])
        if r["page"] == "no-company-site":
            return done("no_company_site", reason="No application on the company's own website was found; "
                                                  "the job is only posted on an aggregator.")
        if r["page"] in ("portal-blocked", "portal-not_found"):
            return done("needs_you", reason=r["reason"])
        if r["page"] == "portal-applied":  # one-click Apply: the confirmation appeared on the click itself
            return submitted()
        submits_with_errors = pressed = 0
        for step in range(max_steps * 2 if portal else max_steps):
            self._hook(job, "step", f"Filling step {step + 1} on {portal or 'the company website'}")
            if r["page"] == "sign-in":
                return done("needs_you", reason=f"{r['site']} needs a sign-in. Sign in once in the browser window, then retry.")
            if r["missing"]:
                return done("needs_answers", missing=r["missing"], filled=r["filled"])
            if r["manual"]:
                return done("needs_you", reason="; ".join(r["manual"]))
            # a portal dialog's last step (LinkedIn "Review") has buttons but no fields
            if r["page"] == "no-form" and not (portal and pressed and self._buttons(s.page)):
                return done("needs_you", reason=(
                    f"Confirmation not found: {portal} showed no confirmation that the application was sent. "
                    "Open the job in the browser to check it." if portal and pressed else
                    "No application form was found on the company page."))
            if self._captcha(s.page):
                return done("needs_you", reason="The form has a CAPTCHA. Solve it in the browser window and press Submit there.")
            btns = self._buttons(s.page)
            pick = next(((fr, b) for fr, b in btns if b["kind"] == "submit"), None) or \
                next(((fr, b) for fr, b in btns if b["kind"] == "next"), None)
            if pick is None:
                return done("needs_you", reason="No Submit button was found on the form.")
            fr, b = pick
            self._hook(job, "before_click", b["kind"])  # unattended mode: never press Submit when paused or in quiet hours
            risky = b["kind"] == "submit" or AMBIGUOUS_RE.match(b["text"] or "") is not None
            if risky:
                try:
                    self._gate(job, b["text"], channel)
                except policy.Blocked as e:
                    return done(e.status, reason=str(e))
            before_text, before_url = self._text(s.page), s.page.url
            try:
                fr.locator(f'[data-rmjf-btn="{b["id"]}"]').first.click(timeout=5000)
            except Exception:  # noqa: BLE001
                if risky:  # the click may still have reached the site: never assume it did not
                    self._after(job, "unknown", why="the click failed")
                    return done("outcome_unknown", reason=f"The {b['text']} button may not have been pressed. Check in "
                                                          "the browser window whether the application was sent.")
                return done("needs_you", reason=f"The {b['text']} button could not be clicked.")
            s.page.wait_for_timeout(1200)
            self._settle(s.page)
            pages = [p for p in s.page.context.pages if not p.is_closed()]
            if s.page.is_closed() and pages:
                s.page = pages[-1]
            pressed += 1
            if confirmed(before_text, self._text(s.page), before_url, s.page.url):
                # a click that was not gated still sent it: recorded and flagged, never hidden
                self._after(job, "submitted" if risky else "submitted_ungated", why="confirmation shown",
                            button=b["text"][:60])
                return submitted()
            if self._captcha(s.page):
                if risky:
                    self._after(job, "unknown", why="CAPTCHA after Submit")
                    return done("outcome_unknown", reason="A CAPTCHA appeared after Submit. Solve it in the browser "
                                                          "window, then record whether the application was sent.")
                return done("needs_you", reason="A CAPTCHA appeared after Submit. Solve it in the browser window.")
            errs = self._errors(s.page)
            if risky and errs:  # the site rejected the form and shows why: it was not sent
                self._after(job, "not_submitted", why="validation errors shown")
            elif risky and b["kind"] != "submit" and any(f["kind"] != "file" for f in self._scan(s.page)):
                self._after(job, "not_submitted", why="the next step of the form opened")
            elif risky:  # no confirmation and no errors: it may or may not have been sent; never press it again
                self._after(job, "unknown", why="no confirmation")
                return done("outcome_unknown", reason=f"Confirmation not found after {b['text']} on "
                                                      f"{portal or s.page.url.split('/')[2]}. Check in the browser "
                                                      "window whether the application was sent, then record it.")
            if b["kind"] == "submit" and errs:
                submits_with_errors += 1
                if submits_with_errors >= 2:
                    return done("failed", reason="The site rejected the form: " + "; ".join(errs)[:400])
            r = self._pass(s)
        return done("failed", reason="The application did not reach a confirmation page.")

    def close_session(self, sid):
        def run():
            s = self.sessions.pop(sid, None)
            if s and not s.page.is_closed():
                s.page.close()
        return self.call(run)

    def auto(self, job):
        return self.call(self._auto, job, timeout=900)

    # --- public API (any thread) ---
    def open(self, job):
        return self.call(self._open, job)

    def answer(self, sid, answers):
        return self.call(self._answer, sid, answers)

    def rescan(self, sid):
        return self.call(self._rescan, sid)

    # --- portal sign-in (the session lives only in the app's browser profile; no password is stored) ---
    def _signin(self, key):
        page = self._context().new_page()
        self._goto(page, portals.signin_url(key))
        return {"opened": portals.name(key), **self.portal_status()}

    def _check_portals(self):
        self.portal_state.update(portals.signed_in(self._context().cookies()))
        return self.portal_status()

    def portal_status(self):
        """Last known sign-in state per portal (None = not checked yet). Never touches the browser."""
        return {"portals": [{"key": k, "name": p["name"], "signed_in": self.portal_state.get(k)}
                            for k, p in portals.PORTALS.items()]}

    def signin(self, key):
        return self.call(self._signin, key)

    def check_portals(self):
        return self.call(self._check_portals)


ASSISTANT = Assistant()

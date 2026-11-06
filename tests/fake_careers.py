"""A local fake careers web: company application forms in the styles of common applicant-tracking systems,
plus a fake aggregator. Every submission is recorded so tests can check exactly what the employer received.
Company pages are served on 127.0.0.1; the fake aggregator is reached as `localhost` (a different host)."""
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HEAD = "<!doctype html><html><head><meta charset=utf-8><title>{t}</title></head><body>"
THANKS = HEAD.format(t="Thank you") + "<h1>Thank you for applying!</h1><p>Your application has been submitted.</p></body></html>"

GREENHOUSE = HEAD.format(t="Senior AI Program Manager") + """
<h1>Senior AI Program Manager</h1><p>Thank you for your interest in this role.</p>
<form id="application-form" method="post" action="/submit/gh" enctype="multipart/form-data">
 <label for="first_name">First Name *</label><input id="first_name" name="first_name" required>
 <label for="last_name">Last Name *</label><input id="last_name" name="last_name" required>
 <label for="email">Email *</label><input id="email" name="email" type="email" required>
 <label for="phone">Phone</label><input id="phone" name="phone" type="tel">
 <label for="resume">Resume/CV *</label><input id="resume" name="resume" type="file" required>
 <label for="cover">Cover Letter</label><input id="cover" name="cover" type="file">
 <label for="li">LinkedIn Profile</label><input id="li" name="linkedin">
 <label for="country">Country *</label>
 <select id="country" name="country" required><option value="">Select...</option><option>United States</option><option>India</option></select>
 <fieldset><legend>Are you legally authorized to work in the country for which you are applying? *</legend>
  <label><input type="radio" name="auth" value="yes" required> Yes</label><label><input type="radio" name="auth" value="no"> No</label></fieldset>
 <fieldset><legend>Will you now or in the future require visa sponsorship? *</legend>
  <label><input type="radio" name="spon" value="yes" required> Yes</label><label><input type="radio" name="spon" value="no"> No</label></fieldset>
 <label for="why">Why do you want to work here? *</label><textarea id="why" name="why" required></textarea>
 <label><input type="checkbox" name="privacy" required> I agree to the privacy policy *</label>
 <button type="submit">Submit Application</button>
</form></body></html>"""

LEVER_JOB = HEAD.format(t="AI Engineer") + """<h1>AI Engineer</h1><p>Remote, India.</p>
<a class="postings-btn" href="/lever/1/apply">Apply for this job</a></body></html>"""
LEVER_APPLY = HEAD.format(t="AI Engineer - Apply") + """
<form method="post" action="/submit/lever" enctype="multipart/form-data">
 <div class="application-question"><div class="application-label">Resume/CV ✱</div>
  <input type="file" name="resume" required></div>
 <div class="application-question"><label for="n">Full name ✱</label><input id="n" name="name" required></div>
 <div class="application-question"><label for="e">Email ✱</label><input id="e" name="email" required></div>
 <div class="application-question"><label for="c">Current company</label><input id="c" name="org"></div>
 <div class="application-question"><label for="y">Years of experience ✱</label><input id="y" name="years" type="number" required></div>
 <button type="submit" class="template-btn-submit">Submit application</button>
</form></body></html>"""

MULTI = {
    1: """<form method="post" action="/multi/2"><label for="fn">First name *</label><input id="fn" name="fn" required>
          <label for="em">Email address *</label><input id="em" name="em" type="email" required>
          <button type="submit">Next</button></form>""",
    2: """<form method="post" action="/multi/3"><label for="np">Notice period *</label><input id="np" name="np" required>
          <label for="ct">City *</label><input id="ct" name="ct" required>
          <button type="submit">Save and continue</button></form>""",
    3: """<form method="post" action="/submit/multi"><h2>Review your application</h2>
          <label><input type="checkbox" name="ok" required> I confirm the information is correct *</label>
          <button type="submit">Submit</button></form>""",
}

CAPTCHA = HEAD.format(t="Apply") + """<form method="post" action="/submit/captcha">
 <label for="a">Full name *</label><input id="a" name="a" required>
 <label for="b">Email *</label><input id="b" name="b" required>
 <label for="c">Phone *</label><input id="c" name="c" required>
 <iframe src="/recaptcha/api2/anchor?k=test" width="304" height="78" title="reCAPTCHA"></iframe>
 <button type="submit">Submit application</button></form></body></html>"""

SIGNIN = HEAD.format(t="Sign in") + """<h1>Sign in to apply</h1><form method="post" action="/signin">
 <label for="u">Email *</label><input id="u" name="u" type="email" required>
 <label for="p">Password *</label><input id="p" name="p" type="password" required>
 <button type="submit">Sign in</button></form></body></html>"""

REJECT = HEAD.format(t="Apply") + """<form method="post" action="/submit/reject">
 <label for="a">First name *</label><input id="a" name="a" required>
 <label for="b">Last name *</label><input id="b" name="b" required>
 <label for="c">Email *</label><input id="c" name="c" required>
 <button type="submit">Submit application</button></form></body></html>"""
REJECTED = HEAD.format(t="Apply") + """<div role="alert" class="error">Applications for this role are closed.</div>
""" + REJECT[REJECT.index("<form"):]

NO_BUTTON = HEAD.format(t="Apply") + """<form><label for="a">First name *</label><input id="a" name="a" required>
 <label for="b">Last name *</label><input id="b" name="b" required><label for="c">Email *</label><input id="c" name="c" required>
 </form></body></html>"""


# ---------------- fake job portals (Naukri, LinkedIn), served under /naukri and /linkedin ----------------
# Each portal job: {"id", "title", "company", "location", "age", "kind"}; kind selects the job page behaviour.
POST_JS = """const post = (path, data) => fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'},
  body: JSON.stringify(data)});"""

NAUKRI_CHAT_JS = POST_JS + """
const QS = [{q: 'How many years of experience do you have?', type: 'text'},
            {q: 'Are you willing to relocate to Pune?', type: 'radio', options: ['Yes', 'No']}];
let i = 0; const answers = [];
function ask() {
  const q = QS[i]; const li = document.createElement('li'); li.className = 'botItem';
  const m = document.createElement('div'); m.className = 'botMsg'; m.textContent = q.q; li.append(m);
  document.getElementById('msgs').append(li);
  const inp = document.getElementById('inp'); inp.replaceChildren();
  if (q.type === 'text') { const d = document.createElement('div'); d.className = 'textArea'; d.contentEditable = 'true'; inp.append(d); }
  else for (const o of q.options) { const l = document.createElement('label'); l.className = 'chatbot_Chip';
    const r = document.createElement('input'); r.type = 'radio'; r.name = 'chip' + i; r.value = o; l.append(r, ' ' + o); inp.append(l); }
}
document.getElementById('apply-button').onclick = () => { document.getElementById('drawer').hidden = false; ask(); };
document.getElementById('save').onclick = () => {
  const q = QS[i]; const inp = document.getElementById('inp');
  const v = q.type === 'text' ? inp.querySelector('[contenteditable]').innerText.trim()
                              : (inp.querySelector('input:checked') || {}).value;
  if (!v) return;
  answers.push([q.q, v]); const u = document.createElement('li'); u.className = 'userMsg'; u.textContent = v;
  document.getElementById('msgs').append(u); i++;
  if (i < QS.length) return ask();
  document.getElementById('drawer').remove();
  post(location.pathname.replace('/job-listings-', '/applied-'), {answers}).then(() => {
    const p = document.createElement('p'); p.textContent = 'You have successfully applied to ' + document.title; document.body.append(p); });
};"""

NAUKRI_ONECLICK_JS = POST_JS + """
document.getElementById('apply-button').onclick = () => post(location.pathname.replace('/job-listings-', '/applied-'), {answers: []})
  .then(() => { const p = document.createElement('p'); p.textContent = 'You have successfully applied to ' + document.title; document.body.append(p); });"""

NAUKRI_SIGNIN_JS = """document.getElementById('apply-button').onclick = () => { document.getElementById('login').hidden = false; };"""

LINKEDIN_EASY_JS = POST_JS + """
const STEPS = [
  {h: 'Contact info', html: '<label for="em">Email address *</label><input id="em" type="email" required>' +
     '<label for="ph">Mobile phone number *</label><input id="ph" type="tel" required>', btn: 'Next'},
  {h: 'Resume and questions', html: '<label for="cv">Upload resume *</label><input id="cv" type="file" required>' +
     '<fieldset><legend>Are you willing to relocate to Pune? *</legend><label><input type="radio" name="rel" value="Yes" required> Yes</label>' +
     '<label><input type="radio" name="rel" value="No"> No</label></fieldset>', btn: 'Review'},
  {h: 'Review your application', html: '<p>Check your answers, then submit.</p>', btn: 'Submit application'}];
const data = {}; const pressed = []; let i = 0;
const NOCON = document.body.dataset.kind === 'nocon';
function show() {
  const s = STEPS[i]; const b = document.getElementById('body');
  b.innerHTML = '<h2>' + s.h + '</h2><p>Step ' + (i + 1) + ' of 3</p>' + s.html +
    '<div id="err" role="alert"></div><button type="button" id="go">' + s.btn + '</button>';
  document.getElementById('go').onclick = go;
}
function go() {
  for (const el of document.querySelectorAll('#body input')) {
    if (el.type === 'radio') { if (el.checked) data[el.name] = el.value; continue; }
    data[el.id] = el.type === 'file' ? (el.files[0] || {}).name || '' : el.value;
  }
  const missing = [...document.querySelectorAll('#body input[required]')].filter((el) =>
    el.type === 'radio' ? !data[el.name] : !data[el.id]);
  if (missing.length) { document.getElementById('err').textContent = 'Please complete the required fields.'; return; }
  pressed.push(STEPS[i].btn); i++;
  if (i < STEPS.length) return show();
  post(location.pathname.replace('/jobs/view/', '/applied/'), {data, pressed}).then(() => {
    if (NOCON) { document.getElementById('dlg').remove(); return; }
    document.getElementById('body').innerHTML = '<h2>Your application was sent to ' + document.body.dataset.company + '!</h2>' +
      '<button type="button">Done</button>'; });
}
document.getElementById('easy').onclick = () => { document.getElementById('dlg').hidden = false; show(); };"""

PORTAL_SIGNIN_WALL = HEAD.format(t="Sign in") + """<h1>Sign in to see more jobs</h1>
 <form method="post" action="/signin"><label for="u">Email or phone</label><input id="u" name="u">
 <label for="p">Password</label><input id="p" name="p" type="password"><button type="submit">Sign in</button></form></body></html>"""
PORTAL_CAPTCHA_WALL = HEAD.format(t="Security check") + """<h1>Let's do a quick security check</h1>
 <iframe src="/recaptcha/api2/anchor?k=test" width="304" height="78" title="reCAPTCHA"></iframe></body></html>"""


def naukri_results(jobs):
    rows = "".join(f'<article class="jobTuple"><a class="title" href="/naukri/job-listings-{j["id"]}">{j["title"]}</a>'
                   f'<a class="subTitle">{j["company"]}</a><span>{j["location"]}</span><span>{j["age"]}</span></article>'
                   for j in jobs)
    return HEAD.format(t="Jobs - Naukri") + f'<header><input type="search" name="qp" aria-label="Search jobs"></header>' \
                                            f'<main><h1>Jobs</h1>{rows}</main></body></html>'


def linkedin_results(jobs):
    rows = "".join(f'<li class="job-card"><a href="/linkedin/jobs/view/{j["id"]}/">{j["title"]}</a><h4>{j["company"]}</h4>'
                   f'<span>{j["location"]}</span><time>{j["age"]}</time></li>' for j in jobs)
    return HEAD.format(t="Jobs | LinkedIn") + f'<header><input type="search" aria-label="Search by title"></header>' \
                                              f'<main><ul>{rows}</ul></main></body></html>'


def naukri_job(j, company_site_url):
    head = HEAD.format(t=f'{j["title"]} - {j["company"]}') + f'<header><input type="search" aria-label="Search jobs"></header>' \
                                                             f'<h1>{j["title"]}</h1><p>{j["company"]} · {j["location"]}</p>'
    kind = j["kind"]
    if kind == "company":
        return head + f'<a href="{company_site_url}" target="_blank" id="company-site-button">Apply on company site</a></body></html>'
    if kind == "captcha":
        return head + '<iframe src="/recaptcha/api2/anchor?k=test" width="304" height="78" title="reCAPTCHA"></iframe>' \
                      '<button id="apply-button">Apply</button></body></html>'
    if kind == "signin":
        return head + '<button id="apply-button">Apply</button><div id="login" class="login-layer" hidden>' \
                      '<label for="u">Email ID / Username</label><input id="u"><label for="pw">Password</label>' \
                      '<input id="pw" type="password"><button type="button">Login</button></div>' \
                      f'<script>{NAUKRI_SIGNIN_JS}</script></body></html>'
    if kind == "oneclick":
        return head + f'<button id="apply-button">Apply</button><script>{NAUKRI_ONECLICK_JS}</script></body></html>'
    return head + '<button id="apply-button">Apply</button><div id="drawer" class="chatbot_Drawer" hidden>' \
                  '<ul id="msgs"></ul><div id="inp"></div><button type="button" id="save" class="sendMsg">Save</button></div>' \
                  f'<script>{NAUKRI_CHAT_JS}</script></body></html>'


def linkedin_job(j, company_site_url):
    head = HEAD.format(t=f'{j["title"]} | {j["company"]} | LinkedIn').replace(
        "<body>", f'<body data-kind="{j["kind"]}" data-company="{j["company"]}">') + \
        f'<header><input type="search" aria-label="Search by title"></header><h1>{j["title"]}</h1><p>{j["company"]} · {j["location"]}</p>'
    if j["kind"] == "company":
        return head + f'<a href="{company_site_url}" target="_blank">Apply on company website</a></body></html>'
    if j["kind"] == "captcha":
        return PORTAL_CAPTCHA_WALL
    return head + '<button id="easy" aria-label="Easy Apply to this job">Easy Apply</button>' \
                  '<div id="dlg" role="dialog" aria-modal="true" aria-label="Apply" hidden>' \
                  '<button type="button" aria-label="Dismiss">×</button><div id="body"></div></div>' \
                  f'<script>{LINKEDIN_EASY_JS}</script></body></html>'


class FakeCareers:
    def __init__(self):
        self.submissions = {}
        self.portal_jobs = {"naukri": [], "linkedin": []}
        self.walls = {"naukri": None, "linkedin": None}  # "signin" / "captcha" on the portal's search page
        self.portal_applied = {}  # "naukri-<id>" / "linkedin-<id>" -> what the portal received
        self.log = []  # (method, host, path) of every request, in order
        self.company_site_url = "/lever/1"
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def url(self, path, host="127.0.0.1"):
        return f"http://{host}:{self.port}{path}"

    def close(self):
        self.srv.shutdown()

    def _handler(self):
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def page(self, body, code=200):
                b = body.encode()
                self.send_response(code)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def redirect(self, to):
                self.send_response(302)
                self.send_header("Location", to)
                self.end_headers()

            def portal_get(self, p):
                m = re.fullmatch(r"/naukri/[a-z0-9-]+-jobs", p)
                if m or p == "/linkedin/jobs/search/":
                    key = "naukri" if m else "linkedin"
                    wall = fake.walls[key]
                    if wall:
                        return self.page(PORTAL_SIGNIN_WALL if wall == "signin" else PORTAL_CAPTCHA_WALL)
                    return self.page((naukri_results if m else linkedin_results)(fake.portal_jobs[key]))
                m = re.fullmatch(r"/naukri/job-listings-(\w+)|/linkedin/jobs/view/(\w+)/", p)
                if m:
                    key, jid = ("naukri", m.group(1)) if m.group(1) else ("linkedin", m.group(2))
                    j = next((j for j in fake.portal_jobs[key] if j["id"] == jid), None)
                    if j:
                        return self.page((naukri_job if key == "naukri" else linkedin_job)(j, fake.company_site_url))
                return self.page("not found", 404)

            def do_GET(self):
                p = urlparse(self.path).path
                host = (self.headers.get("Host") or "").split(":")[0]
                fake.log.append(("GET", host, p))
                if host != "localhost" and (p.startswith("/naukri/") or p.startswith("/linkedin/")):
                    return self.portal_get(p)
                if host == "localhost":  # the aggregator
                    if p == "/details/1":
                        return self.page(HEAD.format(t="Adzuna") + f'<h1>Job</h1><a href="/land/ad/1?v=x">Apply for this job</a>'
                                         f'<a href="/details/1/apply">Apply with Adzuna</a></body></html>')
                    if p == "/land/ad/1":
                        return self.redirect(fake.url("/gh/1"))
                    if p == "/details/2":
                        return self.page(HEAD.format(t="Adzuna") + '<h1>Agency job</h1><a href="/details/2/apply">Apply</a></body></html>')
                    if p.startswith("/details/2/apply"):
                        return self.page(SIGNIN)
                    return self.page("not found", 404)
                pages = {"/gh/1": GREENHOUSE, "/lever/1": LEVER_JOB, "/lever/1/apply": LEVER_APPLY,
                         "/multi/1": HEAD.format(t="Step 1") + MULTI[1], "/captcha/1": CAPTCHA, "/signin/1": SIGNIN,
                         "/reject/1": REJECT, "/nobutton/1": NO_BUTTON,
                         "/search": HEAD.format(t="Search") + "<p>Search results</p></body></html>",
                         "/recaptcha/api2/anchor": "<html><body>captcha</body></html>"}
                if p in pages:
                    return self.page(pages[p])
                return self.page("not found", 404)

            def do_POST(self):
                p = urlparse(self.path).path
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n)
                fake.log.append(("POST", (self.headers.get("Host") or "").split(":")[0], p))
                m = re.fullmatch(r"/naukri/applied-(\w+)|/linkedin/applied/(\w+)/", p)
                if m:
                    fake.portal_applied[f"naukri-{m.group(1)}" if m.group(1) else f"linkedin-{m.group(2)}"] = json.loads(body)
                    return self.page("{}")
                m = re.fullmatch(r"/multi/(\d)", p)
                if m:
                    fake.submissions.setdefault("multi_steps", []).append(parse_qs(body.decode()))
                    return self.page(HEAD.format(t=f"Step {m.group(1)}") + MULTI[int(m.group(1))])
                if p == "/submit/reject":
                    return self.page(REJECTED)
                if p.startswith("/submit/"):
                    fake.submissions[p.rsplit("/", 1)[1]] = body
                    return self.page(THANKS)
                return self.page(SIGNIN)
        return H


def fields(body: bytes) -> dict:
    """Parse a multipart or url-encoded body into {name: value} (files -> filename)."""
    if b"Content-Disposition" not in body:
        return {k: v[0] for k, v in parse_qs(body.decode()).items()}
    out = {}
    for part in body.split(b"\r\n--"):
        m = re.search(rb'name="([^"]+)"(?:; filename="([^"]*)")?\r\n(?:Content-Type: [^\r]*\r\n)?\r\n(.*)', part, re.S)
        if m:
            out[m.group(1).decode()] = (m.group(2) or m.group(3).rstrip(b"\r\n-")).decode("utf-8", "replace")
    return out

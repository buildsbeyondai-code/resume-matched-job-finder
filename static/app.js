"use strict";
// Resume-Matched Job Finder UI. All posting text is inserted with textContent (never innerHTML).
const $ = (s, r = document) => r.querySelector(s);
const PAGE = 50;
const S = { state: null, results: null, page: 1, runId: null, poll: null, undo: null, lastParams: null };

const announce = (msg) => { const a = $("#announcer"); a.textContent = ""; setTimeout(() => (a.textContent = msg), 30); };
const list = (s) => (s || "").split(",").map((x) => x.trim()).filter(Boolean);

async function api(method, url, body, headers) {
  const opts = { method, headers: Object.assign({ "X-RMJF-Request": "1" }, headers || {}) };
  if (body !== undefined) {
    if (body instanceof Blob || body instanceof ArrayBuffer) opts.body = body;
    else { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
  }
  let r;
  try { r = await fetch(url, opts); } catch (e) { throw Object.assign(new Error("Cannot reach the app server. Check that it is still running."), { network: true }); }
  const data = r.headers.get("content-type")?.includes("json") ? await r.json() : null;
  if (!r.ok) throw Object.assign(new Error((data && data.error) || `Request failed (${r.status})`), { field: data && data.field, errors: data && data.errors, status: r.status });
  return data;
}

/* ---------- navigation (links navigate) ---------- */
const APP_SUBS = ["unattended", "today", "runlog"];
function showTab() {
  const tab = (location.hash || "#search").slice(1);
  const valid = ["search", "profile", "saved", "applications", "settings", ...APP_SUBS].includes(tab) ? tab : "search";
  const top = APP_SUBS.includes(valid) ? "applications" : valid; // the unattended views live under Applications
  document.querySelectorAll("[data-view]").forEach((v) => (v.hidden = v.dataset.view !== valid));
  document.querySelectorAll(".tabs a").forEach((a) => (a.dataset.tab === top ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
  $("#appNav").hidden = top !== "applications";
  document.querySelectorAll("#appNav a").forEach((a) => (a.dataset.sub === valid ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
  document.title = `${$(`#view-${valid} h1`).textContent} · Resume-Matched Job Finder`;
  if (valid === "saved") loadSaved();
  if (valid === "applications") loadApps();
  if (valid === "settings") { loadPortals(); loadPolicy(); }
  if (valid === "unattended") loadUA(true);
  if (valid === "today") loadSummary();
  if (valid === "runlog") loadRunLog();
}
window.addEventListener("hashchange", () => { showTab(); $("#main").focus(); });

/* ---------- preferences ---------- */
function initPrefs() {
  const sel = $("#themeSel");
  sel.value = localStorage.getItem("rmjf-theme") || "system";
  sel.addEventListener("change", () => {
    if (sel.value === "system") { localStorage.removeItem("rmjf-theme"); document.documentElement.removeAttribute("data-theme"); }
    else { localStorage.setItem("rmjf-theme", sel.value); document.documentElement.setAttribute("data-theme", sel.value); }
    announce(`Theme: ${sel.options[sel.selectedIndex].text}`);
  });
  const d = $("#descToggle");
  d.checked = localStorage.getItem("rmjf-desc") !== "off";
  d.addEventListener("change", () => {
    localStorage.setItem("rmjf-desc", d.checked ? "on" : "off");
    document.documentElement.classList.toggle("no-desc", !d.checked);
  });
  const net = () => ($("#offline").hidden = navigator.onLine);
  window.addEventListener("online", net); window.addEventListener("offline", net); net();
}

/* ---------- state ---------- */
async function loadState() {
  S.state = await api("GET", "/api/state");
  const { profile, settings } = S.state;
  $("#noProfileNote").hidden = !!profile;
  renderProfile(profile);
  const f = $("#searchForm");
  if (!S.lastParams) {
    f.countries.value = (settings.countries || []).join(", ");
    f.mode.value = settings.mode || "remote";
    f.skills.value = profile ? profile.skills.slice(0, 3).join(", ") : "";
  }
  $("#r-min").value = settings.min_score; $("#r-age").value = settings.max_age_days;
  $("#s-countries").value = (settings.countries || []).join(", "); $("#s-mode").value = settings.mode;
  $("#s-seniority").value = settings.seniority; $("#s-min").value = settings.min_score; $("#s-age").value = settings.max_age_days;
  $("#s-companies").value = (settings.target_companies || []).join(", ");
  const fp = settings.fallback_portals || [];
  $("#s-naukri").checked = fp.includes("naukri"); $("#s-linkedin").checked = fp.includes("linkedin");
  $("#s-ai").checked = settings.ai_answers !== false;
  if ($("#s-pause").getAttribute("aria-invalid") !== "true") $("#s-pause").value = settings.portal_pause_s;
  renderSources();
}

/* ---------- profile (J1) ---------- */
function renderProfile(p) {
  $("#noResume").hidden = !!p;
  $("#profileForm").hidden = !p;
  if (!p) return;
  $("#profileFile").textContent = p.filename ? `From ${p.filename} · ${p.text_chars.toLocaleString()} characters read` : "";
  $("#p-skills").value = p.skills.join(", ");
  $("#p-titles").value = p.titles.join(", ");
  $("#p-years").value = p.years;
}

$("#resumeFile").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  const err = $("#uploadErr"), st = $("#uploadStatus"), input = e.target;
  err.hidden = true; input.removeAttribute("aria-invalid");
  if (!file) return;
  if (file.size > 10 * 1024 * 1024) return uploadError(`${file.name} is ${(file.size / 1048576).toFixed(1)} MB. The limit is 10 MB.`);
  st.textContent = `Reading ${file.name}…`; input.setAttribute("aria-busy", "true");
  try {
    const res = await api("POST", "/api/resume", file, { "X-Filename": file.name.replace(/[^\w .()-]/g, "") });
    st.textContent = `Read ${file.name}. Check the extracted details below, then select Save profile.`;
    renderProfile(res.profile); await loadState();
    announce(`Resume read: ${res.profile.skills.length} skills found.`);
    $("#p-skills").focus();
  } catch (x) { st.textContent = ""; uploadError(x.message); }
  finally { input.removeAttribute("aria-busy"); }
  function uploadError(msg) {
    err.textContent = `Upload failed: ${msg} Choose another file to try again.`; err.hidden = false;
    input.setAttribute("aria-invalid", "true"); input.value = ""; input.focus();
  }
});

$("#profileForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const sum = $("#profileErrors"); sum.hidden = true;
  ["#p-skills", "#p-years"].forEach((s) => $(s).removeAttribute("aria-invalid"));
  const body = { skills: list($("#p-skills").value), titles: list($("#p-titles").value), years: $("#p-years").value };
  const btn = e.submitter; btn.setAttribute("aria-busy", "true"); btn.disabled = true;
  try {
    await api("PUT", "/api/profile", body); await loadState(); announce("Profile saved.");
    if (S.results) loadResults();
  } catch (x) {
    const id = x.field === "years" ? "p-years" : "p-skills";
    $("#" + id).setAttribute("aria-invalid", "true");
    sum.replaceChildren(Object.assign(document.createElement("p"), { textContent: "Fix this to save:" }));
    const a = Object.assign(document.createElement("a"), { href: "#" + id, textContent: x.message });
    a.addEventListener("click", (ev) => { ev.preventDefault(); $("#" + id).focus(); });
    sum.append(a); sum.hidden = false; sum.focus();
  } finally { btn.removeAttribute("aria-busy"); btn.disabled = false; }
});

/* ---------- search (J2, J5) ---------- */
function formParams() {
  const f = $("#searchForm");
  return { countries: list(f.countries.value), mode: f.mode.value, skills: list(f.skills.value), keywords: f.keywords.value.trim(), companies: list(f.companies.value) };
}
$("#searchForm").addEventListener("submit", (e) => { e.preventDefault(); runSearch(formParams()); });
$("#refreshBtn").addEventListener("click", () => S.lastParams && runSearch(S.lastParams));

async function runSearch(params) {
  if (S.runId) return;
  $("#failBanner").hidden = true;
  const btn = $("#searchBtn"); btn.disabled = true; btn.setAttribute("aria-busy", "true");
  try {
    const r = await api("POST", "/api/search", params);
    S.runId = r.id; S.lastParams = r.params;
    $("#progress").hidden = false; $("#firstRun").hidden = true;
    announce("Search started.");
    pollSearch();
  } catch (x) { showFail(x.message); endBusy(); }
}
function endBusy() { const b = $("#searchBtn"); b.disabled = false; b.removeAttribute("aria-busy"); S.runId = null; }
async function pollSearch() {
  try {
    const s = await api("GET", `/api/search/${S.runId}`);
    const ul = $("#sourceStatus"); ul.replaceChildren();
    for (const src of s.sources) {
      const li = document.createElement("li");
      const st = Object.assign(document.createElement("span"), { className: `st st-${src.state}`, textContent: { queued: "Waiting", running: "Searching", done: "Done", failed: "Failed", cancelled: "Cancelled", skipped: "Skipped (no API key)" }[src.state] });
      li.append(st, ` · ${src.label}${src.state === "done" ? ` (${src.count})` : ""}`);
      ul.append(li);
    }
    if (!s.done) { S.poll = setTimeout(pollSearch, 400); return; }
    $("#progress").hidden = true; endBusy();
    if (s.cancelled) { announce("Search cancelled. Previous results are still shown."); return; }
    await loadResults(true);
  } catch (x) { $("#progress").hidden = true; endBusy(); showFail(x.message); }
}
$("#cancelBtn").addEventListener("click", async () => {
  if (!S.runId) return;
  $("#cancelBtn").setAttribute("aria-busy", "true");
  await api("POST", `/api/search/${S.runId}/cancel`).catch(() => {});
  $("#cancelBtn").removeAttribute("aria-busy");
});
function showFail(msg, failed) {
  const b = $("#failBanner"); b.replaceChildren();
  if (failed && failed.length) {
    b.append(`Some sources could not be searched, so results may be incomplete: `);
    b.append(failed.map((f) => `${f.label} (${f.error})`).join("; ") + ". ");
    const retry = Object.assign(document.createElement("button"), { type: "button", className: "btn text", textContent: "Retry search" });
    retry.addEventListener("click", () => S.lastParams && runSearch(S.lastParams));
    b.append(retry);
  } else b.textContent = msg;
  b.hidden = false;
}

/* ---------- results (J3, J4) ---------- */
async function loadResults(fromSearch) {
  const r = await api("GET", "/api/results");
  if (!r.params) { $("#results").hidden = true; $("#firstRun").hidden = false; return; }
  S.results = r; S.lastParams = r.params; S.page = 1;
  $("#refreshBtn").hidden = false; $("#firstRun").hidden = true; $("#results").hidden = false;
  if (r.failed && r.failed.length) showFail("", r.failed); else $("#failBanner").hidden = true;
  if (!fromSearch) {
    const f = $("#searchForm"); f.countries.value = r.params.countries.join(", "); f.mode.value = r.params.mode;
    f.skills.value = r.params.skills.join(", "); f.keywords.value = r.params.keywords; f.companies.value = r.params.companies.join(", ");
  }
  renderResults();
  if (fromSearch) {
    const shown = visibleJobs().length, nNew = r.jobs.filter((j) => j.is_new).length;
    announce(`Search complete. ${shown} matching jobs${nNew ? `, ${nNew} new` : ""}.${r.failed.length ? ` ${r.failed.length} sources failed.` : ""}`);
  }
}
function visibleJobs() {
  if (!S.results) return [];
  const min = +$("#r-min").value || 0, age = +$("#r-age").value || 0, comp = $("#r-company").value.trim().toLowerCase(), sal = +$("#r-salary").value || 0;
  const cutoff = age ? new Date(Date.now() - age * 864e5).toISOString().slice(0, 10) : "";
  return S.results.jobs.filter((j) => j.state !== "hidden" && j.score >= min && (!cutoff || !j.posted || j.posted >= cutoff)
    && (!comp || (j.company || "").toLowerCase().includes(comp)) && (!sal || (j.salary_max || j.salary_min || 0) >= sal));
}
function renderResults() {
  const jobs = visibleJobs(), total = S.results.jobs.length;
  const pages = Math.max(1, Math.ceil(jobs.length / PAGE)); S.page = Math.min(S.page, pages);
  $("#resultMeta").textContent = `· ${jobs.length} shown of ${total} found${S.results.created ? ` · searched ${new Date(S.results.created * 1000).toLocaleString()}` : ""}`;
  $("#emptyResults").hidden = jobs.length > 0;
  $("#emptyWhy").textContent = total ? `${total} jobs were found but all are below your result filters or hidden.` : "Try fewer keywords or skills, another work mode, or more countries.";
  renderList($("#jobList"), jobs.slice((S.page - 1) * PAGE, S.page * PAGE));
  const pager = $("#pager"); pager.replaceChildren();
  if (pages > 1) for (let i = 1; i <= pages; i++) {
    const b = Object.assign(document.createElement("button"), { type: "button", className: "btn text", textContent: i });
    b.setAttribute("aria-label", `Page ${i}`); if (i === S.page) b.setAttribute("aria-current", "page");
    b.addEventListener("click", () => { S.page = i; renderResults(); $("#h-results").scrollIntoView(); });
    pager.append(b);
  }
}
["#r-min", "#r-age", "#r-company", "#r-salary"].forEach((s) => $(s).addEventListener("input", () => S.results && renderResults()));
$("#refineForm").addEventListener("submit", (e) => e.preventDefault());
$("#widenBtn").addEventListener("click", () => {
  $("#r-min").value = 0; $("#r-company").value = ""; $("#r-salary").value = ""; $("#r-age").value = "";
  if (S.results && S.results.jobs.length) { renderResults(); announce(`Result filters cleared. ${visibleJobs().length} jobs shown.`); return; }
  const f = $("#searchForm"); f.keywords.value = ""; f.skills.value = ""; f.companies.value = ""; f.mode.value = "any";
  f.keywords.focus(); announce("Keywords, skills, companies and work mode cleared. Select Search to try again.");
});

function fmtSalary(j) {
  if (!j.salary_min && !j.salary_max) return "";
  const f = (n) => Math.round(n).toLocaleString();
  return j.salary_min && j.salary_max && j.salary_min !== j.salary_max ? `${f(j.salary_min)}–${f(j.salary_max)} ${j.currency || ""}`.trim() : `${f(j.salary_max || j.salary_min)} ${j.currency || ""}`.trim();
}
const MODE = { remote: "Remote", hybrid: "Hybrid", onsite: "On-site" };
function renderList(ol, jobs) {
  ol.replaceChildren();
  for (const j of jobs) {
    const li = $("#jobTpl").content.firstElementChild.cloneNode(true);
    const band = j.score >= 70 ? "high" : j.score >= 40 ? "mid" : "low";
    $(".score", li).classList.add(band);
    $(".score-num", li).textContent = j.score;
    $(".score-label", li).textContent = { high: "Strong match", mid: "Partial match", low: "Weak match" }[band];
    $(".score", li).setAttribute("aria-label", `Match score ${j.score} out of 100, ${$(".score-label", li).textContent}`);
    const url = /^https:\/\//.test(j.url) ? j.url : "";
    const tl = $(".job-link", li); tl.textContent = j.title;
    if (url) { tl.href = url; tl.title = "Open the job posting in a new tab"; } else tl.removeAttribute("target");
    const t = $(".expand", li); t.setAttribute("aria-label", `Details: ${j.title}`);
    const detail = $(".job-detail", li); const did = `d-${j.key}-${ol.id}`; detail.id = did; t.setAttribute("aria-controls", did);
    t.addEventListener("click", () => { const open = detail.hidden; detail.hidden = !open; t.setAttribute("aria-expanded", String(open)); t.textContent = open ? "Hide details" : "Details"; });
    $(".job-sub", li).textContent = [j.company, j.location || "Location not stated", j.posted ? `Posted ${j.posted}` : "", fmtSalary(j)].filter(Boolean).join(" · ");
    const chips = $(".chips", li);
    const chip = (txt, cls) => chips.append(Object.assign(document.createElement("span"), { className: "chip " + (cls || ""), textContent: txt }));
    if (j.is_new) chip("New", "new");
    chip(MODE[j.mode] || j.mode);
    chip(`${j.matched.length} of ${j.matched.length + j.missing.length} skills matched`);
    if ((j.sources || []).length > 1) chip(`${j.sources.length} sources`);
    const st = (AP.apps[j.key] || {}).status; if (st) chip(STATUS[st], `st-${st}`);
    if (/^https:\/\/(job-boards|boards)\.greenhouse\.io|^https:\/\/jobs\.lever\.co|^https:\/\/jobs\.smartrecruiters\.com/.test(url)) chip("No sign-in to apply", "new");
    $(".matched", li).textContent = j.matched.join(", ") || "None";
    $(".missing", li).textContent = j.missing.join(", ") || "None";
    $(".sources-line", li).textContent = "Listed on: " + (j.sources || [{ name: j.source }]).map((s) => s.name).join(", ");
    $(".job-desc", li).textContent = j.description || "No description provided by this source.";
    const v = $(".view", li);
    if (url) { v.href = url; v.setAttribute("aria-label", `Open job: ${j.title} at ${j.company} (opens in a new tab)`); } else v.hidden = true;
    const ul = $(".job-url", li);
    if (url) { const a = Object.assign(document.createElement("a"), { href: url, target: "_blank", rel: "noopener noreferrer", textContent: url });
      ul.append("Job link: ", a); } else ul.textContent = "This source did not provide a link.";
    for (const s of j.sources || []) if (s.url && s.url !== url && /^https:\/\//.test(s.url)) {
      ul.append(" · ", Object.assign(document.createElement("a"), { href: s.url, target: "_blank", rel: "noopener noreferrer", textContent: `Also on ${s.name}` })); }
    const ap = $(".apply", li);
    if (url) { ap.setAttribute("aria-label", `Apply: ${j.title} at ${j.company}`); ap.addEventListener("click", () => startApply(j, ap)); }
    else ap.hidden = true;
    const save = $(".save", li); const saved = j.state === "saved";
    save.textContent = saved ? "Unsave" : "Save"; save.setAttribute("aria-pressed", String(saved));
    save.setAttribute("aria-label", `${saved ? "Unsave" : "Save"} ${j.title}`);
    save.addEventListener("click", () => setState(j, saved ? "" : "saved"));
    const hide = $(".hide", li); hide.setAttribute("aria-label", `Hide ${j.title}`);
    hide.hidden = ol.id === "savedList";
    hide.addEventListener("click", () => setState(j, "hidden", li));
    ol.append(li);
  }
}
async function setState(j, state, li) {
  const prev = j.state || "";
  try {
    await api("POST", `/api/jobs/${j.key}/state`, { state });
    j.state = state;
    if (state === "hidden") {
      const next = li && (li.nextElementSibling || li.previousElementSibling);
      renderResults();
      S.undo = { j, prev };
      $("#undoText").textContent = `Hidden: ${j.title}`; $("#undoBar").hidden = false;
      (next && document.getElementById(next.querySelector(".job-detail").id)?.closest("li")?.querySelector(".expand") || $("#undoBtn")).focus();
    } else {
      announce(state === "saved" ? `Saved ${j.title}` : `Removed ${j.title} from saved`);
      if (S.results) renderResults();
      if (!$("#view-saved").hidden) loadSaved(); else updateSavedCount();
    }
  } catch (x) { announce(`Could not update: ${x.message}`); }
}
$("#undoBtn").addEventListener("click", async () => {
  if (!S.undo) return;
  const { j, prev } = S.undo; S.undo = null;
  await api("POST", `/api/jobs/${j.key}/state`, { state: prev }); j.state = prev;
  $("#undoBar").hidden = true; renderResults(); announce(`Restored ${j.title}`);
});

/* ---------- auto apply ---------- */
const STATUS = { queued: "Queued", running: "Applying now", submitted: "Submitted", applied_manually: "Applied (by you)",
  needs_answers: "Needs your answers", needs_you: "Needs you", no_company_site: "No company website", failed: "Failed",
  cancelled: "Cancelled", dismissed: "Dismissed", ready_for_review: "Ready for your review",
  policy_blocked: "Blocked by your policy", outcome_unknown: "Outcome unknown" };
const RETRYABLE = ["needs_you", "failed", "no_company_site", "cancelled", "policy_blocked", "ready_for_review"];
const OPENABLE = ["needs_you", "no_company_site", "ready_for_review", "outcome_unknown"];
const CHANNEL = { naukri: "Naukri", linkedin: "LinkedIn" };
// where the application was made: the company's own site unless a portal was used
const viaLabel = (a) => (a.status === "submitted" ? `via ${CHANNEL[a.channel] || "company site"}` : "");
let clockSkew = 0; // server time minus browser time, so countdowns are right even if the clocks differ
function stepText(a) {
  if (a.wait_until) {
    const left = Math.max(0, Math.ceil(a.wait_until - (Date.now() / 1000 + clockSkew)));
    return `Waiting ${left} s before the next job-portal application (rate limit, set in Settings).`;
  }
  return a.step ? `${a.step}…` : "Applying now: the browser window is filling and submitting the application…";
}
const AP = { key: null, poll: null, opener: null, apps: {} };
const el = (tag, props, ...kids) => { const e = Object.assign(document.createElement(tag), props || {}); e.append(...kids); return e; };
const clean = (t) => (t || "").replace(/\s*\*\s*$/, "");
function questionInput(q, id) {
  let input;
  if (q.options && q.options.length) {
    input = el("select", {}, el("option", { value: "", textContent: "Choose…" }));
    for (const o of q.options) input.append(el("option", { value: o, textContent: o }));
  } else if (q.kind === "textarea") input = el("textarea", { rows: 3 });
  else input = el("input", { type: q.kind === "email" ? "email" : q.kind === "number" ? "number" : "text" });
  Object.assign(input, { id, required: true }); input.dataset.qid = q.qid;
  return el("div", {}, el("label", { htmlFor: id, textContent: clean(q.label) }), input);
}
function collect(form) {
  const answers = {}; let empty = null;
  for (const i of form.querySelectorAll("[data-qid]")) {
    const v = i.value.trim(); i.setAttribute("aria-invalid", String(!v));
    if (v) answers[i.dataset.qid] = v; else empty = empty || i;
  }
  return { answers, empty };
}
function statusText(a) {
  if (!a) return "";
  if (a.status === "submitted" && CHANNEL[a.channel]) return `Submitted on ${CHANNEL[a.channel]} (the company has no application on its own website). ${a.confirmation || ""}`;
  if (a.status === "submitted") return `Submitted on ${a.site || "the company website"}${a.via ? ` (found through ${a.via})` : ""}. ${a.confirmation || ""}`;
  if (a.status === "queued") return "Waiting in the queue…";
  if (a.status === "running") return stepText(a);
  if (a.status === "needs_answers") return `${a.missing.length} required question${a.missing.length === 1 ? " is" : "s are"} not in your resume details${a.ai_error ? "" : " and Claude could not answer from your resume"}.${a.ai_error ? ` ${a.ai_error}` : ""}`;
  return a.reason || STATUS[a.status] || a.status;
}
// what Claude Opus 5.5 answered on the form, with the fact each answer came from
function aiDetails(a) {
  const used = (a && a.ai) || [];
  if (!used.length) return null;
  return el("details", { className: "ai-used" },
    el("summary", { textContent: `Claude answered ${used.length} question${used.length === 1 ? "" : "s"}` }),
    el("ul", {}, ...used.map((u) => el("li", {}, el("strong", { textContent: `${clean(u.label)}: ` }), u.value,
      ...(u.basis ? [el("span", { className: "muted", textContent: ` (${u.basis})` })] : [])))));
}
function renderApplyDlg(a) {
  $("#applyStatus").textContent = statusText(a);
  const ai = aiDetails(a); $("#applyAI").replaceChildren(...(ai ? [ai] : []));
  const need = a && a.status === "needs_answers";
  $("#applyForm").hidden = !need;
  if (need) { const qs = $("#applyQs"); qs.replaceChildren(...a.missing.map((q, i) => questionInput(q, `apq-${i}`))); $("#applyQs [data-qid]").focus(); }
  const proof = a && a.proof; $("#applyProof").hidden = !proof;
  if (proof) $("#applyProofLink").href = `/api/applications/${a.key}/proof`;
  $("#applyRetry").hidden = !(a && RETRYABLE.includes(a.status));
}
async function pollApply() {
  clearTimeout(AP.poll);
  if (!AP.key || !$("#applyDlg").open) return;
  try {
    const a = await api("GET", `/api/applications/${AP.key}`);
    if (["queued", "running"].includes(a.status)) { $("#applyStatus").textContent = statusText(a); AP.poll = setTimeout(pollApply, 1500); }
    else { renderApplyDlg(a); announce(`${STATUS[a.status]}: ${a.title}`); refreshApps(); }
  } catch (x) { $("#applyStatus").textContent = x.message; }
}
async function startApply(j, btn) {
  AP.key = j.key; AP.opener = btn;
  $("#applyTitle").textContent = `Apply: ${j.title} at ${j.company}`;
  renderApplyDlg(null); $("#applyStatus").textContent = "Starting…";
  if (!$("#applyDlg").open) $("#applyDlg").showModal();
  try {
    const r = await api("POST", "/api/autoapply", { keys: [j.key] });
    if (!r.queued.length) { const a = r.items.find((x) => x.key === j.key); renderApplyDlg(a); if (a && !["queued", "running"].includes(a.status)) return; }
    pollApply();
  } catch (x) { $("#applyStatus").textContent = x.message; }
}
$("#applyForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const { answers, empty } = collect($("#applyForm"));
  if (empty) { $("#applyStatus").textContent = "Answer every question, then press Save and continue."; empty.focus(); return; }
  $("#applySave").disabled = true;
  try { await api("POST", "/api/applications/answers", { answers }); $("#applyForm").hidden = true; pollApply(); }
  catch (x) { $("#applyStatus").textContent = x.message; }
  finally { $("#applySave").disabled = false; }
});
$("#applyRetry").addEventListener("click", async () => {
  try { await api("POST", `/api/applications/${AP.key}/retry`); renderApplyDlg(null); pollApply(); }
  catch (x) { $("#applyStatus").textContent = x.message; }
});
$("#applyClose").addEventListener("click", () => $("#applyDlg").close());
$("#applyDlg").addEventListener("close", () => { clearTimeout(AP.poll); if (AP.opener && document.contains(AP.opener)) AP.opener.focus(); });

function autoCandidates() {
  return visibleJobs().filter((j) => j.state !== "hidden" && /^https:\/\//.test(j.url) && !["submitted", "applied_manually"].includes((AP.apps[j.key] || {}).status));
}
$("#autoBtn").addEventListener("click", async () => {
  const n = Math.min(autoCandidates().length, 25);
  if (!POLICY) await loadPolicy();
  const mode = POLICY ? POLICY.effective_mode : "SEARCH_ONLY";
  const what = { SEARCH_ONLY: "Applying is switched off (Search only). Choose \"Prepare for review\" or \"Submit automatically\" in Settings → Application policy.",
    PREPARE_FOR_REVIEW: `Fill the applications for the top ${n} job${n === 1 ? "" : "s"} shown on each company's own website and stop before Submit, so you can check and send each one yourself?`,
    AUTHORIZED_AUTO_SUBMIT: `Submit applications in your name to the top ${n} job${n === 1 ? "" : "s"} shown (minimum match ${$("#r-min").value || 0}), on each company's own website, within your policy (at most ${POLICY && POLICY.daily_cap} a day)? Jobs already applied to are skipped.` }[mode];
  $("#autoText").textContent = n ? `${what} Questions your resume details can't answer are collected on the Applications tab.` : "No jobs to apply to: every shown job is already applied to or has no link.";
  $("#autoOk").hidden = !n || mode === "SEARCH_ONLY";
  $("#autoOk").textContent = mode === "PREPARE_FOR_REVIEW" ? "Prepare applications" : "Submit applications";
  $("#autoDlg").showModal();
});
$("#autoCancel").addEventListener("click", () => $("#autoDlg").close());
$("#autoOk").addEventListener("click", async () => {
  const keys = autoCandidates().slice(0, 25).map((j) => j.key);
  $("#autoOk").disabled = true;
  try { await api("POST", "/api/autoapply", { keys }); $("#autoDlg").close(); location.hash = "#applications"; }
  catch (x) { $("#autoText").textContent = x.message; }
  finally { $("#autoOk").disabled = false; }
});

let appPoll = null;
async function refreshApps() {
  try { const r = await api("GET", "/api/applications"); AP.apps = Object.fromEntries(r.items.map((a) => [a.key, a]));
    if (r.now) clockSkew = r.now - Date.now() / 1000;
    const done = (r.counts.submitted || 0) + (r.counts.applied_manually || 0); $("#appCount").textContent = done ? `(${done})` : "";
    return r; } catch { return null; }
}
async function loadApps() {
  clearTimeout(appPoll);
  const r = await refreshApps(); if (!r) return;
  $("#appEmpty").hidden = r.items.length > 0;
  $("#appSummary").replaceChildren(...Object.entries(r.counts).map(([k, v]) => el("span", { className: `chip st-${k}`, textContent: `${STATUS[k] || k}: ${v}` })));
  const qf = $("#qForm"); const focusInQ = qf.contains(document.activeElement);
  if (!focusInQ) { qf.hidden = !r.questions.length; $("#qList").replaceChildren(...r.questions.map((q, i) => questionInput(q, `q-${i}`))); }
  $("#appList").replaceChildren(...r.items.map((a) => {
    const actions = el("div", { className: "row" });
    if (a.proof) actions.append(el("a", { className: "btn text", href: `/api/applications/${a.key}/proof`, target: "_blank", rel: "noopener", textContent: "Proof" }));
    const open = /^https:\/\//.test(a.url || "") ? a.url : a.job_url;
    if (OPENABLE.includes(a.status) && /^https:\/\//.test(open || "")) {
      const ob = el("a", { className: "btn text", href: open, target: "_blank", rel: "noopener noreferrer", textContent: "Open in browser" },
        el("span", { className: "sr-only", textContent: ` to finish ${a.title} yourself (opens in a new tab)` }));
      actions.append(ob);
    }
    if (a.status === "outcome_unknown") {  // Submit may have reached the site: only you can tell, it is never retried
      for (const [label, outcome] of [["It was sent", "submitted"], ["It was not sent", "not_submitted"]]) {
        const b = el("button", { type: "button", className: "btn text", textContent: label },
          el("span", { className: "sr-only", textContent: `: ${a.title}` }));
        b.addEventListener("click", async () => {
          try { await api("POST", `/api/applications/${a.key}/resolve`, { outcome }); announce(`Recorded: ${label.toLowerCase()}.`); }
          catch (x) { announce(x.message); }
          loadApps();
        });
        actions.append(b);
      }
    }
    if (RETRYABLE.includes(a.status)) {
      const rt = el("button", { type: "button", className: "btn text", textContent: "Retry" });
      rt.addEventListener("click", async () => { await api("POST", `/api/applications/${a.key}/retry`); loadApps(); });
      actions.append(rt);
    }
    if (["needs_you", "no_company_site", "ready_for_review", "policy_blocked"].includes(a.status)) {
      const mk = el("button", { type: "button", className: "btn text", textContent: "I applied" });
      mk.addEventListener("click", async () => { await api("POST", `/api/applications/${a.key}/mark`); loadApps(); });
      actions.append(mk);
    }
    const when = a.submitted_at ? ` · ${new Date(a.submitted_at * 1000).toLocaleString()}` : "";
    const via = viaLabel(a);
    return el("li", { className: "app-item" },
      el("div", {}, el("h3", { textContent: `${a.title} · ${a.company}` }),
        el("p", {}, el("span", { className: `chip st-${a.status}`, textContent: STATUS[a.status] || a.status }), " ",
          ...(via ? [el("span", { className: "chip via", textContent: via }), " "] : []), statusText(a) + when),
        ...[aiDetails(a)].filter(Boolean)),
      actions);
  }));
  // the current job, its step and the rate-limit countdown (announced politely)
  const active = r.items.some((a) => ["queued", "running"].includes(a.status));
  const cur = r.items.find((a) => a.status === "running");
  $("#appRun").hidden = !active;
  if (active) {
    // the live region changes only when the step changes; the ticking countdown is visual only
    const queued = r.counts.queued || 0;
    const step = cur ? (cur.wait_until ? "Waiting before the next job-portal application (rate limit, set in Settings)." : stepText(cur)) : "Starting…";
    const text = `${cur ? `${cur.title} · ${cur.company}: ` : ""}${step} ${queued} more in the queue.`;
    if ($("#appProgress").textContent !== text) $("#appProgress").textContent = text;
    const left = cur && cur.wait_until ? Math.max(0, Math.ceil(cur.wait_until - (Date.now() / 1000 + clockSkew))) : null;
    $("#appCountdown").textContent = left === null ? "" : `${left} s left`;
  }
  if (active && !$("#view-applications").hidden) appPoll = setTimeout(loadApps, 1000);
}
$("#appCancel").addEventListener("click", async () => {
  const b = $("#appCancel"); b.disabled = true;
  try { const r = await api("POST", "/api/autoapply/cancel"); announce(`Auto apply cancelled. ${r.cancelled} queued job${r.cancelled === 1 ? " was" : "s were"} not started; Retry applies to them later.`); loadApps(); }
  catch (x) { announce(x.message); }
  finally { b.disabled = false; }
});
$("#qForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const { answers, empty } = collect($("#qForm"));
  if (empty) { announce("Answer every question, then press Save and continue."); empty.focus(); return; }
  $("#qSave").disabled = true;
  try { const r = await api("POST", "/api/applications/answers", { answers }); announce(`Saved ${r.saved} answers. ${r.retried.length} applications continue.`); document.activeElement.blur(); loadApps(); }
  catch (x) { announce(x.message); }
  finally { $("#qSave").disabled = false; }
});
refreshApps();

async function loadSaved() {
  const r = await api("GET", "/api/saved").catch(() => ({ jobs: [] }));
  $("#emptySaved").hidden = r.jobs.length > 0;
  renderList($("#savedList"), r.jobs);
  $("#savedCount").textContent = r.jobs.length ? `(${r.jobs.length})` : "";
}
async function updateSavedCount() { const r = await api("GET", "/api/saved").catch(() => ({ jobs: [] })); $("#savedCount").textContent = r.jobs.length ? `(${r.jobs.length})` : ""; }

/* ---------- settings ---------- */
function renderSources() {
  const tb = $("#sourceRows"); tb.replaceChildren();
  for (const s of S.state.sources) {
    const tr = document.createElement("tr");
    const td = (txt) => { const c = document.createElement("td"); c.textContent = txt; tr.append(c); return c; };
    td(s.label); td(s.kind + (s.token ? ` · ${s.token}` : ""));
    td(!s.configured ? (s.enabled ? "On, skipped until an API key is set (see README)" : "Off, needs API key") : s.enabled ? "On" : "Off");
    const a = td("");
    const tog = Object.assign(document.createElement("button"), { type: "button", className: "btn text", textContent: s.enabled ? "Turn off" : "Turn on" });
    tog.setAttribute("aria-label", `${s.enabled ? "Turn off" : "Turn on"} ${s.label}`);
    tog.addEventListener("click", async () => { await api("PUT", `/api/sources/${s.id}`, { enabled: !s.enabled }); await loadState(); announce(`${s.label} turned ${s.enabled ? "off" : "on"}`); });
    const rm = Object.assign(document.createElement("button"), { type: "button", className: "btn text", textContent: "Remove" });
    rm.setAttribute("aria-label", `Remove ${s.label}`);
    rm.addEventListener("click", async () => {
      if (!confirm(`Remove the source "${s.label}"? You can add it again later.`)) return;
      await api("DELETE", `/api/sources/${s.id}`); await loadState(); announce(`Removed ${s.label}`);
    });
    a.append(tog, rm); tb.append(tr);
  }
}
$("#addSource").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = $("#a-err"), tok = $("#a-token"); err.hidden = true; tok.removeAttribute("aria-invalid");
  try {
    await api("POST", "/api/sources", { kind: $("#a-kind").value, token: tok.value.trim(), label: $("#a-label").value.trim() });
    tok.value = ""; $("#a-label").value = ""; await loadState(); announce("Source added.");
  } catch (x) { err.textContent = x.message; err.hidden = false; tok.setAttribute("aria-invalid", "true"); tok.focus(); }
});
$("#settingsForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const clamp = (v, lo, hi, d) => Math.min(hi, Math.max(lo, Number.isFinite(+v) && v !== "" ? +v : d));
  const pause = $("#s-pause"), perr = $("#s-pause-err");
  const pauseBad = (msg) => { perr.textContent = msg; perr.hidden = false; pause.setAttribute("aria-invalid", "true"); pause.focus(); };
  perr.hidden = true; pause.removeAttribute("aria-invalid");
  // nothing is saved while the pause is invalid; the typed value is kept so it can be corrected
  if (!/^\s*\d+\s*$/.test(pause.value) || +pause.value < 5 || +pause.value > 600) return pauseBad("Enter a whole number of seconds from 5 to 600.");
  try {
    await api("PUT", "/api/settings", { countries: list($("#s-countries").value), mode: $("#s-mode").value, seniority: $("#s-seniority").value,
      min_score: clamp($("#s-min").value, 0, 100, 40), max_age_days: clamp($("#s-age").value, 1, 365, 30), target_companies: list($("#s-companies").value),
      fallback_portals: ["naukri", "linkedin"].filter((k) => $(`#s-${k}`).checked), portal_pause_s: +pause.value, ai_answers: $("#s-ai").checked });
  } catch (x) { if (x.field === "portal_pause_s") return pauseBad(x.message); $("#settingsSaved").textContent = x.message; return; }
  await loadState(); $("#settingsSaved").textContent = "Settings saved.";
  setTimeout(() => ($("#settingsSaved").textContent = ""), 4000);
});

/* portal sign-in: opens the portal's sign-in page in the app's browser; status comes from its cookies */
function renderPortals(r) {
  const word = (v) => (v === true ? "signed in" : v === false ? "not signed in" : "not checked yet");
  $("#portalStatus").replaceChildren(...r.portals.map((p) => el("li", { textContent: `${p.name}: ${word(p.signed_in)}` })));
}
async function loadPortals() { try { renderPortals(await api("GET", "/api/portals")); } catch { /* shown as not checked */ } }
for (const b of [$("#signNaukri"), $("#signLinkedin"), $("#checkPortals")]) {
  b.addEventListener("click", async () => {
    const msg = $("#portalMsg"); b.setAttribute("aria-busy", "true"); b.disabled = true;
    msg.textContent = b.dataset.portal ? "Opening the browser window…" : "Checking…";
    try {
      const r = await api("POST", b.dataset.portal ? `/api/portals/${b.dataset.portal}/signin` : "/api/portals/check");
      renderPortals(r);
      msg.textContent = r.opened ? `Sign in to ${r.opened} in the browser window, then select Check sign-in status.` : "Sign-in status updated.";
    } catch (x) { msg.textContent = x.message; }
    finally { b.removeAttribute("aria-busy"); b.disabled = false; }
  });
}
$("#deleteAll").addEventListener("click", () => $("#confirmDlg").showModal());
$("#confirmCancel").addEventListener("click", () => $("#confirmDlg").close());
$("#confirmOk").addEventListener("click", async () => {
  await api("DELETE", "/api/data"); $("#confirmDlg").close();
  S.results = null; S.lastParams = null; $("#results").hidden = true; $("#refreshBtn").hidden = true;
  await loadState(); await loadResults(); updateSavedCount(); UA.loaded = false; refreshUA(); announce("All your data was deleted.");
});

/* ---------- unattended auto-apply (status bar + kill switch on every view, settings, summary, run log) ---------- */
const UA = { st: null, loaded: false, setup: false, reviewing: false, poll: null, wasActive: false, needs: null };
const pad = (n) => String(n).padStart(2, "0");
const fmtTime = (ts) => { const d = new Date(ts * 1000); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; };
const fmtDay = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const fmtDateTime = (ts) => `${fmtDay(new Date(ts * 1000))} ${fmtTime(ts)}`;
const alertNow = (msg) => { const a = $("#uaAlert"); a.textContent = ""; setTimeout(() => (a.textContent = msg), 30); };
const UA_FIELDS = { interval_min: "ua-interval", min_score: "ua-min", daily_cap: "ua-cap", cooldown_days: "ua-cool",
  quiet_start: "ua-qs", quiet_end: "ua-qe", searches: "ua-searches" };
const UA_LIMITS = { interval_min: [15, 1440, "Interval must be 15-1440 minutes."], min_score: [0, 100, "Minimum match score must be 0-100."],
  daily_cap: [1, 100, "Daily cap must be 1-100 applications."], cooldown_days: [0, 365, "Company cooldown must be 0-365 days."] };
const OUTCOME = { submitted: ["✓", "Submitted"], needs_you: ["!", "Needs you"], skipped: ["–", "Skipped"], deferred: ["↻", "Deferred"],
  preview: ["◇", "Dry run"], attempting: ["…", "Applying now"] };
const TRIGGER = { schedule: "Scheduled", "catch-up": "Catch-up after the app was closed", manual: "Run now", preview: "Dry run" };

function uaStatusText(st) {
  if (st.mode === "off") return st.configured ? "Unattended auto-apply: Off" : "Unattended auto-apply: Off (not set up)";
  const parts = [st.mode === "paused" ? "Paused" : st.run_active ? "Running · applying now" : `Running · next run ${st.next_run_hhmm}`];
  if (st.run_active && st.current) parts.push(`${st.current.title} at ${st.current.company}`);
  parts.push(`${st.today.submitted} of ${st.today.cap} today`);
  if (st.needs_you) parts.push(`${st.needs_you} need${st.needs_you === 1 ? "s" : ""} you`);
  if (st.mode === "running" && st.quiet_now) parts.push("quiet hours now");
  return parts.join(" · ");
}
function renderUABar(st) {
  $("#uaStatus").textContent = uaStatusText(st);
  $("#uaIcon").textContent = st.mode === "running" ? "▶" : st.mode === "paused" ? "❚❚" : "○";
  $("#uaBar").dataset.mode = st.mode;
  const kill = $("#uaKill");
  kill.hidden = st.mode === "off";
  kill.textContent = st.mode === "paused" ? "Resume auto-apply" : "Pause auto-apply";
  const link = $("#uaBarLink");
  link.textContent = !st.configured ? "Set up" : st.needs_you ? "Review today" : "Details";
  link.href = !st.configured ? "#unattended" : st.needs_you ? "#today" : "#unattended";
  const b = $("#uaBanner");
  b.hidden = !(st.mode === "paused" && st.auto_paused);
  if (!b.hidden) b.textContent = st.pause_reason;
}
async function refreshUA() {
  clearTimeout(UA.poll);
  try {
    const st = await api("GET", "/api/unattended");
    UA.st = st; renderUABar(st);
    if (UA.needs !== null && st.needs_you > UA.needs) announce(`${st.needs_you - UA.needs} new unattended item${st.needs_you - UA.needs === 1 ? " needs" : "s need"} you. See the daily summary.`);
    UA.needs = st.needs_you;
    if (UA.wasActive && !st.run_active) { // a run just finished: refresh what is on screen
      if (!$("#view-today").hidden) loadSummary(true);
      if (!$("#view-runlog").hidden) loadRunLog(true);
      if (!$("#view-unattended").hidden) renderUAPage(st);
    }
    UA.wasActive = st.run_active;
  } catch { $("#uaStatus").textContent = "Unattended auto-apply: status unavailable (is the app server running?)"; }
  UA.poll = setTimeout(refreshUA, UA.st && UA.st.run_active ? 2000 : 5000);
}
$("#uaKill").addEventListener("click", async () => {
  const b = $("#uaKill");
  if (UA.st && UA.st.mode === "paused") {
    $("#resumeText").textContent = `Applications will be submitted in your name again. ${UA.st.summary_text} The next run is one interval from now.`;
    $("#resumeDlg").showModal(); return;
  }
  b.disabled = true; b.setAttribute("aria-busy", "true");
  try {
    const st = await api("POST", "/api/unattended/pause"); UA.st = st; renderUABar(st);
    alertNow("Auto-apply paused. Any application in progress stops before its next step and waits for you to check it.");
    if (!$("#view-unattended").hidden) renderUAPage(st);
  } catch (x) { alertNow(`Could not pause: ${x.message}`); }
  finally { b.disabled = false; b.removeAttribute("aria-busy"); b.focus(); }
});
$("#resumeCancel").addEventListener("click", () => $("#resumeDlg").close());
$("#resumeDlg").addEventListener("close", () => $("#uaKill").focus());
$("#resumeOk").addEventListener("click", async () => {
  const b = $("#resumeOk"); b.disabled = true; b.setAttribute("aria-busy", "true");
  try {
    const st = await api("POST", "/api/unattended/resume", { confirm: true }); UA.st = st; renderUABar(st);
    $("#resumeDlg").close(); alertNow(`Auto-apply resumed. Next run at ${st.next_run_hhmm}.`);
    if (!$("#view-unattended").hidden) renderUAPage(st);
  } catch (x) { $("#resumeText").textContent = x.message; }
  finally { b.disabled = false; b.removeAttribute("aria-busy"); }
});

/* settings page (J1) */
async function loadUA(fresh) {
  if (fresh) { UA.reviewing = false; }
  $("#uaError").hidden = true;
  if (!UA.loaded) { $("#uaLoading").hidden = false; $("#uaForm").hidden = true; $("#uaEmpty").hidden = true; }
  try {
    const st = await api("GET", "/api/unattended");
    UA.st = st; renderUABar(st); $("#uaLoading").hidden = true;
    renderUAPage(st, !UA.loaded || fresh); UA.loaded = true;
  } catch (x) {
    $("#uaLoading").hidden = true; $("#uaErrorText").textContent = `Couldn't load unattended settings: ${x.message}`; $("#uaError").hidden = false;
  }
}
$("#uaRetry").addEventListener("click", () => loadUA(true));
$("#uaSetupBtn").addEventListener("click", () => { UA.setup = true; renderUAPage(UA.st, true); $("#uaSearchList input, #ua-interval").focus(); });
function renderUAPage(st, fill) {
  const showForm = st.configured || UA.setup;
  $("#uaEmpty").hidden = showForm; $("#uaForm").hidden = !showForm;
  if (!showForm) return;
  $("#uaMode").textContent = st.mode === "running" ? `Running · next run ${st.next_run_hhmm}` : st.mode === "paused" ? "Paused" : "Off";
  if (fill && !UA.reviewing) {
    const s = st.settings;
    $("#ua-interval").value = s.interval_min; $("#ua-min").value = s.min_score; $("#ua-cap").value = s.daily_cap;
    $("#ua-cool").value = s.cooldown_days; $("#ua-qs").value = s.quiet_start; $("#ua-qe").value = s.quiet_end;
    clearUAErrors();
  }
  const checked = fill ? new Set(st.settings.searches) : new Set([...document.querySelectorAll("#uaSearchList input:checked")].map((i) => i.value));
  $("#uaSearchList").replaceChildren(...st.searches.map((x, i) => {
    const id = `ua-s-${i}`;
    const input = el("input", { type: "checkbox", id, value: x.id, checked: checked.has(x.id) });
    return el("li", {}, el("label", { className: "check", htmlFor: id }, input, ` ${x.label}`,
      el("span", { className: "muted", textContent: ` · last run ${fmtDateTime(x.last_run)}` })));
  }));
  $("#uaNoSearches").hidden = st.searches.length > 0;
  const on = st.mode !== "off";
  $("#uaSave").textContent = UA.reviewing ? "Confirm and turn on" : on ? "Save changes" : "Review and turn on";
  $("#uaReview").hidden = !UA.reviewing; $("#uaBack").hidden = !UA.reviewing; $("#uaFields").disabled = UA.reviewing;
  $("#uaRunNow").hidden = !on || UA.reviewing; $("#uaOff").hidden = !on || UA.reviewing;
  $("#uaPreview").hidden = !st.configured || UA.reviewing;
}
function clearUAErrors() {
  $("#uaErrors").hidden = true;
  for (const id of Object.values(UA_FIELDS)) { const f = $("#" + id); f.removeAttribute("aria-invalid"); $(`#${id}-err`).hidden = true; }
}
function uaBody() {
  return { searches: [...document.querySelectorAll("#uaSearchList input:checked")].map((i) => i.value),
    interval_min: $("#ua-interval").value.trim(), min_score: $("#ua-min").value.trim(), daily_cap: $("#ua-cap").value.trim(),
    cooldown_days: $("#ua-cool").value.trim(), quiet_start: $("#ua-qs").value, quiet_end: $("#ua-qe").value };
}
function uaClientErrors(b, enabling) {
  const errs = [];
  for (const [k, [lo, hi, msg]] of Object.entries(UA_LIMITS)) if (!/^\d{1,5}$/.test(b[k]) || +b[k] < lo || +b[k] > hi) errs.push({ field: k, message: msg });
  for (const k of ["quiet_start", "quiet_end"]) if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(b[k])) errs.push({ field: k, message: "Enter a time as HH:MM, for example 22:00." });
  if (enabling && !b.searches.length) errs.push({ field: "searches", message: "Switch on at least one saved search." });
  return errs;
}
function showUAErrors(errs) {
  clearUAErrors();
  const sum = $("#uaErrors");
  sum.replaceChildren(el("p", { textContent: `Fix ${errs.length === 1 ? "this" : `these ${errs.length} problems`} to save. Nothing was saved.` }));
  const ul = el("ul");
  for (const e of errs) {
    const id = UA_FIELDS[e.field];
    if (id) {
      const f = $("#" + id); f.setAttribute("aria-invalid", "true");
      const p = $(`#${id}-err`); p.textContent = e.message; p.hidden = false;
      const a = el("a", { href: "#" + id, textContent: e.message });
      a.addEventListener("click", (ev) => { ev.preventDefault(); (id === "ua-searches" ? $("#uaSearchList input") || f : f).focus(); });
      ul.append(el("li", {}, a));
    } else ul.append(el("li", { textContent: e.message }));
  }
  sum.append(ul); sum.hidden = false; sum.focus();
}
$("#uaForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const st = UA.st, on = st && st.mode !== "off";
  const btn = $("#uaSave");
  const b = uaBody();
  if (!UA.reviewing) {
    const errs = uaClientErrors(b, true);
    if (errs.length) return showUAErrors(errs);
  }
  btn.disabled = true; btn.setAttribute("aria-busy", "true"); $("#uaSaved").textContent = "";
  try {
    if (on) {
      const r = await api("PUT", "/api/unattended/settings", b); UA.st = r; renderUABar(r); clearUAErrors();
      renderUAPage(r, true); $("#uaSaved").textContent = "Changes saved."; announce("Unattended settings saved.");
    } else if (!UA.reviewing) {
      const r = await api("PUT", "/api/unattended/settings", { ...b, enable: true, validate_only: true });
      clearUAErrors(); UA.reviewing = true; $("#uaReviewText").textContent = r.summary_text; renderUAPage(st);
      $("#uaReview").setAttribute("tabindex", "-1"); $("#uaReview").focus();
    } else {
      const r = await api("PUT", "/api/unattended/settings", { ...b, enable: true, confirm: true });
      UA.reviewing = false; UA.st = r; renderUABar(r); renderUAPage(r, true);
      $("#uaSaved").textContent = `Turned on. ${uaStatusText(r)}.`;
      announce(`Unattended auto-apply is on. Next run at ${r.next_run_hhmm}. A dry run has started; it submits nothing.`);
      refreshUA();
    }
  } catch (x) {
    UA.reviewing = false; renderUAPage(UA.st);
    showUAErrors(x.status === 400 && x.field !== undefined ? (x.errors || [{ field: x.field, message: x.message }]) : [{ field: null, message: x.message }]);
  } finally { btn.disabled = false; btn.removeAttribute("aria-busy"); }
});
$("#uaBack").addEventListener("click", () => { UA.reviewing = false; renderUAPage(UA.st); $("#ua-interval").focus(); });
for (const [id, path, done] of [["#uaRunNow", "/api/unattended/run", "Run started. Progress shows in the status bar; results appear in the daily summary."],
  ["#uaPreview", "/api/unattended/preview", "Dry run started. It submits nothing; see the run log for what it would do."],
  ["#uaOff", "/api/unattended/off", "Unattended auto-apply is off. Your settings are kept."]]) {
  $(id).addEventListener("click", async () => {
    const b = $(id); b.disabled = true; b.setAttribute("aria-busy", "true");
    try { const r = await api("POST", path); UA.st = r; renderUABar(r); renderUAPage(r); $("#uaSaved").textContent = done; announce(done); refreshUA(); }
    catch (x) { $("#uaSaved").textContent = x.message; announce(x.message); }
    finally { b.disabled = false; b.removeAttribute("aria-busy"); }
  });
}

/* daily summary (J4, J5) and run log (J6) */
function outcomeChip(o) {
  const [icon, word] = OUTCOME[o] || ["", o];
  const i = el("span", { textContent: icon + " " }); i.setAttribute("aria-hidden", "true");
  return el("span", { className: `chip oc-${o}` }, i, word);
}
function cell(label, ...kids) { const td = el("td", {}, ...kids); td.dataset.label = label; return td; }
function jobActions(j, reload) {
  const box = el("div", { className: "row wrap" });
  if (j.outcome === "submitted" && j.has_proof)
    box.append(el("a", { className: "btn text", href: `/api/applications/${j.key}/proof`, target: "_blank", rel: "noopener",
      textContent: "Proof" }, el("span", { className: "sr-only", textContent: ` screenshot for ${j.title} at ${j.company} (opens in a new tab)` })));
  if (j.open) {
    if (j.app_status === "needs_answers") box.append(el("a", { className: "btn text", href: "#applications", textContent: "Answer question" },
      el("span", { className: "sr-only", textContent: ` for ${j.title}` })));
    if (/^https:\/\//.test(j.url || "")) box.append(el("a", { className: "btn text", href: j.url, target: "_blank", rel: "noopener noreferrer", textContent: "Open posting" },
      el("span", { className: "sr-only", textContent: ` ${j.title} to apply yourself (opens in a new tab)` })));
    const d = el("button", { type: "button", className: "btn text", textContent: "Dismiss" });
    d.setAttribute("aria-label", `Dismiss ${j.title} at ${j.company}`);
    d.addEventListener("click", async () => {
      d.disabled = true;
      try { await api("POST", `/api/unattended/jobs/${j.id}/dismiss`); announce(`Dismissed ${j.title}.`); await reload(); refreshUA(); }
      catch (x) { announce(x.message); d.disabled = false; }
    });
    box.append(d);
  } else if (j.outcome === "needs_you" && j.app_status) box.append(el("span", { className: "muted", textContent: `Now: ${STATUS[j.app_status] || j.app_status}` }));
  return box;
}
function jobTable(caption, jobs, reload, withTime = true) {
  const head = ["Time", "Job", "Company", "Score", "Outcome", "Source", "Reason", "Actions"].filter((h) => withTime || h !== "Time");
  const t = el("table", { className: "table stack" }, el("caption", { className: "sr-only", textContent: caption }),
    el("thead", {}, el("tr", {}, ...head.map((h) => el("th", { scope: "col", textContent: h })))));
  const tb = el("tbody");
  for (const j of jobs) {
    const tr = el("tr");
    if (withTime) tr.append(cell("Time", fmtTime(j.ts)));
    tr.append(cell("Job", j.title), cell("Company", j.company), cell("Score", String(j.score)), cell("Outcome", outcomeChip(j.outcome)),
      cell("Source", j.source), cell("Reason", j.reason || ""), cell("Actions", jobActions(j, reload)));
    tb.append(tr);
  }
  t.append(tb);
  return t;
}
const sumDay = () => $("#dayPick").value || fmtDay(new Date());
async function loadSummary(quiet) {
  const day = sumDay(); $("#dayPick").value = day; $("#dayCsv").href = `/api/unattended/summary.csv?day=${day}`;
  $("#sumError").hidden = true;
  if (!quiet) { $("#sumLoading").hidden = false; $("#sumBody").replaceChildren(); $("#sumEmpty").hidden = true; $("#sumCounts").replaceChildren(); }
  try {
    const r = await api("GET", `/api/unattended/summary?day=${day}`);
    $("#sumLoading").hidden = true;
    $("#sumEmpty").hidden = r.runs > 0;
    const groups = [["applied", "Applied"], ["needs_you", "Needs you"], ["skipped", "Skipped"]];
    $("#sumCounts").replaceChildren(...(r.runs ? groups.map(([k, label]) => el("span", { className: `chip sum-${k}`, textContent: `${label}: ${r.counts[k]}` })) : []));
    const body = [];
    if (r.runs) {
      if (r.in_progress.length) body.push(el("p", { className: "banner info", textContent: `Applying now: ${r.in_progress.map((j) => j.title).join(", ")}` }));
      for (const [k, label] of groups) {
        const id = `sum-h-${k}`;
        body.push(el("section", { className: "sum-group" }, el("h2", { className: "h3", id, textContent: `${label} (${r.counts[k]})` }),
          r[k].length ? jobTable(`${label} on ${day}`, r[k], () => loadSummary(true)) : el("p", { className: "muted", textContent: `None on ${day}.` })));
      }
    }
    $("#sumBody").replaceChildren(...body);
    if (!quiet) announce(r.runs ? `Summary for ${day}: ${r.counts.applied} applied, ${r.counts.needs_you} need you, ${r.counts.skipped} skipped.` : `No unattended runs on ${day}.`);
  } catch (x) { $("#sumLoading").hidden = true; $("#sumError").hidden = false; }
}
$("#dayPick").addEventListener("change", () => loadSummary());
$("#dayToday").addEventListener("click", () => { $("#dayPick").value = fmtDay(new Date()); loadSummary(); });
$("#sumRetry").addEventListener("click", () => loadSummary());

async function loadRunLog(quiet) {
  $("#logError").hidden = true;
  if (!quiet) { $("#logLoading").hidden = false; $("#logTable").hidden = true; $("#logEmpty").hidden = true; }
  try {
    const r = await api("GET", "/api/unattended/runs");
    $("#logLoading").hidden = true; $("#logEmpty").hidden = r.runs.length > 0; $("#logTable").hidden = !r.runs.length;
    const rows = [];
    for (const run of r.runs) {
      const c = run.counts || {};
      const result = run.status === "skipped" ? `Skipped: ${run.note}` : run.status === "running" ? "Running…" :
        `${run.status === "error" ? "Error. " : ""}${run.dry ? `Dry run: ${c.preview || 0} would apply, ` : `${c.submitted || 0} submitted, `}${c.needs_you || 0} need you, ${(c.skipped || 0) + (c.deferred || 0)} skipped or deferred${run.note ? `. ${run.note}` : ""}`;
      const errs = Object.entries(run.source_errors || {}).map(([k, v]) => `${k}: ${v}`).join("; ") || "None";
      const searches = (run.searches || []).map((x) => `${x.label}${x.failed ? " (failed)" : ` (${x.new} new of ${x.found})`}`).join("; ") || "—";
      const tr = el("tr", {}, cell("Started", fmtDateTime(run.started)), cell("Ended", run.ended ? fmtTime(run.ended) : "—"),
        cell("Trigger", TRIGGER[run.trigger] || run.trigger), cell("Result", result), cell("Searches", searches), cell("Source errors", errs));
      const jobsCell = cell("Jobs");
      rows.push(tr);
      if (run.jobs.length) {
        const did = `run-${run.id}-jobs`;
        const btn = el("button", { type: "button", className: "btn text", textContent: `Show ${run.jobs.length} job${run.jobs.length === 1 ? "" : "s"}` });
        btn.setAttribute("aria-expanded", "false"); btn.setAttribute("aria-controls", did);
        const detail = el("tr", { id: did, className: "run-jobs", hidden: true }, el("td", { colSpan: 7 },
          jobTable(`Jobs in the run started ${fmtDateTime(run.started)}`, run.jobs, () => loadRunLog(true), false)));
        btn.addEventListener("click", () => { const open = detail.hidden; detail.hidden = !open; btn.setAttribute("aria-expanded", String(open));
          btn.textContent = `${open ? "Hide" : "Show"} ${run.jobs.length} job${run.jobs.length === 1 ? "" : "s"}`; });
        jobsCell.append(btn); tr.append(jobsCell); rows.push(detail);
      } else { jobsCell.textContent = "None"; tr.append(jobsCell); }
    }
    $("#logRows").replaceChildren(...rows);
  } catch (x) { $("#logLoading").hidden = true; $("#logError").hidden = false; }
}
$("#logRetry").addEventListener("click", () => loadRunLog());

/* ---------- boot ---------- */
(async function boot() {
  initPrefs(); showTab(); refreshUA();
  try { await loadState(); await loadResults(); updateSavedCount(); }
  catch (x) { showFail(x.message); }
})();


/* ---------- application policy (SEARCH_ONLY / PREPARE_FOR_REVIEW / AUTHORIZED_AUTO_SUBMIT) ---------- */
const MODE_TEXT = { SEARCH_ONLY: "Search only: nothing is filled or sent.",
  PREPARE_FOR_REVIEW: "Prepare for review: applications are filled and stop before Submit.",
  AUTHORIZED_AUTO_SUBMIT: "Submit automatically" };
let POLICY = null;
function policyBody() {
  return { mode: (document.querySelector('input[name="pmode"]:checked') || {}).value,
    daily_cap: +$("#p-cap").value, expires_days: +$("#p-days").value, excluded_companies: list($("#p-excl").value),
    channels: ["company", "naukri", "linkedin"].filter((c) => $(`#p-ch-${c}`).checked) };
}
function renderPolicy(p) {
  POLICY = p;
  const r = document.getElementById({ SEARCH_ONLY: "pm-search", PREPARE_FOR_REVIEW: "pm-prepare", AUTHORIZED_AUTO_SUBMIT: "pm-auto" }[p.mode]);
  if (r) r.checked = true;
  $("#p-cap").value = p.daily_cap; $("#p-days").value = p.expires_days; $("#p-excl").value = p.excluded_companies.join(", ");
  for (const c of ["company", "naukri", "linkedin"]) $(`#p-ch-${c}`).checked = p.channels.includes(c);
  let now = MODE_TEXT[p.effective_mode];
  if (p.effective_mode === "AUTHORIZED_AUTO_SUBMIT") now += ` until ${new Date(p.consent.expires * 1000).toLocaleDateString()}: ${p.submitted_today} of ${p.daily_cap} sent today.`;
  else if (p.mode === "AUTHORIZED_AUTO_SUBMIT") now = "Automatic submission is paused: its permission expired or the limits changed. Save the policy to confirm it again. Until then: " + MODE_TEXT.PREPARE_FOR_REVIEW;
  $("#policyNow").textContent = now; $("#policyNow").dataset.mode = p.effective_mode;
}
async function loadPolicy() { try { renderPolicy(await api("GET", "/api/policy")); } catch (x) { $("#policyNow").textContent = x.message; } }
async function savePolicy(body) {
  const err = $("#policyErr"); err.hidden = true;
  try { renderPolicy(await api("PUT", "/api/policy", body)); $("#policySaved").textContent = "Policy saved."; setTimeout(() => ($("#policySaved").textContent = ""), 4000); }
  catch (x) { err.textContent = x.message; err.hidden = false; }
}
$("#policyForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = policyBody();
  if (body.mode !== "AUTHORIZED_AUTO_SUBMIT") return savePolicy(body);
  try {  // show the exact scope; saving needs the hash of what was shown
    const prev = await api("POST", "/api/policy/preview", body);
    $("#consentText").textContent = prev.summary; $("#consentDlg").dataset.hash = prev.summary_hash;
    $("#consentDlg").showModal();
  } catch (x) { $("#policyErr").textContent = x.message; $("#policyErr").hidden = false; }
});
$("#consentCancel").addEventListener("click", () => $("#consentDlg").close());
$("#consentOk").addEventListener("click", async () => {
  $("#consentDlg").close();
  await savePolicy(Object.assign(policyBody(), { confirm: $("#consentDlg").dataset.hash }));
});
$("#consentDlg").addEventListener("close", () => $("#policyForm button[type=submit]").focus());

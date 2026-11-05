"use strict";
// Resume-Matched Job Finder UI. All posting text is inserted with textContent (never innerHTML).
const $ = (s, r = document) => r.querySelector(s);
const PAGE = 50;
const S = { state: null, results: null, page: 1, runId: null, poll: null, undo: null, lastParams: null };

const announce = (msg) => { const a = $("#announcer"); a.textContent = ""; setTimeout(() => (a.textContent = msg), 30); };
const list = (s) => (s || "").split(",").map((x) => x.trim()).filter(Boolean);

async function api(method, url, body, headers) {
  const opts = { method, headers: headers || {} };
  if (body !== undefined) {
    if (body instanceof Blob || body instanceof ArrayBuffer) opts.body = body;
    else { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
  }
  let r;
  try { r = await fetch(url, opts); } catch (e) { throw Object.assign(new Error("Cannot reach the app server. Check that it is still running."), { network: true }); }
  const data = r.headers.get("content-type")?.includes("json") ? await r.json() : null;
  if (!r.ok) throw Object.assign(new Error((data && data.error) || `Request failed (${r.status})`), { field: data && data.field, status: r.status });
  return data;
}

/* ---------- navigation (links navigate) ---------- */
function showTab() {
  const tab = (location.hash || "#search").slice(1);
  const valid = ["search", "profile", "saved", "settings"].includes(tab) ? tab : "search";
  document.querySelectorAll("[data-view]").forEach((v) => (v.hidden = v.dataset.view !== valid));
  document.querySelectorAll(".tabs a").forEach((a) => (a.dataset.tab === valid ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
  document.title = `${$(`#view-${valid} h1`).textContent} · Resume-Matched Job Finder`;
  if (valid === "saved") loadSaved();
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
      const st = Object.assign(document.createElement("span"), { className: `st st-${src.state}`, textContent: { queued: "Waiting", running: "Searching", done: "Done", failed: "Failed", cancelled: "Cancelled" }[src.state] });
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
    const t = $(".expand", li); t.textContent = j.title;
    const detail = $(".job-detail", li); const did = `d-${j.key}-${ol.id}`; detail.id = did; t.setAttribute("aria-controls", did);
    t.addEventListener("click", () => { const open = detail.hidden; detail.hidden = !open; t.setAttribute("aria-expanded", String(open)); });
    $(".job-sub", li).textContent = [j.company, j.location || "Location not stated", j.posted ? `Posted ${j.posted}` : "", fmtSalary(j)].filter(Boolean).join(" · ");
    const chips = $(".chips", li);
    const chip = (txt, cls) => chips.append(Object.assign(document.createElement("span"), { className: "chip " + (cls || ""), textContent: txt }));
    if (j.is_new) chip("New", "new");
    chip(MODE[j.mode] || j.mode);
    chip(`${j.matched.length} of ${j.matched.length + j.missing.length} skills matched`);
    if ((j.sources || []).length > 1) chip(`${j.sources.length} sources`);
    $(".matched", li).textContent = j.matched.join(", ") || "None";
    $(".missing", li).textContent = j.missing.join(", ") || "None";
    $(".sources-line", li).textContent = "Listed on: " + (j.sources || [{ name: j.source }]).map((s) => s.name).join(", ");
    $(".job-desc", li).textContent = j.description || "No description provided by this source.";
    const v = $(".view", li); v.href = /^https:\/\//.test(j.url) ? j.url : "#"; v.setAttribute("aria-label", `View posting: ${j.title} at ${j.company} (opens in a new tab)`);
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
    td(!s.configured ? "Needs API key (see README)" : s.enabled ? "On" : "Off");
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
  await api("PUT", "/api/settings", { countries: list($("#s-countries").value), mode: $("#s-mode").value, seniority: $("#s-seniority").value,
    min_score: clamp($("#s-min").value, 0, 100, 40), max_age_days: clamp($("#s-age").value, 1, 365, 30), target_companies: list($("#s-companies").value) });
  await loadState(); $("#settingsSaved").textContent = "Settings saved.";
  setTimeout(() => ($("#settingsSaved").textContent = ""), 4000);
});
$("#deleteAll").addEventListener("click", () => $("#confirmDlg").showModal());
$("#confirmCancel").addEventListener("click", () => $("#confirmDlg").close());
$("#confirmOk").addEventListener("click", async () => {
  await api("DELETE", "/api/data"); $("#confirmDlg").close();
  S.results = null; S.lastParams = null; $("#results").hidden = true; $("#refreshBtn").hidden = true;
  await loadState(); await loadResults(); updateSavedCount(); announce("All your data was deleted.");
});

/* ---------- boot ---------- */
(async function boot() {
  initPrefs(); showTab();
  try { await loadState(); await loadResults(); updateSavedCount(); }
  catch (x) { showFail(x.message); }
})();

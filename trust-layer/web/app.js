// Vouch - frontend (vanilla JS, talks to server.py)
// Identity, role and access are decided by the server (HttpOnly session cookie); the client never sends them.

const app = document.getElementById("app");
const dialog = document.getElementById("dialog");
const state = { meta: null, company: null, date: null, lastQuestion: "", lastReport: null, category: null };

const EXAMPLES = [
  "How much is the meal voucher?",
  "How many remote work days per week?",
  "When is the payroll data cut-off?",
  "When is the year-end bonus paid?",
  "How much is the home-office allowance?",
  "Overtime surcharge in the Netherlands?",
  "Meal voucher on 1 February 2027?",
];
const TOPIC_QUESTION = {
  "meal_voucher.amount": "How much is the maaltijdcheque?",
  "remote_work.days": "How many thuiswerken dagen per week?",
  "payroll.cutoff_day": "When is the cut-off for loongegevens?",
  "year_end_bonus.payment": "When is the eindejaarspremie paid?",
  "homework_allowance.amount": "How much is the thuiswerkvergoeding?",
  "overtime.surcharge": "What is the overuren toeslag?",
};
const SOURCE_LABEL = { contract: "Contract", policy: "Policy", legal: "Legal", procedure: "Procedure",
  handbook: "Handbook", mail: "Email", teams: "Teams" };
const ROLE_SUMMARY = { answer: "valid", agrees: "valid", conflict: "conflicting", upcoming: "upcoming",
  superseded: "outdated", other_scope: "other scope", document: "document" };
const ROLE_LABEL = { employee: "Employee", hr: "HR", admin: "Supervisor" };
const TAB_PERMISSION = { upload: "ingest", health: "health", eval: "eval" };
const LEVEL = { red: "bad", orange: "warn", green: "good" };

// ---------------------------------------------------------------- helpers
async function api(path, body) {
  const headers = { "X-Requested-With": "trustlayer" };
  if (body) headers["Content-Type"] = "application/json";
  const res = await fetch(path, body ? { method: "POST", headers, body: JSON.stringify(body), credentials: "same-origin" }
    : { headers, credentials: "same-origin" });
  let data = {};
  try { data = await res.json(); } catch { /* empty or non-JSON response */ }
  if (res.status === 401 && path !== "/api/login") { showLogin(); throw new Error("Please sign in again"); }
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = x => x == null ? "–" : Math.round(x * 100) + "%";
const qs = extra => new URLSearchParams({ company: state.company, date: state.date, ...extra }).toString();
function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => { t.hidden = true; }, 3800);
}
function loading() { app.innerHTML = `<div class="empty">Loading…</div>`; }
function fail(e) { app.innerHTML = `<div class="card empty">Something went wrong: ${esc(e.message)}</div>`; }
function bar(label, v) {
  return `<div class="bar"><span>${label}</span><div class="track"><div class="fill" style="width:${pct(v)}"></div></div><strong>${pct(v)}</strong></div>`;
}
function votes(h) {
  const left = h.left.verouderd + h.left.onduidelijk + h.left.fout;
  return `<span title="useful / not ok">+${h.right} / −${left}</span>`;
}
function docBtn(doc) { return `<button class="docbtn" data-doc="${esc(doc.id)}">${esc(doc.label)}</button>`; }
function bindDocButtons(root) {
  root.querySelectorAll("[data-doc]").forEach(b => b.addEventListener("click", e => { e.stopPropagation(); openDoc(b.dataset.doc); }));
}
function flagList(flags) {
  return `<ul class="flags">${flags.map(f => `<li class="${esc(f.level)}"><span class="dot ${esc(f.level)}"></span>${esc(f.text)}</li>`).join("")}</ul>`;
}
function whyHTML(c) {
  if (!c.why) return "";
  return `<details class="why"><summary>Why ${pct(c.trust.score)} trust?</summary>
    <table>${c.why.map(w => `<tr><td>${esc(w.text)}</td><td class="num muted">×${w.weight.toFixed(2)}</td><td class="num">${w.value.toFixed(2)}</td></tr>`).join("")}</table>
    <div class="small muted">Trust = weighted sum of these factors, computed on the server. Swipes never change it.</div></details>`;
}
function fileUrl(docId) { return `/api/file?${qs({ id: docId })}`; }

// ---------------------------------------------------------------- document dialog
async function openDoc(id) {
  try {
    const d = await api(`/api/doc?${qs({ id })}`);
    const doc = d.doc;
    dialog.innerHTML = `
      <h2>${esc(d.label)}</h2>
      <div class="row small muted">
        <span class="badge">${esc(SOURCE_LABEL[doc.source_type] || doc.source_type)}</span>
        <span>published ${esc(doc.published)}</span><span>valid from ${esc(doc.valid_from)}</span><span>${esc(doc.country)}</span>
        <span>owner: ${doc.owner ? esc(doc.owner.name) + (doc.owner.active ? "" : " (inactive)") : "none"}</span>
        <span>${doc.validated ? "validated by " + esc(doc.validated.by) : "not validated"}</span>
      </div>
      ${doc.file ? `<p><a href="${fileUrl(doc.id)}" target="_blank" rel="noopener">Open original file (${esc(doc.file.name)})</a></p>` : ""}
      <h3>Status on ${esc(state.date)}</h3>
      ${d.cards.map(c => `<div class="dlg-card"><span class="badge ${esc(c.verdict.level)}">${esc(c.verdict.text)}</span>
        ${c.display ? `<strong>${esc(c.display)}</strong> <span class="muted small">${esc(c.topic)}</span>` : ""}${flagList(c.flags)}${whyHTML(c)}</div>`).join("")}
      <h3>Original text</h3><p class="doctext">${esc(doc.text)}</p>
      ${d.chain.length > 1 ? `<h3>Version chain</h3><ul class="small">${d.chain.map(c => `<li>${c.id === doc.id ? "<strong>" : ""}${esc(c.label)} (from ${esc(c.valid_from)})${c.id === doc.id ? "</strong>" : ""}</li>`).join("")}</ul>` : ""}
      <div class="row" style="justify-content:flex-end;margin-top:16px"><button id="dlgclose">Close</button></div>`;
    dialog.showModal();
    document.getElementById("dlgclose").onclick = () => dialog.close();
  } catch (e) { toast(e.message); }
}

// ---------------------------------------------------------------- votes remembered per session (display only)
function votedKey() { return `voted:${state.company}`; }
function votedMap() { try { return JSON.parse(sessionStorage.getItem(votedKey()) || "{}"); } catch { return {}; } }
function remember(id, text) {
  const m = votedMap(); m[id] = text;
  try { sessionStorage.setItem(votedKey(), JSON.stringify(m)); } catch { /* no storage: fine */ }
}

// ---------------------------------------------------------------- Dashboard
async function renderDashboard() {
  loading();
  try {
    const { companies } = await api(`/api/dashboard?${new URLSearchParams({ date: state.date })}`);
    app.innerHTML = `
      <div class="page-head"><h1>Your companies</h1><p class="muted">Knowledge is assigned to a company. Pick a category to swipe through its sources.</p></div>
      ${companies.map(co => `
        <section class="company">
          <div class="company-head"><h2>${esc(co.name)}</h2><span class="muted small">${co.documents} documents · ${co.claims} claims</span>
            <button class="ghost" data-search="${esc(co.id)}">Search this company</button></div>
          <div class="cat-grid">${co.categories.map(cat => `
            <button class="cat" data-company="${esc(co.id)}" data-cat="${esc(cat.name)}">
              <div class="cat-name">${esc(cat.name)}</div>
              <div class="cat-levels">
                ${cat.levels.red ? `<span class="badge bad">${cat.levels.red} conflict</span>` : ""}
                ${cat.levels.orange ? `<span class="badge warn">${cat.levels.orange} attention</span>` : ""}
                ${cat.levels.green ? `<span class="badge good">${cat.levels.green} ok</span>` : ""}
              </div>
              <ul class="cat-topics">${cat.topics.map(t => `<li><span class="dot ${LEVEL[t.level]}"></span>${esc(t.label)}${t.scope !== "BE" ? ` (${esc(t.scope)})` : ""}<span class="muted"> · ${esc(t.answer || "no valid rule")}</span></li>`).join("")}</ul>
              <span class="cat-cta">Swipe sources →</span>
            </button>`).join("")}</div>
        </section>`).join("")}`;
    app.querySelectorAll("[data-cat]").forEach(b => b.onclick = () => {
      selectCompany(b.dataset.company); state.category = b.dataset.cat; location.hash = "#/category";
    });
    app.querySelectorAll("[data-search]").forEach(b => b.onclick = () => {
      selectCompany(b.dataset.search); state.lastQuestion = ""; location.hash = "#/search";
    });
  } catch (e) { fail(e); }
}

function selectCompany(id) {
  state.company = id;
  document.getElementById("company").value = id;
}

async function renderCategory() {
  if (!state.category) { location.hash = "#/dashboard"; return; }
  loading();
  try {
    const { cards } = await api(`/api/cards?${qs({ category: state.category })}`);
    const name = state.meta.companies.find(c => c.id === state.company).name;
    app.innerHTML = `
      <div class="page-head"><a href="#/dashboard" class="back">← Dashboard</a><h1>${esc(state.category)}</h1>
        <p class="muted">${esc(name)} · swipe right = useful, left = not ok (with a reason). Use ‹ › to browse without voting.</p></div>
      <div class="card narrow"><div id="deck"></div></div>`;
    deck(document.getElementById("deck"), cards);
  } catch (e) { fail(e); }
}

// ---------------------------------------------------------------- Search + swipe
function renderSearch() {
  const name = state.meta.companies.find(c => c.id === state.company).name;
  app.innerHTML = `
    <div class="page-head"><h1>Search ${esc(name)}</h1><p class="muted">Ask a question or search a document. Every source gets a verdict and a trust explanation.</p></div>
    <div class="card">
      <form class="askbar" id="askform">
        <input id="q" placeholder="e.g. 'meal voucher' or 'contract Luik'…" value="${esc(state.lastQuestion)}" autocomplete="off" maxlength="500">
        <button class="primary">Search</button>
      </form>
      <div class="chips">${EXAMPLES.map(e => `<button class="chip" data-q="${esc(e)}">${esc(e)}</button>`).join("")}</div>
    </div>
    <div id="result"></div>`;
  document.getElementById("askform").onsubmit = e => { e.preventDefault(); search(document.getElementById("q").value); };
  app.querySelectorAll("[data-q]").forEach(b => b.onclick = () => { document.getElementById("q").value = b.dataset.q; search(b.dataset.q); });
  if (state.lastQuestion) search(state.lastQuestion);
}

// the search engine keys on Dutch HR terms; map common English words so English questions work too
const EN_NL = [[/meal voucher/gi, "maaltijdcheque"], [/remote work|work from home|remote/gi, "thuiswerken"],
  [/home-office allowance|home office allowance/gi, "thuiswerkvergoeding"], [/year-end bonus|year end bonus/gi, "eindejaarspremie"],
  [/overtime/gi, "overuren toeslag"], [/netherlands/gi, "nederland"], [/belgium/gi, "belgië"], [/payroll data|payroll/gi, "loongegevens"],
  [/days per week|days/gi, "dagen per week"], [/\bon (\d{1,2}) (january|february|march|april|may|june|july|august|september|october|november|december) (\d{4})/gi,
    (m, d, mo, y) => `op ${d} ${({january:"januari",february:"februari",march:"maart",april:"april",may:"mei",june:"juni",july:"juli",august:"augustus",september:"september",october:"oktober",november:"november",december:"december"})[mo.toLowerCase()]} ${y}`]];
function toQuery(q) { return EN_NL.reduce((s, [re, rep]) => s.replace(re, rep), q); }

async function search(q) {
  q = q.trim();
  if (!q) return;
  state.lastQuestion = q;
  const out = document.getElementById("result");
  out.innerHTML = `<div class="card empty">Searching…</div>`;
  let r;
  try { r = await api(`/api/ask?${qs({ q: toQuery(q) })}`); } catch (e) { out.innerHTML = `<div class="card empty">${esc(e.message)}</div>`; return; }
  if (!r.cards.length) { out.innerHTML = `<div class="card"><p class="answer">${esc(r.answer_text)}</p></div>`; return; }
  const top = r.answer;
  const head = r.status === "ok" ? `
    <div class="card answer-card">
      <div class="answer-top">
        <div class="text">
          <div class="eyebrow">${esc(r.topic.label)} · reference date ${esc(r.peildatum)} · scope ${esc(r.scope)}</div>
          <p class="answer">${esc(r.answer_text_llm || r.answer_text)}</p>
          <div class="row">
            ${r.conflict ? `<span class="badge bad">Conflicting sources</span>` : `<span class="badge good">No conflicts</span>`}
            ${r.needs_review ? `<span class="badge bad">HR review recommended</span>` : ""}
            ${r.upcoming.map(u => `<span class="badge warn">Changes on ${esc(u.claim.valid_from)}</span>`).join(" ")}
            ${r.history.length ? `<span class="badge grey">${r.history.length} outdated version${r.history.length > 1 ? "s" : ""}</span>` : ""}
          </div>
        </div>
        <div class="scorebox">
          <div class="num">${pct(r.answer_score)}</div><div class="lbl">Trust score</div>
          <div class="bars">${bar("Trust", top.trust.score)}${bar("Relevance", top.relevance.score)}${bar("Human", top.human.score)}</div>
        </div>
      </div>
    </div>` : `<div class="card"><p class="answer">${esc(r.answer_text)}</p></div>`;
  out.innerHTML = `${head}
    <div class="search-grid">
      <div class="card"><h2>Swipe through the sources</h2><div id="deck"></div></div>
      ${r.status === "ok" ? receiptHTML(r) : ""}
    </div>`;
  deck(document.getElementById("deck"), r.cards);
  bindDocButtons(out);
}

function receiptHTML(r) {
  return `<div class="card receipt"><h2>Trust receipt</h2>
    <div class="gates">
      <span class="g">${r.gates.company} claims</span><span class="arrow">→</span>
      <span class="g">${r.gates.topic} on this topic</span><span class="arrow">→</span>
      <span class="g">${r.gates.relevant_in_scope} relevant in ${esc(r.scope)}</span><span class="arrow">→</span>
      <span class="g"><strong>${r.gates.valid_now} valid on ${esc(r.peildatum)}</strong></span>
    </div>
    <p class="small muted">${r.conflict
      ? "Valid sources contradict each other, so ranking uses trust only. Swipes do not count here."
      : "Ranking: 50% trust + 30% relevance + 20% human score."}</p>
    <div class="tablewrap"><table>
      <tr><th>#</th><th>Valid source</th><th>Value</th><th>Trust</th><th>Votes</th></tr>
      ${r.current.map((i, n) => `<tr class="${n === 0 ? "winner" : ""}"><td>${n + 1}</td><td>${docBtn(i.doc)}</td>
        <td><strong>${esc(i.claim.display)}</strong></td><td class="num">${pct(i.trust.score)}</td><td class="num">${votes(i.human)}</td></tr>`).join("")}
    </table></div>
    ${r.history.length ? `<h3>History (never shown by default)</h3><div class="tablewrap"><table>
      ${r.history.map(i => `<tr><td>${docBtn(i.doc)}</td><td>${esc(i.claim.display)}</td><td class="num">${votes(i.human)}</td></tr>`).join("")}</table></div>` : ""}
  </div>`;
}

// Card deck: browse with ‹ ›, vote by dragging or ✕ / ✓
const REASONS = { verouderd: "Outdated", onduidelijk: "Unclear", fout: "Wrong" };
function deck(root, cards) {
  let idx = 0;
  if (!cards.length) { root.innerHTML = `<div class="empty">No sources in this category yet.</div>`; return; }
  const counts = {};
  cards.forEach(c => { const k = ROLE_SUMMARY[c.role]; counts[k] = (counts[k] || 0) + 1; });
  root.innerHTML = `
    <div class="deck-summary">${Object.entries(counts).map(([k, n]) => `<span>${n} ${esc(k)}</span>`).join(" · ")}</div>
    <div class="pips">${cards.map((c, i) => `<button class="pip ${esc(c.verdict.level)}" data-i="${i}" title="${esc(c.verdict.text)}: ${esc(c.doc.label)}"></button>`).join("")}</div>
    <div class="stack"></div>
    <div class="reasons" hidden>
      <span class="small muted" style="width:100%;text-align:center">Why not ok?</span>
      ${Object.entries(REASONS).map(([k, v]) => `<button data-reason="${k}">${v}</button>`).join("")}<button data-reason="">Cancel</button>
    </div>
    <div class="swipe-buttons">
      <button class="nav prev" title="Previous (←)">‹</button>
      <button class="no" title="Not ok: drag left">✕</button>
      <button class="yes" title="Useful: drag right">✓</button>
      <button class="nav next" title="Next (→)">›</button>
    </div>
    <p class="small muted center counter"></p>`;
  const $ = s => root.querySelector(s);
  const stack = $(".stack"), reasons = $(".reasons"), btns = $(".swipe-buttons");

  function cardHTML(c, behind) {
    const voted = c.claim_id && votedMap()[c.claim_id];
    return `<div class="swipecard ${behind ? "behind" : ""}">
      <div class="ribbon ${esc(c.verdict.level)}">${esc(c.verdict.text)}</div>
      <span class="stamp yes">USEFUL</span><span class="stamp no">NOT OK</span>
      <div class="eyebrow">${esc(c.topic || "Document without a recognised rule")}</div>
      ${c.display ? `<div class="value">${esc(c.display)}</div>` : ""}
      <div class="quote">“${esc(c.sentence)}”</div>
      <div class="small"><strong>${esc(c.doc.label)}</strong>
        <span class="muted">· ${esc(SOURCE_LABEL[c.doc.source_type] || c.doc.source_type)} · published ${esc(c.doc.published)}</span></div>
      ${flagList(c.flags)}
      ${c.trust ? `<div class="bars">${bar("Trust", c.trust.score)}${bar("Human", c.human.score)}</div>` : ""}
      ${whyHTML(c)}
      <div class="row card-foot">
        <button class="docbtn" data-doc="${esc(c.doc.id)}">Open document</button>
        ${c.doc.has_file ? `<a class="small" href="${fileUrl(c.doc.id)}" target="_blank" rel="noopener">original file</a>` : ""}
        ${voted ? `<span class="badge grey">You: ${esc(voted)}</span>` : ""}
      </div>
    </div>`;
  }
  function show() {
    reasons.hidden = true; btns.hidden = false;
    if (idx >= cards.length) {
      stack.innerHTML = `<div class="swipecard"><div class="empty">All ${cards.length} sources reviewed.<br><br><button class="restart">Start over</button></div></div>`;
      stack.querySelector(".restart").onclick = () => { idx = 0; show(); };
      $(".counter").textContent = ""; markPips(); return;
    }
    stack.innerHTML = (cards[idx + 1] ? cardHTML(cards[idx + 1], true) : "") + cardHTML(cards[idx], false);
    const c = cards[idx];
    $(".no").disabled = $(".yes").disabled = !c.claim_id;
    $(".prev").disabled = idx === 0;
    $(".counter").textContent = `Source ${idx + 1} of ${cards.length}` + (c.claim_id ? "" : " · nothing to rate");
    markPips(); bindDocButtons(stack);
    enableDrag(stack.querySelector(".swipecard:not(.behind)"));
  }
  function markPips() { root.querySelectorAll(".pip").forEach((p, i) => p.classList.toggle("active", i === idx)); }
  function go(d) { idx = Math.max(0, Math.min(cards.length, idx + d)); show(); }
  async function vote(direction, reason) {
    const c = cards[idx];
    if (!c || !c.claim_id) return;
    const el = stack.querySelector(".swipecard:not(.behind)");
    if (el) { el.style.transform = `translateX(${direction === "right" ? 600 : -600}px) rotate(${direction === "right" ? 20 : -20}deg)`; el.style.opacity = 0; }
    try {
      const res = await api("/api/swipe", { claim_id: c.claim_id, direction, reason: reason || null });
      remember(c.claim_id, direction === "right" ? "useful" : REASONS[reason]);
      const note = c.role === "superseded" ? " Outdated: stays in history."
        : c.role === "conflict" ? " Conflicting: ranking stays on trust." : "";
      toast(`${res.vote.changed ? "Vote changed. " : ""}Human score ${pct(c.human.score)} → ${pct(res.human.score)}.${note}`);
      c.human = res.human;
    } catch (e) { toast(e.message); }
    idx++; setTimeout(show, 220);
  }
  function askReason() { reasons.hidden = false; btns.hidden = true; }
  reasons.querySelectorAll("[data-reason]").forEach(b => b.onclick = () => b.dataset.reason ? vote("left", b.dataset.reason) : show());
  $(".yes").onclick = () => vote("right");
  $(".no").onclick = askReason;
  $(".prev").onclick = () => go(-1);
  $(".next").onclick = () => go(1);
  root.querySelectorAll(".pip").forEach(p => p.onclick = () => { idx = +p.dataset.i; show(); });

  function enableDrag(el) {
    if (!el) return;
    let x0 = null, dx = 0;
    const yes = el.querySelector(".stamp.yes"), no = el.querySelector(".stamp.no");
    const canVote = !!cards[idx].claim_id;
    el.addEventListener("pointerdown", e => {
      if (e.target.closest("button, a, summary, details")) return;
      x0 = e.clientX; el.setPointerCapture(e.pointerId); el.style.transition = "none";
    });
    el.addEventListener("pointermove", e => {
      if (x0 === null) return;
      dx = e.clientX - x0;
      el.style.transform = `translateX(${dx}px) rotate(${dx / 20}deg)`;
      if (canVote) { yes.style.opacity = Math.max(0, dx / 120); no.style.opacity = Math.max(0, -dx / 120); }
    });
    el.addEventListener("pointerup", () => {
      if (x0 === null) return;
      el.style.transition = "";
      if (canVote && dx > 110) vote("right");
      else if (canVote && dx < -110) { el.style.transform = ""; no.style.opacity = 0; askReason(); }
      else if (!canVote && Math.abs(dx) > 110) go(1);
      else { el.style.transform = ""; yes.style.opacity = no.style.opacity = 0; }
      x0 = null; dx = 0;
    });
  }
  keyHandler = e => {
    if (dialog.open || e.target.closest("input, textarea, select") || btns.hidden) return;
    if (e.key === "ArrowRight") go(1);
    if (e.key === "ArrowLeft") go(-1);
  };
  show();
}
let keyHandler = null;
document.addEventListener("keydown", e => keyHandler && keyHandler(e));

// ---------------------------------------------------------------- Upload
const upload = { file: null };

async function renderUpload() {
  let docs = [];
  try { ({ docs } = await api(`/api/docs?${qs()}`)); } catch (e) { return fail(e); }
  upload.file = null;
  const name = state.meta.companies.find(c => c.id === state.company).name;
  app.innerHTML = `
    <div class="page-head"><h1>Add a document</h1><p class="muted">The document is assigned to <strong>${esc(name)}</strong> (switch company at the top).</p></div>
    <div class="card">
      <label class="dropzone" id="dropzone">
        <input type="file" id="fileinput" accept=".pdf,.docx,.txt,.md,.csv" hidden>
        <strong>Drop a file here or click to choose</strong>
        <span class="small muted">PDF, Word (.docx), .txt or .md · max 15 MB · the original is kept for audit</span>
        <span id="fileinfo" class="small"></span>
      </label>
      <form class="form" id="upform">
        <label>Title<input name="title" required maxlength="200"></label>
        <label>Source type<select name="source_type">${Object.entries(SOURCE_LABEL).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></label>
        <label>Replaces (new version of)<select name="replaces"><option value="">— new document —</option>
          ${docs.filter(d => d.chain_id).map(d => `<option value="${esc(d.id)}">${esc(d.label)}</option>`).join("")}</select></label>
        <label>Version<input name="version" placeholder="v1" maxlength="40"></label>
        <label>Published<input type="date" name="published" value="${esc(state.meta.today)}"></label>
        <label>Valid from<input type="date" name="valid_from"></label>
        <label>Owner / author<input name="owner" maxlength="120"></label>
        <label>Validated (note)<input name="validated_by" placeholder="empty = not validated" maxlength="120"></label>
        <label class="full">Text <span class="small muted">(filled from your file; you can edit it)</span><textarea name="text" required></textarea></label>
        <div class="full row"><button type="button" id="analyse">Analyse</button><button class="primary" type="button" id="commit" disabled>Save to knowledge base</button></div>
      </form>
    </div>
    <div id="upresult"></div>`;
  const form = document.getElementById("upform");
  const dz = document.getElementById("dropzone"), input = document.getElementById("fileinput");
  input.onchange = () => input.files[0] && readFile(input.files[0], form);
  ["dragenter", "dragover"].forEach(ev => dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.add("over"); }));
  ["dragleave", "drop"].forEach(ev => dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.remove("over"); }));
  dz.addEventListener("drop", e => e.dataTransfer.files[0] && readFile(e.dataTransfer.files[0], form));
  form.elements.text.oninput = () => { document.getElementById("commit").disabled = true; };
  const payload = commit => ({ ...Object.fromEntries(new FormData(form)), company_id: state.company, commit,
    author_role: form.elements.source_type.value === "teams" ? "employee" : undefined, file: upload.file || undefined });
  document.getElementById("analyse").onclick = async () => {
    try { const r = await api("/api/ingest", payload(false)); showIngest(r); document.getElementById("commit").disabled = !r.claims.length; }
    catch (e) { toast(e.message); }
  };
  document.getElementById("commit").onclick = async () => {
    try { const r = await api("/api/ingest", payload(true)); showIngest(r); document.getElementById("commit").disabled = true; toast("Saved to the knowledge base."); }
    catch (e) { toast(e.message); }
  };
}

function readFile(file, form) {
  const info = document.getElementById("fileinfo");
  if (file.size > 15 * 1024 * 1024) { toast("File is larger than 15 MB"); return; }
  info.textContent = `Reading ${file.name}…`;
  const reader = new FileReader();
  reader.onload = async () => {
    const data = String(reader.result).split(",")[1];
    try {
      const r = await api("/api/extract", { file: { name: file.name, data } });
      upload.file = { name: file.name, data };
      const s = r.suggested;
      form.elements.title.value = s.title;
      form.elements.source_type.value = s.source_type;
      if (s.valid_from) form.elements.valid_from.value = s.valid_from;
      if (s.version) form.elements.version.value = s.version;
      form.elements.text.value = r.text;
      info.innerHTML = `<span class="badge good">${esc(file.name)}</span> ${Math.round(r.size / 1024)} kB · read via ${esc(r.method)}
        ${r.warnings.map(w => `<div style="color:var(--warn)">${esc(w)}</div>`).join("")}
        <div class="muted">Check title, type and valid-from date, pick what it replaces, then Analyse.</div>`;
      document.getElementById("commit").disabled = true;
      document.getElementById("upresult").innerHTML = "";
    } catch (e) { upload.file = null; info.innerHTML = `<span style="color:var(--bad)">${esc(e.message)}</span>`; }
  };
  reader.readAsDataURL(file);
}

function showIngest(r) {
  const kindBadge = { replaces: "warn", conflict: "bad", confirms: "good", new: "" };
  document.getElementById("upresult").innerHTML = `
    <div class="card">
      <h2>${r.committed ? "Saved" : "Analysis"} <span class="badge grey">extraction: ${esc(r.method)}</span></h2>
      <h3>Rules found</h3>
      ${r.claims.length ? `<ul>${r.claims.map(c => `<li><strong>${esc(c.display)}</strong> <span class="muted small">${esc(c.topic)} · “${esc(c.sentence)}”</span></li>`).join("")}</ul>`
        : `<p>No HR/payroll rules found for the known topics. This document is treated as noise and cannot be saved.</p>`}
      ${r.impact.length ? `<h3>Impact on the knowledge base</h3><ul class="impact">${r.impact.map(i => `<li><span class="badge ${kindBadge[i.kind] || ""}">${esc(i.kind)}</span> ${esc(i.text)}</li>`).join("")}</ul>` : ""}
      ${r.noise.length ? `<details><summary class="small muted">${r.noise.length} sentences ignored</summary><ul class="small muted">${r.noise.slice(0, 30).map(n => `<li>${esc(n.sentence)}</li>`).join("")}</ul></details>` : ""}
    </div>`;
}

// ---------------------------------------------------------------- Knowledge health
async function renderHealth() {
  loading();
  try {
    const { health: h, eval_history } = await api(`/api/health?${qs()}`);
    const last = eval_history[eval_history.length - 1];
    const sec = (title, items, fn) => `
      <div class="card hsec"><h2>${title} <span class="badge grey count">${items.length}</span></h2>
      ${items.length ? `<ul>${items.map(fn).join("")}</ul>` : `<p class="muted small">Nothing found.</p>`}</div>`;
    const ref = x => x ? `${docBtn(x)} <span class="muted small">(${esc(x.value || "")})</span>` : "";
    app.innerHTML = `
      <div class="page-head"><h1>Knowledge health</h1><p class="muted">${esc(state.meta.companies.find(c => c.id === state.company).name)} on ${esc(state.date)}</p></div>
      <div class="grid3">
        <div class="card kpi"><div class="num" style="color:var(--bad)">${h.conflicts.length}</div><div class="lbl">Conflicts</div></div>
        <div class="card kpi"><div class="num" style="color:var(--warn)">${h.popular_superseded.length}</div><div class="lbl">Popular but replaced</div></div>
        <div class="card kpi"><div class="num">${last ? pct(last.overall_score) : "–"}</div><div class="lbl">Last Test Agent score</div></div>
      </div>
      <div class="grid2" style="margin-top:16px">
        ${sec("Conflicts", h.conflicts, c => `<li><strong>${esc(c.topic)}</strong>: ${ref(c.winner)} wins over ${c.against.map(ref).join(", ")}</li>`)}
        ${sec("Popular but replaced", h.popular_superseded, p => `<li>${ref(p.old)} has ${p.votes} votes, but ${ref(p.current)} applies</li>`)}
        ${sec("High trust, low human score", h.high_trust_low_human, x => `<li>${ref(x.doc)}: trust ${pct(x.trust)}, human ${pct(x.human)}</li>`)}
        ${sec("Signals from swipes", h.swipe_signals, s => `<li>${ref(s.doc)}: ${esc(s.signal)} (${s.count}×)</li>`)}
        ${sec("Suggested archiving", h.archive_proposals, a => `<li>${ref(a.doc)}: ${esc(a.reason)}</li>`)}
        ${sec("Changing soon", h.upcoming, u => `<li>${ref(u.doc)} from ${esc(u.valid_from)}</li>`)}
        ${sec("No (active) owner", h.no_owner, o => `<li>${docBtn(o.doc)} <span class="muted small">${esc(o.owner)}</span></li>`)}
        ${sec("Old (over 2.5 years)", h.stale, s => `<li>${docBtn(s.doc)} <span class="muted small">${s.age_years} years</span></li>`)}
      </div>`;
    bindDocButtons(app);
  } catch (e) { fail(e); }
}

// ---------------------------------------------------------------- Test Agent (supervisors)
function renderEval() {
  app.innerHTML = `
    <div class="page-head"><h1>Test Agent</h1><p class="muted">Golden questions, hard rules checked in code across all companies and dates,
      and 1,000 fake swipes per outdated source to prove popularity never wins.</p></div>
    <div class="card"><button class="primary" id="run">Run Test Agent</button></div>
    <div id="evalout"></div>`;
  document.getElementById("run").onclick = async () => {
    const out = document.getElementById("evalout");
    out.innerHTML = `<div class="card empty">Running…</div>`;
    try { state.lastReport = await api("/api/eval", {}); showReport(state.lastReport); } catch (e) { out.innerHTML = `<div class="card empty">${esc(e.message)}</div>`; }
  };
  if (state.lastReport) showReport(state.lastReport);
}

function showReport(r) {
  const ruleName = {
    version_rule_ok: "Valid version always wins", isolation_ok: "No leaks between companies",
    validity_ok: "Answer is valid on the reference date", conflict_visible_ok: "Conflicts always visible",
    human_signal_respected_but_not_over_version: "Swipes never beat version or trust", teams_noise_filtered: "Teams noise yields no claims",
  };
  document.getElementById("evalout").innerHTML = `
    <div class="grid3" style="margin-top:16px">
      <div class="card kpi"><div class="num">${pct(r.overall_score)}</div><div class="lbl">${r.checks_total - r.checks_failed}/${r.checks_total} checks</div></div>
      <div class="card kpi"><div class="num">${r.golden_passed}/${r.golden_total}</div><div class="lbl">Golden questions</div></div>
      <div class="card kpi"><div class="num">${r.judge.faithfulness_avg ?? "–"}</div><div class="lbl">LLM judge</div></div>
    </div>
    <div class="card" style="margin-top:16px"><h2>Hard rules</h2>
      ${Object.entries(r.rules).map(([k, v]) => `<div class="rule"><span>${esc(ruleName[k] || k)}<div class="checks">${v.checked} checked${v.fake_votes ? `, ${v.fake_votes} fake swipes` : ""}</div></span>
        <span class="badge ${v.pass ? "good" : "bad"}">${v.pass ? "PASS" : "FAIL"}</span></div>`).join("")}
    </div>`;
}

// ---------------------------------------------------------------- sign in
function showLogin(message) {
  document.getElementById("tabs").hidden = true;
  document.querySelector(".controls").hidden = true;
  keyHandler = null;
  app.innerHTML = `
    <div class="card login">
      <img src="logo.png" alt="Vouch" class="login-logo">
      <p class="muted center">Trusted HR & payroll knowledge, with evidence.</p>
      <form class="form" id="loginform">
        <label class="full">Email<input name="email" type="email" autocomplete="username" required maxlength="200"></label>
        <label class="full">Password<input name="password" type="password" autocomplete="current-password" required maxlength="200"></label>
        <div class="full"><button class="primary wide">Sign in</button><div id="loginerr" class="small" style="color:var(--bad);margin-top:8px">${esc(message || "")}</div></div>
      </form>
    </div>`;
  document.getElementById("loginform").onsubmit = async e => {
    e.preventDefault();
    const f = e.target;
    try { await api("/api/login", { email: f.email.value, password: f.password.value }); f.password.value = ""; start(); }
    catch (err) { document.getElementById("loginerr").textContent = err.message; }
  };
}

// ---------------------------------------------------------------- router + init
const VIEWS = { dashboard: renderDashboard, category: renderCategory, search: renderSearch, upload: renderUpload, health: renderHealth, eval: renderEval };
const TAB_OF = { category: "dashboard" };

function route() {
  const tab = location.hash.replace("#/", "").split("?")[0];
  const view = VIEWS[tab] ? tab : "dashboard";
  const need = TAB_PERMISSION[view];
  if (need && !state.permissions.has(need)) { location.hash = "#/dashboard"; return; }
  document.querySelectorAll("#tabs a").forEach(a => a.classList.toggle("active", a.dataset.tab === (TAB_OF[view] || view)));
  keyHandler = null;
  VIEWS[view]();
}

let wired = false;
async function start() {
  let me;
  try { me = await api("/api/me"); } catch { return; }
  state.user = me.user;
  state.permissions = new Set(me.permissions);
  state.meta = await api("/api/meta");
  document.getElementById("tabs").hidden = false;
  document.querySelector(".controls").hidden = false;
  document.querySelectorAll("#tabs a").forEach(a => {
    const need = TAB_PERMISSION[a.dataset.tab];
    a.hidden = !!need && !state.permissions.has(need);  // cosmetic only: the server checks every action
  });
  const sel = document.getElementById("company");
  sel.innerHTML = state.meta.companies.map(c => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("");
  state.company = state.meta.companies[0].id;
  state.date = state.date || state.meta.today;
  const dateEl = document.getElementById("peildatum");
  dateEl.value = state.date;
  document.getElementById("userbox").textContent = `${state.user.name} · ${ROLE_LABEL[state.user.role] || state.user.role}`;
  if (!wired) {
    wired = true;
    sel.onchange = () => { state.company = sel.value; state.lastQuestion = ""; route(); };
    dateEl.onchange = () => { state.date = dateEl.value || state.meta.today; route(); };
    document.getElementById("logout").onclick = async () => {
      try { await api("/api/logout", {}); } catch { /* session already gone */ }
      try { sessionStorage.clear(); } catch { /* no storage */ }
      state.lastQuestion = ""; state.lastReport = null;
      showLogin();
    };
    window.addEventListener("hashchange", route);
  }
  route();
}

start().catch(fail);

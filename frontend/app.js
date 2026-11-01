const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const f2 = v => (v === null || v === undefined) ? "–" : Number(v).toFixed(2);
const REQUIRED = ["Patient_ID","Test_Date","C_rxn_copies_uL","V_rxn_uL","V_template_uL","V_elution_uL","V_plasma_mL","Zygosity_Z","dd_cfDNA_pct"];
$("#cols").textContent = REQUIRED.join(", ");

async function api(url, opts) {
  const res = await fetch(url, opts);
  if (!res.ok) {
    let d = res.statusText;
    try { const j = await res.json(); d = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch (_) {}
    throw new Error(d);
  }
  return res.json();
}
let toastTimer;
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.hidden = true, 3200);
}
const fmtDT = s => new Date(s).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
const ACTION = { new: "New", updated: "Updated", duplicate: "Duplicate, skipped", kept: "Not saved, valid record kept" };

/* ---------- shared table ---------- */
function tableHTML(rows, o = {}) {
  if (!rows.length) return `<table><tbody><tr><td class="empty">${o.empty || "Nothing to show."}</td></tr></tbody></table>`;
  const head = [o.rowNo ? "#" : null, "Patient", "Date", "C_rxn", "V_rxn", "V_tmpl", "V_elut", "V_plasma", "Z", "dd-cfDNA %", "cp_mL", "η", "Prob", "CM-Score", "Status", o.action ? "Saved as" : null].filter(Boolean);
  const text = new Set(["#", "Patient", "Date", "Status", "Saved as"]);
  const body = rows.map(r => {
    const cls = !r.is_valid ? "invalid" : r.status.startsWith("High") ? "high" : "low";
    const st = r.is_valid ? esc(r.status) : `Invalid<span class="sub">${esc(r.error_message)}</span>`;
    const n = v => `<td class="n">${v}</td>`;
    return `<tr class="${cls}">${o.rowNo ? `<td>${r.row_number}</td>` : ""}<td>${esc(r.patient_id || "–")}</td><td>${esc(r.test_date || "–")}</td>
      ${n(f2(r.c_rxn_copies_ul))}${n(f2(r.v_rxn_ul))}${n(f2(r.v_template_ul))}${n(f2(r.v_elution_ul))}${n(f2(r.v_plasma_ml))}${n(r.zygosity_z ?? "–")}
      ${n(f2(r.dd_cfdna_pct))}${n(f2(r.cp_ml))}${n(f2(r.eta))}${n(f2(r.prob))}${n(f2(r.cm_score))}
      <td class="st">${st}</td>${o.action ? `<td>${ACTION[r.action] || ""}</td>` : ""}</tr>`;
  }).join("");
  return `<table><thead><tr>${head.map(h => `<th class="${text.has(h) ? "" : "n"}">${h}</th>`).join("")}</tr></thead><tbody>${body}</tbody></table>`;
}

/* ---------- one upload report (used after upload and from history) ---------- */
function batchHTML(d) {
  const b = d.batch;
  return `<div class="stats">
      <div class="stat"><span>Rows</span><b>${b.total}</b></div>
      <div class="stat high"><span>High risk</span><b>${b.high}</b></div>
      <div class="stat low"><span>Low risk</span><b>${b.low}</b></div>
      <div class="stat bad"><span>Invalid</span><b>${b.invalid}</b></div>
      <div class="stat"><span>Duplicates skipped</span><b>${b.duplicate}</b></div></div>
    <div class="result-head"><div><h2>${esc(b.filename)}</h2><p>${fmtDT(b.uploaded_at)} · ${esc(b.message || "")}</p></div>
      <a class="btn" href="/api/batches/${b.id}/export.csv">Export CSV</a></div>
    <div class="split">
      <div class="card table-card"><div class="scroll">${tableHTML(d.rows, { rowNo: true, action: true, empty: "No rows." })}</div></div>
      <figure class="plot"><img alt="dd-cfDNA percent versus plasma copies, with reference lines at 0.50 percent and 44 copies per mL" src="/api/plot/scatter.png?batch_id=${b.id}&t=${Date.now()}"></figure>
    </div>
    <div class="legend"><span class="chip high">High risk (positive)</span><span class="chip low">Low risk (negative)</span><span class="chip invalid">Invalid row</span></div>`;
}

/* ---------- upload page ---------- */
const drop = $("#drop"), fileInput = $("#csvFile");
drop.addEventListener("click", () => fileInput.click());
drop.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } });
["dragenter", "dragover"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", e => { if (e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]); });
fileInput.addEventListener("change", e => { if (e.target.files[0]) upload(e.target.files[0]); e.target.value = ""; });

async function upload(file) {
  $("#uploadError").hidden = true; $("#uploadResult").innerHTML = ""; $("#uploadBusy").hidden = false;
  const fd = new FormData(); fd.append("file", file);
  try {
    const d = await api("/api/upload", { method: "POST", body: fd });
    $("#uploadResult").innerHTML = batchHTML(d);
    toast(d.batch.message);
    $("#uploadResult").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    const e = $("#uploadError");
    e.innerHTML = `<b>This file could not be processed</b>${esc(err.message)}<br>Expected columns: ${REQUIRED.join(", ")}`;
    e.hidden = false;
  } finally { $("#uploadBusy").hidden = true; }
}

/* ---------- results page ---------- */
const R = { page: 0, size: 50, status: "", total: 0 };
function resultParams(paged) {
  const p = new URLSearchParams();
  if ($("#fPid").value.trim()) p.set("patient_id", $("#fPid").value.trim());
  if ($("#fFrom").value) p.set("date_from", $("#fFrom").value);
  if ($("#fTo").value) p.set("date_to", $("#fTo").value);
  if (R.status) p.set("status", R.status);
  if (paged) { p.set("limit", R.size); p.set("offset", R.page * R.size); }
  return p.toString();
}
async function loadResults() {
  try {
    const d = await api("/api/results?" + resultParams(true));
    R.total = d.total;
    $("#resultsTable").innerHTML = tableHTML(d.rows, { empty: "No results match. Upload a CSV or change the filters." });
    const from = d.total ? R.page * R.size + 1 : 0, to = Math.min(d.total, (R.page + 1) * R.size);
    $("#pageInfo").textContent = d.total ? `Showing ${from}–${to} of ${d.total}` : "0 results";
    $("#prev").disabled = R.page === 0; $("#next").disabled = to >= d.total;
    $("#fExport").href = "/api/results/export.csv?" + resultParams(false);
  } catch (e) { toast(e.message); }
}
let deb; const reload = () => { R.page = 0; loadResults(); };
$("#fPid").addEventListener("input", () => { clearTimeout(deb); deb = setTimeout(reload, 250); });
["#fFrom", "#fTo"].forEach(s => $(s).addEventListener("change", reload));
$$("#fStatus button").forEach(b => b.addEventListener("click", () => {
  $$("#fStatus button").forEach(x => x.classList.toggle("on", x === b)); R.status = b.dataset.v; reload();
}));
$("#fClear").addEventListener("click", () => {
  $("#fPid").value = $("#fFrom").value = $("#fTo").value = ""; R.status = "";
  $$("#fStatus button").forEach((x, i) => x.classList.toggle("on", i === 0)); reload();
});
$("#prev").addEventListener("click", () => { R.page--; loadResults(); });
$("#next").addEventListener("click", () => { R.page++; loadResults(); });

/* ---------- history page ---------- */
const STATUS_PILL = { "Completed": "good", "Completed with errors": "warn", "No new records": "", "Failed": "high" };
async function loadHistory() {
  try {
    const list = await api("/api/batches");
    if (!list.length) { $("#historyTable").innerHTML = `<table><tbody><tr><td class="empty">No uploads yet. Go to Upload to add your first CSV.</td></tr></tbody></table>`; return; }
    $("#historyTable").innerHTML = `<table><thead><tr><th>Uploaded</th><th>File</th><th class="n">Rows</th><th class="n">New</th><th class="n">Updated</th><th class="n">Duplicates</th><th class="n">Valid</th><th class="n">Invalid</th><th>Status</th><th></th></tr></thead><tbody>` +
      list.map(b => `<tr><td>${fmtDT(b.uploaded_at)}</td><td>${esc(b.filename)}${b.status === "Failed" ? `<span class="sub">${esc(b.message)}</span>` : ""}</td>
        <td class="n">${b.total}</td><td class="n">${b.new}</td><td class="n">${b.updated}</td><td class="n">${b.duplicate}</td><td class="n">${b.valid}</td><td class="n">${b.invalid}</td>
        <td><span class="pill ${STATUS_PILL[b.status] ?? ""}">${esc(b.status)}</span></td>
        <td>${b.status === "Failed" ? "" : `<a class="btn sm" href="#/batch/${b.id}">View</a>`}</td></tr>`).join("") + `</tbody></table>`;
  } catch (e) { toast(e.message); }
}
async function loadBatch(id) {
  $("#batchView").innerHTML = "";
  try { $("#batchView").innerHTML = batchHTML(await api("/api/batches/" + id)); } catch (e) { $("#batchView").innerHTML = `<div class="alert error">${esc(e.message)}</div>`; }
}

/* ---------- patients page ---------- */
let patients = [], currentPatient = null, months = 6;
async function loadPatients() {
  try { patients = await api("/api/patients"); renderPatientList(); } catch (e) { toast(e.message); }
}
function renderPatientList() {
  const q = $("#pSearch").value.trim().toLowerCase();
  const list = patients.filter(p => !q || p.patient_id.toLowerCase().includes(q) || (p.full_name || "").toLowerCase().includes(q));
  $("#patientList").innerHTML = list.length ? list.map(p =>
    `<li><button data-id="${esc(p.patient_id)}" class="${p.patient_id === currentPatient ? "sel" : ""}"><span>${esc(p.patient_id)}${p.full_name ? " · " + esc(p.full_name) : ""}</span><span class="muted">${p.n_tests}</span></button></li>`).join("")
    : `<li class="empty">${patients.length ? "No match." : "No patients yet. They appear when you upload a CSV."}</li>`;
  $$("#patientList button").forEach(b => b.addEventListener("click", () => { location.hash = "#/patients/" + encodeURIComponent(b.dataset.id); }));
}
$("#pSearch").addEventListener("input", renderPatientList);
$("#patientForm").addEventListener("submit", async e => {
  e.preventDefault();
  const o = Object.fromEntries(new FormData(e.target));
  Object.keys(o).forEach(k => { if (o[k] === "") o[k] = null; });
  try {
    await api("/api/patients", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(o) });
    toast("Patient saved"); const id = o.patient_id.trim();
    await loadPatients(); location.hash = "#/patients/" + encodeURIComponent(id);
  } catch (err) { toast(err.message); }
});
async function openPatient(id) {
  currentPatient = id;
  try {
    const d = await api(`/api/patients/${encodeURIComponent(id)}?months=${months}`);
    const p = d.patient, q = encodeURIComponent(id);
    const f = $("#patientForm");
    ["patient_id", "full_name", "date_of_birth", "sex", "transplant_date"].forEach(k => f.elements[k].value = p[k] || "");
    $("#detail").innerHTML = `
      <div class="dhead"><div><h2>${esc(p.patient_id)}${p.full_name ? " · " + esc(p.full_name) : ""}</h2>
        <p>Born ${esc(p.date_of_birth || "–")} · ${esc(p.sex || "–")} · transplanted ${esc(p.transplant_date || "–")}</p></div>
        <div class="dact"><select id="monthSel" aria-label="Time window">${[3, 6, 12].map(m => `<option value="${m}" ${m === months ? "selected" : ""}>Last ${m} months</option>`).join("")}</select>
          <a class="btn" href="/api/results/export.csv?patient_id=${q}&exact=true">Export CSV</a>
          <a class="btn primary" href="/api/patients/${q}/report.pdf?months=${months}">Download PDF</a></div></div>
      <img class="trend" alt="CM-Score trend with a threshold line at 0.0" src="/api/patients/${q}/trend.png?months=${months}&t=${Date.now()}">
      <div class="interp"><b>Latest result.</b> ${esc(d.interpretation)}</div>
      <h3 class="sec">HoloGRAFT results</h3>
      <div class="scroll">${tableHTML(d.tests, { empty: "No valid results in this window." })}</div>
      <h3 class="sec">Biopsy outcomes</h3>
      <div class="scroll"><table>${d.biopsies.length ? `<thead><tr><th>Date</th><th>Outcome</th><th>Notes</th><th></th></tr></thead><tbody>` +
        d.biopsies.map(b => `<tr><td>${esc(b.biopsy_date)}</td><td>${esc(b.outcome)}</td><td style="white-space:normal">${esc(b.notes || "")}</td><td><button class="btn sm" data-del="${b.id}">Delete</button></td></tr>`).join("") + `</tbody>`
        : `<tbody><tr><td class="empty">No biopsies recorded.</td></tr></tbody>`}</table></div>
      <form id="biopsyForm" class="bform"><input type="date" name="biopsy_date" required aria-label="Biopsy date">
        <select name="outcome" required aria-label="Outcome"><option value="">Outcome</option><option>No rejection</option><option>Borderline</option><option>TCMR</option><option>ABMR</option><option>Mixed rejection</option><option>Other</option></select>
        <button class="btn primary">Add biopsy</button><textarea name="notes" rows="2" placeholder="Notes (optional)"></textarea></form>`;
    $("#monthSel").addEventListener("change", e => { months = +e.target.value; openPatient(id); });
    $("#biopsyForm").addEventListener("submit", async e => {
      e.preventDefault();
      const o = Object.fromEntries(new FormData(e.target)); if (!o.notes) o.notes = null;
      try { await api(`/api/patients/${q}/biopsies`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(o) }); toast("Biopsy added"); openPatient(id); }
      catch (err) { toast(err.message); }
    });
    $$("[data-del]").forEach(b => b.addEventListener("click", async () => {
      if (confirm("Delete this biopsy record?")) { await api(`/api/biopsies/${b.dataset.del}`, { method: "DELETE" }); openPatient(id); }
    }));
    renderPatientList();
  } catch (e) { $("#detail").innerHTML = `<div class="alert error">${esc(e.message)}</div>`; }
}

/* ---------- router ---------- */
function route() {
  const parts = (location.hash.replace(/^#\/?/, "") || "upload").split("/");
  const page = ["upload", "results", "history", "batch", "patients"].includes(parts[0]) ? parts[0] : "upload";
  const arg = parts[1] ? decodeURIComponent(parts[1]) : null;
  $$(".page").forEach(s => s.hidden = s.id !== "page-" + page);
  $$(".links a").forEach(a => a.classList.toggle("on", a.dataset.nav === (page === "batch" ? "history" : page)));
  window.scrollTo(0, 0);
  if (page === "results") loadResults();
  if (page === "history") loadHistory();
  if (page === "batch" && arg) loadBatch(arg);
  if (page === "patients") { loadPatients().then(() => { if (arg) openPatient(arg); }); }
}
window.addEventListener("hashchange", route);
route();

/* صفحة التخطيط: رفع شيت الطلاب + سعة الجروب ← العدد والبنود والمحافظات والليفيلات والجروبات المطلوبة */
let FILE = null, REP = null, CUR = null, HIST = [], MODE = "physical";
const MODES = {physical: "Physical", online: "Online"};
const B1 = "البند الأول", B2 = "البند الثاني", B3 = "البند الثالث", UNK_GOV = "غير معروفة", UNK_LV = "غير محدد";

(function setupDrop() {
  const dz = $("#dz_students");
  ["dragenter", "dragover"].forEach(e => dz.addEventListener(e, ev => { ev.preventDefault(); dz.classList.add("dragover"); }));
  ["dragleave", "drop"].forEach(e => dz.addEventListener(e, ev => { ev.preventDefault(); dz.classList.remove("dragover"); }));
  dz.addEventListener("drop", ev => {
    const f = ev.dataTransfer && ev.dataTransfer.files[0];
    if (f) { FILE = f; analyze(); }
  });
  let m = "physical";
  try { m = localStorage.getItem("markazi_plan_mode") || m; } catch (e) { /* ignore */ }
  paintMode(m in MODES ? m : "physical");
  const c = savedCap(MODE); if (c) $("#cap").value = c;
})();

function onPick() {
  const f = $("#students").files[0];
  if (f) { FILE = f; analyze(); }
}

/* نوع الجروبات: Physical / Online — كل نوع ليه سعته المحفوظة */
const modePill = m => `<span class="pill ${m === "online" ? "ok" : "brand"}">${m === "online" ? "💻 Online" : "🏫 Physical"}</span>`;
function savedCap(m) {
  try { return localStorage.getItem("markazi_plan_cap_" + m) || (m === "physical" ? localStorage.getItem("markazi_plan_cap") : null); }
  catch (e) { return null; }
}
function paintMode(m) {
  MODE = m;
  $$("#mode_seg button").forEach(b => { const on = b.dataset.mode === m; b.classList.toggle("on", on); b.setAttribute("aria-checked", on); });
}
function setMode(m) {
  if (!(m in MODES) || m === MODE) return;
  paintMode(m);
  try { localStorage.setItem("markazi_plan_mode", m); } catch (e) { /* ignore */ }
  const c = savedCap(m); if (c) $("#cap").value = c;
  if (!REP) return;
  render(); setCurrentMeta();
  // النوع بيتحفظ مع التحليل في السجل
  if (CUR) postJSON(`/api/planning/${CUR.id}`, {mode: m, ...(cap() ? {cap: cap()} : {})})
    .then(r => { if (r.ok) { CUR.mode = m; if (cap()) CUR.cap = cap(); loadHistory(); } });
}

/* سعة الجروب: أي تغيير بيعيد الحساب فورًا من غير رفع تاني */
function cap() { const v = parseInt($("#cap").value, 10); return v > 0 ? v : 0; }
let _capT = null;
function onCap() {
  try { localStorage.setItem("markazi_plan_cap_" + MODE, $("#cap").value); } catch (e) { /* ignore */ }
  if (!REP) return;
  render(); setCurrentMeta();
  // السعة بتتحفظ مع التحليل في السجل
  clearTimeout(_capT);
  _capT = setTimeout(async () => {
    if (!CUR || !cap()) return;
    const r = await postJSON(`/api/planning/${CUR.id}`, {cap: cap()});
    if (r.ok) { CUR.cap = cap(); loadHistory(); }
  }, 700);
}
const ceilDiv = (a, b) => (b && a ? Math.ceil(a / b) : 0);

/* جروبات كل ليفيل في المحافظة (المحافظة/الليفيل غير المعروفين ما بيتحسبوش: هيبقوا VP) */
function govGroups(g) {
  const c = cap(), out = {};
  if (g.gov_code === UNK_GOV || !c) return out;
  Object.entries(g.levels).forEach(([lv, cnt]) => { if (lv !== UNK_LV && cnt) out[lv] = ceilDiv(cnt, c); });
  return out;
}
const sumVals = o => Object.values(o).reduce((s, v) => s + v, 0);

async function analyze() {
  $("#fname").textContent = "📄 " + FILE.name;
  $("#msg").className = "hint"; $("#msg").textContent = "⏳ جاري قراءة الشيت وتحليله...";
  $("#xl").hidden = true;
  const fd = new FormData(); fd.append("students", FILE); if (cap()) fd.append("cap", String(cap())); fd.append("mode", MODE);
  const r = await api("/api/planning/analyze", {method: "POST", body: fd});
  if (!r.ok) {
    $("#msg").className = "hint bad"; $("#msg").textContent = r.j.error || "فشل التحليل";
    $("#out").hidden = true; return;
  }
  $("#msg").textContent = "";
  show(r.j);
  loadHistory();
  toast("تم تحليل الشيت واتحفظ في السجل", "ok");
}

/* عرض تحليل (جديد أو من السجل) */
function show(p) {
  CUR = {id: p.id, name: p.name || p.file_name, file_name: p.file_name, cap: p.cap, mode: p.mode || "physical", created_at: p.created_at, user: p.user};
  REP = p.report;
  OPEN_AREAS.clear();
  Object.assign(RV, {kind: "", gov: "", conf: "", area: null, q: "", page: 1}); $("#rv_q").value = "";
  paintMode(CUR.mode in MODES ? CUR.mode : "physical");
  if (p.cap) $("#cap").value = p.cap;
  history.replaceState(null, "", "/planning?id=" + encodeURIComponent(p.id));
  setCurrentMeta();
  render();
  renderHistory();
}

function setCurrentMeta() {
  if (!CUR) return;
  $("#cur_name").textContent = CUR.name || "";
  $("#cur_meta").innerHTML = modePill(MODE) + " " + esc([CUR.created_at, CUR.user, CUR.file_name !== CUR.name ? CUR.file_name : ""].filter(Boolean).join(" · "));
  $("#xl").hidden = false;
  $("#xl").href = `/api/planning/${encodeURIComponent(CUR.id)}/export.xlsx` + "?mode=" + MODE + (cap() ? "&cap=" + cap() : "");
}

async function openPlan(id) {
  $("#msg").className = "hint"; $("#msg").textContent = "⏳ جاري فتح التحليل...";
  const r = await api("/api/planning/" + encodeURIComponent(id));
  if (!r.ok) { $("#msg").className = "hint bad"; $("#msg").textContent = r.j.error || "تعذر فتح التحليل"; return; }
  $("#msg").textContent = ""; $("#fname").textContent = "";
  show(r.j);
  $("#out").scrollIntoView({behavior: "smooth", block: "start"});
}

async function renamePlan(id) {
  const p = HIST.find(h => h.id === id); if (!p) return;
  const name = prompt("اسم التحليل (مثلاً: شيت أكتوبر بعد التعديل):", p.name || p.file_name);
  if (name == null) return;
  const r = await postJSON(`/api/planning/${encodeURIComponent(id)}`, {name});
  if (!r.ok) { toast(r.j.error || "تعذر الحفظ", "bad"); return; }
  if (CUR && CUR.id === id) { CUR.name = r.j.name; setCurrentMeta(); }
  loadHistory();
}

async function deletePlan(id) {
  const p = HIST.find(h => h.id === id);
  if (!confirm(`مسح التحليل «${p ? (p.name || p.file_name) : id}» من السجل؟`)) return;
  const r = await del(`/api/planning/${encodeURIComponent(id)}`);
  if (!r.ok) { toast(r.j.error || "تعذر المسح", "bad"); return; }
  if (CUR && CUR.id === id) { CUR = null; REP = null; $("#out").hidden = true; $("#xl").hidden = true; history.replaceState(null, "", "/planning"); }
  loadHistory();
}

async function loadHistory() {
  const r = await api("/api/planning/history");
  if (!r.ok) return;
  HIST = r.j; renderHistory();
}

function renderHistory() {
  const q = ($("#h_q").value || "").trim().toLowerCase();
  const rows = HIST.filter(h => !q || (h.name || "").toLowerCase().includes(q) || (h.file_name || "").toLowerCase().includes(q));
  $("#h_count").textContent = n(HIST.length);
  table("#t_hist", [
    ["created_at", "التاريخ"],
    ["name", "الاسم", (v, h) => `<a href="#" onclick="openPlan('${esc(h.id)}');return false"><b>${esc(v || h.file_name)}</b></a>` +
      (h.file_name && h.file_name !== v ? `<div class="small mute">${esc(h.file_name)}</div>` : "")],
    ["n_students", "الطلاب"], ["n_govs", "محافظات"], ["n_levels", "ليفيلات"],
    ["band2", "بند ثاني", v => v ? `<span class="pill warn">${n(v)}</span>` : `<span class="mute">0</span>`],
    ["mode", "النوع", v => modePill(v || "physical")],
    ["cap", "السعة", v => v ? n(v) : "—"], ["groups", "الجروبات", v => v == null ? "—" : `<b>${n(v)}</b>`],
    ["user", "بواسطة"],
    ["id", "", v => `<div class="row" style="gap:4px;flex-wrap:nowrap">
        <button class="btn sm" onclick="openPlan('${esc(v)}')">فتح</button>
        <a class="btn sm sec" href="/api/planning/${esc(v)}/export.xlsx" title="تحميل Excel">📥</a>
        <button class="btn sm sec" onclick="renamePlan('${esc(v)}')" title="تغيير الاسم">✏️</button>
        <button class="btn sm bad" onclick="deletePlan('${esc(v)}')" title="مسح">🗑</button></div>`],
  ], rows, {empty: q ? "مفيش نتايج للبحث ده" : "لسه مفيش تحليلات. ارفع شيت عشان يتحفظ هنا.",
    rowAttr: h => (CUR && h.id === CUR.id ? 'style="background:rgba(99,102,241,.10)"' : "")});
}

const pct = v => (v == null ? "" : v + "%");
const bandPill = b => `<span class="pill ${b === B2 ? "warn" : b === UNK_LV ? "bad" : b === B1 ? "brand" : "ok"}">${esc(b)}</span>`;
const chip = (cls, label, v) => `<span class="pill ${cls}" style="padding:1px 5px;font-size:9.5px">${label}: <b>${v}</b></span>`;

function render() {
  const x = REP, c = cap();
  $("#out").hidden = false;
  x.govs.forEach(g => { g.groups_by_level = govGroups(g); g.groups = sumVals(g.groups_by_level); });
  const bandOf = name => x.bands.find(b => b.band === name) || {students: 0, govs: 0};
  const b1 = bandOf(B1), b2 = bandOf(B2), b3 = bandOf(B3);
  const groupsIn = band => x.govs.filter(g => g.band === band).reduce((s, g) => s + g.groups, 0);
  // Online: الجروب ليفيل واحد من أي محافظة، فالجروبات بتتحسب على مستوى الجمهورية
  const online = MODE === "online";
  const lvStudents = lv => x.govs.reduce((s, g) => s + (g.levels[lv] || 0), 0);
  const natGroups = lv => (lv === UNK_LV ? 0 : ceilDiv(lvStudents(lv), c));
  const totalGroups = online ? x.levels.reduce((s, l) => s + natGroups(l.level), 0) : x.govs.reduce((s, g) => s + g.groups, 0);
  const counted = online ? x.total - (x.unknown_level || 0)
    : x.govs.filter(g => g.gov_code !== UNK_GOV).reduce((s, g) => s + g.students - (g.levels[UNK_LV] || 0), 0);
  const fill = totalGroups ? Math.round(100 * counted / (totalGroups * c)) : 0;

  const k = (v, l, cls, prefix) => `<div class="kpi ${prefix || ""}"><b class="${cls || ""}">${v}</b><span>${l}</span></div>`;
  const b2Line = b2.students
    ? `<div style="margin-top:2px"><span class="badge bad" style="font-size:9.5px;font-weight:700;padding:1px 4px">⚠️ بند ثاني: ${n(b2.govs)}</span></div>`
    : `<div style="font-size:9.5px;color:var(--mute);margin-top:2px">بند ثاني: 0 (سليم ✓)</div>`;
  const govCount = x.govs.filter(g => g.gov_code !== UNK_GOV).length;
  const govCard = `<div style="width:100%">
      <div style="font-size:19px;font-weight:800;line-height:1.1">${n(govCount)} <small style="font-size:11px;font-weight:600;color:var(--mute)">محافظة</small></div>
      <div style="margin-top:2px;display:flex;gap:3px;justify-content:center;flex-wrap:wrap">${chip("brand", "بند 1", b1.govs)}${chip("ok", "بند 3", b3.govs)}</div>
      ${b2Line}</div>`;
  const stCard = `<div style="width:100%">
      <div style="margin-top:2px;display:flex;flex-direction:column;gap:3px;align-items:center">
        ${chip("brand", "بند 1", n(b1.students) + " (" + (b1.pct || 0) + "%)")}
        ${chip("ok", "بند 3", n(b3.students) + " (" + (b3.pct || 0) + "%)")}
        ${b2.students ? chip("warn", "بند 2", n(b2.students)) : ""}
      </div></div>`;
  const grCard = c ? `<div style="width:100%">
      <div style="font-size:22px;font-weight:800;line-height:1.1;color:var(--ok)">${n(totalGroups)} <small style="font-size:11px;font-weight:600;color:var(--mute)">جروب</small></div>
      <div style="margin-top:2px;display:flex;gap:3px;justify-content:center;flex-wrap:wrap">${online ? chip("ok", "💻 Online", "على مستوى الجمهورية")
        : chip("brand", "بند 1", n(groupsIn(B1))) + chip("ok", "بند 3", n(groupsIn(B3))) + (groupsIn(B2) ? chip("warn", "بند 2", n(groupsIn(B2))) : "")}</div></div>`
    : `<span class="mute" style="font-size:13px">حدد السعة</span>`;
  const topLv = x.levels.find(l => l.level !== UNK_LV);
  const issues = (x.unknown_gov || 0) + (x.unknown_level || 0) + ((x.nid && (x.nid.invalid + x.nid.dup)) || 0);

  $("#kpis").innerHTML =
    k(n(x.total), "إجمالي عدد الطلاب", "", "kpi-brand") +
    k(govCard, "عدد المحافظات في كل بند", b2.students ? "bad" : "", b2.students ? "kpi-bad" : "kpi-brand") +
    k(stCard, "الطلاب في كل بند", "", "kpi-brand") +
    k(grCard, "إجمالي الجروبات المطلوبة", "ok", "kpi-ok") +
    k(c && totalGroups ? `${fill}%` : "—", "متوسط امتلاء الجروب", "brand", "kpi-brand") +
    k(c ? `${n(c)} <small style="font-size:12px;color:var(--mute)">طالب</small>` : "—", "سعة الجروب", "", "") +
    k(`<span style="font-size:16px">${n(x.levels.length)} ليفيل</span>`, topLv ? `الأكبر ${esc(topLv.level)} (${n(topLv.students)})` : "الليفيلات", "", issues ? "kpi-bad" : "");

  const notes = [];
  if (x.note) notes.push(esc(x.note));
  if (x.unknown_gov) notes.push(`⚠ ${n(x.unknown_gov)} طالب محافظتهم غير معروفة: ` + Object.entries(x.unknown_gov_names || {}).map(([g, cn]) => `${esc(g)} (${n(cn)})`).join("، "));
  if (x.unknown_level) notes.push(`⚠ ${n(x.unknown_level)} طالب الليفيل بتاعهم مش مفهوم.`);
  if (x.nid && x.nid.dup) notes.push(`⚠ ${n(x.nid.dup)} رقم قومي مكرر.`);
  if (x.nid && x.nid.invalid) notes.push(`⚠ ${n(x.nid.invalid)} رقم قومي مش 14 رقم.`);
  if ((x.unknown_gov || x.unknown_level) && c) notes.push("الطلاب دول مش داخلين في حساب الجروبات.");
  if (!x.govs.some(g => g.areas)) notes.push("ℹ️ " + esc(areasMissingMsg()));
  $("#p_notes").innerHTML = notes.join("<br>");

  const sel = $("#f_band").value;
  $("#f_band").innerHTML = `<option value="">كل البنود</option>` + x.bands.filter(b => b.students).map(b => `<option>${esc(b.band)}</option>`).join("");
  $("#f_band").value = sel;
  renderGovs();

  x.levels.forEach(l => { l.groups = online ? natGroups(l.level) : x.govs.reduce((s, g) => s + (g.groups_by_level[l.level] || 0), 0); });
  table("#t_levels", [["rank", "الترتيب"], ["level", "الليفيل"], ["students", "عدد الطلاب"], ["pct", "النسبة", pct],
    ["govs", "موجود في كام محافظة"], ...(c ? [["groups", "الجروبات المطلوبة", v => `<b>${n(v)}</b>`]] : [])], x.levels);

  renderMatrix();
  renderReview();
  loadCodes();
}

function renderGovs() {
  const band = $("#f_band").value, c = MODE === "online" ? 0 : cap();   // Online: مفيش جروبات لكل محافظة
  const rows = REP.govs.filter(g => !band || g.band === band);
  table("#t_govs", [
    [band ? "band_rank" : "rank", band ? "الترتيب في البند" : "الترتيب"],
    ["gov", "المحافظة"], ["band", "البند", v => bandPill(v)], ["students", "عدد الطلاب"],
    ["areas", "المناطق", (v, g) => !v ? `<span class="mute small">${REP.has_areas === false ? "مفيش عمود منطقة" : "ارفع الشيت تاني"}</span>`
      : `<button class="btn sm sec" style="white-space:nowrap" onclick="toggleAreas('${esc(g.gov_code)}')">${n(v.length)} منطقة <span id="ar_ic_${esc(g.gov_code)}">${OPEN_AREAS.has(g.gov_code) ? "▴" : "▾"}</span></button>` + govIssueBadge(g)],
    ["pct", "من الإجمالي", pct], ["pct_band", "من البند", pct],
    ...(c ? [["groups", "الجروبات المطلوبة", v => `<b>${n(v)}</b>`]] : []),
    ["levels_ranked", "الليفيلات بالترتيب" + (c ? " (طلاب / جروبات)" : ""), (v, g) => (v || []).map((l, i) =>
      `<span class="pill${i === 0 ? " brand" : ""}" title="${l.pct}% من المحافظة">${esc(l.level)}: ${n(l.students)}${c && g.groups_by_level[l.level] ? " / " + n(g.groups_by_level[l.level]) + " G" : ""}</span>`).join(" ")],
  ], rows);
  // صف المناطق تحت كل محافظة (مخفي لحد ما تدوس)
  const trs = $$("#t_govs tr");
  rows.forEach((g, i) => {
    if (!g.areas) return;
    const tr = document.createElement("tr");
    tr.id = "ar_" + g.gov_code; tr.hidden = !OPEN_AREAS.has(g.gov_code);
    tr.innerHTML = `<td colspan="${trs[0].children.length}" style="background:var(--bg, transparent);padding:10px 14px">${areasHtml(g)}</td>`;
    trs[i + 1].after(tr);
  });
  $("#btn_all_areas").textContent = OPEN_AREAS.size ? "▴ إخفاء كل المناطق" : "▾ إظهار كل المناطق";
}

/* المناطق جوّه المحافظة: مرتبة بالعدد، والليفيلات والكتابات التانية في التلميح */
const OPEN_AREAS = new Set();
function areasHtml(g) {
  const lvTip = a => Object.entries(a.levels || {}).sort((x, y) => y[1] - x[1]).map(([l, c]) => `${l}: ${n(c)}`).join(" · ");
  const ac = (REP.area_check || {}).by_gov || {}, iss = ac[g.gov_code];
  const issLinks = iss ? Object.entries(RV_KINDS).filter(([k]) => iss[k]).map(([k, t]) =>
    `<a href="#" onclick="openReview('${k}','${esc(g.gov_code)}');return false" class="pill bad" style="text-decoration:none">${t.icon} ${t.label}: <b>${n(iss[k])}</b></a>`).join(" ") : "";
  return (issLinks ? `<div style="display:flex;flex-wrap:wrap;gap:5px;align-items:center;margin-bottom:8px"><span class="small"><b>محتاج مراجعة:</b></span> ${issLinks}</div>` : "") +
    `<div class="small mute" style="margin-bottom:6px">مناطق ${esc(g.gov)} (${n(g.areas.length)} منطقة، ${n(g.students)} طالب) — مرتبة بعدد الطلاب، ووقّف الماوس على المنطقة تشوف الليفيلات:</div>
    <div style="display:flex;flex-wrap:wrap;gap:5px">` +
    g.areas.map((a, i) => `<span class="pill${i < 3 ? " brand" : ""}${a.area === "بدون منطقة" ? " warn" : ""}"
        ${a.area === "بدون منطقة" && REP.area_check ? `style="cursor:pointer" onclick="openReview('no_area','${esc(g.gov_code)}')"` : ""}
        title="${esc(lvTip(a))}${a.variants && a.variants.length ? "\nكتابات تانية: " + esc(a.variants.join("، ")) : ""}">
        <span class="mute">${a.rank}.</span> ${esc(a.area)}: <b>${n(a.students)}</b> <span class="mute">(${a.pct}%)</span></span>`).join("") + `</div>`;
}
function toggleAreas(code) {
  const tr = document.getElementById("ar_" + code); if (!tr) return;
  tr.hidden = !tr.hidden;
  if (tr.hidden) OPEN_AREAS.delete(code); else OPEN_AREAS.add(code);
  const ic = document.getElementById("ar_ic_" + code); if (ic) ic.textContent = tr.hidden ? "▾" : "▴";
  $("#btn_all_areas").textContent = OPEN_AREAS.size ? "▴ إخفاء كل المناطق" : "▾ إظهار كل المناطق";
}
const areasMissingMsg = () => REP.has_areas === false
  ? "الشيت ده مفيهوش عمود منطقة (Area)، فمفيش مناطق تتعرض."
  : "التحليل ده اتحفظ قبل إضافة المناطق — ارفع نفس الشيت تاني عشان تطلع المناطق.";
function toggleAllAreas() {
  if (!REP.govs.some(g => g.areas)) { toast(areasMissingMsg(), "bad"); return; }
  const open = OPEN_AREAS.size === 0;
  OPEN_AREAS.clear();
  if (open) REP.govs.forEach(g => { if (g.areas) OPEN_AREAS.add(g.gov_code); });
  renderGovs();
}

/* كل ليفيل في كل محافظة: الطلاب، وتحتهم الجروبات المطلوبة (لو فيه سعة) */
function renderMatrix() {
  const c = cap(), online = MODE === "online";
  const sel = $("#f_matrix");
  [...sel.options].forEach(o => { o.disabled = o.value !== "students" && !c; });
  const mode = c ? sel.value : "students";
  const showSt = mode !== "groups", showGr = mode !== "students";
  const lo = REP.levels_order.filter(l => showSt || l !== UNK_LV);
  const govs = REP.govs.filter(g => showSt || g.gov_code !== UNK_GOV);
  const rows = govs.map(g => {
    const r = {gov: g.gov, band: g.band, st: g.students, gr: g.groups || 0};
    lo.forEach(l => { r["lv_" + l] = {st: g.levels[l] || 0, gr: (g.groups_by_level || {})[l] || 0}; });
    return r;
  });
  // صف الإجمالي: في الـOnline الجروبات على مستوى الجمهورية (طلاب الليفيل من كل المحافظات مع بعض)
  const tot = {gov: online && showGr ? "الإجمالي (Online: على مستوى الجمهورية)" : "الإجمالي", band: "", _tot: true,
    st: rows.reduce((s, r) => s + r.st, 0), gr: 0};
  lo.forEach(l => {
    const st = REP.govs.reduce((s, g) => s + (g.levels[l] || 0), 0);
    const gr = online ? (l === UNK_LV ? 0 : ceilDiv(st, c)) : rows.reduce((s, r) => s + r["lv_" + l].gr, 0);
    tot["lv_" + l] = {st: rows.reduce((s, r) => s + r["lv_" + l].st, 0), gr};
    tot.gr += gr;
  });
  rows.push(tot);
  const fmt = (v, bold) => {
    if (!v.st && !v.gr) return `<span class="mute">—</span>`;
    const st = bold ? `<b>${n(v.st)}</b>` : n(v.st);
    const gr = `<span style="color:var(--ok);font-weight:700">${n(v.gr)}</span> <span class="small mute">G</span>`;
    if (!showGr) return st;
    if (!showSt) return v.gr ? gr : `<span class="mute">—</span>`;
    return `${st}<div class="small" style="margin-top:2px">${v.gr ? gr : '<span class="mute">—</span>'}</div>`;
  };
  table("#t_matrix", [["gov", "المحافظة", (v, r) => r._tot ? `<b>${esc(v)}</b>` : esc(v)], ["band", "البند", v => v ? bandPill(v) : ""],
    ...lo.map(l => ["lv_" + l, l, (v, r) => fmt(v, r._tot)]),
    ["st", showGr && !showSt ? "إجمالي الجروبات" : "الإجمالي", (v, r) => fmt({st: r.st, gr: r.gr}, true)]], rows);
}


/* ---------------- مراجعة المناطق ---------------- */
const RV_KINDS = {
  no_area: {label: "بدون منطقة", icon: "∅"},
  street: {label: "شارع بدل منطقة", icon: "🛣️"},
  other_gov: {label: "منطقة في محافظة تانية", icon: "🔀"},
};
const RV = {kind: "", gov: "", conf: "", area: null, q: "", page: 1};
let RV_GROUPS = [];

function govIssueBadge(g) {
  const iss = ((REP.area_check || {}).by_gov || {})[g.gov_code];
  if (!iss) return "";
  const t = Object.values(iss).reduce((a, b) => a + b, 0);
  const tip = Object.entries(RV_KINDS).filter(([k]) => iss[k]).map(([k, x]) => `${x.label}: ${n(iss[k])}`).join(" · ");
  return t ? ` <a href="#" class="pill bad" style="text-decoration:none;white-space:nowrap" title="${esc(tip)}" onclick="openReview('','${esc(g.gov_code)}');return false">⚠ ${n(t)}</a>` : "";
}

function firstKind(gov) {
  const c = gov ? (((REP.area_check || {}).by_gov || {})[gov] || {}) : ((REP.area_check || {}).counts || {});
  return Object.keys(RV_KINDS).find(k => c[k]) || "no_area";
}

/* فتح المراجعة على نوع ومحافظة معينين (من شارة المحافظة أو من مناطقها) */
function openReview(kind, gov) {
  Object.assign(RV, {kind: kind || firstKind(gov), gov: gov || "", conf: "", area: null, q: "", page: 1});
  $("#rv_q").value = "";
  renderReview();
  $("#rv_card").scrollIntoView({behavior: "smooth", block: "start"});
}

let _rvQT = null;
$("#rv_q").addEventListener("input", () => { clearTimeout(_rvQT); _rvQT = setTimeout(() => rvSet({q: $("#rv_q").value.trim()}), 300); });

function rvSet(patch, keepPage) {
  Object.assign(RV, patch);
  if (!keepPage) RV.page = 1;
  renderReview();
}

function rvQuery(extra) {
  const p = new URLSearchParams({kind: RV.kind, gov: RV.gov, conf: RV.kind === "other_gov" ? RV.conf : "", q: RV.q, ...extra});
  if (RV.area != null) p.set("area", RV.area);
  return p.toString();
}

const confPill = v => `<span class="pill ${v === "أكيد" ? "bad" : "warn"}">${esc(v)}</span>`;

function renderReview() {
  const ac = REP && REP.area_check;
  const has = !!(ac && ac.available);
  $("#rv_body").hidden = !has; $("#rv_tabs").hidden = !has; $("#rv_xl").hidden = !has;
  $("#rv_missing").hidden = has;
  if (!has) {
    $("#rv_total").textContent = "";
    $("#rv_missing").textContent = ac && ac.available === false ? "الشيت ده مفيهوش عمود منطقة (Area)."
      : "التحليل ده اتحفظ قبل إضافة مراجعة المناطق — ارفع نفس الشيت تاني عشان تطلع.";
    return;
  }
  if (!RV.kind) RV.kind = firstKind();
  $("#rv_total").textContent = n(ac.total) + " طالب";
  $("#rv_tabs").innerHTML = Object.entries(RV_KINDS).map(([k, t]) =>
    `<button class="btn sm ${RV.kind === k ? "" : "sec"}" onclick="rvSet({kind:'${k}', area:null, conf:''})">${t.icon} ${t.label} <span class="pill ${ac.counts[k] ? "bad" : "ok"}" style="margin-right:4px">${n(ac.counts[k] || 0)}</span></button>`).join("") +
    (ac.gazetteer ? "" : `<span class="small mute">⚠ قاعدة الأماكن مش موجودة: فحص «محافظة تانية» بيعتمد على أسماء المحافظات بس.</span>`);

  // فلتر المحافظة: المحافظات اللي فيها مشاكل من النوع ده بس
  const govOpts = Object.entries(ac.by_gov || {}).filter(([, c]) => c[RV.kind]).sort((a, b) => b[1][RV.kind] - a[1][RV.kind]);
  $("#rv_gov").innerHTML = `<option value="">كل المحافظات</option>` +
    govOpts.map(([g, c]) => `<option value="${esc(g)}">${esc(govAr(g))} (${n(c[RV.kind])})</option>`).join("");
  $("#rv_gov").value = RV.gov;
  $("#rv_conf").hidden = RV.kind !== "other_gov"; $("#rv_conf").value = RV.conf;
  $("#rv_area_pill").innerHTML = RV.area != null
    ? `<span class="pill brand">المنطقة: ${esc(RV.area)} <a href="#" onclick="rvSet({area:null});return false" title="إلغاء">✕</a></span>` : "";

  // المناطق المتكررة (للشارع والمحافظة التانية)
  const showGroups = RV.kind !== "no_area";
  $("#rv_groups_wrap").hidden = !showGroups;
  RV_GROUPS = !showGroups ? [] : (ac.groups || []).filter(x => x.kind === RV.kind && (!RV.gov || x.gov_code === RV.gov)
    && (!RV.conf || RV.kind !== "other_gov" || x.conf === RV.conf));
  if (showGroups) {
    table("#rv_groups", [["gov", "المحافظة"], ["area", "المنطقة"],
      ...(RV.kind === "other_gov" ? [["other_gov", "المحافظة المحتملة", v => v ? `<b>${esc(v)}</b>` : "<span class='mute'>أكتر من محافظة</span>"],
        ["conf", "الدرجة", confPill]] : []),
      ["students", "عدد الطلاب"], ["reason", "التفاصيل", v => `<span class="small">${esc(v)}</span>`],
      ["_i", "", (v, x) => `<button class="btn sm sec" onclick="rvPickGroup(${RV_GROUPS.indexOf(x)})">الطلاب ←</button>`]],
      RV_GROUPS, {empty: "مفيش"});
  }
  $("#rv_xl").href = `/api/planning/${encodeURIComponent(CUR.id)}/flags.xlsx?` + rvQuery({});
  loadFlags();
}

function rvPage(d) { rvSet({page: Math.max(1, RV.page + d)}, true); }

function rvPickGroup(i) {
  const x = RV_GROUPS[i]; if (!x) return;
  rvSet({gov: x.gov_code, area: x.area});
}

let _rvSeq = 0;
async function loadFlags() {
  const seq = ++_rvSeq;
  $("#rv_info").textContent = "⏳ جاري التحميل...";
  const r = await api(`/api/planning/${encodeURIComponent(CUR.id)}/flags?` + rvQuery({page: RV.page, per: 50}));
  if (seq !== _rvSeq) return;
  if (!r.ok) { $("#rv_info").textContent = r.j.error || "تعذر التحميل"; $("#rv_rows").innerHTML = ""; return; }
  const d = r.j, pages = Math.max(1, Math.ceil(d.total / d.per_page));
  table("#rv_rows", [
    ["row_no", "رقم الصف"], ["nid", "الرقم القومي", v => `<span style="font-family:monospace">${esc(v)}</span>`],
    ["gov_raw", "المحافظة"], ["area", "المنطقة", v => v ? esc(v) : "<span class='mute'>— فاضي —</span>"],
    ["address", "العنوان", v => `<span class="small">${esc(v)}</span>`], ["level", "الليفيل"],
    ...(RV.kind === "other_gov" ? [["other_gov_ar", "المحافظة المحتملة"], ["conf", "الدرجة", confPill]] : []),
  ], d.rows, {empty: "مفيش طلاب بالفلتر ده"});
  $("#rv_info").textContent = d.total ? `${n(d.total)} طالب — صفحة ${d.page} من ${pages}` : "";
  $("#rv_prev").disabled = d.page <= 1; $("#rv_next").disabled = d.page >= pages;
}

/* ---------------- أكواد الجروبات ---------------- */
let CODES = null, CD_ROWS = [], CD_PG = 1, _cdT = null, _cdSeq = 0;
const CD_PER = 100;
const CD_FMT = {
  online: "<b>ON-Day-Slot-Level-Track-Class</b> · مثال <code>ON-A-S1-L2-GEN-C047</code> · Day: A = الجمعة / B = السبت · Slot: S1 للكل · Track: GEN لـ L1/L2 وغير كده التراك (CY/DT/WB/DA/EM) · الجروبات على مستوى الجمهورية لكل ليفيل.",
  physical: "<b>PH-Day-Gov-Venue-Level-Track-Class</b> · مثال <code>PH-B-CAI-V01-L2-GEN-C008</code> · Day: A = الجمعة / B = السبت · Gov: 3 حروف (من أكواد المراكز) · Venue: V01 لحد ما المراكز تتحدد · الجروبات لكل محافظة لوحدها.",
};
function loadCodes() { clearTimeout(_cdT); _cdT = setTimeout(fetchCodes, 250); }
async function fetchCodes() {
  if (!CUR) return;
  $("#cd_type").innerHTML = MODE === "online" ? "💻 Online" : "🏫 Physical";
  $("#cd_fmt").innerHTML = CD_FMT[MODE];
  if (!cap()) { $("#cd_body").hidden = true; $("#cd_xl").hidden = true; $("#cd_msg").textContent = "حدد سعة الجروب عشان الأكواد تطلع."; return; }
  const qs = `?mode=${MODE}&cap=${cap()}`, seq = ++_cdSeq;
  $("#cd_xl").hidden = false;
  $("#cd_xl").href = `/api/planning/${encodeURIComponent(CUR.id)}/codes.xlsx` + qs;
  $("#cd_msg").textContent = "⏳ جاري تجهيز الأكواد...";
  const r = await api(`/api/planning/${encodeURIComponent(CUR.id)}/codes` + qs);
  if (seq !== _cdSeq) return;                       // اتغيّر النوع/السعة أثناء التحميل
  if (!r.ok) { $("#cd_msg").textContent = r.j.error || "تعذر تجهيز الأكواد"; $("#cd_body").hidden = true; return; }
  CODES = r.j; $("#cd_msg").textContent = ""; $("#cd_body").hidden = false;
  renderCodes();
}
function renderCodes() {
  const x = CODES, s = x.summary, phys = x.mode === "physical";
  const k = (v, l, cls) => `<div class="kpi"><b class="${cls || ""}">${v}</b><span>${l}</span></div>`;
  const day = d => s.days.find(z => z.day === d) || {groups: 0, students: 0};
  $("#cd_kpis").innerHTML = k(n(s.groups), "إجمالي الجروبات", "ok") + k(n(s.students), "طالب في الجروبات") +
    k(`${n(day("A").groups)} <small style="font-size:12px;color:var(--mute)">جروب · ${n(day("A").students)} طالب</small>`, "A · الجمعة", "brand") +
    k(`${n(day("B").groups)} <small style="font-size:12px;color:var(--mute)">جروب · ${n(day("B").students)} طالب</small>`, "B · السبت", "brand");
  const ex = x.excluded || [];
  $("#cd_excluded").hidden = !ex.length;
  $("#cd_excluded").innerHTML = ex.length ? `⚠ ${n(ex.reduce((a, e) => a + e.students, 0))} طالب مش داخلين في أكواد الـPhysical: ` +
    ex.map(e => `${esc(e.gov)} (${n(e.students)} — ${esc(e.reason)})`).join("، ") : "";
  table("#cd_levels", [["level", "Level"], ["track", "Track"], ["A", "A · الجمعة"], ["B", "B · السبت"],
    ["groups", "الإجمالي", v => `<b>${n(v)}</b>`], ["students", "الطلاب"]], s.levels);
  $("#cd_govs_wrap").hidden = !phys;
  // Online مفيهوش جدول محافظات: جدول الليفيلات ياخد عرض الكارت كله وطوله كامل من غير سكرول جوّاه
  $("#cd_grid").style.gridTemplateColumns = phys ? "" : "1fr";
  $("#cd_levels_wrap").style.maxHeight = phys ? "330px" : "none";
  if (phys) table("#cd_govs", [["gov3", "Gov", v => `<b>${esc(v)}</b>`], ["gov", "المحافظة"], ["A", "A"], ["B", "B"],
    ["groups", "الإجمالي", v => `<b>${n(v)}</b>`], ["students", "الطلاب"]], s.govs);
  const keep = (id, opts, all) => { const v = $(id).value; $(id).innerHTML = `<option value="">${all}</option>` + opts; if ([...$(id).options].some(o => o.value === v)) $(id).value = v; };
  keep("#cd_level", s.levels.map(l => `<option value="${esc(l.level_code)}">${esc(l.level)} ${esc(l.track)}</option>`).join(""), "كل الليفيلات");
  keep("#cd_gov", s.govs.map(g => `<option value="${esc(g.gov3)}">${esc(g.gov3)} · ${esc(g.gov)}</option>`).join(""), "كل المحافظات");
  $("#cd_gov").hidden = !phys;
  cdFilter();
}
function cdFilter() {
  if (!CODES) return;
  const q = ($("#cd_q").value || "").trim().toUpperCase(), d = $("#cd_day").value, lv = $("#cd_level").value, g = $("#cd_gov").value;
  CD_ROWS = CODES.rows.filter(r => (!q || r.code.includes(q)) && (!d || r.day === d) && (!lv || r.level_code === lv) && (!g || r.gov3 === g));
  CD_PG = 1; cdDraw();
}
function cdPage(dlt) { const pages = Math.max(1, Math.ceil(CD_ROWS.length / CD_PER)); CD_PG = Math.min(pages, Math.max(1, CD_PG + dlt)); cdDraw(); }
function cdDraw() {
  const phys = CODES.mode === "physical", pages = Math.max(1, Math.ceil(CD_ROWS.length / CD_PER));
  const rows = CD_ROWS.slice((CD_PG - 1) * CD_PER, CD_PG * CD_PER);
  table("#cd_t", [["code", "Group Code", v => `<b style="direction:ltr;unicode-bidi:embed;font-family:monospace">${esc(v)}</b>`],
    ["day", "اليوم", (v, r) => `${esc(v)} · ${esc(r.day_ar)}`], ["slot", "Slot"],
    ...(phys ? [["gov3", "Gov", (v, r) => `${esc(v)} <span class="small mute">${esc(r.gov)}</span>`], ["venue", "Venue"]] : []),
    ["level", "Level"], ["track", "Track"], ["class", "Class"], ["students", "الطلاب المتوقعين"]], rows, {empty: "مفيش أكواد بالفلتر ده"});
  $("#cd_info").textContent = `${n(CD_ROWS.length)} جروب` + (pages > 1 ? ` · صفحة ${CD_PG} من ${pages}` : "");
}

(async function init() {
  await loadHistory();
  if (PLAN0) openPlan(PLAN0);
})();

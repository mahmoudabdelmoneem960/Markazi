let RES = null, MAP = null, CENTERS = [], cLayer = null, pLayer = null, stopPoll = null;
const STATUS = {queued: "في الانتظار", running: "جاري التوزيع", files: "تجهيز التقارير", done: "تم التوزيع بنجاح", error: "خطأ", cancelled: "ملغي"};

function setStatus(text, cls) {
  const p = $("#pstat_pill") || $("#pstat");
  if (p) {
    p.textContent = text;
    p.className = "pill " + (cls || (text.includes("تم") ? "ok" : text.includes("خطأ") ? "bad" : "brand"));
  }
}

function tab(k) {
  $$(".tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === k));
  ["sum", "map", "whatif"].forEach(t => {
    const el = $("#tab-" + t);
    if (el) el.hidden = (t !== k);
  });
  if (k === "map") initMap();
  history.replaceState(null, "", "#" + k);
}

async function init() {
  setStatus(STATUS[RUN.status] || RUN.status);
  if (RUN.status === "done" || RUN.status === "files") {
    await loadResult();
  }
  if (["queued", "running", "files"].includes(RUN.status) && RUN.job_id) {
    if ($("#progress")) $("#progress").hidden = false;
    stopPoll = pollJob(RUN.job_id, {onTick: onTick, onDone: onDone, onError: onErr});
  } else if (RUN.status === "error" || RUN.status === "cancelled") {
    if ($("#errbox")) $("#errbox").hidden = false;
    if ($("#errtxt")) $("#errtxt").textContent = (RUN.status === "cancelled" ? "تم إلغاء التشغيل. " : "حدث خطأ: ") + (RUN.error || "");
    if (RUN.files && RUN.files.result) await loadResult();
  }
  loadRunsForCompare();
  if (location.hash === "#map") tab("map");
  else if (location.hash === "#whatif") tab("whatif");
}

let shown = false;
async function onTick(j) {
  if ($("#pbar")) $("#pbar").style.width = Math.max(2, j.progress || 0) + "%";
  if ($("#pmsg")) $("#pmsg").textContent = j.message || "";
  if ($("#ptime")) $("#ptime").textContent = "⏱ " + fmtSec(j.elapsed);
  setStatus(STATUS[j.state] || j.state);
  if (j.result && j.result.result_ready && !shown) {
    shown = true;
    await loadResult();
    if ($("#cancel")) $("#cancel").hidden = true;
  }
  if (shown) renderDl(j.files, j.result_elapsed);
}

async function onDone(j) {
  if ($("#progress")) $("#progress").hidden = true;
  setStatus("✓ تم التوزيع بنجاح", "ok");
  if (!shown) await loadResult();
  renderDl("ready", j.result_elapsed);
  toast("تم الانتهاء من التوزيع بنجاح ✓", "ok");
}

async function onErr(j) {
  if ($("#progress")) $("#progress").hidden = true;
  if ($("#errbox")) $("#errbox").hidden = false;
  if ($("#errtxt")) $("#errtxt").textContent = j.state === "cancelled" ? "تم إلغاء التشغيل." : "خطأ: " + (j.error || "");
  setStatus(STATUS[j.state] || "خطأ", "bad");
}

async function cancelRun() {
  if ($("#cancel")) $("#cancel").disabled = true;
  await postJSON("/api/jobs/" + RUN.job_id + "/cancel");
  setTimeout(() => { if ($("#cancel")) $("#cancel").disabled = false; }, 1500);
}

function renderDl(files, relapsed) {
  const d = $("#dl");
  if (!d) return;
  if (files === "ready") {
    d.innerHTML = `
      <a class="dl-card" href="/api/runs/${RUN.id}/file/xlsx">
        <div class="dl-icon" style="background:var(--ok-bg);color:var(--ok)">📊</div>
        <div>
          <div class="dl-title">ملف Excel الشامل (11 شيت)</div>
          <div class="dl-desc">نتائج التوزيع، المسافات، الـVP، عجز السعة</div>
        </div>
      </a>
      <a class="dl-card" href="/api/runs/${RUN.id}/file/qgis">
        <div class="dl-icon" style="background:var(--brand-light);color:var(--brand)">🗺️</div>
        <div>
          <div class="dl-title">طبقات QGIS المكانية (ZIP)</div>
          <div class="dl-desc">نقاط الطلاب والمراكز لبرامج التحليل الجغرافي</div>
        </div>
      </a>
      <a class="dl-card" href="/api/runs/${RUN.id}/file/per_center">
        <div class="dl-icon" style="background:var(--soft);color:var(--ink)">📑</div>
        <div>
          <div class="dl-title">كشوفات المراكز للمنسقين (Excel)</div>
          <div class="dl-desc">صفحة مستقلة لكل مركز لتسليمها لإدارة القاعات</div>
        </div>
      </a>
      <a class="dl-card" href="/review?run=${RUN.id}">
        <div class="dl-icon" style="background:var(--warn-bg);color:var(--warn)">🎯</div>
        <div>
          <div class="dl-title">مراجعة وتعديل المواقع</div>
          <div class="dl-desc">تصحيح مواقع مجموعات الطلاب تقريبية العناوين</div>
        </div>
      </a>
    `;
  } else if (files === "writing") {
    d.innerHTML = `<div class="issue info"><span>⏳</span> <span>جاري تجهيز وتوليد ملفات التحميل... (النتائج بالأدنى جاهزة${relapsed ? " بعد " + fmtSec(relapsed) : ""})</span></div>`;
  } else if (files === "cancelled" || files === "error") {
    d.innerHTML = `<div class="issue error"><span>✗</span> <span>لم يتم تجهيز ملفات التحميل (${files === "cancelled" ? "تم الإلغاء" : "حدث خطأ"}).</span></div>`;
  }
}

async function loadResult() {
  const r = await api(`/api/runs/${RUN.id}/result`);
  if (!r.ok) return;
  RES = r.j; render(RES); $("#out").hidden = false;
  if (RUN.status === "done") renderDl("ready");
}

function render(r) {
  const s = r.summary;
  const pill = $("#pstat_pill");
  if (pill) {
    pill.className = "pill ok";
    pill.textContent = "✓ تم التوزيع بنجاح";
  }

  const k = (v, l, cls, prefix = "") => `
    <div class="kpi ${prefix}">
      <b class="${cls || ""}">${v}</b>
      <span>${l}</span>
    </div>`;

  const assignedRatio = s.students > 0 ? Math.round((s.assigned / s.students) * 100) : 0;

  $("#alerts").innerHTML = (s.alerts || []).map(a => `<div class="issue warn"><span>⚠️</span> <span>${esc(a)}</span></div>`).join("");
  $("#kpis").innerHTML = 
    k(`${n(s.assigned)} <small style="font-size:15px;color:var(--ok)">(${assignedRatio}%)</small>`, "الطلاب المسكنين بنجاح", "ok", "kpi-ok") +
    k(n(s.students), "إجمالي عدد الطلاب", "", "kpi-brand") +
    k(n(s.vp), "قيد الانتظار (VP)", "bad", "kpi-bad") +
    k(n(s.same_area), "في نفس منطقتهم السكنية", "", "kpi-ok") +
    k((s.avg_km || "0") + " كم", "متوسط مسافة الطريق", "brand", "kpi-brand") +
    k(`${n(s.centers)} مركز / ${n(s.groups)} جروب`, "المراكز والقاعات المستغلة", "", "") +
    k(n(s.seats), "إجمالي المقاعد المتاحة", "", "");
  $("#warn").innerHTML = (s.warnings || []).length ? '<ul class="warn">' + s.warnings.map(w => "<li>" + esc(w) + "</li>").join("") + "</ul>" : "";
  $("#dtype").textContent = "نوع المسافة: " + s.distance_label + ((s.road_cache_hits || s.road_computed) ? ` — من الكاش ${n(s.road_cache_hits || 0)} موقع، واتحسب ${n(s.road_computed || 0)}` : "");
  const g = s.geo || {}, gs = g.students || {}, lv = gs.by_level || {}, srcs = gs.by_source || {}, tot = gs.total || 1, pc = x => Math.round(100 * (lv[x] || 0) / tot);
  const SRC = {sheet: "الشيت", osm: "العناوين (OSM)", manual: "تصحيح يدوي", google: "Google", missing: "ناقص"};
  $("#geo").innerHTML = gs.total ? ("مواقع الطلاب: " + Object.entries(srcs).map(([a, b]) => (SRC[a] || a) + " " + n(b)).join(" | ") +
    (Object.keys(lv).length ? ` — الدقة: شارع/معلم ${pc("street") + pc("poi")}%، حي/قرية ${pc("locality")}%، منطقة ${pc("area")}%، مركز/محافظة ${pc("markaz") + pc("gov")}%` : "") +
    (r.review && r.review.groups_total ? ` — <a href="/review?run=${RUN.id}">مراجعة المواقع (${n(r.review.groups_total)} مجموعة)</a>` : "")) : "";
  table("#t_reason", [["reason", "السبب"], ["count", "عدد الطلاب"]], s.vp_reasons);
  const sc = r.sus_centers || [], ss = r.sus_students || [];
  $("#susbox").hidden = !(sc.length || ss.length); $("#sus_c_wrap").hidden = !sc.length; $("#sus_s_wrap").hidden = !ss.length;
  if (sc.length) table("#t_susc", [["center_code", "الكود"], ["center_name", "المركز"], ["gov", "المحافظة"], ["lat", "Latitude"], ["lon", "Longitude"], ["problem", "المشكلة"]], sc);
  if (ss.length) { $("#sus_s_title").textContent = "طلاب (" + n(s.suspect) + (s.suspect > ss.length ? " - أول " + ss.length + " هنا" : "") + ")";
    table("#t_suss", r.sus_students_cols.filter(c => c !== "note").map(c => [c, c]).concat([["note", "الملاحظة"]]), ss); }
  table("#t_def", [["gov", "المحافظة", v => esc(govAr(v))], ["level_code", "الليفيل/التراك"], ["students", "طلاب"], ["seats_in_gov", "مقاعد المحافظة"], ["vp", "VP"], ["deficit", "العجز"], ["group_capacity", "سعة الجروب"], ["extra_groups_needed", "جروبات إضافية"]], r.deficit);
  table("#t_gov", [["gov", "المحافظة"], ["students", "طلاب"], ["assigned", "موزعين"], ["vp", "VP"], ["vp_%", "VP %"]], r.by_gov);
  table("#t_lvl", [["level_code", "الليفيل/التراك"], ["students", "طلاب"], ["assigned", "موزعين"], ["vp", "VP"], ["seats_all_egypt", "مقاعد (كل مصر)"]], r.by_level);
  table("#t_cen", [["center_code", "المركز"], ["center_name", "الاسم"], ["gov", "المحافظة", v => esc(govAr(v))], ["groups", "جروبات"], ["seats", "مقاعد"], ["assigned", "موزعين"], ["occupancy_%", "إشغال %"]], r.center_load);
  // ماذا لو
  $("#w_level").innerHTML = (r.by_level || []).map(x => `<option value="${esc(x.level_code)}">${esc(x.level_code)} (VP ${n(x.vp)})</option>`).join("");
  loadCenters();
}

async function loadCenters() {
  const r = await api(`/api/runs/${RUN.id}/centers`); if (!r.ok) return;
  CENTERS = r.j;
  const govs = [...new Set(CENTERS.map(c => c.gov))].sort();
  $("#m_gov").innerHTML = '<option value="">كل المحافظات</option>' + govs.map(g => `<option value="${esc(g)}">${esc(govAr(g))}</option>`).join("");
  $("#w_center").innerHTML = CENTERS.slice().sort((a, b) => (a.gov + a.center_code).localeCompare(b.gov + b.center_code))
    .map(c => `<option value="${esc(c.center_code)}">${esc(c.center_code)} · ${esc(c.center_name || "")} (${esc(govAr(c.gov))}، إشغال ${c.occupancy == null ? "—" : c.occupancy + "%"})</option>`).join("");
  if (MAP) drawCenters();
}
function initMap() { if (MAP || typeof L === "undefined") { if (MAP) setTimeout(() => MAP.invalidateSize(), 50); return; } MAP = makeMap("map"); drawCenters(); setTimeout(() => MAP.invalidateSize(), 100); }
function filtered() { const g = $("#m_gov").value; return CENTERS.filter(c => !g || c.gov === g); }
function drawCenters() {
  if (!MAP) return;
  if (cLayer) cLayer.remove();
  cLayer = centersLayer(MAP, filtered(), async (c) => {
    $("#m_msg").textContent = "تحميل طلاب " + c.center_code + "...";
    const r = await api(`/api/runs/${RUN.id}/students?center=${encodeURIComponent(c.center_code)}`);
    if (!r.ok) { $("#m_msg").textContent = r.j.error || ""; return; }
    if (pLayer) pLayer.remove(); pLayer = pointsLayer(MAP, r.j.points);
    $("#m_msg").textContent = `${c.center_code}: ${n(r.j.total)} طالب${r.j.total > r.j.shown ? " (معروض " + n(r.j.shown) + ")" : ""}`;
  });
  fitTo(MAP, filtered());
}
function mapGov() { drawCenters(); if (pLayer) { pLayer.remove(); pLayer = null; } $("#m_msg").textContent = ""; if ($("#m_vp").checked) mapVP(); }
async function mapVP() {
  if (!$("#m_vp").checked) { if (pLayer) { pLayer.remove(); pLayer = null; } return; }
  const g = $("#m_gov").value; if (!g) { $("#m_msg").textContent = "اختار محافظة الأول."; $("#m_vp").checked = false; return; }
  $("#m_msg").textContent = "تحميل الـVP...";
  const r = await api(`/api/runs/${RUN.id}/students?gov=${encodeURIComponent(g)}&status=vp&limit=8000`);
  if (!r.ok) { $("#m_msg").textContent = r.j.error || ""; return; }
  if (pLayer) pLayer.remove(); pLayer = pointsLayer(MAP, r.j.points);
  $("#m_msg").textContent = `VP في ${govAr(g)}: ${n(r.j.total)}${r.j.total > r.j.shown ? " (معروض " + n(r.j.shown) + ")" : ""}`;
}

async function whatIf() {
  const body = {center: $("#w_center").value, level: $("#w_level").value, capacity: $("#w_cap").value, groups: $("#w_n").value};
  $("#w_out").innerHTML = '<span class="hint">بحسب...</span>';
  const r = await postJSON(`/api/runs/${RUN.id}/whatif`, body);
  if (!r.ok) { $("#w_out").innerHTML = `<span class="hint bad">${esc(r.j.error)}</span>`; return; }
  const x = r.j;
  $("#w_out").innerHTML = `<div class="kpis">` +
    `<div class="kpi"><b>${n(x.seats)}</b><span>مقاعد جديدة</span></div><div class="kpi"><b class="ok">${n(x.rescued)}</b><span>VP هيتحلّوا</span></div>` +
    `<div class="kpi"><b>${n(x.candidates)}</b><span>VP في حدود ${x.max_km} كم من المركز</span></div><div class="kpi"><b class="bad">${n(x.remaining_vp)}</b><span>VP هيفضلوا (نفس الليفيل والمحافظة)</span></div></div>` +
    `<div class="hint">المسافة: ${esc(x.distance)}. الأقرب ${n(Math.min(x.list.length, 30))}:</div>` +
    `<div class="scroll" style="max-height:260px"><table>${x.list.slice(0, 30).map(s => `<tr><td>${esc(s.nid)}</td><td>${esc(s.area)}</td><td>${fmtKm(s.km)}</td></tr>`).join("")}</table></div>`;
}

async function loadRunsForCompare() {
  const r = await api("/api/runs"); if (!r.ok) return;
  $("#cmp").innerHTML = r.j.filter(x => x.id !== RUN.id && x.status === "done").map(x => `<option value="${esc(x.id)}">${esc(x.created_at)} — ${esc(x.name || x.students_file)}</option>`).join("") || "<option value=''>مفيش تشغيلات تانية</option>";
}
function goCompare() { const b = $("#cmp").value; if (b) location.href = `/compare?a=${RUN.id}&b=${b}`; }
init();

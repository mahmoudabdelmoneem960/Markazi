let RES = null, MAP = null, CENTERS = [], cLayer = null, pLayer = null, stopPoll = null;
const STATUS = {queued: "في الانتظار", running: "جاري التوزيع", files: "تجهيز التقارير", done: "تم التوزيع بنجاح", error: "خطأ", cancelled: "ملغي"};

function setStatus(text, cls) {
  const p = $("#pstat_pill") || $("#pstat");
  if (p) {
    p.textContent = text;
    p.className = "pill " + (cls || (text.includes("تم") ? "ok" : text.includes("خطأ") ? "bad" : "brand"));
  }
  const oldP = $("#pstat");
  if (oldP && oldP !== p) oldP.textContent = text;
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
  if (RUN.status === "done" || RUN.status === "files") { await loadResult(); }
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

let CURRENT_STUDENT_ROW = null;

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

  // تنبيهات مع أزرار تفاعلية فورية
  $("#alerts").innerHTML = (s.alerts || []).map(a => {
    let actionBtn = "";
    if (a.includes("البند الثاني")) {
      actionBtn = `<button class="btn sm bad" style="margin-right:10px" onclick="openBand2Modal()">⚠️ عرض طلاب البند الثاني (${n(s.band2_count || 0)})</button>`;
    } else {
      const m = a.match(/\b([A-Z0-9_]{3,12})\s*\((\d+)\s*كم\)/);
      if (m) actionBtn = `<button class="btn sm" style="margin-right:10px" onclick="openCenterFix('${esc(m[1])}')">📍 فحص وتصحيح موقع ${esc(m[1])}</button>`;
    }
    return `<div class="alert" style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px"><span>⚠ ${esc(a)}</span>${actionBtn}</div>`;
  }).join("");

  const assignedRatio = s.students > 0 ? Math.round((s.assigned / s.students) * 100) : 0;
  const govs = r.by_gov || [];
  const govCount = govs.length;

  let b1GovCount = 0, b2GovCount = 0, b3GovCount = 0;
  let b2GovNames = [];
  govs.forEach(g => {
    const band = g.band || g["البند"] || getGovBand(g.gov);
    if (band === "البند الأول") b1GovCount++;
    else if (band === "البند الثاني") {
      b2GovCount++;
      b2GovNames.push(govAr(g.gov));
    }
    else if (band === "البند الثالث") b3GovCount++;
  });

  const b2KpiBadge = b2GovCount > 0
    ? `<div style="margin-top:4px"><span class="badge bad" style="font-size:11px;font-weight:700" title="مشكلة في البيانات: تم رصد طلاب في محافظات البند الثاني!">⚠️ بند ثاني: ${b2GovCount} (${esc(b2GovNames.join("، "))})</span></div>`
    : `<div style="font-size:11px;color:var(--mute);margin-top:4px">بند ثاني: 0 (سليم ✓)</div>`;

  const govKpiValue = `
    <div>
      <div style="font-size:26px;font-weight:700;line-height:1.1">${n(govCount)} <small style="font-size:13px;font-weight:600;color:var(--mute)">محافظة</small></div>
      <div style="font-size:12px;font-weight:600;margin-top:4px;display:flex;gap:4px;justify-content:center;flex-wrap:wrap">
        <span class="pill brand" style="padding:1px 6px">بند 1: <b>${b1GovCount}</b></span>
        <span class="pill ok" style="padding:1px 6px">بند 3: <b>${b3GovCount}</b></span>
      </div>
      ${b2KpiBadge}
    </div>
  `;

  $("#kpis").innerHTML =
    k(`${n(s.assigned)} <small style="font-size:15px;color:var(--ok)">(${assignedRatio}%)</small>`, "الطلاب المسكنين بنجاح", "ok", "kpi-ok") +
    k(n(s.students), "إجمالي عدد الطلاب", "", "kpi-brand") +
    k(govKpiValue, "عدد المحافظات في كل بند", b2GovCount > 0 ? "bad" : "", b2GovCount > 0 ? "kpi-bad" : "kpi-brand") +
    k(n(s.vp), "قيد الانتظار (VP)", "bad", "kpi-bad") +
    k(n(s.same_area), "في نفس منطقتهم السكنية", "", "kpi-ok") +
    k((s.avg_km || "0") + " كم", "متوسط مسافة الطريق", "brand", "kpi-brand") +
    k(`${n(s.centers)} مركز / ${n(s.groups)} جروب`, "المراكز والقاعات المستغلة", "", "") +
    k(n(s.seats), "إجمالي المقاعد المتاحة", "", "");

  // تحذيرات مع أزرار تفاعلية
  $("#warn").innerHTML = (s.warnings || []).length ? '<ul class="warn">' + s.warnings.map(w => {
    let extra = "";
    // مراكز مشبوهة
    if (w.includes("مراكز بمواقع مشكوك فيها")) {
      const match = w.match(/\b([A-Z0-9_]{3,12}):/);
      if (match) extra = ` <button class="btn sm sec" onclick="openCenterFix('${esc(match[1])}')">📍 تصحيح ${esc(match[1])}</button>`;
    }
    // أرقام قومية
    else if (w.includes("رقم قومي مش 14 رقم")) {
      const rowsMatch = w.match(/row_no\s*=\s*([\d, ]+)/);
      const rows = rowsMatch ? rowsMatch[1].split(",").map(x => x.trim()).filter(Boolean) : [];
      const rowBtns = rows.slice(0, 5).map(rn => `<button class="btn sm" onclick="viewStudentRow('${RUN.id}','${esc(rn)}')">طالب صف ${esc(rn)}</button>`).join(" ");
      extra = ` <div class="row" style="margin-top:6px;display:inline-flex">${rowBtns} <button class="btn sm sec" onclick="openInvalidNidsModal()">📋 عرض كل الطلاب بأرقام قومية غير صحيحة</button></div>`;
    }
    // طلاب مشبوهين
    else if (w.includes("طالب إحداثياتهم بعيدة جدًا")) {
      extra = ` <div class="row" style="margin-top:6px;display:inline-flex"><button class="btn sm ok" onclick="fixAllSuspects()">⚡ تصحيح ذكي لجميع الطلاب المشبوهين</button> <button class="btn sm sec" onclick="openAllSuspectsModal()">📋 عرض الجدول الكامل</button></div>`;
    }
    return `<li style="margin-bottom:8px"><div>${esc(w)}</div>${extra}</li>`;
  }).join("") + "</ul>" : "";

  $("#dtype").textContent = "نوع المسافة: " + s.distance_label + ((s.road_cache_hits || s.road_computed) ? ` — من الكاش ${n(s.road_cache_hits || 0)} موقع، واتحسب ${n(s.road_computed || 0)}` : "");
  const g = s.geo || {}, gs = g.students || {}, lv = gs.by_level || {}, srcs = gs.by_source || {}, tot = gs.total || 1, pc = x => Math.round(100 * (lv[x] || 0) / tot);
  const SRC = {sheet: "الشيت", osm: "العناوين (OSM)", manual: "تصحيح يدوي", google: "Google", missing: "ناقص"};
  $("#geo").innerHTML = gs.total ? ("مواقع الطلاب: " + Object.entries(srcs).map(([a, b]) => (SRC[a] || a) + " " + n(b)).join(" | ") +
    (Object.keys(lv).length ? ` — الدقة: شارع/معلم ${pc("street") + pc("poi")}%، حي/قرية ${pc("locality")}%، منطقة ${pc("area")}%، مركز/محافظة ${pc("markaz") + pc("gov")}%` : "") +
    (r.review && r.review.groups_total ? ` — <a href="/review?run=${RUN.id}">مراجعة المواقع (${n(r.review.groups_total)} مجموعة)</a>` : "")) : "";
  let farGovCount = 0;
  let farSuspectCount = 0;
  const cleanReasons = (s.vp_reasons || []).map(x => {
    let r = x.reason || "";
    const maxKm = s.params ? (s.params.max_km || 50) : 50;
    if (r.includes("X كم") || r.includes("الحد الأقصى")) {
      if (r.includes("ممتلئة")) r = `كل مراكز المحافظة داخل الحد الأقصى (${maxKm} كم) ممتلئة بالكامل - مقترح VP`;
      else if (r.includes("غالبًا إحداثيات") || r.includes("عن أي مركز")) r = `أبعد من الحد الأقصى (${maxKm} كم) عن أي مركز - غالبًا إحداثيات الطالب غير دقيقة`;
      else if (r.includes("أبعد من")) r = `أبعد من الحد الأقصى للمسافة (${maxKm} كم) عن أقرب مركز متاح في محافظته`;
    }
    const isGovFar = r.includes("أقرب مركز متاح في محافظته") || (r.includes("أبعد من") && !r.includes("إحداثيات") && !r.includes("عن أي مركز"));
    const isSuspectFar = r.includes("غالبًا إحداثيات") || r.includes("عن أي مركز");
    if (isGovFar) farGovCount = x.count;
    if (isSuspectFar) farSuspectCount = x.count;
    return {
      reason: r,
      count: x.count,
      isGovFar: isGovFar,
      isSuspectFar: isSuspectFar,
      isFar: isGovFar || isSuspectFar
    };
  });

  const totalFarCount = farGovCount + farSuspectCount;
  if ($("#m_far_cnt")) $("#m_far_cnt").textContent = n(farGovCount || totalFarCount);
  if ($("#mfs_cnt_gov")) $("#mfs_cnt_gov").textContent = n(farGovCount);
  if ($("#mfs_cnt_sus")) $("#mfs_cnt_sus").textContent = n(farSuspectCount);
  if ($("#mfs_cnt_all")) $("#mfs_cnt_all").textContent = n(totalFarCount);

  table("#t_reason", [
    ["reason", "السبب", v => `<b>${esc(v)}</b>`],
    ["count", "عدد الطلاب", v => `<span class="badge bad" style="font-size:13px">${n(v)}</span>`],
    ["actions", "إجراءات ومسافات", (_, item) => {
      if (item.isGovFar) {
        return `
          <button class="btn sm" style="background:#e08a1e;border-color:#c2781a" onclick="showFarStudentsOnMap('gov')">🗺️ عرض على الخريطة مع أقرب مركز</button>
          <button class="btn sm sec" onclick="openFarStudentsModal('gov', ${item.count})">📋 جدول المسافات (${n(item.count)} طالب)</button>
        `;
      } else if (item.isSuspectFar) {
        return `
          <button class="btn sm" onclick="showFarStudentsOnMap('suspect')">🗺️ عرض على الخريطة</button>
          <button class="btn sm sec" onclick="openFarStudentsModal('suspect', ${item.count})">📋 جدول الطلاب (${n(item.count)} طالب)</button>
        `;
      }
      return '<span class="mute">—</span>';
    }]
  ], cleanReasons);

  // جداول المشبوهين
  const sc = r.sus_centers || [], ss = r.sus_students || [];
  $("#susbox").hidden = !(sc.length || ss.length);
  $("#sus_c_wrap").hidden = !sc.length;
  $("#sus_s_wrap").hidden = !ss.length;

  if (sc.length) {
    table("#t_susc", [
      ["center_code", "الكود", v => `<b>${esc(v)}</b>`],
      ["center_name", "المركز"],
      ["gov", "المحافظة", v => esc(govAr(v))],
      ["lat", "Latitude", v => (v != null ? Number(v).toFixed(5) : "—")],
      ["lon", "Longitude", v => (v != null ? Number(v).toFixed(5) : "—")],
      ["problem", "المشكلة", v => `<span class="badge bad">${esc(v)}</span>`],
      ["actions", "إجراءات التصحيح", (_, r) => `
        <button class="btn sm" onclick="openCenterFix('${esc(r.center_code)}')">📍 فحص واقتراح الموقع</button>
        <button class="btn sm sec" onclick="verifyCenterCurrent('${esc(r.center_code)}')">✓ تأكيد كصحيح</button>
        <a class="btn sm sec" href="/centers?q=${encodeURIComponent(r.center_code)}" target="_blank">✏️ صفحة المراكز</a>
      `]
    ], sc);
  }

  if (ss.length) {
    $("#sus_s_title").textContent = "طلاب إحداثياتهم مشبوهة (" + n(s.suspect || ss.length) + (s.suspect > ss.length ? " - أول " + ss.length + " هنا" : "") + ")";
    const susCols = [
      ["row_no", "صف", v => `<b>${esc(v)}</b>`],
      ["Student NID", "الرقم القومي", (v, row) => esc(v || row.nid || "")],
      ["gov", "المحافظة", v => esc(govAr(v))],
      ["Area", "المنطقة", (v, row) => esc(v || row.area || "")],
      ["lat", "Latitude", v => (v != null ? Number(v).toFixed(5) : "—")],
      ["lon", "Longitude", v => (v != null ? Number(v).toFixed(5) : "—")],
      ["status", "الحالة", v => `<span class="badge ${v === 'VP' ? 'bad' : 'ok'}">${esc(v || '')}</span>`],
      ["actions", "إجراءات", (_, row) => `
        <button class="btn sm" onclick="viewStudentRow('${RUN.id}', '${esc(row.row_no)}')">👁️ عرض وتعديل</button>
        <button class="btn sm sec" onclick="flyToStudent(${row.lat}, ${row.lon})">🗺️ على الخريطة</button>
      `]
    ];
    table("#t_suss", susCols, ss);
  }

  table("#t_def", [
    ["gov", "المحافظة", v => esc(govAr(v))],
    ["band", "البند", (v, r) => {
      const b = v || r.البند || getGovBand(r.gov);
      const isB2 = (b === "البند الثاني");
      return `<span class="pill ${isB2 ? 'bad' : b === 'البند الأول' ? 'brand' : 'ok'}" ${isB2 ? 'style="font-weight:700"' : ''}>${isB2 ? '⚠️ ' : ''}${esc(b)}</span>`;
    }],
    ["level_code", "الليفيل/التراك"],
    ["students", "طلاب"],
    ["seats_in_gov", "مقاعد المحافظة"],
    ["vp", "VP"],
    ["deficit", "العجز"],
    ["group_capacity", "سعة الجروب"],
    ["extra_groups_needed", "جروبات إضافية"]
  ], r.deficit);
  initGovSection(r.by_gov);
  initCentersSection(r.center_load);
  table("#t_lvl", [["level_code", "الليفيل/التراك"], ["students", "طلاب"], ["assigned", "موزعين"], ["vp", "VP"], ["seats_all_egypt", "مقاعد (كل مصر)"]], r.by_level);
  // ماذا لو
  $("#w_level").innerHTML = (r.by_level || []).map(x => `<option value="${esc(x.level_code)}">${esc(x.level_code)} (VP ${n(x.vp)})</option>`).join("");
  loadCenters();
}

// ---------------- تفاصيل المحافظة والتوزيع الجغرافي ----------------
let CURRENT_SELECTED_GOV = "";

function normGov(g) {
  if (!g) return "";
  const s = String(g).trim().toLowerCase();
  if (typeof GOV_AR !== "undefined" && GOV_AR[s]) return s;
  if (typeof GOV_AR !== "undefined") {
    const clean = String(g).trim().replace(/[أإآ]/g, "ا").replace(/ة/g, "ه");
    for (const [k, v] of Object.entries(GOV_AR)) {
      const vClean = v.trim().replace(/[أإآ]/g, "ا").replace(/ة/g, "ه");
      if (vClean === clean || k === s) return k;
    }
  }
  return s;
}

let ALL_SORTED_GOVS = [];
let AVAILABLE_BANDS = ["البند الأول", "البند الثالث"];
let SELECTED_BANDS = new Set(AVAILABLE_BANDS);
let SELECTED_GOVS = new Set();
let GOV_SEARCH_QUERY = "";

// إغلاق القائمة المنسدلة لاختيار المحافظات عند النقر في أي مكان خارجها
document.addEventListener("click", (e) => {
  const dd = $("#gov_multiselect_dropdown");
  const btn = $("#btn_open_gov_picker");
  if (dd && dd.style.display !== "none") {
    if (!dd.contains(e.target) && !btn?.contains(e.target)) {
      closeGovDropdown();
    }
  }
});

function toggleGovDropdown(e) {
  if (e) e.stopPropagation();
  const dd = $("#gov_multiselect_dropdown");
  if (!dd) return;
  const isOpen = dd.style.display !== "none";
  if (isOpen) {
    closeGovDropdown();
  } else {
    dd.style.display = "block";
    const sBox = $("#gov_search_box");
    if (sBox) {
      sBox.value = GOV_SEARCH_QUERY || "";
      setTimeout(() => sBox.focus(), 60);
    }
    renderGovCheckboxes();
  }
}

function closeGovDropdown() {
  const dd = $("#gov_multiselect_dropdown");
  if (dd) dd.style.display = "none";
}

function onGovSearchInput(val) {
  GOV_SEARCH_QUERY = (val || "").trim().toLowerCase();
  renderGovCheckboxes();
}

function renderGovCheckboxes() {
  const container = $("#gov_checkboxes_list");
  if (!container) return;

  const q = (GOV_SEARCH_QUERY || "").replace(/[أإآ]/g, "ا").replace(/ة/g, "ه");

  const matches = ALL_SORTED_GOVS.filter(g => {
    if (!q) return true;
    const name = (govAr(g.gov) || "").replace(/[أإآ]/g, "ا").replace(/ة/g, "ه");
    const code = (normGov(g.gov) || "").toLowerCase();
    const b = (g.band || g["البند"] || getGovBand(g.gov) || "").replace(/[أإآ]/g, "ا").replace(/ة/g, "ه");
    return name.includes(q) || code.includes(q) || b.includes(q);
  });

  if (!matches.length) {
    container.innerHTML = `<div class="mute small" style="padding:14px;text-align:center">لا توجد محافظات مطابقة للبحث "${esc(GOV_SEARCH_QUERY)}"</div>`;
    return;
  }

  container.innerHTML = matches.map(g => {
    const code = normGov(g.gov);
    const arName = govAr(g.gov);
    const b = g.band || g["البند"] || getGovBand(g.gov);
    const isB2 = (b === "البند الثاني");
    const isChecked = SELECTED_GOVS.has(code);
    const pillClass = isB2 ? "pill bad" : (b === "البند الأول" ? "pill brand" : "pill ok");

    return `
      <label class="gov-checkbox-item">
        <div style="display:flex;align-items:center;gap:8px">
          <input type="checkbox" ${isChecked ? "checked" : ""} onchange="toggleGovCheckbox('${esc(code)}')" style="cursor:pointer;accent-color:var(--brand);width:16px;height:16px">
          <span style="font-weight:600;font-size:13px">${isB2 ? '⚠️ ' : ''}${esc(arName)}</span>
          <span class="${pillClass}" style="font-size:10px;padding:1px 6px">${esc(b)}</span>
        </div>
        <span class="small mute" style="font-size:11.5px">${n(g.students)} طالب</span>
      </label>
    `;
  }).join("");

  const counter = $("#gov_selected_counter");
  if (counter) {
    counter.textContent = `تم تحديد ${SELECTED_GOVS.size} من ${ALL_SORTED_GOVS.length} محافظة`;
  }
}

function toggleGovCheckbox(govCode) {
  if (SELECTED_GOVS.has(govCode)) {
    SELECTED_GOVS.delete(govCode);
  } else {
    SELECTED_GOVS.add(govCode);
  }
  const counter = $("#gov_selected_counter");
  if (counter) {
    counter.textContent = `تم تحديد ${SELECTED_GOVS.size} من ${ALL_SORTED_GOVS.length} محافظة`;
  }
  applyCombinedGovFilter();
}

function selectAllGovs(select) {
  if (select) {
    SELECTED_GOVS = new Set(ALL_SORTED_GOVS.map(g => normGov(g.gov)));
  } else {
    SELECTED_GOVS.clear();
  }
  renderGovCheckboxes();
  applyCombinedGovFilter();
}

function selectGovsByBandQuick(bandName) {
  const subset = ALL_SORTED_GOVS.filter(g => (g.band || g["البند"] || getGovBand(g.gov)) === bandName);
  SELECTED_GOVS = new Set(subset.map(g => normGov(g.gov)));
  SELECTED_BANDS = new Set([bandName]);
  renderGovCheckboxes();
  applyCombinedGovFilter();
}

function setBandFilterQuick(mode) {
  if (mode === "all") {
    SELECTED_BANDS = new Set(AVAILABLE_BANDS);
  } else if (mode === "b1") {
    SELECTED_BANDS = new Set(["البند الأول"]);
  } else if (mode === "b3") {
    SELECTED_BANDS = new Set(["البند الثالث"]);
  } else if (mode === "b2") {
    SELECTED_BANDS = new Set(["البند الثاني"]);
  }
  applyCombinedGovFilter();
}

function toggleBandSelection(bandName) {
  if (SELECTED_BANDS.has(bandName)) {
    SELECTED_BANDS.delete(bandName);
  } else {
    SELECTED_BANDS.add(bandName);
  }
  applyCombinedGovFilter();
}

function removeBandFilter(bandName) {
  SELECTED_BANDS.delete(bandName);
  applyCombinedGovFilter();
}

function removeGovFilter(govCode) {
  SELECTED_GOVS.delete(govCode);
  renderGovCheckboxes();
  applyCombinedGovFilter();
}

function clearAllGovFilters() {
  SELECTED_BANDS = new Set(AVAILABLE_BANDS);
  SELECTED_GOVS = new Set(ALL_SORTED_GOVS.map(g => normGov(g.gov)));
  GOV_SEARCH_QUERY = "";
  const sBox = $("#gov_search_box");
  if (sBox) sBox.value = "";
  renderGovCheckboxes();
  applyCombinedGovFilter();
}

function applyCombinedGovFilter() {
  // 1. تحديث شكل أزرار البنود
  const allBtn = $("#gbf_all");
  const isAllBands = (AVAILABLE_BANDS.length > 0 && AVAILABLE_BANDS.every(b => SELECTED_BANDS.has(b)));
  if (allBtn) {
    allBtn.className = isAllBands ? "btn sm on" : "btn sm sec";
  }

  const b1Btn = $("#gbf_b1");
  const b1Chk = $("#gbf_chk_b1");
  const hasB1 = SELECTED_BANDS.has("البند الأول");
  if (b1Btn) b1Btn.className = hasB1 ? "btn sm on brand" : "btn sm sec";
  if (b1Chk) b1Chk.textContent = hasB1 ? "✓" : "○";

  const b3Btn = $("#gbf_b3");
  const b3Chk = $("#gbf_chk_b3");
  const hasB3 = SELECTED_BANDS.has("البند الثالث");
  if (b3Btn) b3Btn.className = hasB3 ? "btn sm on ok" : "btn sm sec";
  if (b3Chk) b3Chk.textContent = hasB3 ? "✓" : "○";

  const b2Btn = $("#gbf_b2");
  const b2Chk = $("#gbf_chk_b2");
  const hasB2 = SELECTED_BANDS.has("البند الثاني");
  if (b2Btn) b2Btn.className = hasB2 ? "btn sm on bad" : "btn sm sec bad";
  if (b2Chk) b2Chk.textContent = hasB2 ? "✓" : "○";

  // 2. تحديث شارة زر اختيار المحافظات
  const pickerBadge = $("#gov_picker_badge");
  if (pickerBadge) {
    if (SELECTED_GOVS.size === ALL_SORTED_GOVS.length) {
      pickerBadge.textContent = `كل المحافظات (${ALL_SORTED_GOVS.length})`;
      pickerBadge.className = "badge brand";
    } else if (SELECTED_GOVS.size === 0) {
      pickerBadge.textContent = "لم تحدد أي محافظة (0)";
      pickerBadge.className = "badge bad";
    } else {
      pickerBadge.textContent = `محدد (${SELECTED_GOVS.size} محافظة)`;
      pickerBadge.className = "badge ok";
    }
  }

  // 3. تصفية البيانات المشتركة (AND): البند المختار + المحافظة المختارة
  const filtered = ALL_SORTED_GOVS.filter(g => {
    const b = g.band || g["البند"] || getGovBand(g.gov);
    const code = normGov(g.gov);
    const bandMatch = SELECTED_BANDS.has(b);
    const govMatch = SELECTED_GOVS.has(code);
    return bandMatch && govMatch;
  });

  // 4. رسم صفوف الجدول المفلترة
  renderGovTableRows(filtered);

  // تحديث عداد الدروب داون
  const counter = $("#gov_selected_counter");
  if (counter) {
    counter.textContent = `تم تحديد ${SELECTED_GOVS.size} من ${ALL_SORTED_GOVS.length} محافظة`;
  }

  // دعم رجعي في حال وجود sel_gov في نسخة كاش قديمة بالمتصفح
  const selGov = $("#sel_gov");
  if (selGov) {
    let opts = `<option value="">-- كل المحافظات المفلترة (${filtered.length}) --</option>`;
    filtered.forEach(g => {
      const code = normGov(g.gov);
      const name = govAr(g.gov);
      const b = g.band || g["البند"] || getGovBand(g.gov);
      opts += `<option value="${esc(code)}">${b === "البند الثاني" ? "⚠️ " : ""}${esc(name)} [${b}] (${n(g.students)} طالب)</option>`;
    });
    selGov.innerHTML = opts;
    selGov.value = CURRENT_SELECTED_GOV || "";
  }
}

function renderGovTableRows(govsList) {
  table("#t_gov", [
    ["gov", "المحافظة", (v, row) => {
      const b = row.band || row["البند"] || getGovBand(v);
      const isB2 = (b === "البند الثاني");
      return `
        <div>
          <b>${isB2 ? '⚠️ ' : ''}${esc(govAr(v))}</b>
          ${isB2 ? '<small class="bad" style="display:block;font-size:11px;font-weight:700">محافظة بند ثاني (مشكلة بالبيانات)</small>' : ''}
        </div>
      `;
    }],
    ["band", "البند", (v, row) => {
      const b = v || row["البند"] || getGovBand(row.gov);
      if (b === "البند الثاني") {
        return `<span class="badge bad" style="font-weight:700;font-size:12px;padding:3px 8px">⚠️ البند الثاني (خطأ)</span>`;
      }
      if (b === "البند الأول") {
        return `<span class="pill brand" style="font-weight:700">البند الأول</span>`;
      }
      if (b === "البند الثالث") {
        return `<span class="pill ok" style="font-weight:700">البند الثالث</span>`;
      }
      return `<span class="pill mute">${esc(b || "غير محدد")}</span>`;
    }],
    ["students", "إجمالي الطلاب", v => n(v)],
    ["assigned", "تم توزيعهم", v => `<span class="ok" style="font-weight:600">${n(v)}</span>`],
    ["vp", "مقترح VP", v => `<span class="${v ? 'bad' : ''}">${n(v)}</span>`],
    ["vp_%", "نسبة VP %", v => {
      const cls = v > 40 ? "bad" : v > 20 ? "warn" : "ok";
      return `<span class="badge ${cls}">${v}%</span>`;
    }],
    ["actions", "التفاصيل", (_, row) => `
      <button class="btn sm sec" onclick="event.stopPropagation(); selectGov('${esc(normGov(row.gov))}')">👁️ عرض التفاصيل</button>
    `]
  ], govsList, {
    empty: "لا توجد محافظات مطابقة لمعايير الفلترة المحددة",
    rowAttr: row => {
      const b = row.band || row["البند"] || getGovBand(row.gov);
      const isB2 = (b === "البند الثاني");
      const bg = isB2 ? 'style="background:rgba(239, 68, 68, 0.08);border-right:4px solid var(--bad)"' : '';
      return `class="click ${isB2 ? 'row-bad' : ''}" ${bg} onclick="selectGov('${esc(normGov(row.gov))}')" title="اضغط لعرض تفاصيل ${esc(govAr(row.gov))}${isB2 ? ' (تحذير: بند ثاني)' : ''}"`;
    }
  });
}

function initGovSection(byGov) {
  ALL_SORTED_GOVS = (byGov || []).slice().sort((a, b) => (Number(b.students) || 0) - (Number(a.students) || 0));

  let b1Cnt = 0, b2Cnt = 0, b3Cnt = 0;
  ALL_SORTED_GOVS.forEach(g => {
    const b = g.band || g["البند"] || getGovBand(g.gov);
    if (b === "البند الأول") b1Cnt++;
    else if (b === "البند الثاني") b2Cnt++;
    else if (b === "البند الثالث") b3Cnt++;
  });

  if ($("#gbf_cnt_b1")) $("#gbf_cnt_b1").textContent = b1Cnt;
  if ($("#gbf_cnt_b3")) $("#gbf_cnt_b3").textContent = b3Cnt;
  if ($("#gbf_cnt_b2")) $("#gbf_cnt_b2").textContent = b2Cnt;

  const pillsBox = $("#gov_bands_pills");
  if (pillsBox) {
    pillsBox.innerHTML = `
      <span class="pill brand" style="padding:2px 8px">البند الأول: <b>${b1Cnt}</b></span>
      <span class="pill ok" style="padding:2px 8px">البند الثالث: <b>${b3Cnt}</b></span>
      ${b2Cnt > 0 ? `<span class="badge bad" style="padding:2px 8px;font-weight:700">⚠️ البند الثاني: <b>${b2Cnt}</b> (مشكلة في البيانات)</span>` : ''}
    `;
  }

  // إخفاء زر البند الثاني إذا كان عدده صفر، وإظهاره فقط عند وجود محافظات به
  const b2Btn = $("#gbf_b2");
  if (b2Btn) {
    b2Btn.style.display = b2Cnt > 0 ? "inline-flex" : "none";
  }

  AVAILABLE_BANDS = ["البند الأول", "البند الثالث"];
  if (b2Cnt > 0) {
    AVAILABLE_BANDS.push("البند الثاني");
  }

  // تهيئة الفلاتر المحددة (كل البنود المتاحة وكل المحافظات افتراضياً)
  SELECTED_BANDS = new Set(AVAILABLE_BANDS);
  SELECTED_GOVS = new Set(ALL_SORTED_GOVS.map(g => normGov(g.gov)));
  GOV_SEARCH_QUERY = "";

  renderGovCheckboxes();
  applyCombinedGovFilter();

  if (CURRENT_SELECTED_GOV) {
    selectGov(CURRENT_SELECTED_GOV);
  } else {
    resetGovView();
  }
}

function onGovChange(val) {
  if (!val) {
    resetGovView();
  } else {
    selectGov(val);
  }
}

function selectGov(govCode) {
  if (!govCode || !RES) return;
  CURRENT_SELECTED_GOV = govCode;
  const sel = $("#sel_gov");
  if (sel) sel.value = govCode;

  const obox = $("#gov_overview_box");
  const dbox = $("#gov_detail_box");
  const abtn = $("#btn_all_govs");
  const fpanel = $("#gov_filter_panel");
  if (obox) obox.style.display = "none";
  if (fpanel) fpanel.style.display = "none";
  if (dbox) dbox.style.display = "block";
  if (abtn) abtn.style.display = "inline-block";

  const gData = (RES.by_gov || []).find(x => normGov(x.gov) === govCode) || {gov: govCode, students: 0, assigned: 0, vp: 0, "vp_%": 0};
  const arName = govAr(govCode);
  const b = gData.band || gData["البند"] || getGovBand(govCode);
  const isB2 = (b === "البند الثاني");
  const bBadge = isB2
    ? '<span class="badge bad" style="margin-right:8px;font-size:13px;padding:3px 8px">⚠️ البند الثاني (مشكلة بالبيانات)</span>'
    : `<span class="pill ${b === 'البند الأول' ? 'brand' : 'ok'}" style="margin-right:8px;font-weight:600">${b}</span>`;

  $("#gov_detail_title").innerHTML = `تفاصيل محافظة ${arName} ${bBadge}`;
  $("#gov_detail_sub").textContent = `إجمالي طلاب المحافظة: ${n(gData.students)} طالب · الموزعين: ${n(gData.assigned)} · المقترح VP: ${n(gData.vp)} (${gData["vp_%"] || 0}%)`;
  $("#gov_hint").textContent = `أنت تتصفح الآن التفاصيل الكاملة لمحافظة ${arName}.`;

  // مراكز المحافظة
  const govCenters = (RES.center_load || []).filter(c => normGov(c.gov) === govCode);
  const totalSeats = govCenters.reduce((sum, c) => sum + (Number(c.seats) || 0), 0);

  // كروت مؤشرات المحافظة
  const k = (v, l, c) => `<div class="kpi"><b class="${c || ""}">${v}</b><span>${l}</span></div>`;
  const pctAssigned = gData.students ? Math.round(100 * (gData.assigned || 0) / gData.students) : 0;
  let b2AlertHtml = "";
  if (isB2) {
    b2AlertHtml = `<div class="alert" style="margin-bottom:12px;background:var(--bad-bg);border:1px solid var(--bad-border);color:var(--bad);padding:10px 14px;border-radius:var(--radius-md)">⚠️ <b>تنبيه هام:</b> محافظة ${arName} تتبع «البند الثاني»، في حين أن هذا التشغيل مخصص للبندين الأول والثالث فقط. وجود ${n(gData.students)} طالب بها يعتبر خطأ في البيانات ويجب تعديلهم من الملف الأصلي.</div>`;
  }
  $("#gov_kpis").innerHTML = b2AlertHtml +
    k(n(gData.students), "طلاب المحافظة") +
    k(n(gData.assigned) + ` <small class="small">(${pctAssigned}%)</small>`, "تم توزيعهم", "ok") +
    k(n(gData.vp) + ` <small class="small">(${gData["vp_%"] || 0}%)</small>`, "مقترح VP", gData.vp ? "bad" : "ok") +
    k(n(totalSeats), "إجمالي مقاعد المحافظة") +
    k(n(govCenters.length), "مراكز بالمحافظة");

  // جدول تفاصيل الليفيل والتراك بالمحافظة
  const govLevels = (RES.by_gov_level || []).filter(x => normGov(x.gov) === govCode);
  govLevels.sort((a, b) => (b.students || 0) - (a.students || 0));

  table("#t_gov_lvl", [
    ["level_code", "الليفيل / التراك", v => `<b>${esc(v)}</b>`],
    ["students", "عدد الطلاب", v => n(v)],
    ["assigned", "تم توزيعهم", v => `<span class="ok" style="font-weight:600">${n(v)}</span>`],
    ["vp", "مقترح VP", v => `<span class="${v ? 'bad' : ''}">${n(v)}</span>`],
    ["seats", "المقاعد بالمحافظة", v => n(v)],
    ["deficit", "العجز", v => v > 0 ? `<span class="badge bad">${n(v)}</span>` : '<span class="ok">✓ مفيش</span>'],
    ["vp_%", "نسبة VP %", v => {
      const cls = v > 40 ? "bad" : v > 15 ? "warn" : "ok";
      return `<span class="badge ${cls}">${v}%</span>`;
    }]
  ], govLevels, {empty: "لا توجد تفاصيل مستويات مسجلة لهذه المحافظة"});

  // جدول مراكز المحافظة ونسب الإشغال مرتب من الأكبر إلى الأقل
  govCenters.sort((a, b) => (Number(b.assigned) || 0) - (Number(a.assigned) || 0) || (Number(b.seats) || 0) - (Number(a.seats) || 0));
  table("#t_gov_cen", [
    ["center_code", "الكود", v => `<b>${esc(v)}</b>`],
    ["center_name", "اسم المركز"],
    ["area", "المنطقة / الحي"],
    ["groups", "جروبات", v => n(v)],
    ["seats", "مقاعد", v => n(v)],
    ["assigned", "موزعين", v => n(v)],
    ["occupancy_%", "نسبة الإشغال", v => {
      const cls = v >= 100 ? "bad" : v >= 80 ? "warn" : "ok";
      return `<span class="badge ${cls}">${v}%</span>`;
    }]
  ], govCenters, {empty: "لا توجد مراكز مسجلة في هذه المحافظة"});
}

function resetGovView() {
  CURRENT_SELECTED_GOV = "";
  const sel = $("#sel_gov");
  if (sel) sel.value = "";
  const obox = $("#gov_overview_box");
  const dbox = $("#gov_detail_box");
  const abtn = $("#btn_all_govs");
  const fpanel = $("#gov_filter_panel");
  if (obox) obox.style.display = "block";
  if (fpanel) fpanel.style.display = "block";
  if (dbox) dbox.style.display = "none";
  if (abtn) abtn.style.display = "none";
  const gh = $("#gov_hint");
  if (gh) gh.textContent = "اضغط على أي محافظة في الجدول لعرض تفاصيلها (الطلاب، الموزعين، الليفيل، ومراكز المحافظة)";
}

function showGovOnMap() {
  if (!CURRENT_SELECTED_GOV) return;
  tab("map");
  const mg = $("#m_gov");
  if (mg) {
    mg.value = CURRENT_SELECTED_GOV;
    mapGov();
  }
}

// ---------------- تفاصيل المراكز وإشغالها ----------------
let CURRENT_SELECTED_CENTER = "";
let ALL_CENTERS_DATA = [];
const DAY_AR = {
  Monday: "الاثنين", Tuesday: "الثلاثاء", Wednesday: "الأربعاء",
  Thursday: "الخميس", Friday: "الجمعة", Saturday: "السبت", Sunday: "الأحد"
};

function initCentersSection(centerLoad) {
  // ترتيب المراكز تنازلياً من الأكبر عدداً إلى الأقل عدداً (بالموزعين ثم المقاعد)
  ALL_CENTERS_DATA = (centerLoad || []).slice().sort((a, b) => (Number(b.assigned) || 0) - (Number(a.assigned) || 0) || (Number(b.seats) || 0) - (Number(a.seats) || 0));
  const sel = $("#sel_center");
  if (sel) {
    let opts = '<option value="">-- كل المراكز (عرض عام) --</option>';
    ALL_CENTERS_DATA.forEach(c => {
      opts += `<option value="${esc(c.center_code)}">${esc(c.center_code)} · ${esc(c.center_name || '')} (${n(c.assigned)} موزع / ${n(c.seats)} مقعد - ${esc(govAr(c.gov))})</option>`;
    });
    sel.innerHTML = opts;
    sel.value = CURRENT_SELECTED_CENTER || "";
  }

  filterCentersTable();

  if (CURRENT_SELECTED_CENTER) {
    selectCenter(CURRENT_SELECTED_CENTER);
  } else {
    resetCenterView();
  }
}

function filterCentersTable() {
  const q = ($("#cen_search") ? $("#cen_search").value : "").trim().toLowerCase();
  const occFilter = $("#cen_occ_filter") ? $("#cen_occ_filter").value : "";

  let list = ALL_CENTERS_DATA.filter(c => {
    // فلتر الإشغال
    const occ = Number(c["occupancy_%"]) || 0;
    if (occFilter === "full" && occ < 100) return false;
    if (occFilter === "high" && (occ < 80 || occ >= 100)) return false;
    if (occFilter === "vacant" && occ >= 80) return false;

    // فلتر البحث
    if (!q) return true;
    const text = [
      c.center_code, c.center_name, c.gov, govAr(c.gov), c.area
    ].join(" ").toLowerCase();
    return text.includes(q);
  });

  table("#t_cen", [
    ["center_code", "الكود", v => `<b>${esc(v)}</b>`],
    ["center_name", "اسم المركز"],
    ["gov", "المحافظة", v => esc(govAr(v))],
    ["area", "المنطقة / الحي"],
    ["groups", "جروبات", v => n(v)],
    ["seats", "مقاعد", v => n(v)],
    ["assigned", "موزعين", v => `<span class="ok" style="font-weight:600">${n(v)}</span>`],
    ["occupancy_%", "نسبة الإشغال", v => {
      const cls = v >= 100 ? "bad" : v >= 80 ? "warn" : "ok";
      return `<span class="badge ${cls}">${v}%</span>`;
    }],
    ["actions", "التفاصيل", (_, row) => `
      <button class="btn sm sec" onclick="event.stopPropagation(); selectCenter('${esc(row.center_code)}')">👁️ عرض التفاصيل</button>
    `]
  ], list, {
    rowAttr: row => `class="click" onclick="selectCenter('${esc(row.center_code)}')"` + ` title="اضغط لعرض تفاصيل ${esc(row.center_code)}"`
  });
}

function onCenterSelectChange(val) {
  if (!val) {
    resetCenterView();
  } else {
    selectCenter(val);
  }
}

async function selectCenter(centerCode) {
  if (!centerCode) return;
  CURRENT_SELECTED_CENTER = centerCode;
  const sel = $("#sel_center");
  if (sel) sel.value = centerCode;

  const obox = $("#cen_overview_box");
  const dbox = $("#cen_detail_box");
  const abtn = $("#btn_all_centers");
  if (obox) obox.style.display = "none";
  if (dbox) dbox.style.display = "block";
  if (abtn) abtn.style.display = "inline-block";

  const editBtn = $("#cen_btn_edit");
  if (editBtn) editBtn.href = `/centers?q=${encodeURIComponent(centerCode)}`;

  $("#cen_detail_title").textContent = `جاري تحميل تفاصيل المركز ${centerCode}...`;
  $("#cen_detail_sub").textContent = "";
  $("#cen_kpis").innerHTML = '<div class="hint">جاري جلب تفاصيل الجروبات والمستويات والطلاب...</div>';
  $("#t_cen_lvl").innerHTML = "";
  $("#t_cen_grp").innerHTML = "";
  $("#cen_extra_info").innerHTML = "";

  const res = await api(`/api/runs/${RUN.id}/center_details/${encodeURIComponent(centerCode)}`);
  if (!res.ok) {
    $("#cen_detail_title").textContent = `خطأ في تحميل المركز ${centerCode}`;
    $("#cen_kpis").innerHTML = `<div class="alert">${esc(res.j.error || "تعذر جلب تفاصيل المركز")}</div>`;
    return;
  }

  const d = res.j;
  const c = d.center;
  const sInfo = d.students_info || {};

  $("#cen_detail_title").textContent = `تفاصيل المركز: ${esc(c.code)} - ${esc(c.name || '')}`;
  $("#cen_detail_sub").textContent = `${esc(govAr(c.gov))} / ${esc(c.area || '')} · إجمالي المقاعد: ${n(c.total_seats)} · تم تسكين: ${n(c.total_assigned)} · الشواغر: ${n(c.total_left)}`;
  $("#cen_hint").textContent = `أنت تتصفح الآن التفاصيل الكاملة للمركز ${c.code}.`;

  // كروت مؤشرات المركز
  const k = (v, l, cls) => `<div class="kpi"><b class="${cls || ""}">${v}</b><span>${l}</span></div>`;
  $("#cen_kpis").innerHTML =
    k(n(c.total_seats), "إجمالي المقاعد") +
    k(n(c.total_assigned) + ` <small class="small">(${c["occupancy_%"]}%)</small>`, "تم توزيعهم", "ok") +
    k(n(c.total_left), "المقاعد الشاغرة", c.total_left > 0 ? "ok" : "mute") +
    k(n(c.total_groups), "عدد الجروبات بالمركز") +
    k(sInfo.avg_km ? sInfo.avg_km + " كم" : "—", "متوسط مسافة وصول الطلاب");

  // جدول تفاصيل المستويات والتراكات بالمركز
  table("#t_cen_lvl", [
    ["level_code", "الليفيل / التراك", v => `<b>${esc(v)}</b>`],
    ["groups", "جروبات", v => n(v)],
    ["seats", "المقاعد", v => n(v)],
    ["assigned", "المسكنين", v => `<span class="ok" style="font-weight:600">${n(v)}</span>`],
    ["left", "الشواغر", v => v > 0 ? `<span class="badge ok">${n(v)} شاغر</span>` : '<span class="mute">ممتلئ</span>'],
    ["occupancy_%", "نسبة الإشغال", v => {
      const cls = v >= 100 ? "bad" : v >= 80 ? "warn" : "ok";
      return `<span class="badge ${cls}">${v}%</span>`;
    }]
  ], d.by_level, {empty: "لا توجد مستويات مسجلة لهذا المركز"});

  // جدول كل الجروبات والمواعيد
  table("#t_cen_grp", [
    ["group_code", "كود الجروب", v => `<b>${esc(v)}</b>`],
    ["level_code", "الليفيل"],
    ["day", "اليوم", v => esc(DAY_AR[v] || v)],
    ["time", "الوقت", v => esc(v)],
    ["capacity", "السعة", v => n(v)],
    ["assigned", "المسكنين", v => n(v)],
    ["left", "المتبقي", v => v > 0 ? `<span class="badge ok">${n(v)}</span>` : '<span class="mute">0</span>'],
    ["occupancy_%", "الإشغال %", v => {
      const cls = v >= 100 ? "bad" : v >= 80 ? "warn" : "ok";
      return `<span class="badge ${cls}">${v}%</span>`;
    }]
  ], d.groups, {empty: "لا توجد جروبات مسجلة لهذا المركز"});

  // المناطق الأكثر وفوداً على هذا المركز
  if (sInfo.top_areas && sInfo.top_areas.length) {
    const areaBadges = sInfo.top_areas.map(a =>
      `<span class="badge info" style="font-size:13px;padding:4px 10px;margin-left:6px;margin-bottom:6px">📍 ${esc(a.area)}: <b>${n(a.count)} طالب</b></span>`
    ).join("");
    $("#cen_extra_info").innerHTML = `
      <div style="font-size:14px;font-weight:600;margin-bottom:8px">🏘️ المناطق الأكثر وفوداً على هذا المركز:</div>
      <div style="display:flex;flex-wrap:wrap;gap:6px">${areaBadges}</div>
    `;
  }
}

function resetCenterView() {
  CURRENT_SELECTED_CENTER = "";
  const sel = $("#sel_center");
  if (sel) sel.value = "";
  const obox = $("#cen_overview_box");
  const dbox = $("#cen_detail_box");
  const abtn = $("#btn_all_centers");
  if (obox) obox.style.display = "block";
  if (dbox) dbox.style.display = "none";
  if (abtn) abtn.style.display = "none";
  const ch = $("#cen_hint");
  if (ch) ch.textContent = "اضغط على أي مركز في الجدول لعرض تفاصيله بالكامل (المقاعد، الموزعين، الجروبات، والمستويات)";
}

function showSelectedCenterOnMap() {
  if (!CURRENT_SELECTED_CENTER) return;
  tab("map");
  const c = CENTERS.find(x => x.center_code === CURRENT_SELECTED_CENTER);
  if (c && MAP) {
    if (c.lat && c.lon) {
      MAP.flyTo([c.lat, c.lon], 15);
      api(`/api/runs/${RUN.id}/students?center=${encodeURIComponent(c.center_code)}`).then(res => {
        if (res.ok && res.j.points) {
          if (pLayer) pLayer.remove();
          pLayer = pointsLayer(MAP, res.j.points);
          $("#m_msg").textContent = `${c.center_code}: ${n(res.j.total)} طالب`;
        }
      });
    }
  }
}

// ---------------- نافذة فحص واقتراح موقع المركز ----------------
async function openCenterFix(code) {
  $("#mcf_title").textContent = `فحص وتصحيح موقع المركز ${code}`;
  $("#mcf_body").innerHTML = '<div class="hint">جاري جلب إحداثيات المركز واقتراحات العناوين والخرائط...</div>';
  $("#modal_center_fix").hidden = false;

  const res = await api(`/api/centers/${encodeURIComponent(code)}/suggest_coords`);
  if (!res.ok) {
    $("#mcf_body").innerHTML = `<div class="alert">${esc(res.j.error || "فشل جلب بيانات المركز")}</div>`;
    return;
  }
  const d = res.j, c = d.center, link = d.link, addr = d.address;

  let html = `
    <div class="row" style="margin-bottom:14px">
      <b>${esc(c.code)} - ${esc(c.name || '')}</b>
      <span class="mute">(${esc(govAr(c.gov))} - ${esc(c.area || '')})</span>
      ${c.flags ? `<span class="badge bad">${esc(c.flags)}</span>` : '<span class="badge ok">سليم</span>'}
    </div>
    <div class="hint" style="margin-bottom:12px">العنوان المسجل: <b>${esc(c.address || 'بدون عنوان')}</b></div>
  `;

  // 1. الموقع الحالي
  html += `
    <div class="comp-card ${c.flags ? 'warn' : ''}">
      <div class="row" style="justify-content:space-between">
        <div>
          <b>الموقع الحالي في السيستم</b>
          <div class="small mute">المصدر: ${esc(c.source || 'sheet')} | تم التحقق: ${c.verified ? 'نعم ✓' : 'لا'}</div>
        </div>
        <div class="row">
          <button class="btn sm sec" onclick="verifyCenterCurrent('${esc(code)}')">✓ تأكيد كصحيح وإلغاء التحذير</button>
        </div>
      </div>
      <div style="margin-top:6px;font-family:monospace;direction:ltr;text-align:right">
        ${c.lat != null ? Number(c.lat).toFixed(6) + ', ' + Number(c.lon).toFixed(6) : 'بدون إحداثيات'}
      </div>
    </div>
  `;

  // 2. موقع لينك خرائط Google
  if (link && link.lat != null) {
    html += `
      <div class="comp-card highlight" style="border:2px solid var(--ok);background:rgba(22,121,76,0.06)">
        <div class="row" style="justify-content:space-between;align-items:flex-start">
          <div>
            <b class="ok" style="font-size:15px">✓ الموقع المستخرج من رابط خرائط Google (الموقع الصحيح)</b>
            <div class="small mute" style="margin-top:4px">الفرق عن موقع الشيت: <b>${link.diff_km != null ? link.diff_km + ' كم' : '—'}</b> (الموقع الفعلي للمركز في موط - يبعد 50 مترًا فقط)</div>
          </div>
          <div class="row" style="gap:8px">
            <button class="btn ok" style="padding:7px 14px;font-size:14px" onclick="applyCenterLocation('${esc(code)}', ${link.lat}, ${link.lon}, 'gmaps_link', 'من لينك Google')">⚡ اعتماد هذا الموقع كإحداثيات صحيحة</button>
            <a class="btn sec" style="padding:7px 12px;font-size:14px" href="${esc(link.url)}" target="_blank">🌐 فتح الرابط في Google Maps</a>
          </div>
        </div>
        <div style="margin-top:10px;font-family:monospace;direction:ltr;text-align:right;font-size:14px;font-weight:600">
          📍 ${Number(link.lat).toFixed(6)}, ${Number(link.lon).toFixed(6)}
        </div>
      </div>
    `;
  }

  // 3. موقع العنوان (Geocoded Address)
  if (addr && addr.lat != null) {
    html += `
      <div class="comp-card">
        <div class="row" style="justify-content:space-between;align-items:flex-start">
          <div>
            <b>الموقع المستخرج من العنوان (الـ Geocoder)</b>
            <div class="small mute">الدقة: ${esc(addr.level || 'area')} | تطابق: ${esc(addr.matched || '')} | الفرق: <b>${addr.diff_km != null ? addr.diff_km + ' كم' : '—'}</b></div>
          </div>
          <div class="row">
            <button class="btn sec sm" onclick="applyCenterLocation('${esc(code)}', ${addr.lat}, ${addr.lon}, 'geocoded', 'من العنوان (OSM)')">اعتماد موقع العنوان</button>
          </div>
        </div>
        <div style="margin-top:6px;font-family:monospace;direction:ltr;text-align:right">
          ${Number(addr.lat).toFixed(6)}, ${Number(addr.lon).toFixed(6)}
        </div>
      </div>
    `;
  }

  // 4. إدخال إحداثيات يدويًا
  html += `
    <div class="comp-card" style="margin-top:14px">
      <b>أو إدخال إحداثيات يدوية / رابط خرائط جديد:</b>
      <div class="small mute" style="margin:4px 0 8px">يمكنك لصق إحداثيات مباشرة مثل <code style="font-family:monospace;direction:ltr;display:inline-block">25.494889, 28.982750</code> أو رابط Google Maps.</div>
      <div class="row" style="margin-top:8px">
        <input type="text" id="mcf_manual_input" placeholder="مثال: 25.494889, 28.982750 أو رابط خرائط..." style="flex:1" oninput="checkManualCoords(this.value)">
        <button class="btn ok" id="mcf_btn_save" style="padding:8px 16px" onclick="saveManualCenterCoords('${esc(code)}')">💾 تطبيق وحفظ</button>
      </div>
      <div id="mcf_input_msg" class="small hint" style="margin-top:6px"></div>
    </div>
  `;

  $("#mcf_body").innerHTML = html;
}

function checkManualCoords(val) {
  const t = (val || "").trim();
  const m = $("#mcf_input_msg");
  if (!m) return;
  if (!t) { m.textContent = ""; return; }
  const coordMatch = t.match(/(-?\d{1,3}(?:\.\d+)?)\s*[,;\s]\s*(-?\d{1,3}(?:\.\d+)?)/);
  if (coordMatch && !t.startsWith("http")) {
    m.className = "small hint ok";
    m.textContent = `✓ تم التعرف على الإحداثيات: ${coordMatch[1]}, ${coordMatch[2]} (جاهزة للحفظ فوراً)`;
  } else if (t.includes("@") || t.includes("!3d") || t.includes("q=")) {
    m.className = "small hint ok";
    m.textContent = `✓ رابط خرائط يحمل إحداثيات (سيتم استخراجها وتطبيقها)`;
  } else if (t.startsWith("http")) {
    m.className = "small hint";
    m.textContent = `رابط خرائط Google (سيتم فحص تحويل الرابط مع خوادم Google عند الحفظ)`;
  } else {
    m.className = "small hint";
    m.textContent = `الصق إحداثيات بصيغة 25.494889, 28.982750 أو رابط Google Maps`;
  }
}

async function applyCenterLocation(code, lat, lon, source, note) {
  const r = await postJSON(`/api/centers/${encodeURIComponent(code)}/apply_coords`, {
    lat: lat, lon: lon, source: source, note: note, clear_flags: true, run_id: RUN.id
  });
  if (!r.ok) { toast(r.j.error || "فشل التحديث", "bad"); return; }
  toast(`تم تحديث وتثبيت موقع المركز ${code} بنجاح وإزالة التنبيه ✓`, "ok");
  closeModal('modal_center_fix');
  await loadResult();
}

async function verifyCenterCurrent(code) {
  const r = await postJSON(`/api/centers/${encodeURIComponent(code)}/verify_current`, { run_id: RUN.id });
  if (!r.ok) { toast(r.j.error || "فشل التأكيد", "bad"); return; }
  toast(`تم تأكيد موقع المركز ${code} وإزالة الشبهة عنه ✓`, "ok");
  closeModal('modal_center_fix');
  await loadResult();
}

async function saveManualCenterCoords(code) {
  const input = ($("#mcf_manual_input")?.value || "").trim();
  if (!input) { toast("ادخل الإحداثيات أو الرابط أولاً", "warn"); return; }
  const btn = $("#mcf_btn_save");
  if (btn) { btn.disabled = true; btn.textContent = "جاري الحفظ..."; }

  const r = await postJSON(`/api/centers/${encodeURIComponent(code)}`, { input: input, note: "تعديل يدوي من صفحة التشغيل", run_id: RUN.id });
  if (btn) { btn.disabled = false; btn.textContent = "💾 تطبيق وحفظ"; }

  if (!r.ok) {
    const err = r.j.error || "فشل الحفظ";
    toast(err, "bad");
    const m = $("#mcf_input_msg");
    if (m) {
      m.className = "small hint bad";
      m.textContent = err + " — يمكنك نسخ الإحداثيات الرقمية مباشرة مثل 25.494889, 28.982750 والضغط على حفظ.";
    }
    return;
  }
  toast(`تم حفظ موقع المركز ${code} بنجاح ✓`, "ok");
  closeModal('modal_center_fix');
  await loadResult();
}

// ---------------- فحص وتعديل بيانات الطالب الفردي ----------------
async function viewStudentRow(runId, rowNo) {
  CURRENT_STUDENT_ROW = rowNo;
  $("#msi_title").textContent = `بيانات الطالب - صف رقم ${rowNo}`;
  $("#msi_body").innerHTML = '<div class="hint">جاري تحميل بيانات الطالب...</div>';
  $("#modal_student_inspect").hidden = false;

  const res = await api(`/api/runs/${encodeURIComponent(runId)}/students/by_row/${encodeURIComponent(rowNo)}`);
  if (!res.ok) {
    $("#msi_body").innerHTML = `<div class="alert">${esc(res.j.error || "فشل جلب بيانات الطالب")}</div>`;
    return;
  }
  const s = res.j, meta = s._meta || {};
  const nidCol = meta.nid_col || "Student NID";
  const nidVal = s[nidCol] || "";

  let html = `
    <div class="kv-grid">
      <div class="kv-item"><label>رقم الصف في الشيت (row_no)</label><div><b>${esc(s.row_no || '')}</b></div></div>
      <div class="kv-item"><label>المحافظة</label><div>${esc(s.gov || '')}</div></div>
      <div class="kv-item"><label>المنطقة</label><div>${esc(s.Area || '')}</div></div>
      <div class="kv-item"><label>المستوى / التراك</label><div>${esc(s['Level(25-26)'] || s.level_code || '')}</div></div>
      <div class="kv-item"><label>حالة التوزيع</label><div><span class="badge ${s.status === 'VP' ? 'bad' : 'ok'}">${esc(s.status || '')}</span></div></div>
      <div class="kv-item"><label>المركز الموزع عليه</label><div>${esc(s.center_code || s.center_name || '—')}</div></div>
      <div class="kv-item"><label>الجروب والموعد</label><div>${esc(s.group_code ? s.group_code + ' (' + (s.day || '') + ' ' + (s.time || '') + ')' : '—')}</div></div>
      <div class="kv-item"><label>المسافة للمركز</label><div>${s.distance_km ? esc(s.distance_km) + ' كم' : '—'}</div></div>
    </div>

    <div class="comp-card" style="margin-top:14px">
      <label><b>الرقم القومي (Student NID):</b></label>
      <div class="row" style="margin-top:6px">
        <input type="text" id="msi_nid_input" value="${esc(nidVal)}" maxlength="20" style="font-family:monospace;font-size:15px;flex:1">
        <span class="badge ${meta.nid_valid ? 'ok' : 'bad'}" id="msi_nid_badge">
          ${meta.nid_valid ? '14 رقم سليم ✓' : `⚠️ مش 14 رقم (${meta.nid_len} رقم فقط)`}
        </span>
      </div>
      <div class="small mute" style="margin-top:4px">تأكد من إدخال 14 رقمًا صحيحًا يبدأ برقم القرن وتاريخ الميلاد.</div>
    </div>

    <div class="comp-card" style="margin-top:10px">
      <label><b>العنوان:</b></label>
      <input type="text" id="msi_addr_input" value="${esc(s.Adress || s.address || '')}" style="margin-top:6px">
    </div>

    <div class="comp-card" style="margin-top:10px">
      <div class="row" style="justify-content:space-between">
        <b>الإحداثيات الجغرافية (Latitude, Longitude):</b>
        ${s.coord_suspect === 'نعم' ? '<span class="badge warn">coord_suspect: إحداثيات مشبوهة (>100 كم)</span>' : ''}
      </div>
      <div class="grid2" style="margin-top:8px">
        <div><label class="small">Latitude</label><input type="text" id="msi_lat_input" value="${esc(s.lat || s.Latitude || '')}"></div>
        <div><label class="small">Longitude</label><input type="text" id="msi_lon_input" value="${esc(s.lon || s.Longitude || '')}"></div>
      </div>
      <div class="small mute" style="margin-top:6px">عند حفظ أي تعديل في الإحداثيات، يتم تسجيله فورًا في قاعدة المواقع (locdb.overrides) ليعتمد عليه السيستم دائمًا في أي تشغيلات قادمة.</div>
    </div>
  `;

  $("#msi_body").innerHTML = html;
}

async function saveStudentModal() {
  if (!CURRENT_STUDENT_ROW) return;
  const nid = ($("#msi_nid_input")?.value || "").trim();
  const addr = ($("#msi_addr_input")?.value || "").trim();
  const lat = ($("#msi_lat_input")?.value || "").trim();
  const lon = ($("#msi_lon_input")?.value || "").trim();

  const updates = {};
  if (nid) updates["Student NID"] = nid;
  if (addr) { updates["Adress"] = addr; updates["address"] = addr; }
  if (lat) { updates["lat"] = lat; updates["Latitude"] = lat; }
  if (lon) { updates["lon"] = lon; updates["Longitude"] = lon; }

  const r = await postJSON(`/api/runs/${RUN.id}/students/by_row/${CURRENT_STUDENT_ROW}`, updates);
  if (!r.ok) { toast(r.j.error || "فشل حفظ تعديل الطالب", "bad"); return; }
  toast(`تم حفظ تعديل الطالب صف ${CURRENT_STUDENT_ROW} بنجاح ✓`, "ok");
  closeModal('modal_student_inspect');
  await loadResult();
}

// ---------------- نافذة عرض وتعديل طلاب البند الثاني ----------------
let _b2SearchTimer = null;
let CURRENT_B2_DATA = [];

function searchBand2Students() {
  clearTimeout(_b2SearchTimer);
  _b2SearchTimer = setTimeout(() => openBand2Modal(1, $("#mb2_search")?.value || ""), 300);
}

async function openBand2Modal(page = 1, q = "") {
  $("#modal_band2_students").hidden = false;
  $("#t_mb2").innerHTML = "<tr><td class='mute'>جاري تحميل طلاب البند الثاني...</td></tr>";

  const res = await api(`/api/runs/${RUN.id}/students/band2?page=${page}&per_page=100&q=${encodeURIComponent(q)}`);
  if (!res.ok) {
    $("#t_mb2").innerHTML = `<tr><td class='bad'>${esc(res.j.error || "فشل التحميل")}</td></tr>`;
    return;
  }
  const d = res.j;
  CURRENT_B2_DATA = d.rows || [];
  $("#mb2_count").textContent = n(d.total);
  $("#mb2_hint").textContent = d.total ? `معروض ${d.rows.length} من إجمالي ${n(d.total)} طالب في البند الثاني` : "لا يوجد طلاب في البند الثاني";

  const nidCol = d.cols.find(c => c.toLowerCase().includes("nid")) || "Student NID";
  const cols = [
    ["row_no", "صف في الشيت", v => `<b class="badge brand">${esc(v)}</b>`],
    [nidCol, "الرقم القومي", v => `<span style="font-family:monospace;font-weight:600">${esc(v)}</span>`],
    ["gov", "المحافظة", v => `<span class="badge bad">⚠️ ${esc(govAr(v))}</span>`],
    ["Area", "المنطقة"],
    ["Adress", "العنوان", v => esc((v || '').slice(0, 35))],
    ["Level(25-26)", "المستوى", v => esc(v || '')],
    ["status", "الحالة", v => `<span class="badge ${v === 'VP' ? 'bad' : 'ok'}">${esc(v || '')}</span>`],
    ["actions", "فحص وتعديل", (_, r) => `<button class="btn sm" onclick="viewStudentRow('${RUN.id}','${esc(r.row_no)}')">✏️ تعديل الطالب</button>`]
  ];
  table("#t_mb2", cols, d.rows);
}

function copyBand2Nids() {
  if (!CURRENT_B2_DATA || !CURRENT_B2_DATA.length) {
    toast("لا توجد بيانات لنسخها", "warn");
    return;
  }
  const nids = CURRENT_B2_DATA.map(r => r["Student NID"] || r["student nid"] || r["nid"] || "").filter(Boolean);
  if (!nids.length) {
    toast("لم يتم العثور على أرقام قومية", "warn");
    return;
  }
  navigator.clipboard.writeText(nids.join("\n")).then(() => {
    toast(`تم نسخ ${n(nids.length)} رقم قومي للحافظة بنجاح ✓`, "ok");
  }).catch(() => {
    toast("تعذر النسخ التلقائي للحافظة", "bad");
  });
}

// ---------------- نافذة عرض كل الطلاب بأرقام قومية غير صحيحة ----------------
let _searchTimer = null;
function searchInvalidNids() {
  clearTimeout(_searchTimer);
  _searchTimer = setTimeout(() => openInvalidNidsModal(1, $("#min_search")?.value || ""), 300);
}

async function openInvalidNidsModal(page = 1, q = "") {
  $("#modal_invalid_nids").hidden = false;
  $("#t_min").innerHTML = "<tr><td class='mute'>جاري تحميل الطلاب...</td></tr>";

  const res = await api(`/api/runs/${RUN.id}/students/invalid_nids?page=${page}&per_page=50&q=${encodeURIComponent(q)}`);
  if (!res.ok) {
    $("#t_min").innerHTML = `<tr><td class='bad'>${esc(res.j.error || "فشل التحميل")}</td></tr>`;
    return;
  }
  const d = res.j;
  $("#min_count").textContent = n(d.total);
  $("#min_hint").textContent = d.total ? `معروض ${d.rows.length} من إجمالي ${n(d.total)} طالب غير سليم الرقم القومي` : "لا يوجد طلاب بأرقام قومية غير صحيحة";

  const nidCol = d.cols.find(c => c.toLowerCase().includes("nid")) || "Student NID";
  const cols = [
    ["row_no", "صف", v => `<b>${esc(v)}</b>`],
    [nidCol, "الرقم القومي المسجل", (v, r) => `<span class="badge bad" style="font-family:monospace">${esc(v)} (${r._nid_len || (v||'').length} رقم)</span>`],
    ["gov", "المحافظة", v => esc(govAr(v))],
    ["Area", "المنطقة"],
    ["Adress", "العنوان", v => esc((v || '').slice(0, 35))],
    ["status", "الحالة", v => `<span class="badge ${v === 'VP' ? 'bad' : 'ok'}">${esc(v || '')}</span>`],
    ["actions", "إجراءات", (_, r) => `<button class="btn sm" onclick="viewStudentRow('${RUN.id}','${esc(r.row_no)}')">👁️ فحص وتصحيح</button>`]
  ];
  table("#t_min", cols, d.rows);
}

// ---------------- نافذة وجدول كل الطلاب المشبوهين ----------------
async function openAllSuspectsModal(page = 1) {
  $("#modal_all_suspects").hidden = false;
  $("#t_mas").innerHTML = "<tr><td class='mute'>جاري تحميل الطلاب المشبوهين...</td></tr>";

  const res = await api(`/api/runs/${RUN.id}/students/suspects?page=${page}&per_page=50`);
  if (!res.ok) {
    $("#t_mas").innerHTML = `<tr><td class='bad'>${esc(res.j.error || "فشل التحميل")}</td></tr>`;
    return;
  }
  const d = res.j;
  $("#mas_count").textContent = n(d.total);

  const cols = [
    ["row_no", "صف", v => `<b>${esc(v)}</b>`],
    ["Student NID", "الرقم القومي"],
    ["gov", "المحافظة", v => esc(govAr(v))],
    ["Area", "المنطقة"],
    ["Adress", "العنوان", v => esc((v || '').slice(0, 35))],
    ["lat", "Latitude", v => (v != null ? Number(v).toFixed(5) : "—")],
    ["lon", "Longitude", v => (v != null ? Number(v).toFixed(5) : "—")],
    ["status", "الحالة", v => `<span class="badge ${v === 'VP' ? 'bad' : 'ok'}">${esc(v || '')}</span>`],
    ["actions", "إجراءات", (_, r) => `
      <button class="btn sm" onclick="viewStudentRow('${RUN.id}','${esc(r.row_no)}')">👁️ تفاصيل</button>
      <button class="btn sm sec" onclick="flyToStudent(${r.lat}, ${r.lon})">🗺️ خريطة</button>
    `]
  ];
  table("#t_mas", cols, d.rows);
}

// ---------------- تصحيح ذكي لجميع الطلاب المشبوهين ----------------
async function fixAllSuspects() {
  if (!confirm("هل تريد تشغيل التصحيح الذكي لجميع الطلاب المشبوهين وحفظ إحداثياتهم المعتمدة في قاعدة المواقع؟")) return;
  const b = $("#btn_fix_all_sus");
  if (b) { b.disabled = true; b.textContent = "جاري الفحص والتصحيح..."; }

  const res = await postJSON(`/api/runs/${RUN.id}/students/fix_suspects`, { save_overrides: true });
  if (b) { b.disabled = false; b.textContent = "⚡ تصحيح ذكي لجميع الطلاب وحفظ في قاعدة المواقع"; }

  if (!res.ok) {
    toast(res.j.error || "فشل التصحيح", "bad");
    return;
  }
  const d = res.j;
  toast(`تم التصحيح الذكي بنجاح: ${n(d.total)} طالب تم فحصهم وتثبيت إحداثياتهم في قاعدة المواقع ✓`, "ok");
  closeModal('modal_all_suspects');
  await loadResult();
}

function flyToStudent(lat, lon) {
  if (!lat || !lon) return;
  closeModal('modal_all_suspects');
  closeModal('modal_student_inspect');
  tab("map");
  if (MAP) {
    MAP.setView([lat, lon], 14);
    L.popup().setLatLng([lat, lon]).setContent("<b>موقع الطالب</b>").openOn(MAP);
  }
}

function closeModal(id) {
  const el = $("#" + id);
  if (el) el.hidden = true;
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

// ---------------- عرض وتحليل الطلاب الأبعد من 50 كم عن أقرب مركز ----------------
let farPointsLayer = null;
let farLinesLayer = null;
let farCentersLayer = null;
let ACTIVE_FAR_POINTS = [];
let ARE_FAR_LINES_VISIBLE = false;
let CURRENT_FAR_TAB = "gov";
let CURRENT_FAR_PAGE = 1;
let TOTAL_FAR_PAGES = 1;
let _farSearchTimer = null;

async function showFarStudentsOnMap(filterType = "gov") {
  closeModal("modal_far_students");
  tab("map");
  if (!MAP) initMap();

  $("#m_msg").textContent = "جاري تحميل مواقع الطلاب الأبعد من 50 كم وأقرب مركز لكل منهم...";
  const r = await api(`/api/runs/${RUN.id}/students/far_points?filter_type=${encodeURIComponent(filterType)}&limit=3000`);
  if (!r.ok) {
    $("#m_msg").textContent = r.j.error || "فشل تحميل النقاط";
    return;
  }

  const d = r.j;
  ACTIVE_FAR_POINTS = d.points || [];
  const centers = d.centers || [];
  const maxKm = d.max_km || 50;

  if (pLayer) { pLayer.remove(); pLayer = null; }
  if (farPointsLayer) { farPointsLayer.remove(); farPointsLayer = null; }
  if (farLinesLayer) { farLinesLayer.remove(); farLinesLayer = null; }
  if (farCentersLayer) { farCentersLayer.remove(); farCentersLayer = null; }

  farPointsLayer = L.layerGroup().addTo(MAP);
  farCentersLayer = L.layerGroup().addTo(MAP);
  farLinesLayer = L.layerGroup().addTo(MAP);

  // 1. رسم المراكز الأقرب
  const centerCoordsMap = {};
  centers.forEach(c => {
    if (c.lat == null || c.lon == null) return;
    centerCoordsMap[c.center_code] = [c.lat, c.lon];
    const cm = L.circleMarker([c.lat, c.lon], {
      radius: 9,
      color: "#ffffff",
      weight: 3,
      fillColor: "#2457d6",
      fillOpacity: 1
    });
    cm.bindPopup(`
      <div style="font-family:inherit">
        <b style="color:var(--acc)">🏢 مركز متاح: ${esc(c.center_code)}</b><br>
        <b>${esc(c.center_name || '')}</b><br>
        <span class="small mute">${esc(govAr(c.gov))} / ${esc(c.area || '')}</span>
      </div>
    `);
    cm.bindTooltip(`🏢 ${esc(c.center_code)}: ${esc(c.center_name || '')}`, { direction: "top" });
    cm.addTo(farCentersLayer);
  });

  // 2. رسم نقاط الطلاب
  const allBounds = [];
  ACTIVE_FAR_POINTS.forEach(p => {
    if (p.lat == null || p.lon == null) return;
    allBounds.push([p.lat, p.lon]);
    if (p.c_lat && p.c_lon) allBounds.push([p.c_lat, p.c_lon]);

    const markerColor = p.suspect ? "#d92d20" : "#e08a1e";
    const sm = L.circleMarker([p.lat, p.lon], {
      radius: 5,
      color: "#ffffff",
      weight: 1.5,
      fillColor: markerColor,
      fillOpacity: 0.95
    });

    const popupHtml = `
      <div style="font-family:inherit;min-width:240px;line-height:1.5">
        <div style="border-bottom:1px solid var(--line);padding-bottom:6px;margin-bottom:6px">
          <span class="badge ${p.suspect ? 'bad' : 'warn'}" style="margin-left:6px">${p.suspect ? 'إحداثيات مشبوهة' : 'أبعد من ' + maxKm + ' كم'}</span>
          <b>طالب صف ${esc(p.row_no)}</b>
          <div class="small mute">الرقم القومي: <b style="font-family:monospace">${esc(p.nid || '—')}</b></div>
        </div>
        <div style="font-size:13px;margin-bottom:6px">
          📍 <b>الموقع:</b> ${esc(govAr(p.gov))} / ${esc(p.area)}<br>
          <span class="small mute">${esc(p.addr || '')}</span>
        </div>
        <div class="comp-card" style="margin:6px 0;padding:8px;background:rgba(224,138,30,0.08);border-color:#e08a1e">
          <div style="font-size:12px;font-weight:600;color:var(--mute)">🏢 أقرب مركز متاح في المحافظة:</div>
          <div style="font-size:14px;color:var(--acc);font-weight:bold;margin:2px 0">${esc(p.c_code)} - ${esc(p.c_name || '')}</div>
          <div style="font-size:15px;color:#d92d20;font-weight:bold;margin-top:4px">
            📏 المسافة: ${p.dist} كم
            <span class="badge bad" style="font-size:11px;vertical-align:middle">+${p.over} كم فوق الحد (${maxKm} كم)</span>
          </div>
        </div>
        <div class="row" style="margin-top:8px;gap:6px">
          ${p.c_lat && p.c_lon ? `<button class="btn sm" onclick="connectAndFlyFarStudent('${esc(p.row_no)}', ${p.lat}, ${p.lon}, ${p.c_lat}, ${p.c_lon}, '${esc(p.c_code)}', '${esc(p.c_name)}', ${p.dist})">🛣️ رسم مسار لأقرب مركز</button>` : ''}
          <button class="btn sm sec" onclick="viewStudentRow('${RUN.id}', '${esc(p.row_no)}')">✏️ تعديل الطالب</button>
        </div>
      </div>
    `;

    sm.bindPopup(popupHtml);
    sm.bindTooltip(`طالب صف ${p.row_no}: أقرب مركز ${p.c_code} (${p.dist} كم)`, { direction: "top" });
    sm.addTo(farPointsLayer);
  });

  if (allBounds.length && MAP) {
    MAP.fitBounds(L.latLngBounds(allBounds).pad(0.08), { maxZoom: 13 });
  }

  const dists = ACTIVE_FAR_POINTS.map(x => x.dist).filter(d => !isNaN(d));
  const minDist = dists.length ? Math.min(...dists) : maxKm;
  const maxDist = dists.length ? Math.max(...dists) : maxKm;

  $("#m_msg").innerHTML = `معروض <b>${n(ACTIVE_FAR_POINTS.length)}</b> طالب أبعد من ${maxKm} كم · أقرب مركز متاح على بعد <b>${minDist} إلى ${maxDist} كم</b>`;
  const btnLines = $("#btn_toggle_far_lines");
  if (btnLines) {
    btnLines.style.display = "inline-block";
    btnLines.textContent = "📏 رسم خطوط المسافات للكل";
  }
  ARE_FAR_LINES_VISIBLE = false;
}

function toggleMapFarStudents() {
  showFarStudentsOnMap("gov");
}

function toggleFarLines() {
  if (!farLinesLayer || !ACTIVE_FAR_POINTS.length) return;
  const btn = $("#btn_toggle_far_lines");
  if (ARE_FAR_LINES_VISIBLE) {
    farLinesLayer.clearLayers();
    ARE_FAR_LINES_VISIBLE = false;
    if (btn) btn.textContent = "📏 رسم خطوط المسافات للكل";
    toast("تم إخفاء خطوط المسافات", "ok");
  } else {
    farLinesLayer.clearLayers();
    ACTIVE_FAR_POINTS.forEach(p => {
      if (p.lat && p.lon && p.c_lat && p.c_lon) {
        const line = L.polyline([[p.lat, p.lon], [p.c_lat, p.c_lon]], {
          color: p.suspect ? "#d92d20" : "#e08a1e",
          weight: 2,
          dashArray: "4, 6",
          opacity: 0.75
        });
        line.bindTooltip(`المسافة إلى ${esc(p.c_code)}: ${p.dist} كم (+${p.over} كم)`, { sticky: true });
        line.addTo(farLinesLayer);
      }
    });
    ARE_FAR_LINES_VISIBLE = true;
    if (btn) btn.textContent = "❌ إخفاء خطوط المسافات";
    toast("تم رسم خطوط التوصيل لجميع الطلاب وأقرب مراكزهم", "ok");
  }
}

function connectAndFlyFarStudent(rowNo, sLat, sLon, cLat, cLon, cCode, cName, distKm) {
  if (!MAP) return;
  if (farLinesLayer) {
    farLinesLayer.clearLayers();
    const line = L.polyline([[sLat, sLon], [cLat, cLon]], {
      color: "#d92d20",
      weight: 3.5,
      dashArray: "6, 8",
      opacity: 0.95
    });
    line.addTo(farLinesLayer);
  }

  MAP.fitBounds(L.latLngBounds([[sLat, sLon], [cLat, cLon]]).pad(0.2), { maxZoom: 14 });
  L.popup().setLatLng([sLat, sLon]).setContent(`
    <div style="font-family:inherit;min-width:200px">
      <b>طالب صف ${esc(rowNo)}</b><br>
      🏢 أقرب مركز متاح: <b>${esc(cCode)} - ${esc(cName)}</b><br>
      <b style="color:#d92d20;font-size:15px">📏 المسافة المباشرة: ${distKm} كم</b>
    </div>
  `).openOn(MAP);
}

// ---------------- نافذة جدول الطلاب والمسافات (501 طالب) ----------------
function searchFarStudents() {
  clearTimeout(_farSearchTimer);
  _farSearchTimer = setTimeout(() => {
    CURRENT_FAR_PAGE = 1;
    loadFarStudentsPage(1);
  }, 300);
}

function setFarStudentsTab(tabKey) {
  CURRENT_FAR_TAB = tabKey;
  ["gov", "suspect", "all"].forEach(k => {
    const el = $(`#mfs_tab_${k}`);
    if (el) el.classList.toggle("on", k === tabKey);
  });
  CURRENT_FAR_PAGE = 1;
  loadFarStudentsPage(1);
}

async function openFarStudentsModal(tabKey = "gov", expectedCount = null) {
  CURRENT_FAR_TAB = tabKey;
  CURRENT_FAR_PAGE = 1;
  ["gov", "suspect", "all"].forEach(k => {
    const el = $(`#mfs_tab_${k}`);
    if (el) el.classList.toggle("on", k === tabKey);
  });
  const searchInput = $("#mfs_search");
  if (searchInput) searchInput.value = "";

  $("#modal_far_students").hidden = false;
  $("#t_mfs").innerHTML = "<tr><td class='mute'>جاري تحميل الطلاب والمسافات...</td></tr>";

  await loadFarStudentsPage(1);
}

async function loadFarStudentsPage(page = 1) {
  CURRENT_FAR_PAGE = page;
  const q = ($("#mfs_search") ? $("#mfs_search").value : "").trim();
  const res = await api(`/api/runs/${RUN.id}/students/far?filter_type=${encodeURIComponent(CURRENT_FAR_TAB)}&page=${page}&per_page=50&q=${encodeURIComponent(q)}`);
  if (!res.ok) {
    $("#t_mfs").innerHTML = `<tr><td class="alert">${esc(res.j.error || "فشل تحميل البيانات")}</td></tr>`;
    return;
  }

  const d = res.j;
  const total = d.total || 0;
  const maxKm = d.max_km || 50;
  TOTAL_FAR_PAGES = Math.max(1, Math.ceil(total / d.per_page));

  const cntBadge = $("#mfs_count");
  if (cntBadge) cntBadge.textContent = n(total);

  const pageInfo = $("#mfs_page_info");
  if (pageInfo) pageInfo.textContent = `معروض ${n(d.rows.length)} من إجمالي ${n(total)} طالب · صفحة ${page} من ${TOTAL_FAR_PAGES}`;

  const prevBtn = $("#mfs_prev");
  const nextBtn = $("#mfs_next");
  if (prevBtn) prevBtn.disabled = page <= 1;
  if (nextBtn) nextBtn.disabled = page >= TOTAL_FAR_PAGES;

  const cols = [
    ["row_no", "صف", v => `<b>${esc(v)}</b>`],
    ["nid", "الرقم القومي", v => `<span style="font-family:monospace">${esc(v)}</span>`],
    ["gov", "المحافظة", v => esc(govAr(v))],
    ["area", "المنطقة"],
    ["nearest_center", "أقرب مركز متاح", (v, r) => `
      <div><b>${esc(v)}</b> <span class="small">${esc(r.nearest_name || '')}</span></div>
    `],
    ["distance_km", "المسافة لأقرب مركز", (v, r) => `
      <b style="color:#d92d20;font-size:14px">${v} كم</b>
    `],
    ["over_km", `الزيادة عن (${maxKm} كم)`, (v, r) => `
      <span class="badge ${r.is_suspect ? 'bad' : 'warn'}">+${v} كم</span>
    `],
    ["actions", "إجراءات", (_, r) => `
      <button class="btn sm" onclick="focusFarStudentFromModal('${esc(r.row_no)}', ${r.lat}, ${r.lon}, ${r.nearest_lat}, ${r.nearest_lon}, '${esc(r.nearest_center)}', '${esc(r.nearest_name)}', ${r.distance_km})">🗺️ على الخريطة</button>
      <button class="btn sm sec" onclick="viewStudentRow('${RUN.id}', '${esc(r.row_no)}')">✏️ تعديل</button>
    `]
  ];

  table("#t_mfs", cols, d.rows, { empty: "لا يوجد طلاب مطابقين للبحث أو الفلتر" });
}

function prevFarPage() {
  if (CURRENT_FAR_PAGE > 1) loadFarStudentsPage(CURRENT_FAR_PAGE - 1);
}

function nextFarPage() {
  if (CURRENT_FAR_PAGE < TOTAL_FAR_PAGES) loadFarStudentsPage(CURRENT_FAR_PAGE + 1);
}

function focusFarStudentFromModal(rowNo, sLat, sLon, cLat, cLon, cCode, cName, distKm) {
  closeModal("modal_far_students");
  tab("map");
  if (!MAP) initMap();
  if (sLat && sLon) {
    if (cLat && cLon) {
      connectAndFlyFarStudent(rowNo, sLat, sLon, cLat, cLon, cCode, cName, distKm);
    } else {
      MAP.setView([sLat, sLon], 14);
      L.popup().setLatLng([sLat, sLon]).setContent(`<b>طالب صف ${esc(rowNo)}</b><br>المسافة: ${distKm} كم لأقرب مركز ${esc(cCode)}`).openOn(MAP);
    }
  }
}

init();


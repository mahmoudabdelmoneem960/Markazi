/* ==========================================================================
   Markazi - Wizard & Smart Runner Controller (v4.5)
   ========================================================================== */

let TOKEN = null, PF = null, CURRENT_STEP = 1;

const GEOH = {
  addr: "يحسب المواقع من نصوص العناوين والمناطق بدقة، وهو الوضع الموصى به لشيتاتنا.",
  auto: "يستخدم إحداثيات الشيت إذا كانت موجودة، وإلا يستخرجها من العنوان.",
  sheet: "يستخدم إحداثيات الشيت فقط ولا يحدد أي مواقع مفقودة."
};

function geoHint() {
  const el = $("#geohint");
  if (!el) return;
  el.textContent = GEOH[$("#geo_mode").value] + (HAS_GAZ ? "" : " ⚠ قاعدة الأماكن المحلية غير موجودة: سيتم استخدام مستوى المحافظة.");
}

// Wizard Navigation
function goToStep(s) {
  if (s === 2 && !PF) {
    toast("ارفع الشيتين أو اضغط على عينة سريعة أولاً للفحص.", "bad");
    return;
  }
  if (s === 3 && !PF) {
    toast("يجب إكمال الفحص المسبق أولاً.", "bad");
    return;
  }
  CURRENT_STEP = s;
  
  // Update step panels visibility
  $("#step1").hidden = (s !== 1);
  $("#step2").hidden = (s !== 2);
  $("#step3").hidden = (s !== 3);

  // Update Stepper Nodes
  [1, 2, 3].forEach(i => {
    const node = $(`#node${i}`);
    node.classList.remove("active", "completed");
    if (i < s) node.classList.add("completed");
    if (i === s) node.classList.add("active");
  });

  // Stepper progress line fill
  const fill = $("#stepperFill");
  if (fill) {
    fill.style.width = s === 1 ? "0%" : s === 2 ? "50%" : "100%";
  }

  window.scrollTo({top: 0, behavior: "smooth"});
  if (s === 3 && $("#road_mode").value === "osrm") {
    checkOsrm();
  }
}

// Drag & Drop Handlers
function setupDropzone(id, inputId) {
  const dz = $(id);
  const input = $(inputId);
  if (!dz || !input) return;

  ['dragenter', 'dragover'].forEach(name => {
    dz.addEventListener(name, e => {
      e.preventDefault();
      e.stopPropagation();
      dz.classList.add('dragover');
    });
  });

  ['dragleave', 'drop'].forEach(name => {
    dz.addEventListener(name, e => {
      e.preventDefault();
      e.stopPropagation();
      dz.classList.remove('dragover');
    });
  });

  dz.addEventListener('drop', e => {
    const files = e.dataTransfer.files;
    if (files.length > 0) {
      input.files = files;
      onFileSelected(inputId.replace('#', ''));
    }
  });
}

function onFileSelected(key) {
  const input = $(`#${key}`);
  const badge = $(`#badge_${key}`);
  const nameEl = $(`#name_${key}`);
  if (input && input.files.length > 0) {
    const file = input.files[0];
    const sizeStr = file.size > 1024 * 1024 ? 
      (file.size / (1024 * 1024)).toFixed(1) + " MB" : 
      Math.round(file.size / 1024) + " KB";
    nameEl.textContent = `${file.name} (${sizeStr})`;
    badge.style.display = "flex";
  }
}

// Load Ready Sample in One Click
async function loadSample() {
  const btn = $("#btnSample");
  btn.disabled = true;
  btn.textContent = "⏳ جاري تحميل العينة...";
  $("#umsg").className = "hint";
  $("#umsg").textContent = "جاري استدعاء شيتات العينة التجريبية...";

  const r = await api("/api/runs/sample", {method: "POST"});
  btn.disabled = false;
  btn.textContent = "⚡ تجربة سريعة بعينة جاهزة";

  if (!r.ok) {
    $("#umsg").className = "hint bad";
    $("#umsg").textContent = r.j.error || "تعذر تحميل ملفات العينة";
    toast(r.j.error || "تعذر تحميل العينة", "bad");
    return;
  }

  PF = r.j;
  TOKEN = r.j.token;
  $("#name").value = "تشغيل تجريبي - عينة مصر (600 طالب و12 مركز)";
  
  // Set badges
  $("#name_students").textContent = "students_sample.xlsx (عينة تجريبية)";
  $("#badge_students").style.display = "flex";
  $("#name_centers").textContent = "centers_sample.csv (عينة تجريبية)";
  $("#badge_centers").style.display = "flex";

  renderPreflight(r.j);
  toast("تم تجهيز العينة التجريبية بنجاح!", "ok");
  goToStep(2);
}

// Check OSRM Status
async function checkOsrm() {
  const st = $("#osrm_status");
  const pill = $("#osrm_pill");
  if (st) {
    st.className = "hint";
    st.textContent = "جاري فحص اتصال سيرفر الطرق OSRM...";
  }

  const url = ($("#osrm_url") ? $("#osrm_url").value : SETTINGS.osrm_url || "http://127.0.0.1:5001").trim();
  const r = await api("/api/osrm_check?url=" + encodeURIComponent(url));

  if (r.j.ok) {
    if (st) { st.className = "hint ok"; st.textContent = `✓ OSRM جاهز ومتصل (${r.j.ms} ms)`; }
    if (pill) { pill.className = "pill ok"; pill.textContent = `✓ متصل (${r.j.ms} ms)`; }
    return true;
  } else {
    if (st) {
      st.className = "hint bad";
      st.textContent = `✗ ${r.j.message} — تأكد من تشغيل start_osrm.bat أو اختر «تقدير».`;
    }
    if (pill) { pill.className = "pill bad"; pill.textContent = "✗ غير متصل"; }
    return false;
  }
}

// Upload & Pre-flight
async function upload() {
  const s = $("#students").files[0], c = $("#centers").files[0];
  if (!s || !c) {
    $("#umsg").className = "hint bad";
    $("#umsg").textContent = "يرجى اختيار شيت الطلاب وشيت المراكز معاً للمتابعة.";
    toast("يرجى اختيار الملفين معاً", "bad");
    return;
  }

  const btn = $("#upload");
  btn.disabled = true;
  btn.innerHTML = "<span>⏳ جاري الرفع والفحص...</span>";
  $("#umsg").className = "hint";
  $("#umsg").textContent = "يتم الآن قراءة الأعمدة وفحص البيانات والأرقام القومية...";

  const fd = new FormData();
  fd.append("students", s);
  fd.append("centers", c);

  const r = await api("/api/runs/upload", {method: "POST", body: fd});
  btn.disabled = false;
  btn.innerHTML = "<span>فحص وتجهيز الملفات</span> <span>←</span>";

  if (!r.ok) {
    $("#umsg").className = "hint bad";
    $("#umsg").textContent = r.j.error || "فشل رفع الملفات";
    toast(r.j.error || "فشل الفحص", "bad");
    return;
  }

  $("#umsg").textContent = "";
  PF = r.j;
  TOKEN = r.j.token;
  renderPreflight(r.j);
  toast("تم فحص الشيتين بنجاح!", "ok");
  goToStep(2);
}

function kv(items) {
  return "<table>" + items.map(([k, v, c]) => `<tr><th style="position:static;width:40%">${esc(k)}</th><td class="${c || ""}">${v}</td></tr>`).join("") + "</table>";
}

// Render Preflight Inspection Report
function renderPreflight(p) {
  const s = p.students, c = p.centers;

  // KPI calculations
  $("#kpi_st_count").textContent = n(s.rows);
  $("#kpi_cen_seats").textContent = n(c.seats);
  $("#kpi_cen_count").textContent = `${n(c.centers)} مركز / ${n(c.rows)} جروب`;

  const balanceBox = $("#kpi_balance_box");
  const balanceStat = $("#kpi_balance_stat");
  const ratio = c.seats > 0 ? Math.round((c.seats / (s.rows || 1)) * 100) : 0;
  balanceStat.textContent = `${ratio}%`;
  balanceBox.className = "kpi " + (ratio >= 100 ? "kpi-ok" : "kpi-bad");

  // Status pill
  const pill = $("#pf_status_pill");
  if (pill) {
    pill.className = "pill " + (p.ok ? "ok" : "bad");
    pill.textContent = p.ok ? "✓ الملفات متوافقة وجاهزة للتوزيع" : "⚠ توجد أخطاء يجب معالجتها";
  }

  // Issues
  $("#issues").innerHTML = p.issues.map(i => `
    <div class="issue ${i.level}">
      <span>${i.level === "error" ? "❌" : i.level === "warn" ? "⚠️" : "ℹ️"}</span>
      <span>${esc(i.text)}</span>
    </div>
  `).join("") || `<div class="issue info"><span>✓</span> <span>جميع الأعمدة والأرقام القومية ومطابقة المحافظات سليمة 100%.</span></div>`;

  const lv = Object.entries(s.levels || {}).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${esc(k)} <b>${n(v)}</b>`).join(" · ");
  const gv = Object.entries(s.govs || {}).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${esc(govAr(k))} <b>${n(v)}</b>`).join(" · ");

  $("#pf_students").innerHTML = kv([
    ["عدد صفوف الطلاب", `<b>${n(s.rows)}</b> طالب`],
    ["اسم الملف", esc(p.files.students) + (s.adapted_note ? `<div class="small mute">${esc(s.adapted_note)}</div>` : "")],
    ["أرقام قومية غير صالحة / مكررة", s.nid_invalid == null ? "—" : `${n(s.nid_invalid)} غير صالح / ${n(s.nid_dup)} مكرر`, (s.nid_invalid || s.nid_dup) ? "warn" : "ok"],
    ["محافظات غير معروفة", n(s.unknown_gov_count || 0), s.unknown_gov_count ? "bad" : "ok"],
    ["طلاب بدون ليفيل محدد", n(s.no_level || 0), s.no_level ? "bad" : "ok"],
    ["بدون منطقة / بدون عنوان", `${n(s.no_area)} / ${n(s.no_addr)}`],
    ["إحداثيات متوفرة بالشيت", n(s.with_coords) + (s.coords_suspect && s.coords_suspect.length ? " <span class='warn'>(تقريبية)</span>" : "")],
    ["توزيع الليفيلات", lv || "—"],
    ["توزيع المحافظات", gv || "—"]
  ]);

  const clv = Object.entries(c.levels || {}).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${esc(k)} <b>${n(v)}</b>`).join(" · ");
  const cgv = Object.entries(c.govs || {}).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${esc(govAr(k))} <b>${n(v)}</b>`).join(" · ");

  $("#pf_centers").innerHTML = kv([
    ["المراكز والقاعات", `<b>${n(c.centers)}</b> مركز / <b>${n(c.rows)}</b> جروب`],
    ["اسم الملف", esc(p.files.centers)],
    ["إجمالي المقاعد المتاحة", `<b>${n(c.seats)}</b> مقعد`],
    ["أخطاء أكواد وسعة الجروبات", `${n(c.bad_group_codes)} كود خاطئ / ${n(c.no_capacity)} بدون سعة`, (c.bad_group_codes || c.no_capacity) ? "bad" : "ok"],
    ["مراكز بدون إحداثيات بالشيت", n((c.centers_no_coords || []).length) + ((c.centers_no_coords || []).length ? ` <span class="small mute">(${n(c.no_coords_with_link)} بالرابط، ${n(c.no_coords_with_addr_only)} بالعنوان)</span>` : "")],
    ["مقاعد حسب الليفيل", clv || "—"],
    ["مقاعد حسب المحافظة", cgv || "—"]
  ]);

  // Cross Deficit Table
  $("#pf_cross_wrap").hidden = !(p.cross && p.cross.length);
  table("#pf_cross", [
    ["gov", "المحافظة", v => `<b>${esc(govAr(v))}</b>`],
    ["level_code", "الليفيل والتراك"],
    ["students", "الطلاب المطلوبون", v => n(v)],
    ["seats", "المقاعد المتاحة", v => n(v)],
    ["deficit", "العجز المقدر", v => `<span class="pill bad">-${n(v)} مقعد</span>`]
  ], p.cross || []);

  if (s.with_coords === 0 && $("#geo_mode").value === "sheet") {
    $("#geo_mode").value = "addr";
  }
  geoHint();

  $("#go").disabled = !p.ok;
  $("#smsg").className = "hint " + (p.ok ? "ok" : "bad");
  $("#smsg").textContent = p.ok ? "✓ البيانات جاهزة للانطلاق" : "يجب تصحيح الأخطاء الحمراء في الشيتات أولاً.";
}

// Start Distribution Engine
async function start() {
  if (!TOKEN) {
    toast("لم يتم العثور على جلسة رفع صالحة.", "bad");
    return;
  }

  const params = {
    geo_mode: $("#geo_mode").value,
    road_mode: $("#road_mode").value,
    osrm_url: $("#osrm_url").value.trim(),
    max_km: $("#max_km").value,
    detour: $("#detour").value
  };

  if (params.road_mode === "osrm" && !(await checkOsrm())) {
    $("#smsg").className = "hint bad";
    $("#smsg").textContent = "سيرفر OSRM غير متصل: يرجى تشغيل start_osrm.bat أو اختيار وضع التقدير.";
    toast("سيرفر OSRM غير متصل", "bad");
    return;
  }

  const goBtn = $("#go");
  goBtn.disabled = true;
  goBtn.innerHTML = "<span>⚙️ جاري بدء المحرك...</span>";
  $("#smsg").className = "hint";
  $("#smsg").textContent = "يتم الآن بدء المعالجة في الخلفية...";

  const r = await postJSON("/api/runs", {
    token: TOKEN,
    name: ($("#name").value || "").trim(),
    params
  });

  if (!r.ok) {
    goBtn.disabled = false;
    goBtn.innerHTML = "<span>🚀 ابدأ عملية التوزيع الآن</span>";
    $("#smsg").className = "hint bad";
    $("#smsg").textContent = r.j.error || "تعذر بدء التوزيع";
    toast(r.j.error || "تعذر بدء التوزيع", "bad");
    return;
  }

  toast("تم إطلاق عملية التوزيع بنجاح! جاري الانتقال للوحة المتابعة...", "ok");
  setTimeout(() => {
    location.href = "/runs/" + r.j.run_id;
  }, 600);
}

// Initialization on DOM Ready
document.addEventListener("DOMContentLoaded", () => {
  setupDropzone("#dz_students", "#students");
  setupDropzone("#dz_centers", "#centers");

  const gm = $("#geo_mode");
  if (gm) {
    gm.addEventListener("change", geoHint);
    gm.value = SETTINGS.geo_mode || "addr";
    geoHint();
  }

  const rm = $("#road_mode");
  if (rm) {
    rm.value = SETTINGS.road_mode || "osrm";
    rm.addEventListener("change", () => {
      if (rm.value === "osrm") checkOsrm();
      else {
        const st = $("#osrm_status");
        if (st) st.textContent = "سيتم التقدير الرياضي: مسافة الخط المستقيم × معامل الالتفاف.";
      }
    });
  }

  checkOsrm();
});

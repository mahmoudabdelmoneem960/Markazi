let RUNS = [];
const STATUS = {queued: ["في الانتظار", "warn"], running: ["شغال", "warn"], files: ["بيجهّز الملفات", "warn"], done: ["تم", "ok"], error: ["خطأ", "bad"], cancelled: ["اتلغى", "bad"]};
async function load() {
  const [s, r] = await Promise.all([api("/api/status"), api("/api/runs")]);
  if (s.ok) {
    const l = s.j.locations, k = (v, l, c) => `<div class="kpi"><b class="${c || ""}">${v}</b><span>${l}</span></div>`;
    $("#kpis").innerHTML = k(n(r.ok ? r.j.length : 0), "تشغيل محفوظ") + k(n(l.centers_with_coords) + " / " + n(l.centers), "مراكز ليها موقع") +
      k(n(l.centers_verified), "مراكز متأكد منها", "ok") + k(n(l.overrides), "تصحيحات يدوية") + k(n(l.geo_cache), "عناوين محفوظة") +
      k(l.gazetteer ? l.gazetteer_mb + " ميجا" : "مش موجودة", "قاعدة الأماكن", l.gazetteer ? "" : "bad");
  }
  RUNS = r.ok ? r.j : [];
  draw();
}
function draw() {
  const q = $("#q").value.trim();
  const rows = RUNS.filter(x => !q || (x.name || "").includes(q) || (x.students_file || "").includes(q));
  table("#t", [
    ["created_at", "التاريخ", v => esc(v)],
    ["name", "الاسم", (v, r) => `<a href="/runs/${r.id}"><b>${esc(v || r.students_file)}</b></a><div class="small">${esc(r.students_file)} + ${esc(r.centers_file)}</div>`],
    ["status", "الحالة", v => { const s = STATUS[v] || [v, ""]; return `<span class="pill ${s[1]}">${esc(s[0])}</span>`; }],
    ["summary", "الطلاب", v => v ? n(v.students) : ""],
    ["summary", "موزعين", v => v ? `<span class="ok">${n(v.assigned)}</span>` : ""],
    ["summary", "VP", v => v ? `<span class="bad">${n(v.vp)}</span>` : ""],
    ["summary", "متوسط المسافة", v => v && v.avg_km != null ? fmtKm(v.avg_km) : ""],
    ["summary", "المسافة / المواقع", v => v ? esc(v.distance_label || "") + "<div class='small'>" + esc({auto: "تلقائي", addr: "من العناوين", sheet: "من الشيت"}[v.geo_mode] || v.geo_mode || "") + "</div>" : ""],
    ["id", "", (v, r) => `<a class="btn sec sm" href="/runs/${v}">فتح</a> ` + (r.status === "done" ? `<a class="btn sec sm" href="/api/runs/${v}/file/xlsx">Excel</a> ` : "") +
      `<button class="del sm" data-id="${esc(v)}" onclick="removeRun(this.dataset.id)">حذف</button>`],
  ], rows, {empty: "مفيش تشغيلات لسه. ابدأ من «تشغيل جديد»."});
}
async function removeRun(id) {
  if (!confirm("حذف التشغيل ده وملفاته؟")) return;
  const r = await del("/api/runs/" + id);
  if (!r.ok) { toast(r.j.error || "فشل الحذف", "bad"); return; }
  toast("اتحذف", "ok"); load();
}
/* البحث عن طالب بالرقم القومي أو الكود في كل التشغيلات */
async function searchStudent() {
  const q = $("#sq").value.trim();
  if (!q) { $("#s_wrap").hidden = true; $("#s_msg").textContent = ""; return; }
  $("#s_msg").className = "hint"; $("#s_msg").textContent = "⏳ جاري البحث في كل التشغيلات...";
  const r = await api("/api/runs/search_student?q=" + encodeURIComponent(q));
  if (!r.ok) { $("#s_msg").className = "hint bad"; $("#s_msg").textContent = r.j.error || "فشل البحث"; $("#s_wrap").hidden = true; return; }
  const d = r.j;
  $("#s_msg").className = "hint" + (d.total ? "" : " bad");
  $("#s_msg").textContent = !d.total ? "مفيش طالب بالرقم أو الكود ده في أي تشغيل."
    : `${n(d.total)} نتيجة` + (d.shown < d.total ? ` (ظاهر أول ${n(d.shown)} — اكتب الرقم كامل عشان تضيّق)` : "");
  $("#s_wrap").hidden = !d.total;
  table("#s_t", [
    ["nid", "الرقم القومي", (v, s) => `<b style="direction:ltr;unicode-bidi:embed">${esc(v)}</b>` + (s.code ? `<div class="small mute">كود: ${esc(s.code)}</div>` : "")],
    ["run_name", "التشغيل", (v, s) => `<a href="/runs/${esc(s.run_id)}">${esc(v)}</a><div class="small mute">${esc(s.run_at)}</div>`],
    ["row_no", "صف"],
    ["gov", "المحافظة", (v, s) => esc(v) + (s.Area ? `<div class="small mute">${esc(s.Area)}</div>` : "")],
    ["Level(25-26)", "الليفيل"],
    ["status", "الحالة", v => `<span class="pill ${v === "VP" ? "bad" : "ok"}">${esc(v)}</span>`],
    ["center_code", "المركز", (v, s) => v ? `<b>${esc(v)}</b><div class="small">${esc(s.center_name || "")}</div>`
      : (s.nearest_center ? `<span class="mute">أقرب مركز: ${esc(s.nearest_center)}</span>` : "—")],
    ["group_code", "الجروب", (v, s) => v ? `<b>${esc(v)}</b>` + ((s.day || s.time) ? `<div class="small mute">${esc([s.day, s.time].filter(Boolean).join(" · "))}</div>` : "") : "—"],
    ["distance_km", "المسافة", v => v ? fmtKm(+v) : "—"],
    ["run_id", "", (v, s) => `<a class="btn sec sm" href="/runs/${esc(v)}?student=${encodeURIComponent(s.row_no)}">فتح</a>`],
  ], d.rows, {empty: "", rowAttr: s => (s.exact ? "" : 'style="opacity:.8"')});
}
load();

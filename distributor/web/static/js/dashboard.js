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
load();

let CENTERS = [], MAP = null, markers = {}, SEL = null, gTimer = null, gJob = null;
const GV = {same_current: "جوجل بيأكد الموقع الحالي", same_link: "جوجل بيأكد موقع اللينك", fill: "جوجل حدد موقع كان ناقص", differs: "جوجل مختلف عن الشيت واللينك",
  conflict: "نتيجتين من جوجل مختلفين", approx: "جوجل تقريبي", approx_far: "جوجل تقريبي وبعيد", none: "جوجل ملقاش نتيجة"};
const srcLabel = c => ({manual: "يدوي", sheet: "شيت", gmaps_link: "لينك Google", geocoded: "من العنوان", google: "Google API"}[c.source] || c.source || "—");
const kmJS = (a, b, c, d) => Math.hypot((c - a) * 110.57, (d - b) * 111.32 * Math.cos((a + c) / 2 * Math.PI / 180));

async function status() {
  const r = await api("/api/status"); if (!r.ok) return;
  const s = r.j.locations, k = (v, l, c) => `<div class="kpi"><b class="${c || ""}">${v}</b><span>${l}</span></div>`;
  $("#kpis").innerHTML = k(n(s.centers_with_coords) + " / " + n(s.centers), "مراكز ليها موقع") + k(n(s.centers_verified), "مراكز متأكد منها", "ok") +
    k(n(s.overrides), "تصحيحات يدوية") + k(n(s.geo_cache), "عناوين محفوظة") + k(s.gazetteer ? s.gazetteer_mb + " ميجا" : "مش موجودة", "قاعدة الأماكن", s.gazetteer ? "" : "bad");
}
async function load() { const r = await api("/api/centers"); if (r.ok) { CENTERS = r.j; draw(); drawMap(); } }
function googleCell(c) {
  if (!c.google_verdict) return "";
  const v = c.google_verdict, review = ["differs", "approx_far", "conflict"].includes(v);
  const gap = (c.lat != null && c.google_lat != null) ? kmJS(c.lat, c.lon, c.google_lat, c.google_lon) * 1000 : null;
  const applied = review && gap != null && gap <= 150, stale = ["same_current", "same_link", "fill"].includes(v) && gap != null && gap > 300;
  const cls = applied ? "ok" : stale ? "warn" : ({same_current: "ok", same_link: "ok", fill: "ok", differs: "bad", conflict: "warn", approx_far: "warn"}[v] || "");
  let h = `<span class="${cls}">${esc(applied ? "الموقع دلوقتي = موقع جوجل" : stale ? "الموقع اتعدّل بعد تأكيد جوجل" : (GV[v] || v))}</span>`;
  if (c.google_lat != null) { h += ` <a href="https://www.google.com/maps?q=${c.google_lat},${c.google_lon}" target="_blank">موقع جوجل</a>`;
    if (gap != null) h += ` <span class="small">(${fmtM(gap)})</span>`;
    if ((review && !applied) || stale) h += ` <button class="sec sm" data-code="${esc(c.code)}" onclick="useGoogle(this.dataset.code)">استخدمه</button>`; }
  return h;
}
function draw() {
  const q = $("#cq").value.trim(), fl = $("#cflag").checked;
  const rows = CENTERS.filter(c => (!q || [c.code, c.name, c.gov, c.area, govAr(c.gov)].some(x => String(x || "").includes(q))) && (!fl || c.flags));
  table("#ct", [
    ["code", "الكود", (v, c) => `<b>${esc(v)}</b><div class="small">${esc(c.name)}</div>`],
    ["gov", "المحافظة", (v, c) => esc(govAr(v)) + `<div class="small">${esc(c.area)}</div>`],
    ["lat", "الموقع", (v, c) => c.lat != null ? `<a href="https://www.google.com/maps?q=${c.lat},${c.lon}" target="_blank">${c.lat.toFixed(5)}, ${c.lon.toFixed(5)}</a>` : '<span class="bad">ناقص</span>'],
    ["source", "المصدر", (v, c) => srcLabel(c) + (c.verified ? ' <span class="pill ok">متأكد</span>' : "")],
    ["link_diff_m", "لينك Google", (v, c) => c.link_lat != null ? ((v != null ? `<span class="${v <= 150 ? "ok" : v <= 500 ? "warn" : "bad"}">${fmtM(v)}</span> ` : '<span class="warn">مفيش موقع حالي</span> ') +
      `<a href="https://www.google.com/maps?q=${c.link_lat},${c.link_lon}" target="_blank">موقع اللينك</a>` +
      ((v == null || v > 150) ? ` <button class="sec sm" data-code="${esc(c.code)}" onclick="useLink(this.dataset.code)">استخدمه</button>` : "") +
      (c.link_name ? `<div class="small">${esc(c.link_name)}</div>` : "")) : ""],
    ["google_verdict", "Google API", (v, c) => googleCell(c)],
    ["flags", "تحذيرات", v => `<span class="warn">${esc(v || "")}</span>`],
  ], rows, {rowAttr: (c) => `class="click${SEL === c.code ? " sel" : ""}" data-code="${esc(c.code)}" onclick="select(this.dataset.code)"`});
}
function drawMap() {
  if (typeof L === "undefined") return;
  if (!MAP) MAP = makeMap("map");
  Object.values(markers).forEach(m => m.remove()); markers = {};
  CENTERS.forEach(c => {
    if (c.lat == null) return;
    const m = L.marker([c.lat, c.lon], {draggable: true, title: c.code}).addTo(MAP);
    m.bindPopup(`<b>${esc(c.code)}</b> ${esc(c.name)}<br>${esc(govAr(c.gov))} / ${esc(c.area)}<br>${srcLabel(c)}${c.verified ? " · متأكد" : ""}<br><span class="small">اسحب الدبوس لتعديل الموقع</span>`);
    m.on("click", () => select(c.code, false));
    m.on("dragend", async (e) => {
      const p = e.target.getLatLng();
      if (!confirm(`تغيير موقع ${c.code} للنقطة الجديدة (${p.lat.toFixed(5)}, ${p.lng.toFixed(5)})؟`)) { e.target.setLatLng([c.lat, c.lon]); return; }
      await saveCenter(c.code, `${p.lat.toFixed(6)}, ${p.lng.toFixed(6)}`, "سحب على الخريطة");
    });
    markers[c.code] = m;
  });
  fitTo(MAP, CENTERS);
}
function select(code, fly = true) {
  SEL = code; draw(); $("#csave").disabled = false;
  const c = CENTERS.find(x => x.code === code); if (!c) return;
  $("#sel").innerHTML = `المختار: <b>${esc(c.code)}</b> ${esc(c.name)} — ${esc(govAr(c.gov))} / ${esc(c.area)}` + (c.note ? `<div class="small">${esc(c.note)}</div>` : "");
  if (MAP && c.lat != null && fly) { MAP.flyTo([c.lat, c.lon], Math.max(MAP.getZoom(), 14)); markers[c.code] && markers[c.code].openPopup(); }
}
async function saveCenter(code, input, note) {
  const r = await postJSON("/api/centers/" + encodeURIComponent(code), {input, note});
  if (!r.ok) { toast(r.j.error || "فشل الحفظ", "bad"); await load(); return false; }
  toast("اتحفظ" + (r.j.flags && r.j.flags.length ? " — تحذير: " + r.j.flags.join("، ") : ""), r.j.flags && r.j.flags.length ? "" : "ok");
  await load(); status(); return true;
}
async function saveSel() { if (!SEL) return; const ok = await saveCenter(SEL, $("#ci").value, null); if (ok) $("#ci").value = ""; }
async function useLink(code) { const c = CENTERS.find(x => x.code === code); if (!c || c.link_lat == null) return;
  if (!confirm(`${c.lat == null ? "تحديد" : "تغيير"} موقع ${code} لموقع اللينك (${c.link_lat.toFixed(5)}, ${c.link_lon.toFixed(5)})؟`)) return;
  await saveCenter(code, c.link_lat + ", " + c.link_lon, "من لينك Google"); }
async function useGoogle(code) { const c = CENTERS.find(x => x.code === code); if (!c || c.google_lat == null) return;
  if (!confirm(`${c.lat == null ? "تحديد" : "تغيير"} موقع ${code} لموقع جوجل (${c.google_lat.toFixed(5)}, ${c.google_lon.toFixed(5)})؟`)) return;
  await saveCenter(code, c.google_lat + ", " + c.google_lon, "من Google API"); }
async function doImport() {
  const f = $("#imp").files[0]; if (!f) { $("#cmsg").className = "hint bad"; $("#cmsg").textContent = "اختار ملف الأول."; return; }
  const fd = new FormData(); fd.append("file", f); $("#cmsg").className = "hint"; $("#cmsg").textContent = "جاري الاستيراد...";
  const r = await api("/api/centers/import", {method: "POST", body: fd});
  $("#cmsg").className = r.ok ? "hint ok" : "hint bad";
  $("#cmsg").textContent = r.ok ? `تم: ${r.j.new} مركز جديد، ${r.j.updated_coords} موقع اتحدّث، ${r.j.kept_manual} تصحيح اتحافظ عليه، ${r.j.no_coords} من غير موقع.` : r.j.error;
  load(); status();
}
async function verifyLinks() {
  const m = $("#cmsg"); $("#vbtn").disabled = true; m.className = "hint"; m.textContent = "بدء الفحص...";
  const r = await postJSON("/api/centers/verify_links");
  if (!r.ok) { $("#vbtn").disabled = false; m.className = "hint bad"; m.textContent = r.j.error; return; }
  pollJob(r.j.id, {onTick: j => { m.textContent = (j.message || "") + " (" + (j.progress || 0) + "%)"; },
    onDone: j => { $("#vbtn").disabled = false; const x = j.result || {}, nc = (x.no_coords || []).length; m.className = "hint " + ((x.mismatch || []).length ? "warn" : "ok");
      m.textContent = `اتقرا ${x.checked} من ${x.of} لينك` + (nc ? `، و${nc} لينك مفيهوش إحداثيات واضحة` : "") + ". " +
        ((x.mismatch || []).length ? `${x.mismatch.length} مركز موقعه مختلف عن اللينك بأكتر من 150 م، أكبرهم: ` + x.mismatch.slice(0, 8).map(q => q.code + (q.diff_m != null ? " (" + fmtM(q.diff_m) + ")" : "")).join("، ") : "كلهم متطابقين مع اللينكات ✓");
      load(); status(); },
    onError: j => { $("#vbtn").disabled = false; m.className = "hint bad"; m.textContent = j.error || "اتلغى"; load(); }});
}
async function googleStatus() {
  const r = await api("/api/google/status"); if (!r.ok) return; const s = r.j, m = $("#gmsg");
  m.className = "hint " + (s.key ? "" : "warn");
  m.textContent = s.key ? `مفتاح جوجل موجود. اتبعت الشهر ده ${n(s.used)} طلب من حد ${n(s.cap)} (الحد المجاني 10,000).`
    : (s.key_problem || "مفيش مفتاح Google API (اختياري): لو حطيته في data\\google_key.txt هيشتغل زر «تحقق بـ Google API».");
}
function gSummary(x) {
  const v = x.verdicts || {}, ap = x.applied || [], rv = x.review || [];
  return `اتفحص ${x.checked || 0} مركز (${x.requests || 0} طلب). جوجل أكد ${(v.same_current || 0) + (v.same_link || 0)} مركز، واتطبّق تغيير موقع على ${ap.length}` + (ap.length ? " (" + ap.map(a => a.code).join("، ") + ")" : "") + ". " +
    (rv.length ? `${rv.length} مركز محتاج مراجعتك: ` + rv.slice(0, 10).map(a => a.code).join("، ") + ". شوف عمود Google API." : "مفيش مراكز محتاجة مراجعة ✓") + ((x.none || []).length ? ` وجوجل ملقاش ${x.none.length} مركز.` : "");
}
async function googleCancel() { if (gJob) await postJSON("/api/jobs/" + gJob + "/cancel"); }
async function googleVerify() {
  const scope = $("#gscope").value, m = $("#gres"); $("#gbtn").disabled = true; m.className = "hint"; m.textContent = "بجهّز...";
  const p = await postJSON("/api/google/verify", {scope, dry_run: true});
  if (!p.ok || !p.j.key) { $("#gbtn").disabled = false; m.className = "hint bad"; m.textContent = p.j.key_problem || p.j.error || "مفيش مفتاح جوجل: حط المفتاح في data\\google_key.txt وأعد تشغيل السيستم."; return; }
  if (!p.j.centers) { $("#gbtn").disabled = false; m.className = "hint warn"; m.textContent = "مفيش مراكز تتفحص (كلهم متأكد منهم)."; return; }
  if (!confirm(`هيتبعت ${p.j.requests} طلب لجوجل لـ${p.j.centers} مركز (اسم المركز وعنوانه بس).\nاتبعت الشهر ده ${p.j.used} من حد ${p.j.cap}.\nنكمل؟`)) { $("#gbtn").disabled = false; m.textContent = ""; return; }
  const r = await postJSON("/api/google/verify", {scope, run: true});
  if (!r.ok) { $("#gbtn").disabled = false; m.className = "hint bad"; m.textContent = r.j.error || "تعذر البدء"; return; }
  gJob = r.j.id; $("#gcancel").hidden = false;
  const done = () => { $("#gbtn").disabled = false; $("#gcancel").hidden = true; gJob = null; load(); status(); googleStatus(); };
  pollJob(gJob, {onTick: j => { m.className = "hint"; m.textContent = (j.message || "") + " (" + (j.progress || 0) + "%)"; },
    onDone: j => { const x = j.result || {}; m.className = "hint " + ((x.review || []).length ? "warn" : "ok"); m.textContent = gSummary(x); done(); },
    onError: j => { const x = j.result; m.className = "hint bad"; m.textContent = (j.error || "اتلغى") + (x && x.checked ? " — اتفحص " + x.checked + " مركز قبل ما يقف: " + gSummary(x) : ""); done(); }});
}
status(); load(); googleStatus();

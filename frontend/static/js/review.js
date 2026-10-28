let ITEMS = [], SEL = null, MAP = null, curM = null, newM = null, limit = 200, runId = RUN_ID || "";
async function loadRuns() {
  const r = await api("/api/runs"); if (!r.ok) return;
  const done = r.j.filter(x => x.status === "done");
  $("#rrun").innerHTML = done.map(x => `<option value="${esc(x.id)}">${esc(x.created_at)} — ${esc(x.name || x.students_file)}</option>`).join("") || "<option value=''>مفيش تشغيلات</option>";
  if (runId && done.some(x => x.id === runId)) $("#rrun").value = runId; else runId = $("#rrun").value;
}
async function loadReview() {
  runId = $("#rrun").value || runId;
  const q = encodeURIComponent($("#rq").value.trim()), mn = $("#rn").value || 1;
  const r = await api(`/api/review?run=${runId}&q=${q}&min_n=${mn}&limit=${limit}`);
  if (!r.ok || !r.j.run) { $("#rk").innerHTML = '<span class="hint">مفيش تشغيل لسه. شغّل التوزيع الأول.</span>'; $("#rt").innerHTML = ""; return; }
  const j = r.j, k = (v, l, c) => `<div class="kpi"><b class="${c || ""}">${v}</b><span>${l}</span></div>`;
  $("#rk").innerHTML = k(n(j.total_low), "طالب موقعهم تقريبي", "warn") + k(n(j.groups_total), "مجموعة (حي/شارع)") + k(n(j.empty_low), "من غير عنوان (مش قابلين للتصحيح)") + k(n(j.matching), "مطابق للفلتر");
  ITEMS = j.items;
  table("#rt", [
    ["gov", "المحافظة", (v, x) => esc(govAr(v)) + `<div class="small">${esc(x.area)}</div>`],
    ["text", "الحي/الشارع", (v, x) => `<b>${esc(v)}</b><div class="small">${esc(x.sample)}</div>`],
    ["n", "طلاب"], ["n_vp", "VP", v => `<span class="${v ? "bad" : ""}">${n(v || 0)}</span>`],
    ["level", "الدقة", (v, x) => esc(v) + ` <span class="small">(${x.conf})</span>`],
    ["fixed", "", v => v ? '<span class="ok">✓ متصحّح</span>' : ""],
  ], ITEMS, {rowAttr: (x, i) => `class="click${SEL === i ? " sel" : ""}" data-i="${i}" onclick="select(+this.dataset.i)"`});
}
function more() { limit += 300; loadReview(); }
function initMap() { if (MAP || typeof L === "undefined") return; MAP = makeMap("map"); MAP.on("click", e => placeNew(e.latlng.lat, e.latlng.lng)); }
function placeNew(lat, lon) {
  if (SEL == null) return;
  if (newM) newM.remove();
  newM = L.marker([lat, lon], {draggable: true}).addTo(MAP).bindPopup("الموقع الجديد (اسحبه لو عايز)").openPopup();
  newM.on("dragend", e => { const p = e.target.getLatLng(); $("#gi").value = `${p.lat.toFixed(6)}, ${p.lng.toFixed(6)}`; });
  $("#gi").value = `${lat.toFixed(6)}, ${lon.toFixed(6)}`;
}
function select(i) {
  SEL = i; const x = ITEMS[i]; loadReviewTable();
  $("#gsave").disabled = false; $("#gdel").hidden = !x.fixed; $("#gi").value = ""; $("#gm").textContent = "";
  $("#sel").innerHTML = `<b>${esc(x.text)}</b> — ${esc(govAr(x.gov))} / ${esc(x.area)} · ${n(x.n)} طالب (${n(x.n_vp || 0)} VP) · الدقة الحالية: ${esc(x.level)} · مثال: <span class="small">${esc(x.sample)}</span>` +
    (x.fixed ? `<div class="ok">متصحّح يدويًا إلى ${x.fixed_lat.toFixed(5)}, ${x.fixed_lon.toFixed(5)}</div>` : "") +
    `<div><a href="https://www.google.com/maps?q=${x.lat},${x.lon}" target="_blank">افتح الموقع الحالي في خرائط جوجل</a></div>`;
  initMap(); if (!MAP) return;
  if (curM) curM.remove(); if (newM) { newM.remove(); newM = null; }
  const la = x.fixed ? x.fixed_lat : x.lat, lo = x.fixed ? x.fixed_lon : x.lon;
  curM = L.circleMarker([la, lo], {radius: 10, color: "#2457d6", fillColor: "#6c93ff", fillOpacity: .8}).addTo(MAP).bindPopup("الموقع الحالي للمجموعة");
  MAP.flyTo([la, lo], 14);
}
function loadReviewTable() { $$("#rt tr.click").forEach(tr => tr.classList.toggle("sel", +tr.dataset.i === SEL)); }
async function saveSel() {
  if (SEL == null) return; const x = ITEMS[SEL], m = $("#gm"); m.className = "msg"; m.textContent = "...";
  const r = await postJSON("/api/review/group", {gkey: x.gkey, gov: x.gov, area: x.area, text: x.text, input: $("#gi").value});
  if (!r.ok) { m.className = "msg bad"; m.textContent = r.j.error; return; }
  m.className = "msg ok"; m.textContent = `اتحفظ لـ ${n(x.n)} طالب (هيتطبق في التوزيع الجاي)`; toast("اتحفظ", "ok");
  await loadReview(); select(SEL);
}
async function delSel() {
  if (SEL == null) return; const x = ITEMS[SEL];
  if (!confirm("إلغاء التصحيح اليدوي للمجموعة دي؟")) return;
  await postJSON("/api/review/group/delete", {gkey: x.gkey}); await loadReview(); select(SEL);
}
async function saveStudent() {
  const m = $("#smsg"); m.className = "msg"; m.textContent = "...";
  const r = await postJSON("/api/review/student", {nid: $("#sn").value.trim(), input: $("#si").value});
  m.className = "msg " + (r.ok ? "ok" : "bad"); m.textContent = r.ok ? "اتحفظ (هيتطبق في التوزيع الجاي)" : r.j.error;
}
(async () => { await loadRuns(); await loadReview(); initMap(); })();

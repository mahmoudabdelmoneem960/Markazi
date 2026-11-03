/* مراجعة مواقع المراكز: لينك الشيت مقابل العنوان ← المستخدم يختار الصح ويحفظه.
   بيعتمد على centers.js (CENTERS, MAP, load, status, select). */
const RV_ST = {
  far: {label: "اللينك بعيد عن العنوان", cls: "bad", icon: "⚠"},
  link_unreadable: {label: "اللينك مش بيتقري", cls: "bad", icon: "⛔"},
  no_link: {label: "مفيش لينك", cls: "warn", icon: "∅"},
  vague: {label: "العنوان مش كفاية للحكم", cls: "warn", icon: "❓"},
  pending: {label: "لسه ما اتفحصش", cls: "", icon: "…"},
  ok: {label: "اللينك قريب من العنوان", cls: "ok", icon: "✓"},
  confirmed: {label: "متأكد (اتراجع)", cls: "ok", icon: "✔"},
};
const LEVEL_AR = {poi: "معلم", street: "شارع", locality: "حي/قرية", area: "منطقة", markaz: "مركز", gov: "محافظة بس", none: "مش لاقيه"};
let RV_FILTER = "need", RV_CODE = null, RV_PREVIEW = null, RV_LAYER = null, rvJob = null;
const RV_NEED = ["far", "link_unreadable", "no_link", "vague"];

function reviewRender() {
  const counts = {};
  CENTERS.forEach(c => { counts[c.status] = (counts[c.status] || 0) + 1; });
  const need = RV_NEED.reduce((s, k) => s + (counts[k] || 0), 0);
  const chip = (key, label, cnt, cls) => `<button class="btn sm ${RV_FILTER === key ? "" : "sec"}" onclick="RV_FILTER='${key}';reviewRender()">${label} <span class="pill ${cls}" style="margin-right:4px">${n(cnt)}</span></button>`;
  $("#rv_chips").innerHTML = chip("need", "محتاج مراجعة", need, need ? "bad" : "ok") +
    Object.keys(RV_ST).filter(k => counts[k]).map(k => chip(k, RV_ST[k].icon + " " + RV_ST[k].label, counts[k], RV_ST[k].cls)).join("") +
    chip("all", "الكل", CENTERS.length, "");
  if (counts.pending && !rvJob) {
    $("#rvmsg").className = "hint warn";
    $("#rvmsg").textContent = `${n(counts.pending)} مركز لسه ما اتفحصش: دوس «فحص المواقع».`;
  }
  const order = Object.keys(RV_ST);
  const rows = CENTERS.filter(c => RV_FILTER === "all" || (RV_FILTER === "need" ? RV_NEED.includes(c.status) : c.status === RV_FILTER))
    .sort((a, b) => order.indexOf(a.status) - order.indexOf(b.status) || (b.addr_gap_km || 0) - (a.addr_gap_km || 0));
  table("#rv_t", [
    ["code", "المركز", (v, c) => `<b>${esc(v)}</b><div class="small">${esc(c.name)}</div>`],
    ["gov", "المحافظة / المنطقة", (v, c) => esc(govAr(v)) + `<div class="small">${esc(c.area)}</div>`],
    ["address", "العنوان", v => `<span class="small">${esc(v || "—")}</span>`],
    ["status", "الحالة", (v, c) => `<span class="pill ${RV_ST[v].cls}">${RV_ST[v].icon} ${esc(RV_ST[v].label)}</span>` +
      (c.addr_gap_km != null ? `<div class="small">${c.addr_gap_km} كم عن العنوان` + (c.tolerance_km ? ` (المسموح ${c.tolerance_km})` : "") + `</div>` : "")],
    ["gmaps_url", "لينك الشيت", (v, c) => v ? `<a href="${esc(v)}" target="_blank" rel="noopener">فتح ↗</a>` + (c.link_name ? `<div class="small">${esc(c.link_name)}</div>` : "") : "<span class='mute'>—</span>"],
    ["addr_level", "دقة العنوان", v => v ? esc(LEVEL_AR[v] || v) : "—"],
    ["code", "", (v) => `<button class="btn sm" data-code="${esc(v)}" onclick="reviewOpen(this.dataset.code)">مراجعة</button>`],
  ], rows, {empty: RV_FILTER === "need" ? "مفيش مراكز محتاجة مراجعة ✓" : "مفيش",
    rowAttr: c => RV_CODE === c.code ? 'style="background:rgba(99,102,241,.10)"' : ""});
  if (RV_CODE) reviewPanel();
}

function gSearchUrl(c) {
  const q = [c.name, c.address, c.area, govAr(c.gov)].filter(Boolean).join("، ");
  return "https://www.google.com/maps/search/?api=1&query=" + encodeURIComponent(q);
}
const mapsAt = (lat, lon) => `https://www.google.com/maps?q=${lat},${lon}`;

function reviewOpen(code) {
  RV_CODE = code; RV_PREVIEW = null;
  reviewRender();
  $("#rv_panel").scrollIntoView({behavior: "smooth", block: "nearest"});
}
function reviewClose() { RV_CODE = null; RV_PREVIEW = null; $("#rv_panel").hidden = true; reviewMap(null); reviewRender(); }

function reviewPanel() {
  const c = CENTERS.find(x => x.code === RV_CODE);
  if (!c) { $("#rv_panel").hidden = true; return; }
  const st = RV_ST[c.status];
  const opt = (title, body, cls) => `<div class="card" style="margin:0;padding:12px 14px;flex:1;min-width:260px;${cls ? "border-color:var(--" + cls + ")" : ""}"><b>${title}</b><div style="margin-top:6px">${body}</div></div>`;
  const linkBox = c.link_lat != null
    ? `<div class="small">${c.link_lat.toFixed(5)}, ${c.link_lon.toFixed(5)}${c.link_name ? " — " + esc(c.link_name) : ""}</div>
       ${c.addr_gap_km != null ? `<div class="small ${c.status === "far" ? "bad" : "ok"}">بعيد ${c.addr_gap_km} كم عن تقدير العنوان</div>` : ""}
       <div class="row" style="gap:6px;margin-top:8px"><a class="btn sm sec" href="${esc(c.gmaps_url)}" target="_blank" rel="noopener">فتح ↗</a>
       <button class="btn sm ok" onclick="reviewSave(${c.link_lat}, ${c.link_lon}, CENTERS.find(x=>x.code===RV_CODE).gmaps_url, 'لينك الشيت صح')">✓ اللينك ده صح — احفظه</button></div>`
    : `<div class="small bad">${c.gmaps_url ? "اللينك ده مش بيدّي إحداثيات (ممكن يكون لينك بحث أو صفحة مش مكان)." : "مفيش لينك في الشيت."}</div>` +
      (c.gmaps_url ? `<a class="btn sm sec" style="margin-top:8px" href="${esc(c.gmaps_url)}" target="_blank" rel="noopener">فتح ↗</a>` : "");
  const addrBox = c.addr_lat != null
    ? `<div class="small">${c.addr_lat.toFixed(5)}, ${c.addr_lon.toFixed(5)} — الدقة: <b>${esc(LEVEL_AR[c.addr_level] || c.addr_level)}</b> (± ${c.tolerance_km} كم)</div>
       ${c.addr_matched ? `<div class="small mute">اتطابق على: ${esc(c.addr_matched)}</div>` : ""}
       <div class="row" style="gap:6px;margin-top:8px"><a class="btn sm sec" href="${mapsAt(c.addr_lat, c.addr_lon)}" target="_blank" rel="noopener">فتح ↗</a>
       <button class="btn sm sec" onclick="reviewSave(${c.addr_lat}, ${c.addr_lon}, '', 'تقدير من العنوان (تقريبي)')" title="تقريبي — الأفضل تجيب اللينك من جوجل">استخدمه (تقريبي)</button></div>`
    : `<div class="small warn">${c.address ? "العنوان ما اتعرفش بدقة كفاية (" + esc(LEVEL_AR[c.addr_level] || "—") + ")." : "مفيش عنوان للمركز في الشيت."}</div>`;
  const pv = RV_PREVIEW;
  const pvHtml = !pv ? "" : `<div style="margin-top:8px;padding:8px 10px;border-radius:8px;background:rgba(22,121,76,.08)">
      <div class="small"><b>${pv.lat.toFixed(5)}, ${pv.lon.toFixed(5)}</b>${pv.place_name ? " — " + esc(pv.place_name) : ""}</div>
      <div class="small">${pv.from_address_km != null ? `بعيد <b class="${pv.tolerance_km && pv.from_address_km > pv.tolerance_km ? "bad" : "ok"}">${pv.from_address_km} كم</b> عن تقدير العنوان · ` : ""}${pv.from_link_km != null ? `${pv.from_link_km} كم عن لينك الشيت · ` : ""}${pv.from_current_km != null ? `${pv.from_current_km} كم عن الموقع الحالي` : ""}</div>
      ${pv.tolerance_km && pv.from_address_km > pv.tolerance_km ? `<div class="small bad">⚠ بعيد عن العنوان — اتأكد إنه المكان الصح.</div>` : ""}
      <button class="btn sm ok" style="margin-top:6px" onclick="reviewSave(RV_PREVIEW.lat, RV_PREVIEW.lon, RV_PREVIEW.url, 'لينك جديد من Google Maps')">💾 احفظ الموقع ده للمركز</button></div>`;
  const googleBox = `<div class="small">دوّر على المركز بعنوانه، افتح المكان الصح، وانسخ اللينك (Share ← Copy link) والصقه هنا:</div>
      <div class="row" style="gap:6px;margin-top:8px"><a class="btn sm" href="${gSearchUrl(c)}" target="_blank" rel="noopener">🔎 دوّر في Google Maps</a></div>
      <div class="row" style="gap:6px;margin-top:8px"><input type="text" id="rv_in" class="inline" style="flex:1;min-width:200px" placeholder="الصق اللينك الصح أو lat, lon">
      <button class="btn sm sec" onclick="reviewPreview()">معاينة</button></div><div class="small" id="rv_pmsg"></div>` + pvHtml;
  $("#rv_panel").hidden = false;
  $("#rv_panel").innerHTML = `
    <div class="row" style="justify-content:space-between;margin-bottom:10px">
      <div><b>${esc(c.code)}</b> — ${esc(c.name)} · ${esc(govAr(c.gov))} / ${esc(c.area)}
        <span class="pill ${st.cls}">${st.icon} ${esc(st.label)}</span>
        <div class="small">${esc(c.address || "")}</div>
        ${c.status === "confirmed" ? `<div class="small ok">✔ اتراجع${c.review_by ? " بواسطة " + esc(c.review_by) : ""} ${esc(c.review_at || "")} — اللينك المعتمد: <a href="${esc(c.correct_url || "")}" target="_blank" rel="noopener">فتح ↗</a></div>` : ""}
      </div>
      <div class="row" style="gap:6px">
        ${c.status === "confirmed" ? `<button class="btn sm sec" onclick="reviewReopen()">↺ رجّعه للمراجعة</button>` : ""}
        ${c.lat != null && c.status !== "confirmed" ? `<button class="btn sm sec" onclick="reviewSave(${c.lat}, ${c.lon}, '', 'الموقع الحالي صح')" title="${c.lat.toFixed(5)}, ${c.lon.toFixed(5)}">✓ الموقع الحالي صح</button>` : ""}
        <button class="btn sm sec" onclick="reviewClose()">✕ قفل</button>
      </div>
    </div>
    <div class="row" style="gap:10px;align-items:stretch;flex-wrap:wrap">
      ${opt("🔴 لينك الشيت", linkBox, c.status === "far" || c.status === "link_unreadable" ? "bad" : "")}
      ${opt("🟢 تقدير من العنوان", addrBox)}
      ${opt("🔎 هات اللينك الصح من Google Maps", googleBox, "brand")}
    </div>
    <div class="small mute" style="margin-top:6px">على الخريطة تحت: 🔴 لينك الشيت · 🟢 تقدير العنوان (والدايرة = المسافة المسموحة) · 🔵 الموقع الحالي · 🟣 المعاينة.</div>`;
  if (pv) $("#rv_in").value = pv.url;
  reviewMap(c);
}

/* نقط المراجعة على خريطة الصفحة */
function reviewMap(c) {
  if (typeof L === "undefined" || !MAP) return;
  if (RV_LAYER) { RV_LAYER.remove(); RV_LAYER = null; }
  if (!c) return;
  RV_LAYER = L.layerGroup().addTo(MAP);
  const pts = [];
  const dot = (lat, lon, color, tip) => { L.circleMarker([lat, lon], {radius: 9, color, fillColor: color, fillOpacity: .85, weight: 2}).bindTooltip(tip).addTo(RV_LAYER); pts.push([lat, lon]); };
  if (c.addr_lat != null) {
    if (c.tolerance_km) L.circle([c.addr_lat, c.addr_lon], {radius: c.tolerance_km * 1000, color: "#16794c", weight: 1, fillOpacity: .06}).addTo(RV_LAYER);
    dot(c.addr_lat, c.addr_lon, "#16794c", "تقدير من العنوان");
  }
  if (c.link_lat != null) dot(c.link_lat, c.link_lon, "#b42318", "لينك الشيت");
  if (c.lat != null) dot(c.lat, c.lon, "#2563eb", "الموقع الحالي");
  if (RV_PREVIEW) dot(RV_PREVIEW.lat, RV_PREVIEW.lon, "#7c3aed", "المعاينة");
  if (pts.length === 1) MAP.setView(pts[0], 15);
  else if (pts.length) MAP.fitBounds(pts, {padding: [40, 40], maxZoom: 16});
}

async function reviewPreview() {
  const v = $("#rv_in").value.trim();
  if (!v) { $("#rv_pmsg").className = "small bad"; $("#rv_pmsg").textContent = "الصق اللينك الأول."; return; }
  $("#rv_pmsg").className = "small"; $("#rv_pmsg").textContent = "⏳ بقرا اللينك...";
  const r = await postJSON(`/api/centers/${encodeURIComponent(RV_CODE)}/preview`, {input: v});
  if (!r.ok) { $("#rv_pmsg").className = "small bad"; $("#rv_pmsg").textContent = r.j.error || "تعذر قراءة اللينك"; return; }
  RV_PREVIEW = r.j;
  reviewPanel();
}

async function reviewSave(lat, lon, url, how) {
  const c = CENTERS.find(x => x.code === RV_CODE); if (!c) return;
  if (!confirm(`اعتماد موقع ${c.code}:\n${how}\n(${Number(lat).toFixed(5)}, ${Number(lon).toFixed(5)})\n\nهيتحفظ كتصحيح يدوي وما يتغيرش لما ترفع الشيت تاني. نكمل؟`)) return;
  const r = await postJSON(`/api/centers/${encodeURIComponent(c.code)}/confirm`, {lat, lon, url: url || "", how});
  if (!r.ok) { toast(r.j.error || "فشل الحفظ", "bad"); return; }
  toast(`اتحفظ موقع ${c.code} ✓`, "ok");
  const code = c.code;
  RV_PREVIEW = null;
  await load(); status();
  // روح للمركز اللي بعده في نفس القايمة
  const next = CENTERS.find(x => RV_NEED.includes(x.status) && x.code !== code);
  RV_CODE = next && RV_FILTER === "need" ? next.code : code;
  reviewRender();
}

async function reviewReopen() {
  const r = await postJSON(`/api/centers/${encodeURIComponent(RV_CODE)}/reopen`);
  if (!r.ok) { toast(r.j.error || "تعذر", "bad"); return; }
  await load();
}

async function reviewRun() {
  const m = $("#rvmsg"); $("#rvbtn").disabled = true; m.className = "hint"; m.textContent = "بدء الفحص...";
  const r = await postJSON("/api/centers/review_locations");
  if (!r.ok) { $("#rvbtn").disabled = false; m.className = "hint bad"; m.textContent = r.j.error; return; }
  rvJob = r.j.id; $("#rvcancel").hidden = false;
  const done = () => { $("#rvbtn").disabled = false; $("#rvcancel").hidden = true; rvJob = null; load(); status(); };
  pollJob(rvJob, {
    onTick: j => { m.className = "hint"; m.textContent = (j.message || "") + " (" + (j.progress || 0) + "%)"; },
    onDone: j => {
      const x = j.result || {}, c = x.counts || {}, need = RV_NEED.reduce((s, k) => s + (c[k] || 0), 0);
      m.className = "hint " + (need ? "warn" : "ok");
      m.textContent = `اتفحص ${n(x.centers)} مركز` + (x.links_read ? ` (واتقرا ${n(x.links_read)} لينك جديد)` : "") + ". " +
        (need ? `${n(need)} مركز محتاج مراجعة: ` + RV_NEED.filter(k => c[k]).map(k => `${RV_ST[k].label} ${n(c[k])}`).join("، ") : "كل اللينكات قريبة من عناوينها ✓") +
        (x.gazetteer ? "" : " — ⚠ قاعدة الأماكن مش موجودة، فمفيش تقدير من العنوان.");
      RV_FILTER = "need"; done();
    },
    onError: j => { m.className = "hint bad"; m.textContent = j.error || "اتلغى"; done(); },
  });
}
async function reviewCancel() { if (rvJob) await postJSON("/api/jobs/" + rvJob + "/cancel"); }

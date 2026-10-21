/* خرايط Leaflet مشتركة (بلاطات OpenStreetMap) */
function makeMap(el, opts = {}) {
  const map = L.map(el, {preferCanvas: true, zoomControl: true}).setView(opts.center || [26.8, 30.8], opts.zoom || 6);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {maxZoom: 19, attribution: "&copy; OpenStreetMap contributors"}).addTo(map);
  return map;
}
const occColor = o => o == null ? "#6c93ff" : o >= 100 ? "#b42318" : o >= 80 ? "#e08a1e" : "#16794c";
function centerPopup(c) {
  return `<b>${esc(c.center_code)}</b> ${esc(c.center_name || c.name || "")}<br>${esc(govAr(c.gov))} / ${esc(c.area || "")}` +
    (c.seats != null ? `<br>مقاعد ${n(c.seats)} · موزعين ${n(c.assigned)} · إشغال ${c.occupancy == null ? "—" : c.occupancy + "%"}` : "") +
    `<br><a href="https://www.google.com/maps?q=${c.lat},${c.lon}" target="_blank">فتح في خرائط جوجل</a>`;
}
/* دواير للمراكز: الحجم بعدد المقاعد واللون بالإشغال */
function centersLayer(map, centers, onClick) {
  const g = L.layerGroup().addTo(map);
  centers.forEach(c => {
    if (c.lat == null || c.lon == null) return;
    const r = Math.max(6, Math.min(22, Math.sqrt((c.seats || 50) / 4)));
    const m = L.circleMarker([c.lat, c.lon], {radius: r, color: "#fff", weight: 1.5, fillColor: occColor(c.occupancy), fillOpacity: .9});
    m.bindPopup(centerPopup(c));
    m.bindTooltip(esc(c.center_code) + " " + esc(c.center_name || ""), {direction: "top"});
    if (onClick) m.on("click", () => onClick(c, m));
    m.addTo(g);
    c._marker = m;
  });
  return g;
}
/* نقاط الطلاب (canvas): أخضر موزع، أحمر VP */
function pointsLayer(map, pts) {
  const g = L.layerGroup().addTo(map);
  pts.forEach(p => {
    const m = L.circleMarker([p.lat, p.lon], {radius: 3.5, weight: 0, fillColor: p.s === "VP" ? "#d92d20" : "#16794c", fillOpacity: .75});
    m.bindPopup(`${p.s === "VP" ? "<b class='bad'>VP</b>" : "<b>موزع</b> " + esc(p.c)}<br>${esc(p.n)} · ${esc(p.lv)}` +
      (p.d ? `<br>${fmtKm(p.d)}` : "") + `<br><span class='small'>دقة الموقع: ${esc(p.g)}</span>`);
    m.addTo(g);
  });
  return g;
}
function fitTo(map, items) {
  const pts = items.filter(c => c.lat != null && c.lon != null).map(c => [c.lat, c.lon]);
  if (pts.length) map.fitBounds(L.latLngBounds(pts).pad(0.15), {maxZoom: 14});
}

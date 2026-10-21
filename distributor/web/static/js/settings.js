["geo_mode", "road_mode", "osrm_url", "max_km", "detour"].forEach(k => { if (SETTINGS[k] != null) $("#" + k).value = SETTINGS[k]; });
async function checkOsrm() {
  const st = $("#osrm_status"); st.className = "hint"; st.textContent = "جاري الفحص...";
  const r = await api("/api/osrm_check?url=" + encodeURIComponent($("#osrm_url").value.trim()));
  st.className = "hint " + (r.j.ok ? "ok" : "bad"); st.textContent = (r.j.ok ? "✓ " : "✗ ") + r.j.message;
}
async function saveSettings() {
  const body = {}; ["geo_mode", "road_mode", "osrm_url", "max_km", "detour"].forEach(k => { body[k] = $("#" + k).value; });
  const r = await postJSON("/api/settings", body);
  $("#smsg").className = "hint " + (r.ok ? "ok" : "bad"); $("#smsg").textContent = r.ok ? "اتحفظ ✓" : (r.j.error || "فشل");
}
async function status() {
  const r = await api("/api/status"); if (!r.ok) return; const l = r.j.locations;
  const g = await api("/api/google/status");
  $("#st").innerHTML = `<table>
    <tr><th style="position:static">الإصدار</th><td>${esc(r.j.version)}</td></tr>
    <tr><th style="position:static">المراكز</th><td>${n(l.centers_with_coords)} ليها موقع من ${n(l.centers)} · ${n(l.centers_verified)} متأكد منها</td></tr>
    <tr><th style="position:static">العناوين المحفوظة</th><td>${n(l.geo_cache)} · تصحيحات يدوية ${n(l.overrides)}</td></tr>
    <tr><th style="position:static">قاعدة الأماكن</th><td class="${l.gazetteer ? "ok" : "bad"}">${l.gazetteer ? "موجودة (" + l.gazetteer_mb + " ميجا)" : "مش موجودة: العناوين مش هتتحول لإحداثيات"}</td></tr>
    <tr><th style="position:static">Google API</th><td>${g.ok ? (g.j.key ? `المفتاح موجود · اتبعت الشهر ده ${n(g.j.used)} من ${n(g.j.cap)}` : (g.j.key_problem || "مفيش مفتاح (اختياري)")) : ""}</td></tr></table>`;
  checkOsrm();
}
async function changePw() {
  const r = await postJSON("/api/password", {old: $("#pw0").value, new: $("#pw1").value});
  $("#pmsg").className = "hint " + (r.ok ? "ok" : "bad"); $("#pmsg").textContent = r.ok ? "اتغيرت ✓" : (r.j.error || "فشل");
  if (r.ok) { $("#pw0").value = ""; $("#pw1").value = ""; }
}
status();

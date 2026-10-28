let TOKEN = null, PF = null;
const GEOH = {addr: "بيتجاهل إحداثيات الطلاب اللي في الشيت ويحسبها من gov وArea وAdress.",
  auto: "لو الشيت فيه Latitude/Longitude صالحة بيستخدمها، وإلا بيجيبها من العنوان.",
  sheet: "بيستخدم إحداثيات الشيت زي ما هي من غير أي geocoding."};
function geoHint() { $("#geohint").textContent = GEOH[$("#geo_mode").value] + (HAS_GAZ ? "" : " ⚠ قاعدة الأماكن مش موجودة: العناوين مش هتتحول لإحداثيات."); }
$("#geo_mode").addEventListener("change", geoHint); geoHint();
$("#geo_mode").value = SETTINGS.geo_mode || "addr"; geoHint();
$("#road_mode").value = SETTINGS.road_mode || "osrm";

async function checkOsrm() {
  const st = $("#osrm_status"); st.className = "hint"; st.textContent = "جاري فحص الاتصال...";
  const r = await api("/api/osrm_check?url=" + encodeURIComponent($("#osrm_url").value.trim()));
  st.className = "hint " + (r.j.ok ? "ok" : "bad"); st.textContent = (r.j.ok ? "✓ " : "✗ ") + r.j.message;
  if (!r.j.ok && $("#road_mode").value === "osrm") st.textContent += " — اختار «تقدير» أو شغّل start_osrm.bat.";
  return r.j.ok;
}
$("#road_mode").addEventListener("change", () => { if ($("#road_mode").value === "osrm") checkOsrm(); else { $("#osrm_status").textContent = "هيتحسب بالتقدير (خط مستقيم × المعامل)."; } });

async function upload() {
  const s = $("#students").files[0], c = $("#centers").files[0];
  if (!s || !c) { $("#umsg").className = "hint bad"; $("#umsg").textContent = "اختار الشيتين الأول."; return; }
  $("#upload").disabled = true; $("#umsg").className = "hint"; $("#umsg").textContent = "رفع وقراءة الملفات...";
  const fd = new FormData(); fd.append("students", s); fd.append("centers", c);
  const r = await api("/api/runs/upload", {method: "POST", body: fd});
  $("#upload").disabled = false;
  if (!r.ok) { $("#umsg").className = "hint bad"; $("#umsg").textContent = r.j.error || "فشل الرفع"; return; }
  $("#umsg").textContent = ""; PF = r.j; TOKEN = r.j.token; renderPreflight(r.j);
  $("#s2").hidden = false; $("#s3").hidden = false;
  if ($("#road_mode").value === "osrm") checkOsrm();
  $("#s2").scrollIntoView({behavior: "smooth"});
}

function kv(items) { return "<table>" + items.map(([k, v, c]) => `<tr><th style="position:static">${esc(k)}</th><td class="${c || ""}">${v}</td></tr>`).join("") + "</table>"; }
function renderPreflight(p) {
  $("#issues").innerHTML = p.issues.map(i => `<div class="issue ${i.level}">${i.level === "error" ? "✗ " : i.level === "warn" ? "⚠ " : "ℹ "}${esc(i.text)}</div>`).join("") ||
    `<div class="issue info">✓ مفيش ملاحظات على الشيتين.</div>`;
  const s = p.students, c = p.centers;
  const lv = Object.entries(s.levels || {}).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${esc(k)} <b>${n(v)}</b>`).join(" · ");
  const gv = Object.entries(s.govs || {}).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${esc(govAr(k))} <b>${n(v)}</b>`).join(" · ");
  $("#pf_students").innerHTML = kv([
    ["عدد الطلاب", n(s.rows)],
    ["الملف", esc(p.files.students) + (s.adapted_note ? `<div class="small">${esc(s.adapted_note)}</div>` : "")],
    ["رقم قومي غير صالح / مكرر", s.nid_invalid == null ? "مفيش عمود" : `${n(s.nid_invalid)} / ${n(s.nid_dup)}`, (s.nid_invalid || s.nid_dup) ? "warn" : ""],
    ["محافظة غير معروفة", n(s.unknown_gov_count || 0), s.unknown_gov_count ? "warn" : ""],
    ["بدون ليفيل", n(s.no_level || 0), s.no_level ? "warn" : ""],
    ["بدون منطقة / بدون عنوان", `${n(s.no_area)} / ${n(s.no_addr)}`],
    ["إحداثيات موجودة في الشيت", n(s.with_coords) + (s.coords_suspect && s.coords_suspect.length ? " <span class='warn'>(شكلها تقريبية)</span>" : "")],
    ["الليفيلات", lv || "—"], ["المحافظات", gv || "—"],
  ]);
  const clv = Object.entries(c.levels || {}).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${esc(k)} <b>${n(v)}</b>`).join(" · ");
  const cgv = Object.entries(c.govs || {}).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${esc(govAr(k))} <b>${n(v)}</b>`).join(" · ");
  $("#pf_centers").innerHTML = kv([
    ["المراكز / الجروبات", `${n(c.centers)} / ${n(c.rows)}`], ["الملف", esc(p.files.centers)],
    ["إجمالي المقاعد", n(c.seats)],
    ["جروب كوده غلط / سعة ناقصة / مكرر", `${n(c.bad_group_codes)} / ${n(c.no_capacity)} / ${n(c.dup_groups)}`, (c.bad_group_codes || c.no_capacity || c.dup_groups) ? "warn" : ""],
    ["مراكز من غير إحداثيات في الشيت", n((c.centers_no_coords || []).length) + ((c.centers_no_coords || []).length ? ` <span class="small">(${n(c.no_coords_with_link)} ليهم لينك، ${n(c.no_coords_with_addr_only)} عنوان بس، ${n((c.no_location_at_all || []).length)} ولا حاجة)</span>` : "")],
    ["مقاعد حسب الليفيل", clv || "—"], ["مقاعد حسب المحافظة", cgv || "—"],
  ]);
  $("#pf_cross_wrap").hidden = !(p.cross && p.cross.length);
  table("#pf_cross", [["gov", "المحافظة", v => esc(govAr(v))], ["level_code", "الليفيل"], ["students", "طلاب"], ["seats", "مقاعد"], ["deficit", "عجز", v => `<span class="bad">${n(v)}</span>`]], p.cross || []);
  if (s.with_coords === 0 && $("#geo_mode").value === "sheet") $("#geo_mode").value = "addr";
  geoHint();
  $("#go").disabled = !p.ok;
  $("#smsg").className = "hint " + (p.ok ? "" : "bad"); $("#smsg").textContent = p.ok ? "" : "صلّح الأخطاء الحمرا في الشيتات وارفع تاني.";
}

async function start() {
  if (!TOKEN) return;
  const params = {geo_mode: $("#geo_mode").value, road_mode: $("#road_mode").value, osrm_url: $("#osrm_url").value.trim(),
    max_km: $("#max_km").value, detour: $("#detour").value};
  if (params.road_mode === "osrm" && !(await checkOsrm())) { $("#smsg").className = "hint bad"; $("#smsg").textContent = "OSRM مش شغال: شغّله أو اختار «تقدير»."; return; }
  $("#go").disabled = true; $("#smsg").className = "hint"; $("#smsg").textContent = "بدء...";
  const r = await postJSON("/api/runs", {token: TOKEN, name: $("#name").value.trim(), params});
  if (!r.ok) { $("#go").disabled = false; $("#smsg").className = "hint bad"; $("#smsg").textContent = r.j.error || "تعذر البدء"; return; }
  location.href = "/runs/" + r.j.run_id;
}

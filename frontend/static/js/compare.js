const UPLOAD = "__upload__";
async function loadRuns(selectB) {
  const [r, e] = await Promise.all([api("/api/runs"), api("/api/compare/externals")]);
  if (!r.ok) return;
  const opts = r.j.filter(x => x.status === "done").map(x => `<option value="${esc(x.id)}">${esc(x.created_at)} — ${esc(x.name || x.students_file)} (${n((x.summary || {}).assigned)} موزع)</option>`).join("");
  const ext = (e.ok ? e.j : []).map(x => `<option value="${esc(x.id)}">📄 ${esc(x.name)} — ${esc(x.created_at)} (${n(x.assigned)} موزع)</option>`).join("");
  const keepA = $("#a").value || A0;
  $("#a").innerHTML = opts;
  $("#b").innerHTML = opts + (ext ? `<optgroup label="تشغيلات خارجية مرفوعة">${ext}</optgroup>` : "") +
    `<option value="${UPLOAD}">📤 رفع تشغيلة خارجية (ملف من برا السيستم)...</option>`;
  if (keepA) $("#a").value = keepA;
  const b = selectB || B0; if (b) $("#b").value = b;
  bChanged();
  if (A0 && B0 && !selectB) run();
}
function bChanged() { $("#extBox").hidden = $("#b").value !== UPLOAD; }
async function uploadExt() {
  const f = $("#extFile").files[0], a = $("#a").value;
  if (!f) { $("#extMsg").className = "hint bad"; $("#extMsg").textContent = "اختار ملف الأول."; return; }
  if (!a) { $("#extMsg").className = "hint bad"; $("#extMsg").textContent = "اختار التشغيل الأساسي (A) من السيستم."; return; }
  const fd = new FormData(); fd.append("file", f); fd.append("run", a);
  $("#extGo").disabled = true; $("#extMsg").className = "hint"; $("#extMsg").textContent = "بقرا الملف وبربطه بالتشغيل...";
  const r = await api("/api/compare/external", {method: "POST", body: fd});
  $("#extGo").disabled = false;
  if (!r.ok) { $("#extMsg").className = "hint bad"; $("#extMsg").textContent = r.j.error; return; }
  $("#extMsg").className = "hint ok";
  $("#extMsg").textContent = `اتقرا ${n(r.j.rows)} صف، ${n(r.j.matched)} طالب اتربطوا بالتشغيل A.`;
  await loadRuns(r.j.id);
  run();
}
function showExternal(x) {
  const e = x.external; $("#extCard").hidden = !e; if (!e) return;
  const c = e.counts || {}, k = (v, l, cls) => `<div class="kpi"><b class="${v ? cls : "ok"}">${n(v)}</b><span>${l}</span></div>`;
  $("#extName").textContent = e.name || "";
  const cols = Object.entries(e.columns || {}).map(([k2, v]) => `${({nid: "الرقم القومي", center: "كود المركز", center_name: "اسم المركز", group: "الجروب", status: "الحالة", distance: "المسافة"})[k2] || k2}: «${esc(v)}»`).join(" · ");
  const unres = Object.keys(e.unresolved_centers || {});
  $("#extInfo").innerHTML = `الأعمدة اللي اتقرت: ${cols}<br>المسافة: ${esc(e.distance_note)} · الحد الأقصى ${esc(e.max_km)} كم` +
    (unres.length ? `<br><span class="bad">مراكز مش معروفة في السيستم: ${unres.slice(0, 10).map(esc).join("، ")}${unres.length > 10 ? " ..." : ""}</span>` : "");
  $("#extKpis").innerHTML = k(c.other_gov, "مركز في محافظة تانية", "bad") + k(c.level_mismatch, "ليفيل الجروب مختلف", "bad") +
    k(c.over_capacity, "جروبات فوق السعة", "bad") + k(c.over_max_km, "أبعد من الحد الأقصى", "warn") + k(c.unknown_center, "مركز مش في السيستم", "warn") +
    k(c.not_in_run, "طلاب مش في تشغيلة السيستم", "warn") + k(c.duplicate, "رقم قومي متكرر", "warn") + k(c.no_center, "موزع من غير مركز", "warn");
  table("#t_ext", [["problem_ar", "المشكلة"], ["key", "الرقم القومي"], ["row_no", "الصف في الملف"], ["center", "المركز"], ["group", "الجروب"], ["details", "التفاصيل"]],
    e.issues || [], {empty: "✅ التوزيع الخارجي ماشي على كل قواعد السيستم"});
}
async function run() {
  const a = $("#a").value, b = $("#b").value;
  if (b === UPLOAD) { $("#msg").textContent = "ارفع الملف الخارجي الأول."; return; }
  if (!a || !b || a === b) { $("#msg").textContent = "اختار تشغيلين مختلفين."; return; }
  $("#msg").textContent = "بقارن..."; history.replaceState(null, "", `/compare?a=${a}&b=${b}`);
  const r = await api(`/api/compare?a=${a}&b=${b}`);
  if (!r.ok) { $("#msg").className = "hint bad"; $("#msg").textContent = r.j.error; return; }
  $("#msg").textContent = ""; $("#out").hidden = false; $("#xl").hidden = false; $("#xl").href = `/api/compare.xlsx?a=${a}&b=${b}`;
  const x = r.j, p = x.pair;
  showExternal(x);
  table("#t_sum", [["t", "التشغيل"], ["students", "طلاب"], ["assigned", "موزعين"], ["vp", "VP"], ["same_area", "نفس المنطقة"], ["avg_km", "متوسط المسافة", v => fmtKm(v)], ["median_km", "وسيط المسافة", v => fmtKm(v)], ["over_20km", "أبعد من 20 كم"]],
    [{t: "A (السيستم)", ...x.a}, {t: x.b.external ? "B (خارجي)" : "B", ...x.b}]);
  const k = (v, l, c) => `<div class="kpi"><b class="${c || ""}">${v}</b><span>${l}</span></div>`;
  $("#kpis").innerHTML = k(n(p.common), "طلاب في المقارنة (" + esc(p.key) + ")") + k((p.same_outcome_pct == null ? "—" : p.same_outcome_pct + "%"), "نفس النتيجة (نفس المركز أو الاتنين VP)") +
    k(n(p.same_group), "نفس المركز ونفس الجروب") + k(n(p.diff_center), "اتوزعوا على مركزين مختلفين", "warn") +
    k(n(p.a_assigned_b_vp), "موزع في A وVP في B", "bad") + k(n(p.a_vp_b_assigned), "VP في A وموزع في B", "ok") + k(n(p.both_vp), "VP في الاتنين") +
    k(p.mean_abs_dist_diff == null ? "—" : p.mean_abs_dist_diff + " كم", "متوسط فرق المسافة") + (p.only_a || p.only_b ? k(n(p.only_a) + " / " + n(p.only_b), "موجود في A بس / في B بس") : "");
  table("#t_gov", [["gov", "المحافظة"], ["students", "طلاب"], ["assigned_a", "موزعين A"], ["assigned_b", "موزعين B"], ["vp_a", "VP A"], ["vp_b", "VP B"], ["same_pct", "% نفس النتيجة"], ["avg_a", "متوسط A", v => fmtKm(v)], ["avg_b", "متوسط B", v => fmtKm(v)]], x.by_gov);
  $("#chn").textContent = `(${n(x.changed_total)}${x.changed_total > x.changed.length ? "، معروض أول " + n(x.changed.length) + "، الباقي في ملف Excel" : ""})`;
  table("#t_ch", x.changed_cols.map(c => [c, ({key: p.key, gov: "المحافظة", Area: "المنطقة", Adress: "العنوان", level_code: "الليفيل", status_a: "A: الحالة", center_a: "A: المركز", dist_a: "A: كم", status_b: "B: الحالة", center_b: "B: المركز", dist_b: "B: كم"})[c] || c]), x.changed);
}
loadRuns();

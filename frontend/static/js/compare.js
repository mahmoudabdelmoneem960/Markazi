async function loadRuns() {
  const r = await api("/api/runs"); if (!r.ok) return;
  const opts = r.j.filter(x => x.status === "done").map(x => `<option value="${esc(x.id)}">${esc(x.created_at)} — ${esc(x.name || x.students_file)} (${n((x.summary || {}).assigned)} موزع)</option>`).join("");
  $("#a").innerHTML = opts; $("#b").innerHTML = opts;
  if (A0) $("#a").value = A0; if (B0) $("#b").value = B0;
  if (A0 && B0) run();
}
async function run() {
  const a = $("#a").value, b = $("#b").value; if (!a || !b || a === b) { $("#msg").textContent = "اختار تشغيلين مختلفين."; return; }
  $("#msg").textContent = "بقارن..."; history.replaceState(null, "", `/compare?a=${a}&b=${b}`);
  const r = await api(`/api/compare?a=${a}&b=${b}`);
  if (!r.ok) { $("#msg").className = "hint bad"; $("#msg").textContent = r.j.error; return; }
  $("#msg").textContent = ""; $("#out").hidden = false; $("#xl").hidden = false; $("#xl").href = `/api/compare.xlsx?a=${a}&b=${b}`;
  const x = r.j, p = x.pair;
  table("#t_sum", [["t", "التشغيل"], ["students", "طلاب"], ["assigned", "موزعين"], ["vp", "VP"], ["same_area", "نفس المنطقة"], ["avg_km", "متوسط المسافة", v => fmtKm(v)], ["median_km", "وسيط المسافة", v => fmtKm(v)], ["over_20km", "أبعد من 20 كم"]],
    [{t: "A", ...x.a}, {t: "B", ...x.b}]);
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

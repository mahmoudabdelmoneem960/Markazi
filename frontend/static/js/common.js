/* أدوات مشتركة لكل الصفحات */
const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
const n = x => (x == null || x === "" || isNaN(Number(x))) ? (x == null ? "" : String(x)) : Number(x).toLocaleString("en");
const esc = t => String(t == null ? "" : t).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const fmtKm = x => (x == null || x === "" || isNaN(Number(x))) ? "" : Number(x).toFixed(1) + " كم";
const fmtM = m => m == null ? "" : (m >= 1000 ? (m / 1000).toFixed(1) + " كم" : Math.round(m) + " م");
const fmtSec = s => { s = Math.floor(s || 0); return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); };
const GOV_AR = {alexandria: "الإسكندرية", cairo: "القاهرة", giza: "الجيزة", qalyubia: "القليوبية", dakahlia: "الدقهلية", sharqia: "الشرقية",
  gharbia: "الغربية", monufia: "المنوفية", beheira: "البحيرة", sohag: "سوهاج", damietta: "دمياط", fayoum: "الفيوم", minya: "المنيا",
  beni_suef: "بني سويف", kafr_el_sheikh: "كفر الشيخ", port_said: "بورسعيد", suez: "السويس", ismailia: "الإسماعيلية", south_sinai: "جنوب سيناء",
  new_valley: "الوادي الجديد", north_sinai: "شمال سيناء", assiut: "أسيوط", luxor: "الأقصر", aswan: "أسوان", qena: "قنا", red_sea: "البحر الأحمر", matrouh: "مطروح"};
const govAr = g => GOV_AR[g] || g || "";

// تصنيف الـ 27 محافظة إلى بنود (البند الأول / البند الثاني / البند الثالث)
const GOV_BANDS = {
  // البند الأول (9 محافظات)
  cairo: "البند الأول", suez: "البند الأول", ismailia: "البند الأول", port_said: "البند الأول",
  beheira: "البند الأول", beni_suef: "البند الأول", minya: "البند الأول", south_sinai: "البند الأول", north_sinai: "البند الأول",

  // البند الثاني (9 محافظات) - مشكلة في البيانات (غير مستهدفة حالياً)
  giza: "البند الثاني", qalyubia: "البند الثاني", monufia: "البند الثاني", qena: "البند الثاني",
  aswan: "البند الثاني", assiut: "البند الثاني", luxor: "البند الثاني", red_sea: "البند الثاني", matrouh: "البند الثاني",

  // البند الثالث (9 محافظات)
  alexandria: "البند الثالث", sharqia: "البند الثالث", dakahlia: "البند الثالث", gharbia: "البند الثالث",
  damietta: "البند الثالث", kafr_el_sheikh: "البند الثالث", fayoum: "البند الثالث", sohag: "البند الثالث", new_valley: "البند الثالث"
};

function getGovBand(g) {
  if (!g) return "غير محدد";
  const s = String(g).trim().toLowerCase();
  if (GOV_BANDS[s]) return GOV_BANDS[s];
  const clean = s.replace(/[أإآ]/g, "ا").replace(/ة/g, "ه").replace(/ى/g, "ي");
  for (const [code, ar] of Object.entries(GOV_AR)) {
    const arClean = ar.replace(/[أإآ]/g, "ا").replace(/ة/g, "ه").replace(/ى/g, "ي").toLowerCase();
    if (arClean === clean || code === s) return GOV_BANDS[code] || "غير محدد";
  }
  return "غير محدد";
}

async function api(url, opts = {}) {
  let r;
  try { r = await fetch(url, opts); } catch (e) { return {ok: false, status: 0, j: {error: "تعذر الاتصال بالسيستم"}}; }
  let j = null;
  try { j = await r.json(); } catch (e) { j = {error: "رد غير متوقع من السيستم (حالة " + r.status + ")"}; }
  if (r.status === 401 && j && j.login) { location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search); }
  return {ok: r.ok, status: r.status, j};
}
const postJSON = (url, body) => api(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body || {})});
const del = url => api(url, {method: "DELETE"});

function table(el, cols, rows, opts = {}) {
  const t = typeof el === "string" ? $(el) : el;
  if (!rows || !rows.length) { t.innerHTML = "<tr><td class='mute'>" + esc(opts.empty || "مفيش بيانات") + "</td></tr>"; return; }
  t.innerHTML = "<tr>" + cols.map(c => "<th>" + esc(c[1]) + "</th>").join("") + "</tr>" +
    rows.map((r, i) => "<tr" + (opts.rowAttr ? " " + opts.rowAttr(r, i) : "") + ">" + cols.map(c => {
      const v = r[c[0]];
      const s = c[2] ? c[2](v, r) : (v == null ? "" : (typeof v === "number" && Math.abs(v) < 1e9 ? n(v) : esc(v)));
      return "<td>" + s + "</td>";
    }).join("") + "</tr>").join("");
}

let _toastT = null;
function toast(msg, cls) {
  let t = $(".toast"); if (!t) { t = document.createElement("div"); t.className = "toast"; document.body.appendChild(t); }
  t.textContent = msg; t.style.background = cls === "bad" ? "var(--bad)" : cls === "ok" ? "var(--ok)" : "var(--ink)"; t.hidden = false;
  clearTimeout(_toastT); _toastT = setTimeout(() => { t.hidden = true; }, 4000);
}

/* متابعة مهمة في الخلفية: onTick(job) كل ثانية ونص، onDone(job) عند الانتهاء، onError(job|{error}) */
function pollJob(id, {onTick, onDone, onError, interval = 1500} = {}) {
  let fails = 0, timer = null, stopped = false;
  const stop = () => { stopped = true; clearInterval(timer); };
  const tick = async () => {
    if (stopped) return;
    const r = await api("/api/jobs/" + id);
    if (r.status === 404) { stop(); onError && onError({error: "المهمة مش موجودة (السيستم اتعمله إعادة تشغيل؟)"}); return; }
    if (!r.ok) { if (++fails >= 6) { stop(); onError && onError({error: r.j.error || "فشلت متابعة المهمة"}); } return; }
    fails = 0;
    const j = r.j;
    onTick && onTick(j);
    if (j.state === "done") { stop(); onDone && onDone(j); }
    else if (j.state === "error" || j.state === "cancelled") { stop(); onError && onError(j); }
  };
  tick(); timer = setInterval(tick, interval);
  return stop;
}

function markNav() { $$("nav.menu a").forEach(a => { if (location.pathname === a.getAttribute("href") || (a.dataset.prefix && location.pathname.startsWith(a.dataset.prefix))) a.classList.add("on"); }); }
document.addEventListener("DOMContentLoaded", markNav);

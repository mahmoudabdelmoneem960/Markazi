"""مصادر الجروبات للتشغيل: شيت المراكز، أو قاعدة المراكز، + شيت جروبات اختياري (كود الجروب / المركز / اليوم / التوقيت / السعة).

الناتج دايمًا "شيت مراكز" بالشكل اللي المحرك فاهمه (صف لكل جروب):
Group Code, Center Code, Center Name, City, Area, العنوان التفصيلي للمركز, لوكيشن المكان, Day, Time, سعة القاعة, Latitude, Longitude
"""
import re

import pandas as pd

from distributor import config, db
from distributor.services import sheets

OUT_COLS = ["Group Code", "Center Code", "Center Name", "City", "Area", "العنوان التفصيلي للمركز", "لوكيشن المكان",
            "Day", "Time", "سعة القاعة", "Latitude", "Longitude"]
DAY_AB = {"A": "Friday", "B": "Saturday"}
DAY_AR = {"الجمعة": "Friday", "الجمعه": "Friday", "السبت": "Saturday", "الأحد": "Sunday", "الاحد": "Sunday",
          "الاثنين": "Monday", "الإثنين": "Monday", "الثلاثاء": "Tuesday", "الأربعاء": "Wednesday", "الاربعاء": "Wednesday",
          "الخميس": "Thursday"}


def _norm(c):
    return re.sub(r"[\s_\-]+", " ", str(c).strip().lower())


def _col(df, names=(), contains=()):
    cols = {_norm(c): c for c in df.columns}
    for n in names:
        if _norm(n) in cols:
            return cols[_norm(n)]
    for c in df.columns:
        if any(k in _norm(c) for k in contains):
            return c
    return None


def _day(v, code=""):
    s = sheets.clean(v)
    if s:
        return DAY_AR.get(s, s[:1].upper() + s[1:] if s.isascii() else s)
    m = re.match(r"\s*(?:PH|ON)-([AB])-", str(code), re.I)          # كود بالشكل الجديد: A = الجمعة، B = السبت
    return DAY_AB[m.group(1).upper()] if m else ""


def normalize_groups(G, default_cap=25, need_center=True):
    """شيت جروبات (أو شيت مراكز) → DataFrame: Group Code, Center Code, Day, Time, سعة القاعة. بيرمي ValueError لو ناقص."""
    G = G.copy()
    G.columns = [str(c).strip() for c in G.columns]
    gcol = _col(G, ("Group Code", "group", "كود الجروب", "كود المجموعة", "الجروب"), ("group code", "كود الجروب"))
    ccol = _col(G, ("Center Code", "كود المركز", "center"), ("center code", "كود المركز"))
    dcol = _col(G, ("Day", "اليوم", "Slot 1 Day", "يوم"), ("day", "اليوم"))
    tcol = _col(G, ("Time", "التوقيت", "الوقت", "الميعاد", "Slot 1 Start", "Start", "بداية"), ("time", "start", "التوقيت", "الميعاد"))
    kcol = _col(G, (), ("سعة", "سعه", "capacity"))
    miss = [n for n, c in (("كود الجروب (Group Code)", gcol), ("كود المركز (Center Code)", ccol if need_center else 1)) if c is None]
    if miss:
        raise ValueError("شيت الجروبات ناقص الأعمدة دي: " + "، ".join(miss))
    out = pd.DataFrame({"Group Code": G[gcol].map(sheets.clean)})
    out["Center Code"] = G[ccol].map(sheets.clean) if ccol else ""
    out["Day"] = [_day(d, g) for d, g in zip(G[dcol] if dcol else [""] * len(G), out["Group Code"])]
    out["Time"] = G[tcol].map(sheets.clean) if tcol else ""
    cap = pd.to_numeric(G[kcol], errors="coerce") if kcol else pd.Series([float("nan")] * len(G))
    out["سعة القاعة"] = cap.fillna(default_cap).astype(int).to_numpy()
    out = out[out["Group Code"].ne("")].reset_index(drop=True)
    if not len(out):
        raise ValueError("شيت الجروبات فاضي (مفيش أكواد جروبات).")
    return out, {"cap_from_sheet": kcol is not None, "has_day": dcol is not None or out["Day"].ne("").any(),
                 "has_time": tcol is not None}


def with_day_time(C):
    """شيت المراكز زي ما هو، بس لو اليوم/التوقيت مكتوبين بأسماء تانية (Slot 1 Day / Slot 1 Start) بيتنسخوا لـ Day / Time."""
    C = C.copy()
    C.columns = [str(c).strip() for c in C.columns]
    if "Day" not in C.columns:
        dcol = _col(C, ("اليوم", "Slot 1 Day", "يوم"), ("day", "اليوم"))
        gcol = "Group Code" if "Group Code" in C.columns else None
        if dcol or gcol:
            C["Day"] = [_day(d, g) for d, g in zip(C[dcol] if dcol else [""] * len(C), C[gcol] if gcol else [""] * len(C))]
    if "Time" not in C.columns:
        tcol = _col(C, ("التوقيت", "الوقت", "الميعاد", "Slot 1 Start", "Start"), ("time", "start", "التوقيت", "الميعاد"))
        if tcol:
            C["Time"] = C[tcol].map(sheets.clean)
    return C


def db_centers():
    """مراكز قاعدة المواقع: صف لكل مركز بالأعمدة اللي المحرك محتاجها."""
    con = db.connect()
    try:
        rows = [dict(r) for r in con.execute("SELECT code, name, gov, area, address, gmaps_url, lat, lon FROM centers")]
    finally:
        con.close()
    return pd.DataFrame([{"Center Code": r["code"], "Center Name": r["name"] or "", "City": (r["gov"] or "").lower(),
                          "Area": r["area"] or "", "العنوان التفصيلي للمركز": r["address"] or "",
                          "لوكيشن المكان": r["gmaps_url"] or "", "Latitude": r["lat"], "Longitude": r["lon"]} for r in rows],
                        columns=["Center Code", "Center Name", "City", "Area", "العنوان التفصيلي للمركز", "لوكيشن المكان",
                                 "Latitude", "Longitude"])


def centers_info(C):
    """بيانات المراكز (صف لكل مركز) من شيت مراكز مرفوع."""
    from distributor.core import locdb
    recs = locdb.center_records(C)
    return pd.DataFrame([{"Center Code": r["code"], "Center Name": r["name"], "City": r["gov"], "Area": r["area"],
                          "العنوان التفصيلي للمركز": r["address"], "لوكيشن المكان": r["url"],
                          "Latitude": r["lat"], "Longitude": r["lon"]} for r in recs.values()])


def latest_groups():
    """جروبات آخر تشغيل ناجح (من شيت المراكز اللي اترفع معاه). بيرجّع (DataFrame, وصف) أو (None, None)."""
    con = db.connect()
    try:
        runs = [dict(r) for r in con.execute("SELECT id, name, centers_file, created_at FROM runs "
                                             "WHERE status IN ('done','files') ORDER BY created_at DESC LIMIT 20")]
    finally:
        con.close()
    for r in runs:
        inp = config.RUNS_DIR / r["id"] / "input"
        if not inp.is_dir():
            continue
        cp = next((p for p in inp.iterdir() if p.name.startswith("centers")), None)
        if cp is None:
            continue
        try:
            G, _ = normalize_groups(sheets.parse_file(cp.name, cp.read_bytes()))
        except ValueError:
            continue
        return G, f"جروبات آخر تشغيل: {r['name']} ({r['created_at'][:10]})"
    return None, None


def build(groups, info):
    """جروبات + بيانات المراكز → شيت مراكز كامل للمحرك. بيرجّع (DataFrame, ملاحظات)."""
    info = info.drop_duplicates("Center Code")
    m = groups.merge(info, on="Center Code", how="left")
    unknown = sorted(set(m.loc[m["City"].isna(), "Center Code"]) - {""})
    no_center = int(m["Center Code"].eq("").sum())
    m = m[m["City"].notna()].copy()
    notes = []
    if unknown:
        notes.append(("warn", f"{len(unknown)} مركز في شيت الجروبات مش موجود في بيانات المراكز وجروباتهم اتجاهلت: "
                      + "، ".join(unknown[:12]) + (" ..." if len(unknown) > 12 else "")))
    if no_center:
        notes.append(("warn", f"{no_center} جروب من غير كود مركز واتجاهلوا."))
    if not len(m):
        raise ValueError("ولا جروب في شيت الجروبات مركزه موجود في بيانات المراكز. اتأكد من عمود Center Code.")
    return m.reindex(columns=OUT_COLS).reset_index(drop=True), notes


def assemble(source, C=None, G=None, default_cap=25):
    """source: 'sheet' (شيت المراكز) أو 'db' (قاعدة المراكز). G اختياري (شيت جروبات).
    بيرجّع (شيت المراكز الجاهز للمحرك أو None لو هنستخدم شيت المراكز زي ما هو, ملاحظات, وصف مصدر الجروبات)."""
    notes = []
    if source == "sheet":
        if C is None:
            raise ValueError("ارفع شيت المراكز، أو اختار «المراكز من قاعدة البيانات».")
        if G is None:
            return with_day_time(C), notes, "من شيت المراكز"
        groups, gi = normalize_groups(G, default_cap)
        info = pd.concat([centers_info(C), db_centers()], ignore_index=True)      # الشيت الأول، والقاعدة للي ناقص
        label = "من شيت الجروبات"
    else:
        info = db_centers()
        if not len(info):
            raise ValueError("قاعدة المراكز فاضية: ارفع شيت المراكز مرة الأول.")
        if G is not None:
            groups, gi = normalize_groups(G, default_cap)
            label = "من شيت الجروبات"
        else:
            groups, label = latest_groups()
            if groups is None:
                raise ValueError("مفيش جروبات محفوظة من تشغيل قبل كده: ارفع شيت الجروبات (كود الجروب، كود المركز، اليوم، التوقيت).")
            gi = {"cap_from_sheet": True, "has_day": groups["Day"].ne("").any(), "has_time": groups["Time"].ne("").any()}
            notes.append(("info", f"الجروبات مأخوذة من {label.split(': ', 1)[-1]} ({len(groups):,} جروب)."))
    if G is not None and not gi["cap_from_sheet"]:
        notes.append(("info", f"شيت الجروبات مفيهوش عمود سعة: كل جروب سعته {default_cap}."))
    if G is not None and not gi["has_day"]:
        notes.append(("info", "شيت الجروبات مفيهوش عمود اليوم."))
    out, n2 = build(groups, info)
    return out, notes + n2, label

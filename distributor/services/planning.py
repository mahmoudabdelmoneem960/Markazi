"""التخطيط: تحليل شيت الطلاب لوحده قبل أي توزيع (العدد، البنود، المحافظات، الليفيلات وترتيبهم)."""
import io
import json
import re
import uuid

import pandas as pd

from distributor import db
from distributor.core import engine
from distributor.services import areacheck, sheets
from distributor.services.compare import GOV_AR

UNKNOWN_GOV = "غير معروفة"
UNKNOWN_LEVEL = "غير محدد"
BAND_ORDER = [engine.BAND_1, engine.BAND_2, engine.BAND_3, "غير محدد"]


def _level_sort(code):
    """ترتيب طبيعي للّيفيلات: L1, L2, L3CY, L3DT ... والـ«غير محدد» في الآخر."""
    m = re.match(r"L(\d+)([A-Z]*)$", str(code))
    return (0, int(m.group(1)), m.group(2)) if m else (1, 0, str(code))


def _pct(x, total):
    return round(100.0 * x / total, 1) if total else 0.0


def groups_needed(count, cap):
    """أقل عدد جروبات لعدد طلاب في ليفيل واحد (الجروب ليفيل واحد ومحافظة واحدة)."""
    return -(-int(count) // int(cap)) if cap and count else 0


def _gov_groups(g, cap):
    """جروبات المحافظة = مجموع جروبات كل ليفيل فيها. المحافظة/الليفيل غير المعروفين مش بيتحسبوا (هيبقوا VP)."""
    if g["gov_code"] == UNKNOWN_GOV:
        return {}
    return {k: groups_needed(c, cap) for k, c in g["levels"].items() if k != UNKNOWN_LEVEL and c}


NO_AREA = "بدون منطقة"


def _areas(df, area_col):
    """المناطق جوّه كل محافظة بالأعداد، مرتبة من الأكبر.
    الكتابات المختلفة لنفس المنطقة (المندرة/المندره، حي X/X) بتتجمع بنفس مفتاح المحرك (engine.area_key)،
    والاسم المعروض هو أكتر كتابة متكررة."""
    raw = area_col.map(sheets.clean)
    key = raw.map(lambda a: " ".join(sorted(engine.area_key(a))) if a else "")
    d = pd.DataFrame({"gov": df["gov"].to_numpy(), "level": df["level"].to_numpy(), "raw": raw.to_numpy(), "key": key.to_numpy()})
    out = {}
    for g, sub in d.groupby("gov", sort=False):
        gtotal = len(sub)
        rows = []
        for k, a in sub.groupby("key", sort=False):
            names = a["raw"].value_counts()
            lv = a["level"].value_counts()
            rows.append({"area": str(names.index[0]) if k else NO_AREA, "students": int(len(a)), "pct": _pct(len(a), gtotal),
                         "levels": {str(x): int(c) for x, c in lv.items()},
                         "variants": [str(x) for x in names.index[1:6]]})
        rows.sort(key=lambda r: (r["area"] == NO_AREA, -r["students"]))
        for i, r in enumerate(rows):
            r["rank"] = i + 1
        out[str(g)] = rows
    return out


def analyze(S_raw):
    """S_raw: DataFrame شيت الطلاب زي ما اتقرا. بيرجّع dict جاهز للعرض."""
    S, note = sheets.adapt_students(S_raw)
    S = S.copy()
    S.columns = [str(c).strip() for c in S.columns]
    sc = sheets.student_columns(S)
    missing = [name for k, name in (("gov", "gov (المحافظة)"), ("level", "Level (الليفيل)")) if not sc[k]]
    if missing:
        raise ValueError("شيت الطلاب ناقص الأعمدة دي: " + "، ".join(missing))

    total = len(S)
    gov = S[sc["gov"]].map(lambda g: engine.GOV_MAP.get(engine.norm_ar(g))).fillna(UNKNOWN_GOV)
    lvl = S[sc["level"]].map(engine.level_key).fillna(UNKNOWN_LEVEL)
    band = gov.map(lambda g: engine.CITY_BANDS.get(g, "غير محدد"))
    df = pd.DataFrame({"gov": gov, "level": lvl, "band": band})

    nid_info = {}
    if sc["nid"]:
        nid = engine._clean_id(S[sc["nid"]])
        valid = nid.str.fullmatch(r"\d{14}")
        nid_info = {"invalid": int((~valid).sum()), "dup": int(nid[valid].duplicated().sum())}

    levels_order = sorted(df["level"].unique(), key=_level_sort)
    areas_by_gov = _areas(df, S[sc["area"]]) if sc["area"] else {}

    # الليفيلات مرتبة بالعدد
    lv_counts = df["level"].value_counts()
    levels = [{"rank": i + 1, "level": k, "students": int(v), "pct": _pct(v, total),
               "govs": int(df.loc[df["level"] == k, "gov"].nunique())}
              for i, (k, v) in enumerate(lv_counts.items())]

    # المحافظات مرتبة بالعدد + توزيع الليفيلات جوّه كل محافظة
    cross = pd.crosstab(df["gov"], df["level"]).reindex(columns=levels_order, fill_value=0)
    gov_counts = df["gov"].value_counts()
    band_rank = {}
    govs = []
    for i, (g, v) in enumerate(gov_counts.items()):
        g = str(g)
        b = engine.CITY_BANDS.get(g, "غير محدد")
        band_rank[b] = band_rank.get(b, 0) + 1
        row = cross.loc[g]
        lv_sorted = [(k, int(c)) for k, c in row.sort_values(ascending=False).items() if c]
        govs.append({"rank": i + 1, "band_rank": band_rank[b], "gov_code": g, "gov": GOV_AR.get(g, g), "band": b,
                     "students": int(v), "pct": _pct(v, total), "pct_band": 0.0,
                     "levels": {k: int(c) for k, c in row.items()},
                     "levels_ranked": [{"level": k, "students": c, "pct": _pct(c, v)} for k, c in lv_sorted],
                     "areas": areas_by_gov.get(g, [])})

    # البنود
    bands = []
    for b in BAND_ORDER:
        sub = df[df["band"] == b]
        if b == "غير محدد" and sub.empty:
            continue
        bc = int(len(sub))
        for g in govs:
            if g["band"] == b:
                g["pct_band"] = _pct(g["students"], bc)
        top_lv = sub["level"].value_counts()
        bands.append({"band": b, "students": bc, "pct": _pct(bc, total), "govs": int(sub["gov"].nunique()),
                      "govs_list": [g["gov"] for g in govs if g["band"] == b],
                      "levels": {k: int(top_lv.get(k, 0)) for k in levels_order}})
    band_sorted = sorted(range(len(bands)), key=lambda i: -bands[i]["students"])
    for r, i in enumerate(band_sorted):
        bands[i]["rank"] = r + 1

    area_check, flag_rows = areacheck.check(df, S, sc)

    return {"_flags": flag_rows, "area_check": area_check, "total": int(total), "note": note, "columns": {k: v for k, v in sc.items() if v}, "has_areas": bool(sc["area"]),
            "nid": nid_info, "levels_order": levels_order, "levels": levels, "govs": govs, "bands": bands,
            "unknown_gov": int((df["gov"] == UNKNOWN_GOV).sum()),
            "unknown_level": int((df["level"] == UNKNOWN_LEVEL).sum()),
            "unknown_gov_names": S.loc[df["gov"] == UNKNOWN_GOV, sc["gov"]].astype(str).str.strip()
                                  .value_counts().head(10).to_dict()}


def to_excel(rep, cap=None):
    """تقرير Excel: ملخص، البنود، المحافظات، الليفيلات، ومصفوفة محافظة × ليفيل (+ الجروبات المطلوبة لو فيه سعة)."""
    lo = rep["levels_order"]
    gg = {g["gov_code"]: _gov_groups(g, cap) for g in rep["govs"]} if cap else {}
    lv_groups = {k: sum(x.get(k, 0) for x in gg.values()) for k in lo}
    total_groups = sum(lv_groups.values())
    summary = pd.DataFrame(([("سعة الجروب", cap), ("إجمالي الجروبات المطلوبة", total_groups)] if cap else [])
                           + [("إجمالي الطلاب", rep["total"]), ("عدد المحافظات", len(rep["govs"])),
                            ("عدد الليفيلات", len(rep["levels"])), ("محافظة غير معروفة", rep["unknown_gov"]),
                            ("ليفيل غير محدد", rep["unknown_level"])]
                           + ([("رقم قومي غير صحيح", rep["nid"]["invalid"]), ("رقم قومي مكرر", rep["nid"]["dup"])] if rep["nid"] else []),
                           columns=["البند", "القيمة"])
    bands = pd.DataFrame([{"الترتيب": b["rank"], "البند": b["band"], "عدد الطلاب": b["students"], "النسبة %": b["pct"],
                           "عدد المحافظات": b["govs"], "المحافظات": "، ".join(b["govs_list"]),
                           **({"الجروبات المطلوبة": sum(sum(gg.get(g["gov_code"], {}).values()) for g in rep["govs"] if g["band"] == b["band"])} if cap else {}),
                           **b["levels"]} for b in rep["bands"]])
    govs = pd.DataFrame([{"الترتيب العام": g["rank"], "الترتيب في البند": g["band_rank"], "المحافظة": g["gov"], "البند": g["band"],
                          "عدد الطلاب": g["students"], "النسبة من الإجمالي %": g["pct"], "النسبة من البند %": g["pct_band"],
                          **({"الجروبات المطلوبة": sum(gg.get(g["gov_code"], {}).values())} if cap else {}),
                          "أكبر ليفيل": (g["levels_ranked"][0]["level"] if g["levels_ranked"] else ""),
                          "ترتيب الليفيلات": " > ".join(f'{x["level"]} ({x["students"]:,})' for x in g["levels_ranked"])}
                         for g in rep["govs"]])
    levels = pd.DataFrame([{"الترتيب": x["rank"], "الليفيل": x["level"], "عدد الطلاب": x["students"], "النسبة %": x["pct"],
                            "عدد المحافظات": x["govs"], **({"الجروبات المطلوبة": lv_groups.get(x["level"], 0)} if cap else {})}
                           for x in rep["levels"]])
    matrix = pd.DataFrame([{"المحافظة": g["gov"], "البند": g["band"], **{k: g["levels"].get(k, 0) for k in lo}, "الإجمالي": g["students"]}
                           for g in rep["govs"]])
    if not matrix.empty:
        tot = {"المحافظة": "الإجمالي", "البند": "", **{k: int(matrix[k].sum()) for k in lo}, "الإجمالي": int(matrix["الإجمالي"].sum())}
        matrix = pd.concat([matrix, pd.DataFrame([tot])], ignore_index=True)
    sheets_out = [("ملخص", summary), ("البنود", bands), ("المحافظات", govs), ("الليفيلات", levels), ("محافظة × ليفيل", matrix)]
    area_rows = [{"المحافظة": g["gov"], "البند": g["band"], "ترتيب المحافظة": g["rank"], "ترتيب المنطقة": a["rank"],
                  "المنطقة": a["area"], "عدد الطلاب": a["students"], "النسبة من المحافظة %": a["pct"],
                  **{k: a["levels"].get(k, 0) for k in lo},
                  "كتابات تانية لنفس المنطقة": "، ".join(a.get("variants") or [])}
                 for g in rep["govs"] for a in g.get("areas") or []]
    if area_rows:
        sheets_out.insert(3, ("المناطق", pd.DataFrame(area_rows)))
    ac = rep.get("area_check") or {}
    if ac.get("available"):
        gp = [{"المشكلة": areacheck.KIND_AR[x["kind"]], "الدرجة": x["conf"], "المحافظة": x["gov"], "المنطقة": x["area"],
               "المحافظة المحتملة": x["other_gov"], "عدد الطلاب": x["students"], "التفاصيل": x["reason"]} for x in ac["groups"]]
        nb = [{"المحافظة": GOV_AR.get(g, g), "بدون منطقة": c[areacheck.NO_AREA], "شارع بدل منطقة": c[areacheck.STREET],
               "منطقة في محافظة تانية": c[areacheck.OTHER_GOV]} for g, c in ac["by_gov"].items()]
        sheets_out.insert(4, ("مشاكل المناطق (محافظات)", pd.DataFrame(nb)))
        sheets_out.insert(5, ("مناطق مشكوك فيها", pd.DataFrame(gp)))
    if cap:
        gm = pd.DataFrame([{"المحافظة": g["gov"], "البند": g["band"], **{k: gg[g["gov_code"]].get(k, 0) for k in lo if k != UNKNOWN_LEVEL},
                            "إجمالي الجروبات": sum(gg[g["gov_code"]].values())} for g in rep["govs"] if g["gov_code"] != UNKNOWN_GOV])
        if not gm.empty:
            gm = pd.concat([gm, pd.DataFrame([{"المحافظة": "الإجمالي", "البند": "",
                                                **{c: int(gm[c].sum()) for c in gm.columns[2:]}}])], ignore_index=True)
        sheets_out.append(("جروبات محافظة × ليفيل", gm))
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="xlsxwriter") as xw:
        for name, d in sheets_out:
            d.to_excel(xw, sheet_name=name, index=False)
            xw.sheets[name].right_to_left()
    out.seek(0)
    return out


# ---------------- الـ history ----------------
def total_groups(rep, cap):
    if not rep or not cap:
        return None
    return sum(sum(_gov_groups(g, cap).values()) for g in rep["govs"])


def save(rep, file_name, user, cap=None):
    """بيحفظ نتيجة التحليل (أرقام مجمّعة) + صفوف الطلاب اللي عندهم مشاكل في المنطقة بس (مش الشيت كله).
    الصفوف دي فيها الرقم القومي والعنوان عشان توصلهم وتصلّحهم، وبتتحفظ في data/ زي نتايج التوزيع."""
    flag_rows = rep.pop("_flags", [])
    pid = uuid.uuid4().hex[:12]
    con = db.connect()
    try:
        db.plan_insert(con, {"id": pid, "user": user, "name": file_name, "file_name": file_name,
                             "n_students": rep["total"], "cap": cap, "report": rep, "flags": flag_rows})
    finally:
        con.close()
    return pid


def history(limit=200):
    con = db.connect()
    try:
        rows = db.plans_list(con, limit)
    finally:
        con.close()
    out = []
    for r in rows:
        rep = json.loads(r.pop("report") or "null")
        r["n_govs"] = sum(1 for g in (rep or {}).get("govs", []) if g["gov_code"] != UNKNOWN_GOV)
        r["n_levels"] = len((rep or {}).get("levels", []))
        r["groups"] = total_groups(rep, r.get("cap"))
        r["band2"] = next((b["students"] for b in (rep or {}).get("bands", []) if b["band"] == engine.BAND_2), 0)
        out.append(r)
    return out


def get(plan_id):
    con = db.connect()
    try:
        return db.plan_get(con, plan_id)
    finally:
        con.close()


def update(plan_id, **fields):
    con = db.connect()
    try:
        db.plan_update(con, plan_id, **fields)
    finally:
        con.close()


def delete(plan_id):
    con = db.connect()
    try:
        db.plan_delete(con, plan_id)
    finally:
        con.close()


# ---------------- مراجعة المناطق ----------------
FLAG_COLS = [("row_no", "رقم الصف في الشيت"), ("nid", "الرقم القومي"), ("gov_raw", "المحافظة (زي الشيت)"), ("area", "المنطقة"),
             ("address", "العنوان"), ("level", "الليفيل"), ("kind_ar", "المشكلة"), ("conf", "الدرجة"),
             ("other_gov_ar", "المحافظة المحتملة"), ("reason", "التفاصيل")]


def _filter_flags(rows, kind="", gov="", conf="", area=None, q=""):
    q = (q or "").strip()
    out = []
    for r in rows:
        if kind and r["kind"] != kind or gov and r["gov_code"] != gov or conf and r["conf"] != conf:
            continue
        if area is not None and (r["area"] or "—") != area:
            continue
        if q and q not in str(r["nid"]) and q not in (r["area"] or "") and q not in (r["address"] or "") and q != str(r["row_no"]):
            continue
        out.append(r)
    return out


def flags(plan_id, page=1, per_page=50, **filters):
    con = db.connect()
    try:
        rows = db.plan_flags(con, plan_id)
    finally:
        con.close()
    if rows is None:
        return None
    rows = _filter_flags(rows, **filters)
    page = max(1, int(page or 1))
    per_page = max(10, min(500, int(per_page or 50)))
    return {"total": len(rows), "page": page, "per_page": per_page,
            "rows": [{**r, "kind_ar": areacheck.KIND_AR[r["kind"]]} for r in rows[(page - 1) * per_page: page * per_page]]}


def flags_excel(plan_id, **filters):
    con = db.connect()
    try:
        rows = db.plan_flags(con, plan_id)
    finally:
        con.close()
    if rows is None:
        return None
    rows = _filter_flags(rows, **filters)
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="xlsxwriter") as xw:
        sheets_out = [("كل المشاكل", rows)] + [(areacheck.KIND_AR[k], [r for r in rows if r["kind"] == k])
                                               for k in (areacheck.NO_AREA, areacheck.STREET, areacheck.OTHER_GOV)]
        for name, rr in sheets_out:
            d = pd.DataFrame([{ar: (areacheck.KIND_AR[r["kind"]] if c == "kind_ar" else r.get(c, "")) for c, ar in FLAG_COLS}
                              for r in rr], columns=[ar for _, ar in FLAG_COLS])
            d.to_excel(xw, sheet_name=name, index=False)
            ws = xw.sheets[name]
            ws.right_to_left()
            ws.set_column(0, 0, 10)
            ws.set_column(1, 1, 17, xw.book.add_format({"num_format": "@"}))
            ws.set_column(2, 9, 22)
    out.seek(0)
    return out

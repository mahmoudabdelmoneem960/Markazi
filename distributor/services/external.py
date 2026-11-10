"""تشغيلة خارجية: شيت توزيع اتعمل برا السيستم (يدوي / سيستم تاني) بيتقارن بتشغيلة من السيستم.

الشيت بيتقرا بأي أسماء أعمدة معروفة:
  الرقم القومي (لازم) + واحد على الأقل من: كود المركز / اسم المركز / كود الجروب. والحالة والمسافة اختياري.
كل صف بيتربط بالطالب في تشغيلة السيستم (بالرقم القومي)، والمركز بيتحوّل لكود مركز السيستم (بالكود، أو من كود الجروب،
أو من اسم المركز). بعد كده بنفحص التوزيع الخارجي بنفس قواعد السيستم: المحافظة، الليفيل، أقصى مسافة، سعة الجروب.
الملف المحوَّل بيتحفظ في data/external/<id>/ وبيتقارن بـ compare.compare(run_id, id) زي أي تشغيلة.
"""
import gzip
import json
import re
import time
import uuid

import numpy as np
import pandas as pd

from distributor import config
from distributor.core import engine
from distributor.services import runs, sheets

OK = "تم التوزيع"
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_NOT_ASSIGNED = {"لم يتم التوزيع", "لم يوزع", "غير موزع", "مش موزع", "unassigned", "not assigned", "waiting", "انتظار"}
PROBLEMS = {"unknown_center": "مركز مش موجود في تشغيلة السيستم", "other_gov": "مركز في محافظة غير محافظة الطالب",
            "level_mismatch": "ليفيل الجروب مختلف عن ليفيل الطالب", "over_max_km": "أبعد من الحد الأقصى للمسافة",
            "over_capacity": "الجروب فيه طلاب أكتر من سعته", "no_center": "موزع من غير مركز أو جروب",
            "not_in_run": "الطالب مش موجود في تشغيلة السيستم", "duplicate": "الرقم القومي متكرر في الملف الخارجي"}


def ext_dir():
    return config.DATA_DIR / "external"


def is_external(x):
    return bool(x) and str(x).startswith("ext") and str(x).isalnum()


def _dir(eid):
    if not is_external(eid):
        raise ValueError("bad external id")
    return ext_dir() / eid


def _norm_col(c):
    return re.sub(r"[\s_\-]+", " ", str(c).strip().lower())


def _pick(cols, names=(), contains=(), avoid=()):
    nc = {_norm_col(c): c for c in cols}
    for n in names:
        if _norm_col(n) in nc:
            return nc[_norm_col(n)]
    for c in cols:
        lc = _norm_col(c)
        if any(k in lc for k in contains) and not any(a in lc for a in avoid):
            return c
    return None


def detect_columns(X):
    cols = list(X.columns)
    avoid = ("parent", "ولي", "أب", "اب ")
    return {
        "nid": _pick(cols, ("Student NID", "national_id", "national id", "nid", "الرقم القومي", "رقم قومي", "الرقم القومى"),
                     ("nid", "national", "قومي", "قومى"), avoid),
        "center": _pick(cols, ("center_code", "Center Code", "كود المركز", "center"), ("center code", "كود المركز")),
        "center_name": _pick(cols, ("Center Name", "center_name", "اسم المركز", "المركز"), ("center name", "اسم المركز")),
        "group": _pick(cols, ("group_code", "Group Code", "group", "كود الجروب", "الجروب", "كود المجموعة", "المجموعة"),
                       ("group code", "كود الجروب", "كود المجموعه")),
        "status": _pick(cols, ("status", "الحالة", "حالة التوزيع", "الحاله")),
        "distance": _pick(cols, ("distance_km", "distance", "المسافة", "المسافة كم", "المسافه")),
    }


def _nid(v):
    s = sheets.clean(v).translate(_AR_DIGITS)
    return re.sub(r"\.0+$", "", re.sub(r"\s+", "", s))


def import_sheet(run_id, name, raw, user=""):
    """بيقرا الشيت الخارجي، يربطه بتشغيلة السيستم، يفحصه، ويحفظه. بيرجّع ملخص (id، الأعمدة، الربط، المشاكل)."""
    S = runs.load_students(run_id)
    G = runs.load_groups(run_id)
    res = runs.load_result(run_id) or {}
    if S is None or G is None:
        raise ValueError("تشغيلة السيستم دي مالهاش نتيجة (لسه شغالة أو اتمسحت).")
    nid_sys = next((c for c in S.columns if c.strip().lower() in ("student nid", "nid")), None)
    if nid_sys is None or S[nid_sys].astype(str).str.fullmatch(r"\d{14}").mean() < 0.95:
        raise ValueError("تشغيلة السيستم دي أرقامها القومية مش سليمة، فمش هينفع نربط بيها شيت خارجي. اختار تشغيلة تانية.")
    X = sheets.parse_file(name, raw)
    X.columns = [str(c).strip() for c in X.columns]
    cols = detect_columns(X)
    if not cols["nid"]:
        raise ValueError("الشيت الخارجي لازم فيه عمود الرقم القومي (Student NID / national_id / الرقم القومي).")
    if not (cols["center"] or cols["center_name"] or cols["group"]):
        raise ValueError("الشيت الخارجي لازم فيه عمود واحد على الأقل من: كود المركز، اسم المركز، كود الجروب.")
    if not len(X):
        raise ValueError("الشيت الخارجي فاضي.")

    params = (res.get("summary") or {}).get("params") or {}
    max_km = float(params.get("max_km") or engine.MAX_KM)
    detour = float(params.get("detour") or engine.DETOUR)

    # ---- مراجع تشغيلة السيستم ----
    G = G.copy()
    G["center_code"] = G["center_code"].astype(str)
    G["group_code"] = G["group_code"].astype(str)
    cen = G.drop_duplicates("center_code").set_index("center_code")
    code_of = {c.strip().lower(): c for c in cen.index}
    grp = {str(r.group_code).strip().lower(): r for r in G.itertuples()}
    names = {}
    for c, nm in cen["center_name"].astype(str).items():
        names.setdefault(engine.norm_ar(nm), set()).add(c)
    name_of = {k: next(iter(v)) for k, v in names.items() if len(v) == 1 and k not in ("", "nan")}
    sysS = S.set_index(S[nid_sys].astype(str))
    sysS = sysS[~sysS.index.duplicated()]

    # ---- كل صف في الشيت الخارجي ----
    rows, issues, unresolved = [], [], {}
    seen = set()
    get = lambda c, i: sheets.clean(X[c].iat[i]) if c else ""  # noqa: E731
    for i in range(len(X)):
        nid = _nid(X[cols["nid"]].iat[i])
        c_raw, n_raw, g_raw, st_raw = get(cols["center"], i), get(cols["center_name"], i), get(cols["group"], i), get(cols["status"], i)
        center, group = "", g_raw
        gk = grp.get(g_raw.lower()) if g_raw else None
        if c_raw and c_raw.lower() in code_of:
            center = code_of[c_raw.lower()]
        elif gk is not None:
            center = gk.center_code
        elif n_raw and engine.norm_ar(n_raw) in name_of:
            center = name_of[engine.norm_ar(n_raw)]
        elif c_raw or n_raw:
            center = c_raw or n_raw                                   # مش معروف في السيستم: بيفضل زي ما هو
            unresolved[center] = unresolved.get(center, 0) + 1
        if gk is not None:
            group = gk.group_code
        s_low = st_raw.lower()
        if st_raw:
            vp = "vp" in s_low or st_raw in _NOT_ASSIGNED or s_low in _NOT_ASSIGNED
        else:
            vp = not (center or group)
        if nid in seen:
            issues.append({"key": nid, "row_no": i + 2, "problem": "duplicate", "center": center, "group": group, "details": ""})
            continue
        seen.add(nid)
        s = sysS.loc[nid] if nid in sysS.index else None
        r = {"row_no": i + 2, "Student NID": nid, "status": "VP" if vp else OK, "center_code": "" if vp else (center or "?"),
             "group_code": "" if vp else group, "distance_km": "", "same_area": "",
             "gov": s["gov"] if s is not None and "gov" in s else "", "gov_code": s["gov_code"] if s is not None else "",
             "Area": s["Area"] if s is not None and "Area" in s else "", "level_code": s["level_code"] if s is not None else "",
             "البند": s["البند"] if s is not None and "البند" in s else ""}
        if s is None:
            issues.append({"key": nid, "row_no": i + 2, "problem": "not_in_run", "center": center, "group": group, "details": ""})
        if not vp:
            if not (center or group):
                issues.append({"key": nid, "row_no": i + 2, "problem": "no_center", "center": "", "group": "", "details": st_raw})
            elif center not in cen.index:
                issues.append({"key": nid, "row_no": i + 2, "problem": "unknown_center", "center": center, "group": group, "details": ""})
            # المسافة: من الشيت لو موجودة، وإلا تقدير بنفس طريقة التشغيلة (خط مستقيم × المعامل) من موقع الطالب في السيستم
            d = pd.to_numeric(get(cols["distance"], i), errors="coerce") if cols["distance"] else np.nan
            if np.isnan(d) and s is not None and center in cen.index:
                la, lo = pd.to_numeric(s.get("lat"), errors="coerce"), pd.to_numeric(s.get("lon"), errors="coerce")
                if not (np.isnan(la) or np.isnan(lo)):
                    d = round(float(engine.haversine(la, lo, float(cen.at[center, "lat"]), float(cen.at[center, "lon"]))) * detour, 1)
            if not np.isnan(d):
                r["distance_km"] = round(float(d), 1)
            if s is not None and center in cen.index:
                c_gov = str(cen.at[center, "gov"]).strip().lower()
                r["same_area"] = "نعم" if engine.area_key(s.get("Area", "")) == engine.area_key(cen.at[center, "area"]) else "لا"
                if s["gov_code"] and c_gov != s["gov_code"]:
                    issues.append({"key": nid, "row_no": i + 2, "problem": "other_gov", "center": center, "group": group,
                                   "details": f"الطالب {s['gov_code']} ← المركز {c_gov}"})
                lv = gk.level_code if gk is not None else engine.group_key(group) if group else None
                if lv and s["level_code"] and lv != s["level_code"]:
                    issues.append({"key": nid, "row_no": i + 2, "problem": "level_mismatch", "center": center, "group": group,
                                   "details": f"الطالب {s['level_code']} ← الجروب {lv}"})
            if r["distance_km"] != "" and r["distance_km"] > max_km:
                issues.append({"key": nid, "row_no": i + 2, "problem": "over_max_km", "center": center, "group": group,
                               "details": f"{r['distance_km']} كم (الحد {max_km:g})"})
        rows.append(r)
    B = pd.DataFrame(rows)
    # السعة: عدد طلاب الجروب في الملف الخارجي مقابل سعته في السيستم
    over = []
    if len(B):
        cnt = B[(B["status"] == OK) & B["group_code"].isin(G["group_code"])].groupby("group_code").size()
        cap = G.set_index("group_code")["capacity"]
        for g, k in cnt.items():
            if k > int(cap.get(g, 0)):
                over.append({"group": g, "center": grp[g.lower()].center_code, "students": int(k), "capacity": int(cap.get(g, 0))})
                issues.append({"key": "", "row_no": "", "problem": "over_capacity", "center": grp[g.lower()].center_code, "group": g,
                               "details": f"{k} طالب والسعة {int(cap.get(g, 0))}"})

    eid = "ext" + uuid.uuid4().hex[:10]
    d = _dir(eid)
    d.mkdir(parents=True)
    with gzip.open(d / "students.csv.gz", "wt", encoding="utf-8", newline="") as f:
        B.to_csv(f, index=False)
    counts = {k: sum(1 for x in issues if x["problem"] == k) for k in PROBLEMS}
    meta = {"id": eid, "name": name, "run_id": run_id, "created_at": time.strftime("%Y-%m-%d %H:%M:%S"), "user": user,
            "rows": int(len(X)), "students": int(len(B)), "assigned": int((B["status"] == OK).sum()) if len(B) else 0,
            "matched": int(B["Student NID"].isin(sysS.index).sum()) if len(B) else 0,
            "columns": {k: v for k, v in cols.items() if v}, "unresolved_centers": dict(sorted(unresolved.items(), key=lambda x: -x[1])[:30]),
            "counts": counts, "issues_total": len(issues), "over_capacity": over[:100], "max_km": max_km,
            "distance_note": "من الشيت" if cols["distance"] else f"تقدير (خط مستقيم × {detour:g}) من موقع الطالب في السيستم"}
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, default=str), encoding="utf-8")
    pd.DataFrame(issues, columns=["key", "row_no", "problem", "center", "group", "details"]).to_csv(
        d / "issues.csv", index=False, encoding="utf-8-sig")
    return meta


def load_meta(eid):
    p = _dir(eid) / "meta.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def load_students(eid):
    p = _dir(eid) / "students.csv.gz"
    if not p.exists():
        return None
    with gzip.open(p, "rt", encoding="utf-8") as f:
        return pd.read_csv(f, dtype=str, keep_default_na=False)


def load_issues(eid):
    p = _dir(eid) / "issues.csv"
    if not p.exists():
        return pd.DataFrame(columns=["key", "row_no", "problem", "center", "group", "details"])
    df = pd.read_csv(p, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    df["problem_ar"] = df["problem"].map(PROBLEMS)
    return df


def list_external(run_id=None):
    if not ext_dir().exists():
        return []
    out = []
    for p in ext_dir().iterdir():
        if is_external(p.name) and (p / "meta.json").exists():
            m = json.loads((p / "meta.json").read_text(encoding="utf-8"))
            if not run_id or m.get("run_id") == run_id:
                out.append({k: m.get(k) for k in ("id", "name", "run_id", "created_at", "students", "assigned", "matched", "issues_total")})
    return sorted(out, key=lambda x: x["created_at"] or "", reverse=True)


def delete(eid):
    import shutil
    shutil.rmtree(_dir(eid), ignore_errors=True)

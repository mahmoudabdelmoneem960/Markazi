"""الفحص المسبق: قبل ما نضيّع وقت في توزيع، نعرض مشاكل الشيتين وإحصائياتهم."""
import re

import numpy as np
import pandas as pd

from distributor.core import engine, geoprep
from distributor.services import sheets


def _issue(issues, level, text):
    issues.append({"level": level, "text": text})


def preflight(S_raw, C_raw):
    """S_raw/C_raw: DataFrames زي ما اتقروا من الملفات. بيرجّع dict للعرض + ok (ينفع نشغّل؟)."""
    issues = []
    S, note = sheets.adapt_students(S_raw)
    S = S.copy()
    C = C_raw.copy()
    S.columns = [str(c).strip() for c in S.columns]
    C.columns = [str(c).strip() for c in C.columns]
    sc, cc = sheets.student_columns(S), sheets.center_columns(C)
    n = len(S)
    st = {"rows": int(n), "adapted_note": note, "columns": {k: v for k, v in sc.items() if v}}

    # ---- الطلاب ----
    missing = [name for k, name in (("level", "Level"), ("gov", "gov (المحافظة)")) if not sc[k]]
    if not sc["lat"] or not sc["lon"]:
        if not sc["area"] or not sc["addr"]:
            missing.append("Latitude/Longitude أو (Area + Adress)")
    if missing:
        _issue(issues, "error", "شيت الطلاب ناقص الأعمدة دي: " + "، ".join(missing))
    st["missing"] = missing
    if sc["nid"]:
        nid = engine._clean_id(S[sc["nid"]])
        valid = nid.str.fullmatch(r"\d{14}")
        st["nid_invalid"] = int((~valid).sum())
        st["nid_dup"] = int(nid[valid].duplicated().sum())
        if st["nid_invalid"] > 0.01 * n:
            _issue(issues, "error", f"{st['nid_invalid']:,} رقم قومي مش 14 رقم (غالبًا الشيت اتحفظ CSV والرقم اتحول لصيغة علمية). صدّره تاني والعمود Text.")
        elif st["nid_invalid"]:
            _issue(issues, "warn", f"{st['nid_invalid']} رقم قومي مش 14 رقم.")
        if st["nid_dup"]:
            _issue(issues, "warn", f"{st['nid_dup']:,} رقم قومي مكرر (الطالب هيتوزع مرتين).")
    else:
        st["nid_invalid"] = st["nid_dup"] = None
        _issue(issues, "warn", "مفيش عمود رقم قومي: النتيجة هتتعرّف بالصف بس (row_no).")
    if sc["gov"]:
        gov_codes = S[sc["gov"]].map(lambda g: engine.GOV_MAP.get(engine.norm_ar(g)))
        unk = S.loc[gov_codes.isna(), sc["gov"]].astype(str).str.strip()
        st["unknown_gov"] = unk.value_counts().head(10).to_dict()
        st["unknown_gov_count"] = int(gov_codes.isna().sum())
        st["govs"] = gov_codes.value_counts().to_dict()
        if st["unknown_gov_count"]:
            _issue(issues, "warn", f"{st['unknown_gov_count']:,} طالب محافظتهم غير معروفة (هيبقوا VP): " + "، ".join(list(st["unknown_gov"])[:5]))
    else:
        gov_codes = pd.Series([None] * n)
        st["govs"] = {}
    if sc["level"]:
        lv = S[sc["level"]].map(engine.level_key)
        st["levels"] = lv.value_counts().to_dict()
        st["no_level"] = int(lv.isna().sum())
        if st["no_level"]:
            _issue(issues, "warn", f"{st['no_level']:,} طالب الليفيل بتاعهم مش مفهوم (هيبقوا VP).")
    else:
        lv = pd.Series([None] * n)
        st["levels"], st["no_level"] = {}, n
    st["no_area"] = int(S[sc["area"]].map(sheets.clean).eq("").sum()) if sc["area"] else n
    st["no_addr"] = int(S[sc["addr"]].map(sheets.clean).eq("").sum()) if sc["addr"] else n
    if sc["lat"] and sc["lon"]:
        la = pd.to_numeric(S[sc["lat"]], errors="coerce").to_numpy(float)
        lo = pd.to_numeric(S[sc["lon"]], errors="coerce").to_numpy(float)
        okc = geoprep._bbox_ok(la, lo)
        st["with_coords"] = int(okc.sum())
        if okc.sum() >= 300 and sc["area"] and sc["addr"]:
            gc = gov_codes.to_numpy(object)
            sus = geoprep._sheet_coords_suspect(gc[okc], S[sc["area"]].fillna("").to_numpy(object)[okc],
                                                S[sc["addr"]].fillna("").to_numpy(object)[okc], la[okc], lo[okc])
            st["coords_suspect"] = sus
            if sus:
                _issue(issues, "warn", "إحداثيات الطلاب في الشيت شكلها تقريبية (مش حقيقية): " + " | ".join(sus)
                       + " — الأفضل تختار «من العناوين دايمًا».")
    else:
        st["with_coords"] = 0
    if st["no_area"] or st["no_addr"]:
        _issue(issues, "info", f"{st['no_area']:,} طالب من غير منطقة و{st['no_addr']:,} من غير عنوان: موقعهم هيتحدد بدقة أقل.")

    # ---- المراكز ----
    ct = {"rows": int(len(C)), "columns": {k: v for k, v in cc.items() if v}}
    cmiss = [name for k, name in (("code", "Center Code"), ("group", "Group Code"), ("city", "City"), ("area", "Area"), ("cap", "سعة القاعة")) if not cc[k]]
    ct["missing"] = cmiss
    if cmiss:
        _issue(issues, "error", "شيت المراكز ناقص الأعمدة دي: " + "، ".join(cmiss))
        return {"students": st, "centers": ct, "cross": [], "issues": issues, "ok": False}
    keys = C[cc["group"]].map(engine.group_key)
    ct["bad_group_codes"] = int(keys.isna().sum())
    if ct["bad_group_codes"]:
        _issue(issues, "warn", f"{ct['bad_group_codes']} جروب كوده مش بصيغة L1_xxxx / L3WB_xxxx (هيتجاهلوا).")
    cap = pd.to_numeric(C[cc["cap"]], errors="coerce").fillna(0).astype(int)
    ct["no_capacity"] = int((cap <= 0).sum())
    if ct["no_capacity"]:
        _issue(issues, "warn", f"{ct['no_capacity']} جروب سعته صفر أو ناقصة.")
    ct["dup_groups"] = int(C[cc["group"]].duplicated().sum())
    if ct["dup_groups"]:
        _issue(issues, "warn", f"{ct['dup_groups']} كود جروب مكرر في الشيت.")
    codes = C[cc["code"]].astype(str)
    ct["centers"] = int(codes.nunique())
    ct["seats"] = int(cap.sum())
    cgov = C[cc["city"]].astype(str).str.strip().str.lower()
    ct["levels"] = cap.groupby(keys).sum().to_dict()
    ct["govs"] = cap.groupby(cgov).sum().to_dict()
    has_coords = set()
    if cc["lat"] and cc["lon"]:
        la = pd.to_numeric(C[cc["lat"]], errors="coerce")
        lo = pd.to_numeric(C[cc["lon"]], errors="coerce")
        has_coords = set(codes[la.notna() & lo.notna()])
    no_coords = sorted(set(codes) - has_coords)
    ct["centers_no_coords"] = no_coords
    has_link = set(codes[C[cc["url"]].astype(str).str.startswith("http")]) if cc["url"] else set()
    has_addr = set(codes[C[cc["addr"]].map(sheets.clean).ne("")]) if cc["addr"] else set()
    ct["no_coords_with_link"] = len([c for c in no_coords if c in has_link])
    ct["no_coords_with_addr_only"] = len([c for c in no_coords if c not in has_link and c in has_addr])
    ct["no_location_at_all"] = [c for c in no_coords if c not in has_link and c not in has_addr]
    if no_coords:
        _issue(issues, "info", f"{len(no_coords)} مركز من غير إحداثيات في الشيت: هيتاخدوا من قاعدة المواقع لو متسجلين، وإلا من لينك Google أو العنوان"
               + (f" ({ct['no_coords_with_link']} ليهم لينك)" if ct["no_coords_with_link"] else "") + ".")
    if ct["no_location_at_all"]:
        _issue(issues, "warn", f"{len(ct['no_location_at_all'])} مركز مالوش لا إحداثيات ولا لينك ولا عنوان: هيتجاهلوا لو مش في قاعدة المواقع: "
               + "، ".join(ct["no_location_at_all"][:8]))
    # مقابلة الشيت بقاعدة المواقع: مراكز هتتغير مواقعها أو هتتضاف (التشغيل بيحدّث القاعدة من الشيت)
    ct["db_moved"], ct["db_new"], ct["db_manual_differs"] = [], [], []
    if cc["lat"] and cc["lon"]:
        try:
            from distributor import db as _db
            from distributor.core import locdb as _locdb
            con = _db.connect()
            try:
                dbrows = {r["code"]: dict(r) for r in con.execute("SELECT code, lat, lon, source FROM centers")}
            finally:
                con.close()
            for code, rec in _locdb.center_records(C).items():
                r = dbrows.get(code)
                if r is None:
                    ct["db_new"].append(code)
                elif rec["lat"] is not None and r["lat"] is not None and _locdb.km(rec["lat"], rec["lon"], r["lat"], r["lon"]) > 0.05:
                    (ct["db_manual_differs"] if r["source"] in ("manual", "google") else ct["db_moved"]).append(code)
        except Exception:  # noqa
            pass
        if ct["db_moved"]:
            _issue(issues, "warn", f"{len(ct['db_moved'])} مركز موقعه في الشيت مختلف عن المسجّل في قاعدة المواقع (أكتر من 50 م) وهيتحدّث من الشيت، "
                   f"وكاش العناوين هيتجدّد (أول تشغيل هيبقى أبطأ): " + "، ".join(ct["db_moved"][:10]))
        if ct["db_manual_differs"]:
            _issue(issues, "info", f"{len(ct['db_manual_differs'])} مركز ليه تصحيح يدوي محفوظ مختلف عن الشيت (التصحيح المحفوظ هو اللي بيتستخدم).")
        if ct["db_new"] and dbrows:
            _issue(issues, "info", f"{len(ct['db_new'])} مركز جديد مش في قاعدة المواقع هيتضاف منها.")

    # ---- مقابلة الطلب بالمقاعد (محافظة × ليفيل) ----
    cross = []
    if sc["gov"] and sc["level"]:
        dem = pd.DataFrame({"g": gov_codes, "k": lv}).dropna().value_counts().to_dict()
        seats = cap.groupby([cgov, keys]).sum().to_dict()
        for (g, k), cnt in sorted(dem.items(), key=lambda x: -x[1]):
            s = int(seats.get((g, k), 0))
            if cnt > s:
                cross.append({"gov": g, "level_code": k, "students": int(cnt), "seats": s, "deficit": int(cnt - s)})
        no_seats = [x for x in cross if x["seats"] == 0]
        if no_seats:
            _issue(issues, "warn", f"{len(no_seats)} تركيبة (محافظة × ليفيل) مفيش ليها أي جروب في محافظتها، مثلًا: "
                   + "، ".join(f"{x['gov']} {x['level_code']} ({x['students']})" for x in no_seats[:5]))
        tot_def = sum(x["deficit"] for x in cross)
        if tot_def:
            _issue(issues, "info", f"العجز الإجمالي في المقاعد (قبل التوزيع): {tot_def:,} طالب أكتر من المقاعد المتاحة في محافظاتهم.")
    ok = not any(i["level"] == "error" for i in issues)
    return {"students": st, "centers": ct, "cross": cross[:200], "issues": issues, "ok": ok}

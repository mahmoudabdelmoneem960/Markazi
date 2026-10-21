"""قراءة شيتات الطلاب والمراكز بأي شكل من الأشكال المدعومة وتحويلها للشكل اللي المحرك بيفهمه."""
import io
import re

import pandas as pd

from distributor.core import engine

LVL_WORD = {"CY": "cyber", "DT": "data", "WB": "web", "DA": "digital", "EM": "embedded"}
LVL_NAMES = {"first": "L1", "second": "L2", "third_ai": "L3DT", "third_cyber_security": "L3CY", "third_web": "L3WB",
             "third_digital": "L3DA", "third_embedded": "L3EM", "forth_ai": "L4DT", "forth_cyber_security": "L4CY",
             "forth_web": "L4WB", "forth_digital": "L4DA", "forth_embedded": "L4EM", "fifth_ai": "L5DT",
             "fifth_cyber_security": "L5CY", "fifth_web": "L5WB", "fifth_digital": "L5DA", "fifth_embedded": "L5EM"}
EMPTY = ("none", "nan", "null", "")


def parse_file(name, raw):
    """CSV/Excel كنصوص (عشان الرقم القومي ما يتحولش لرقم علمي). بيقرا أول شيت في ملف Excel."""
    data = io.BytesIO(raw)
    try:
        if str(name).lower().endswith(".csv"):
            try:
                return pd.read_csv(data, dtype=str, encoding="utf-8-sig", low_memory=False)
            except UnicodeDecodeError:
                data.seek(0)
                return pd.read_csv(data, dtype=str, encoding="cp1256", low_memory=False)
        return pd.read_excel(data, dtype=str)
    except Exception as e:  # noqa
        raise ValueError(f"تعذر قراءة الملف '{name}': لازم يكون Excel (.xlsx) أو CSV سليم.") from e


def clean(v):
    s = "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip()
    return "" if s.lower() in EMPTY else s


def adapt_students(S):
    """شيت 'Student DB' المصدَّر من قاعدة البيانات (national_id / city / area / address / level / group)
    بيتحوّل للشكل القياسي (Student NID / gov / Area / Adress / Level). أي شيت تاني بيرجع زي ما هو.
    بيرجّع (الشيت، ملاحظة أو None). الأسماء والتليفونات والإيميلات ما بتدخلش النتيجة."""
    low = {str(c).strip().lower(): c for c in S.columns}
    if "student nid" in low or "gov" in low or not ("national_id" in low and "city" in low):
        return S, None
    inv = {code: ar for ar, code in engine.GOV_MAP.items()}
    out = pd.DataFrame({"Student NID": S[low["national_id"]].map(clean)})
    out["gov"] = S[low["city"]].map(lambda c: inv.get(clean(c).lower(), clean(c)))
    out["Area"] = S[low["area"]].map(clean) if "area" in low else ""
    out["Adress"] = S[low["address"]].map(clean) if "address" in low else ""
    groups = S[low["group"]].map(clean) if "group" in low else pd.Series([""] * len(S))
    levels = S[low["level"]].map(clean) if "level" in low else pd.Series([""] * len(S))

    def level_text(g, lv):
        m = re.search(r"-(L\d[A-Z]*)-", g)
        code = m.group(1) if m else LVL_NAMES.get(lv.lower())
        if not code:
            return ""
        mm = re.match(r"L(\d)([A-Z]*)", code)
        return f"Level ({mm.group(1)})" + (f": {LVL_WORD.get(mm.group(2), mm.group(2))}" if mm.group(2) else "")
    out["Level(25-26)"] = [level_text(g, lv) for g, lv in zip(groups, levels)]
    if "group" in low:
        out["old_group"] = groups
    if "center name" in low:
        out["old_center_name"] = S[low["center name"]].map(clean)
    note = (f"الشيت اتقرا كـ«Student DB»: city ← gov وnational_id ← Student NID وaddress ← Adress، والليفيل اتحسب من كود الجروب القديم "
            f"({int((out['Area'] == '').sum()):,} طالب من غير منطقة و{int((out['Adress'] == '').sum()):,} من غير عنوان).")
    return out, note


def student_columns(S):
    """الأعمدة المهمة في شيت الطلاب (أو None لو ناقصة)."""
    return {
        "nid": engine._find(S, names={"student nid", "nid", "national id", "الرقم القومي", "رقم قومي"}, contains=["nid"]),
        "gov": engine._find(S, names={"gov", "governorate", "المحافظة", "المحافظه"}),
        "area": engine._find(S, names={"area", "المنطقة", "المنطقه"}),
        "addr": engine._find(S, names={"adress", "address", "العنوان", "عنوان"}, contains=["adress", "address", "عنوان"]),
        "level": engine._find(S, startswith="level"),
        "lat": engine._find(S, names=engine.LAT), "lon": engine._find(S, names=engine.LON),
    }


def center_columns(C):
    return {
        "code": "Center Code" if "Center Code" in C.columns else None,
        "group": "Group Code" if "Group Code" in C.columns else None,
        "city": "City" if "City" in C.columns else None,
        "area": "Area" if "Area" in C.columns else None,
        "name": "Center Name" if "Center Name" in C.columns else None,
        "cap": engine._find(C, contains=["سعة", "سعه", "capacity"]),
        "lat": engine._find(C, names=engine.LAT), "lon": engine._find(C, names=engine.LON),
        "addr": next((c for c in C.columns if "عنوان" in str(c) or str(c).lower() in ("address", "full_address")), None),
        "url": next((c for c in C.columns if C[c].astype(str).str.startswith("http").any()), None),
    }

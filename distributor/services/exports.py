"""تصدير: ملف Excel فيه شيت لكل مركز (قوايم الطلاب للمنسقين) + شيت VP لكل محافظة."""
import re

import pandas as pd

from distributor.services import runs


def _sheet_name(s, used):
    s = re.sub(r"[\[\]:*?/\\]", " ", str(s)).strip()[:28] or "sheet"
    base, k = s, 1
    while s in used:
        k += 1
        s = f"{base[:25]}~{k}"
    used.add(s)
    return s


def per_center_xlsx(run_id):
    """بيكتب data/runs/<id>/per_center.xlsx (مرة واحدة) وبيرجّع مساره."""
    p = runs.run_dir(run_id) / runs.FILES["per_center"]
    if p.exists():
        return p
    st = runs.load_students(run_id)
    if st is None:
        raise ValueError("مفيش نتيجة للتشغيل ده.")
    nid = next((c for c in st.columns if c.strip().lower() in ("student nid", "nid")), "row_no")
    keep = [c for c in (nid, "gov", "Area", "Adress", "level_code", "group_code", "day", "time", "distance_km", "same_area") if c in st.columns]
    ok = st[st["status"] != "VP"]
    used = set()
    with pd.ExcelWriter(p, engine="xlsxwriter") as w:
        summary = (ok.groupby(["center_code", "center_name", "center_city"]).size().rename("students").reset_index()
                   .sort_values(["center_city", "center_code"]))
        summary.to_excel(w, sheet_name=_sheet_name("ملخص المراكز", used), index=False)
        for (code, name), g in ok.groupby(["center_code", "center_name"], sort=True):
            g.sort_values(["group_code", "distance_km"])[keep].to_excel(w, sheet_name=_sheet_name(code, used), index=False)
        vp = st[st["status"] == "VP"]
        vcols = [c for c in (nid, "gov", "Area", "Adress", "level_code", "nearest_center", "distance_km", "note") if c in vp.columns]
        for gov, g in vp.groupby("gov", sort=True):
            g[vcols].to_excel(w, sheet_name=_sheet_name(f"VP {gov}", used), index=False)
    return p

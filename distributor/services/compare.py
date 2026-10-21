"""مقارنة تشغيلين: نفس الطالب راح فين في كل تشغيل؟"""
import io

import numpy as np
import pandas as pd

from distributor.services import runs

OK = "تم التوزيع"
GOV_AR = {"alexandria": "الإسكندرية", "cairo": "القاهرة", "giza": "الجيزة", "qalyubia": "القليوبية", "dakahlia": "الدقهلية",
          "sharqia": "الشرقية", "gharbia": "الغربية", "monufia": "المنوفية", "beheira": "البحيرة", "sohag": "سوهاج",
          "damietta": "دمياط", "fayoum": "الفيوم", "minya": "المنيا", "beni_suef": "بني سويف", "kafr_el_sheikh": "كفر الشيخ",
          "port_said": "بورسعيد", "suez": "السويس", "ismailia": "الإسماعيلية", "south_sinai": "جنوب سيناء",
          "new_valley": "الوادي الجديد", "north_sinai": "شمال سيناء", "assiut": "أسيوط", "luxor": "الأقصر", "aswan": "أسوان",
          "qena": "قنا", "red_sea": "البحر الأحمر", "matrouh": "مطروح"}


def _key_col(df):
    c = next((c for c in df.columns if c.strip().lower() in ("student nid", "nid")), None)
    if c is not None:
        v = df[c].astype(str)
        if v.str.fullmatch(r"\d{14}").mean() > 0.95 and v.is_unique:
            return c
    return "row_no"


def _prep(df):
    df = df.copy()
    df["distance_km"] = pd.to_numeric(df["distance_km"], errors="coerce")
    return df


def _outcome(df):
    return np.where(df["status"] == "VP", "VP", df["center_code"].replace("", "?"))


def headline(df):
    ok = df["status"] == OK
    d = df.loc[ok, "distance_km"]
    return {"students": int(len(df)), "assigned": int(ok.sum()), "vp": int((~ok).sum()),
            "same_area": int((df["same_area"] == "نعم").sum()),
            "avg_km": round(float(d.mean()), 2) if len(d) else None, "median_km": round(float(d.median()), 2) if len(d) else None,
            "over_20km": int((d > 20).sum())}


def compare(a_id, b_id, max_changed=3000):
    A, B = runs.load_students(a_id), runs.load_students(b_id)
    if A is None or B is None:
        raise ValueError("أحد التشغيلين مالوش ملف نتيجة (لسه شغال أو اتمسح).")
    A, B = _prep(A), _prep(B)
    ka, kb = _key_col(A), _key_col(B)
    key_name = "الرقم القومي" if ka != "row_no" and kb != "row_no" else "رقم الصف"
    if key_name == "رقم الصف":
        ka = kb = "row_no"
    A = A.set_index(A[ka].astype(str))
    B = B.set_index(B[kb].astype(str))
    common = A.index.intersection(B.index)
    a, b = A.loc[common], B.loc[common]
    oa, ob = _outcome(a), _outcome(b)
    ka_ok, kb_ok = (a["status"] == OK).to_numpy(), (b["status"] == OK).to_numpy()
    both = ka_ok & kb_ok
    same_center = both & (a["center_code"].to_numpy() == b["center_code"].to_numpy())
    same_group = same_center & (a["group_code"].to_numpy() == b["group_code"].to_numpy())
    both_vp = ~ka_ok & ~kb_ok
    dd = (a["distance_km"].to_numpy()[both] - b["distance_km"].to_numpy()[both])
    pair = {"key": key_name, "common": int(len(common)), "only_a": int(len(A) - len(common)), "only_b": int(len(B) - len(common)),
            "same_outcome": int((oa == ob).sum()), "same_outcome_pct": round(100 * float((oa == ob).mean()), 1) if len(common) else None,
            "same_group": int((same_group | both_vp).sum()), "same_center": int(same_center.sum()),
            "diff_center": int((both & ~same_center).sum()), "a_assigned_b_vp": int((ka_ok & ~kb_ok).sum()),
            "a_vp_b_assigned": int((~ka_ok & kb_ok).sum()), "both_vp": int(both_vp.sum()),
            "mean_abs_dist_diff": round(float(np.nanmean(np.abs(dd))), 2) if len(dd) else None}
    # حسب المحافظة
    gov_rows = []
    for g, ix in a.groupby("gov_code").groups.items():
        a_, b_ = a.loc[ix], b.loc[ix]
        o1, o2 = _outcome(a_), _outcome(b_)
        gov_rows.append({"gov": GOV_AR.get(g, g), "gov_code": g, "students": int(len(ix)),
                         "assigned_a": int((a_["status"] == OK).sum()), "assigned_b": int((b_["status"] == OK).sum()),
                         "vp_a": int((a_["status"] == "VP").sum()), "vp_b": int((b_["status"] == "VP").sum()),
                         "same_pct": round(100 * float((o1 == o2).mean()), 1),
                         "avg_a": round(float(a_.loc[a_["status"] == OK, "distance_km"].mean()), 1) if (a_["status"] == OK).any() else None,
                         "avg_b": round(float(b_.loc[b_["status"] == OK, "distance_km"].mean()), 1) if (b_["status"] == OK).any() else None})
    gov_rows.sort(key=lambda x: -x["students"])
    # الطلاب اللي اتغيروا
    ch_mask = oa != ob
    cols = [c for c in ("gov", "Area", "Adress", "level_code") if c in a.columns]
    ch = a.loc[ch_mask, cols].copy()
    ch.insert(0, "key", a.index[ch_mask])
    ch["status_a"] = a.loc[ch_mask, "status"].to_numpy()
    ch["center_a"] = a.loc[ch_mask, "center_code"].to_numpy()
    ch["dist_a"] = a.loc[ch_mask, "distance_km"].to_numpy()
    ch["status_b"] = b.loc[ch_mask, "status"].to_numpy()
    ch["center_b"] = b.loc[ch_mask, "center_code"].to_numpy()
    ch["dist_b"] = b.loc[ch_mask, "distance_km"].to_numpy()
    changed = ch.astype(object).where(ch.notna(), None)
    return {"a": {"id": a_id, **headline(A)}, "b": {"id": b_id, **headline(B)}, "pair": pair, "by_gov": gov_rows,
            "changed_total": int(ch_mask.sum()), "changed": changed.head(max_changed).to_dict("records"),
            "changed_cols": list(changed.columns)}


def compare_xlsx(a_id, b_id):
    """ملف Excel كامل للمقارنة (كل الطلاب اللي اتغيروا)."""
    r = compare(a_id, b_id, max_changed=10 ** 7)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as w:
        pd.DataFrame([{"التشغيل": "A", **r["a"]}, {"التشغيل": "B", **r["b"]}]).to_excel(w, sheet_name="ملخص", index=False)
        pd.DataFrame([r["pair"]]).to_excel(w, sheet_name="المقارنة", index=False)
        pd.DataFrame(r["by_gov"]).to_excel(w, sheet_name="حسب المحافظة", index=False)
        pd.DataFrame(r["changed"], columns=r["changed_cols"]).to_excel(w, sheet_name="طلاب نتيجتهم اتغيرت", index=False)
    return buf.getvalue()

"""ماذا لو: لو ضفنا جروب (أو أكتر) في مركز معيّن، كام طالب من الـVP هيتحلّوا؟ (تقدير سريع من غير إعادة توزيع كاملة)."""
import numpy as np
import pandas as pd

from distributor.core import engine
from distributor.services import runs


def simulate(run_id, center_code, level_code, capacity, n_groups=1, osrm_url=None):
    """بيرجّع: عدد المقاعد الجديدة، الـVP المرشحين (في حدود المسافة)، كام هيتحلّوا، وقايمة الأقرب."""
    res = runs.load_result(run_id)
    st = runs.load_students(run_id)
    cen = {c["center_code"]: c for c in runs.centers_geo(run_id)}
    if res is None or st is None or center_code not in cen:
        raise ValueError("التشغيل أو المركز مش موجود.")
    p = res["summary"].get("params", {})
    max_km = float(p.get("max_km") or engine.MAX_KM)
    detour = float(p.get("detour") or engine.DETOUR)
    c = cen[center_code]
    gov = str(c["gov"]).strip().lower()
    vp = st[(st["status"] == "VP") & (st["level_code"] == level_code) & (st["gov_code"] == gov)].copy()
    vp["lat"] = pd.to_numeric(vp["lat"], errors="coerce")
    vp["lon"] = pd.to_numeric(vp["lon"], errors="coerce")
    vp = vp.dropna(subset=["lat", "lon"])
    seats = int(capacity) * int(n_groups)
    if not len(vp):
        return {"seats": seats, "candidates": 0, "rescued": 0, "remaining_vp": 0, "list": [], "distance": "—"}
    straight = engine.haversine(vp["lat"].to_numpy(), vp["lon"].to_numpy(), float(c["lat"]), float(c["lon"]))
    dist, dlabel = straight * detour, f"تقدير طريق (x{detour})"
    if p.get("road_mode") == "osrm":
        url = osrm_url or p.get("osrm_url") or ""
        ok, _, _ = engine.osrm_check(url)
        if ok:
            try:
                m, _ = engine.osrm_matrix(url, np.column_stack([vp["lat"].to_numpy(), vp["lon"].to_numpy()]),
                                          np.array([[float(c["lat"]), float(c["lon"])]]))
                road = m[:, 0]
                bad = np.isnan(road)
                road[bad] = dist[bad]
                dist, dlabel = road, "مسافة طريق (OSRM)"
            except Exception:  # noqa
                pass
    vp["sim_km"] = np.round(dist, 1)
    within = vp[vp["sim_km"] <= max_km].sort_values("sim_km")
    rescued = min(seats, len(within))
    nid_col = next((x for x in vp.columns if x.strip().lower() in ("student nid", "nid")), "row_no")
    lst = [{"nid": r[nid_col], "area": r.get("Area", ""), "km": float(r["sim_km"])} for _, r in within.head(seats).iterrows()]
    return {"seats": seats, "candidates": int(len(within)), "rescued": int(rescued), "remaining_vp": int(len(vp) - rescued),
            "vp_total_level_gov": int(len(vp)), "list": lst, "distance": dlabel, "center": c, "max_km": max_km}

"""التشغيلات: مسودة الرفع → الفحص المسبق → تنفيذ التوزيع في الخلفية → حفظ النتايج في data/runs/<id>/."""
import gzip
import io
import json
import os
import shutil
import time
import uuid
import zipfile
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from distributor import config, db, jobs
from distributor.core import engine, geoprep
from distributor.services import preflight, sheets

FILES = {"result": "result.json", "review": "review.json", "students": "students.csv.gz", "groups": "groups.csv",
         "centers": "centers.csv", "xlsx": "results.xlsx", "qgis": "qgis_layers.zip", "per_center": "per_center.xlsx"}


def run_dir(run_id):
    if not str(run_id).isalnum():
        raise ValueError("bad run id")
    return config.RUNS_DIR / str(run_id)


# ---------------- المسودة (الملفات المرفوعة قبل التشغيل) ----------------
def save_draft(sname, sraw, cname, craw):
    config.ensure_dirs()
    token = uuid.uuid4().hex[:12]
    d = config.TMP_DIR / token
    d.mkdir(parents=True)
    (d / ("students" + Path(sname).suffix.lower())).write_bytes(sraw)
    (d / ("centers" + Path(cname).suffix.lower())).write_bytes(craw)
    (d / "meta.json").write_text(json.dumps({"students": sname, "centers": cname, "at": time.time()}), encoding="utf-8")
    _cleanup_drafts()
    return token


def load_draft(token):
    if not str(token).isalnum():
        raise ValueError("bad token")
    d = config.TMP_DIR / token
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    sp = next(p for p in d.iterdir() if p.name.startswith("students"))
    cp = next(p for p in d.iterdir() if p.name.startswith("centers"))
    return meta, sp, cp


def _cleanup_drafts(max_age=6 * 3600):
    try:
        for p in config.TMP_DIR.iterdir():
            if p.is_dir() and time.time() - p.stat().st_mtime > max_age:
                shutil.rmtree(p, ignore_errors=True)
    except Exception:  # noqa
        pass


def draft_preflight(token):
    meta, sp, cp = load_draft(token)
    S = sheets.parse_file(meta["students"], sp.read_bytes())
    C = sheets.parse_file(meta["centers"], cp.read_bytes())
    rep = preflight.preflight(S, C)
    rep["token"] = token
    rep["files"] = {"students": meta["students"], "centers": meta["centers"]}
    return rep


# ---------------- إنشاء وتنفيذ ----------------
def create_run(token, params, name="", user=""):
    meta, sp, cp = load_draft(token)
    run_id = uuid.uuid4().hex[:10]
    d = run_dir(run_id) / "input"
    d.mkdir(parents=True)
    shutil.copy2(sp, d / sp.name)
    shutil.copy2(cp, d / cp.name)
    shutil.rmtree(sp.parent, ignore_errors=True)
    p = {"max_km": float(params.get("max_km") or engine.MAX_KM), "road_mode": params.get("road_mode", "osrm"),
         "osrm_url": (params.get("osrm_url") or config.OSRM_URL).strip(), "detour": float(params.get("detour") or engine.DETOUR),
         "geo_mode": params.get("geo_mode", "addr")}
    con = db.connect()
    try:
        db.run_insert(con, {"id": run_id, "name": name or meta["students"], "params": p, "students_file": meta["students"],
                            "centers_file": meta["centers"]})
        db.audit(con, user, "run_create", run_id, {"name": name, "params": p})
    finally:
        con.close()
    job = jobs.submit("run", lambda j: execute(j, run_id), run_id=run_id)
    con = db.connect()
    try:
        db.run_update(con, run_id, job_id=job["id"], status="running")
    finally:
        con.close()
    return run_id, job["id"]


def records(df):
    return df.astype(object).where(df.notna(), None).to_dict("records")


def _write_excel(path, sheet_list, say):
    with pd.ExcelWriter(path, engine="xlsxwriter") as w:
        for k, (name, df) in enumerate(sheet_list, 1):
            say(f"كتابة ملف Excel ({k}/{len(sheet_list)}): {name}")
            df.to_excel(w, sheet_name=name, index=False)
            ws = w.sheets[name]
            hdr = w.book.add_format({"bold": True, "bg_color": "#E8EEF9", "border": 1})
            for ci, col in enumerate(df.columns):
                ws.write(0, ci, col, hdr)
                ws.set_column(ci, ci, min(max(len(str(col)) + 3, 11), 40))
            ws.freeze_panes(1, 0)
            if len(df.columns):
                ws.autofilter(0, 0, max(len(df), 1), len(df.columns) - 1)


def execute(job, run_id):
    """بيتنفذ في الخلفية. النتيجة بتظهر في الصفحة أول ما تتحسب، وملفات Excel/QGIS بتتجهز بعدها."""
    con = db.connect()
    try:
        run = db.run_get(con, run_id)
    finally:
        con.close()
    p = run["params"]
    d = run_dir(run_id)
    progress = jobs.progress_fn(job)
    t0 = time.time()
    try:
        progress(1, "قراءة شيت الطلاب...")
        inp = d / "input"
        sp = next(x for x in inp.iterdir() if x.name.startswith("students"))
        cp = next(x for x in inp.iterdir() if x.name.startswith("centers"))
        S, adapt_note = sheets.adapt_students(sheets.parse_file(run["students_file"], sp.read_bytes()))
        progress(2, "قراءة شيت المراكز...")
        C = sheets.parse_file(run["centers_file"], cp.read_bytes())
        P = geoprep.prepare(S, C, mode=p["geo_mode"], max_km=p["max_km"], progress=progress)
        S, C = P["S"], P["C"]
        r = engine.distribute(S, C, max_km=p["max_km"], progress=progress, road_mode=p["road_mode"],
                              osrm_url=p["osrm_url"], detour=p["detour"])
        summ = r["summary"]
        summ["warnings"] = ([adapt_note] if adapt_note else []) + P["warnings"] + summ["warnings"]
        summ["alerts"] = P["alerts"] + summ["alerts"]
        summ["geo"] = P["info"]
        summ["params"] = p
        st = r["students"]
        # مجموعات المراجعة: كام طالب من كل مجموعة طلع VP (نفس تعريف المجموعة: موقعهم تقريبي ومن العناوين)
        low = np.isin(st["geo_level"].to_numpy(object), geoprep.LOW_LEVELS) & (st["geo_source"].to_numpy(object) == "osm")
        vp_idx = np.nonzero((st["status"] == "VP").to_numpy() & low)[0]
        vp_by_g = Counter(P["gkeys"][i] for i in vp_idx if P["gkeys"][i])
        rev = P["review"]
        for it in rev["items"]:
            it["n_vp"] = vp_by_g.get(it["gkey"], 0)
        rev["items"].sort(key=lambda x: (-x["n_vp"], -x["n"]))
        (d / FILES["review"]).write_text(json.dumps(rev, ensure_ascii=False), encoding="utf-8")
        rev_df = pd.DataFrame([{k: it[k] for k in ("gov", "area", "text", "n", "n_vp", "level", "conf", "lat", "lon", "sample", "gkey")}
                               for it in rev["items"][:3000]])
        result = {
            "run_id": run_id, "summary": summ,
            "by_gov": records(r["by_gov"]), "by_level": records(r["by_level"]),
            "deficit": records(r["deficit"]),
            "center_load": records(r["center_load"].sort_values("occupancy_%", ascending=False)),
            "sus_centers": records(r["sus_centers"]),
            "sus_students_cols": list(r["sus_students"].columns), "sus_students": records(r["sus_students"].head(100)),
            "review": {k: rev[k] for k in ("total_low", "empty_low", "groups_total")},
        }
        (d / FILES["result"]).write_text(json.dumps(result, ensure_ascii=False, default=str), encoding="utf-8")
        job["result"] = {"run_id": run_id, "result_ready": True}       # الملخص الكامل في result.json (الصفحة بتحمّله من API)
        job["result_at"] = time.time()
        job["files"] = "writing"
        # الملفات اللي بتتقرا بعدين (خريطة، مقارنة، تصدير لكل مركز)
        lat_c, lon_c = engine._find(st, names=engine.LAT), engine._find(st, names=engine.LON)
        st2 = st.copy()
        st2["lat"] = pd.to_numeric(st2[lat_c], errors="coerce") if lat_c else np.nan
        st2["lon"] = pd.to_numeric(st2[lon_c], errors="coerce") if lon_c else np.nan
        with gzip.open(d / FILES["students"], "wt", encoding="utf-8", newline="") as f:
            st2.to_csv(f, index=False)
        r["load"].to_csv(d / FILES["groups"], index=False, encoding="utf-8-sig")
        r["centers_pts"].to_csv(d / FILES["centers"], index=False, encoding="utf-8-sig")
        con = db.connect()
        try:
            db.run_update(con, run_id, status="files", n_students=int(summ["students"]), n_centers=int(summ["centers"]),
                          summary={k: summ.get(k) for k in ("students", "assigned", "vp", "same_area", "avg_km", "distance_label",
                                                            "centers", "groups", "seats", "suspect")} | {"geo_mode": p["geo_mode"]},
                          result_elapsed=round(job["result_at"] - t0, 1))
        finally:
            con.close()
        sheet_list = [("الطلاب - النتيجة", st), ("VP", st[st.status == "VP"]),
                      ("العجز حسب المحافظة والليفيل", r["deficit"]), ("VP حسب المنطقة", r["vp_area"]),
                      ("مراجعة المواقع (مجموعات)", rev_df),
                      ("مراكز إحداثياتها مشبوهة", r["sus_centers"]), ("طلاب إحداثياتهم مشبوهة", r["sus_students"]),
                      ("ملخص المحافظات", r["by_gov"]), ("ملخص الليفيل", r["by_level"]),
                      ("إشغال المراكز", r["center_load"]), ("إشغال الجروبات", r["load"])]
        done = [0]

        def say(m):
            progress(93 + int(5 * done[0] / len(sheet_list)), m)
            done[0] += 1
        _write_excel(d / FILES["xlsx"], sheet_list, say)
        progress(98, "تجهيز طبقات QGIS")
        with zipfile.ZipFile(d / FILES["qgis"], "w", zipfile.ZIP_DEFLATED) as z:
            for name, df in (("students_points.csv", r["points"]), ("centers_points.csv", r["centers_pts"]),
                             ("assignment_lines.csv", r["lines"])):
                z.writestr(name, df.to_csv(index=False, encoding="utf-8-sig"))
            z.writestr("README.txt", "QGIS: Layer > Add Layer > Add Delimited Text Layer.\n"
                       "students_points / centers_points: X=lon, Y=lat, CRS=EPSG:4326.\n"
                       "assignment_lines: Geometry definition = Well known text (WKT), field wkt, CRS=EPSG:4326.\n")
        job["files"] = "ready"
        job["state"], job["progress"], job["message"] = "done", 100, "تم"
        con = db.connect()
        try:
            db.run_update(con, run_id, status="done", finished_at=db.now(), elapsed=round(time.time() - t0, 1))
        finally:
            con.close()
    except engine.JobCancelled:
        _mark(run_id, "cancelled", "اتلغى")
        if job.get("files") == "writing":
            job["files"] = "cancelled"
        raise
    except Exception as e:  # noqa
        _mark(run_id, "error", str(e))
        if job.get("files") == "writing":
            job["files"] = "error"
        raise


def _mark(run_id, status, error):
    con = db.connect()
    try:
        db.run_update(con, run_id, status=status, error=error, finished_at=db.now())
    finally:
        con.close()


# ---------------- قراءة ----------------
def list_runs():
    con = db.connect()
    try:
        return db.runs_list(con)
    finally:
        con.close()


def get_run(run_id):
    con = db.connect()
    try:
        run = db.run_get(con, run_id)
    finally:
        con.close()
    if run:
        d = run_dir(run_id)
        run["files"] = {k: (d / v).exists() for k, v in FILES.items()}
    return run


def load_result(run_id):
    p = run_dir(run_id) / FILES["result"]
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def load_students(run_id):
    p = run_dir(run_id) / FILES["students"]
    if not p.exists():
        return None
    with gzip.open(p, "rt", encoding="utf-8") as f:
        return pd.read_csv(f, dtype=str, keep_default_na=False)


def load_groups(run_id):
    p = run_dir(run_id) / FILES["groups"]
    return pd.read_csv(p, encoding="utf-8-sig") if p.exists() else None


def delete_run(run_id, user=""):
    d = run_dir(run_id)
    shutil.rmtree(d, ignore_errors=True)
    con = db.connect()
    try:
        db.run_delete(con, run_id)
        db.audit(con, user, "run_delete", run_id)
    finally:
        con.close()


def file_path(run_id, key):
    name = FILES.get(key)
    if not name:
        return None
    p = run_dir(run_id) / name
    return p if p.exists() else None


# ---------------- بيانات الخريطة ----------------
def centers_geo(run_id):
    """المراكز بمواقعها وإشغالها (للخريطة)."""
    g = load_groups(run_id)
    if g is None:
        return []
    agg = (g.groupby(["center_code", "center_name", "gov", "area", "lat", "lon"], dropna=False)
           .agg(groups=("group_code", "size"), seats=("capacity", "sum"), assigned=("assigned", "sum")).reset_index())
    agg["occupancy"] = (100 * agg["assigned"] / agg["seats"].replace(0, np.nan)).round(0)
    return records(agg)


def students_points(run_id, center=None, gov=None, status=None, level=None, limit=6000):
    """نقاط الطلاب لعرضها على الخريطة (محدودة العدد)."""
    st = load_students(run_id)
    if st is None:
        return {"points": [], "total": 0, "shown": 0}
    m = pd.Series(True, index=st.index)
    if center:
        m &= (st["center_code"] == center) | ((st["status"] == "VP") & (st.get("nearest_center", "") == center))
    if gov:
        m &= st["gov_code"] == gov
    if status == "vp":
        m &= st["status"] == "VP"
    elif status == "assigned":
        m &= st["status"] != "VP"
    if level:
        m &= st["level_code"] == level
    sub = st[m]
    lat = pd.to_numeric(sub["lat"], errors="coerce")
    lon = pd.to_numeric(sub["lon"], errors="coerce")
    ok = lat.notna() & lon.notna()
    sub, lat, lon = sub[ok], lat[ok], lon[ok]
    total = len(sub)
    if total > limit:
        sub, lat, lon = sub.iloc[:limit], lat.iloc[:limit], lon.iloc[:limit]
    nid_col = next((c for c in sub.columns if c.strip().lower() in ("student nid", "nid")), None)
    pts = [{"lat": round(float(a), 5), "lon": round(float(b), 5), "s": "VP" if s == "VP" else "A", "c": c or "",
            "d": dk, "n": nid, "lv": lv, "g": gl}
           for a, b, s, c, dk, nid, lv, gl in zip(lat, lon, sub["status"], sub["center_code"], sub["distance_km"],
                                                   sub[nid_col] if nid_col else sub["row_no"], sub["level_code"], sub["geo_level"])]
    return {"points": pts, "total": int(total), "shown": len(pts)}

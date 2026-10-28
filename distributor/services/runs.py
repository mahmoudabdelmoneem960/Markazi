"""التشغيلات: مسودة الرفع → الفحص المسبق → تنفيذ التوزيع في الخلفية → حفظ النتايج في data/runs/<id>/."""
import gzip
import io
import json
import os
import re
import shutil
import time
import uuid
import zipfile
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from distributor import config, db, jobs
from distributor.core import engine, geoprep, locdb
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
            "by_gov_level": records(r["by_gov_level"]) if "by_gov_level" in r else [],
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
    res = json.loads(p.read_text(encoding="utf-8"))
    sc = res.get("sus_centers") or []
    existing_codes = {c.get("center_code") for c in sc}
    addr_gaps = (res.get("summary", {}).get("geo", {}).get("centers", {}).get("address_gap") or [])
    if addr_gaps:
        con = db.connect()
        try:
            for ag in addr_gaps:
                code = ag.get("code")
                if code and code not in existing_codes:
                    r = con.execute("SELECT code, name, gov, lat, lon, flags FROM centers WHERE code=?", (code,)).fetchone()
                    if r:
                        sc.append({
                            "center_code": r["code"],
                            "center_name": r["name"],
                            "gov": r["gov"],
                            "lat": r["lat"],
                            "lon": r["lon"],
                            "problem": r["flags"] or f"بعيد {ag.get('km', 0)} كم عن موقع عنوانه"
                        })
                        existing_codes.add(code)
        finally:
            con.close()
        res["sus_centers"] = sc

    if "by_gov_level" not in res:
        st_p = run_dir(run_id) / FILES["students"]
        gp_p = run_dir(run_id) / FILES["groups"]
        if st_p.exists() and gp_p.exists():
            try:
                with gzip.open(st_p, "rt", encoding="utf-8") as f:
                    st = pd.read_csv(f, usecols=["gov_code", "level_code", "status"])
                groups_df = pd.read_csv(gp_p, usecols=["gov", "level_code", "capacity"])
                seats_g = groups_df.groupby(["gov", "level_code"])["capacity"].sum()
                st["is_vp"] = st["status"] == "VP"
                grouped = st.groupby(["gov_code", "level_code"]).agg(
                    students=("status", "size"),
                    assigned=("is_vp", lambda s: int((~s).sum())),
                    vp=("is_vp", "sum")
                ).reset_index()
                bgl = []
                for _, row in grouped.iterrows():
                    g, lv = str(row["gov_code"]), str(row["level_code"])
                    s, a, v = int(row["students"]), int(row["assigned"]), int(row["vp"])
                    seats = int(seats_g.get((g, lv), 0))
                    bgl.append({
                        "gov": g, "level_code": lv, "students": s, "assigned": a, "vp": v,
                        "seats": seats, "deficit": max(0, s - seats),
                        "vp_%": round(100.0 * v / s, 1) if s else 0.0
                    })
                res["by_gov_level"] = bgl
                try:
                    p.write_text(json.dumps(res, ensure_ascii=False, default=str), encoding="utf-8")
                except Exception:
                    pass
            except Exception:
                res["by_gov_level"] = []

    # تنظيف أسباب الـ VP إذا كانت تحتوي على "X كم" مجهولة
    max_km = res.get("summary", {}).get("params", {}).get("max_km", 50)
    for vpr in (res.get("summary", {}).get("vp_reasons") or []):
        r = vpr.get("reason", "")
        if "X كم" in r:
            if "ممتلئة" in r:
                vpr["reason"] = f"كل مراكز المحافظة داخل الحد الأقصى ({max_km:g} كم) ممتلئة بالكامل - مقترح VP"
            elif "غالبًا إحداثيات" in r:
                vpr["reason"] = f"أبعد من الحد الأقصى ({max_km:g} كم) عن أي مركز - غالبًا إحداثيات الطالب غير دقيقة"
            elif "أبعد من" in r:
                vpr["reason"] = f"أبعد من الحد الأقصى للمسافة ({max_km:g} كم) عن أقرب مركز متاح في محافظته"
    return res


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


def get_center_run_details(run_id, code):
    """تفاصيل مركز معين داخل تشغيل: الجروبات، المستويات، نسب الإشغال، والطلاب المسكنين."""
    code = str(code).strip()
    gp_p = run_dir(run_id) / FILES["groups"]
    if not gp_p.exists():
        raise ValueError("ملف الجروبات غير موجود لهذا التشغيل")
    groups_df = pd.read_csv(gp_p, encoding="utf-8-sig")
    c_groups = groups_df[groups_df["center_code"].astype(str) == code]
    if c_groups.empty:
        raise ValueError(f"المركز {code} غير موجود في هذا التشغيل")

    first = c_groups.iloc[0]
    total_seats = int(c_groups["capacity"].sum())
    total_assigned = int(c_groups["assigned"].sum())
    center_info = {
        "code": code,
        "name": str(first.get("center_name") or ""),
        "gov": str(first.get("gov") or ""),
        "area": str(first.get("area") or ""),
        "lat": float(first.get("lat")) if pd.notna(first.get("lat")) else None,
        "lon": float(first.get("lon")) if pd.notna(first.get("lon")) else None,
        "total_groups": len(c_groups),
        "total_seats": total_seats,
        "total_assigned": total_assigned,
        "total_left": max(0, total_seats - total_assigned),
        "occupancy_%": round(100.0 * total_assigned / total_seats, 1) if total_seats > 0 else 0.0,
    }

    # تفاصيل المستويات والتراكات في هذا المركز
    by_lvl = (c_groups.groupby("level_code")
              .agg(groups=("group_code", "size"), seats=("capacity", "sum"), assigned=("assigned", "sum"))
              .reset_index())
    by_lvl["left"] = by_lvl["seats"] - by_lvl["assigned"]
    by_lvl["occupancy_%"] = (100 * by_lvl["assigned"] / by_lvl["seats"].replace(0, np.nan)).round(1).fillna(0.0)
    by_lvl = by_lvl.sort_values("seats", ascending=False)

    # قائمة كل الجروبات داخل المركز
    grp_list = []
    for _, g in c_groups.iterrows():
        cap = int(g.get("capacity") or 0)
        ass = int(g.get("assigned") or 0)
        grp_list.append({
            "group_code": str(g.get("group_code") or ""),
            "level_code": str(g.get("level_code") or ""),
            "day": str(g.get("day") or ""),
            "time": str(g.get("time") or ""),
            "capacity": cap,
            "assigned": ass,
            "left": max(0, cap - ass),
            "occupancy_%": round(100.0 * ass / cap, 1) if cap > 0 else 0.0
        })
    grp_list.sort(key=lambda x: (x["level_code"], x["group_code"]))

    # إحصائيات الطلاب والمسافات
    st_p = run_dir(run_id) / FILES["students"]
    students_info = {"avg_km": 0.0, "top_areas": []}
    if st_p.exists():
        try:
            with gzip.open(st_p, "rt", encoding="utf-8") as f:
                st = pd.read_csv(f, usecols=["center_code", "Area", "distance_km"])
            c_st = st[st["center_code"].astype(str) == code]
            if not c_st.empty:
                d_km = pd.to_numeric(c_st["distance_km"], errors="coerce")
                students_info["avg_km"] = round(float(d_km.mean()), 1) if d_km.notna().any() else 0.0
                top_a = c_st["Area"].value_counts().head(5).to_dict()
                students_info["top_areas"] = [{"area": k, "count": int(v)} for k, v in top_a.items() if k]
        except Exception:
            pass

    return {
        "center": center_info,
        "by_level": records(by_lvl),
        "groups": grp_list,
        "students_info": students_info
    }


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


# ---------------- فحص وتعديل بيانات الطلاب والأرقام القومية ----------------
def get_student_row(run_id, row_no):
    """إرجاع بيانات الطالب كاملة برقم الصف row_no أو بالرقم القومي."""
    st = load_students(run_id)
    if st is None:
        raise ValueError("ملف الطلاب غير موجود")
    row_str = str(row_no).strip()
    match = st[st["row_no"] == row_str]
    if len(match) == 0:
        nid_col = next((c for c in st.columns if c.strip().lower() in ("student nid", "nid")), "Student NID")
        match = st[st[nid_col] == row_str]
        if len(match) == 0:
            raise KeyError(f"الطالب غير موجود برقم صف أو رقم قومي: {row_no}")
    rec = match.iloc[0].to_dict()
    nid_col = next((c for c in rec if c.strip().lower() in ("student nid", "nid")), "Student NID")
    nid = str(rec.get(nid_col, "")).strip()
    nid_valid = bool(re.fullmatch(r"\d{14}", nid))
    rec["_meta"] = {
        "nid_col": nid_col,
        "nid_len": len(nid),
        "nid_valid": nid_valid,
        "is_suspect": rec.get("coord_suspect") == "نعم"
    }
    return rec


def get_invalid_nids(run_id, page=1, per_page=50, search=""):
    """جلب قائمة كل الطلاب أصحاب الأرقام القومية غير السليمة (أقل أو أكثر من 14 رقم أو غير أرقام)."""
    st = load_students(run_id)
    if st is None:
        raise ValueError("ملف الطلاب غير موجود")
    nid_col = next((c for c in st.columns if c.strip().lower() in ("student nid", "nid")), None)
    if not nid_col:
        return {"total": 0, "page": 1, "per_page": per_page, "rows": [], "cols": []}
    nid_series = st[nid_col].astype(str).str.strip()
    invalid_mask = ~nid_series.str.fullmatch(r"\d{14}")
    sub = st[invalid_mask].copy()
    if search:
        search_str = search.strip().lower()
        mask = sub["row_no"].str.contains(search_str, case=False, na=False) | sub[nid_col].str.contains(search_str, case=False, na=False)
        if "gov" in sub.columns:
            mask |= sub["gov"].str.contains(search_str, case=False, na=False)
        if "Area" in sub.columns:
            mask |= sub["Area"].str.contains(search_str, case=False, na=False)
        sub = sub[mask]
    total = len(sub)
    start = max(0, (page - 1) * per_page)
    end = start + per_page
    page_rows = sub.iloc[start:end].to_dict("records")
    for r in page_rows:
        r["_nid_len"] = len(str(r.get(nid_col, "")).strip())
    cols = ["row_no", nid_col, "_nid_len", "gov", "Area", "Adress", "Level(25-26)", "status", "center_code", "distance_km"]
    present_cols = [c for c in cols if c in sub.columns or c == "_nid_len"]
    return {"total": total, "page": page, "per_page": per_page, "rows": page_rows, "cols": present_cols}


def update_student(run_id, row_no, updates: dict, user=""):
    """تعديل بيانات طالب معين في التشغيلة وحفظها مع تسجيل التصحيح في DB إذا شمل الرقم القومي أو الإحداثيات."""
    d = run_dir(run_id)
    p = d / FILES["students"]
    if not p.exists():
        raise ValueError("ملف الطلاب غير موجود")
    with gzip.open(p, "rt", encoding="utf-8") as f:
        st = pd.read_csv(f, dtype=str, keep_default_na=False)
    row_str = str(row_no).strip()
    idx = st.index[st["row_no"] == row_str]
    if len(idx) == 0:
        raise KeyError(f"الطالب غير موجود برقم صف {row_no}")
    i = idx[0]
    for k, v in updates.items():
        if k in st.columns:
            st.at[i, k] = str(v)
    with gzip.open(p, "wt", encoding="utf-8", newline="") as f:
        st.to_csv(f, index=False)

    nid_col = next((c for c in st.columns if c.strip().lower() in ("student nid", "nid")), "Student NID")
    nid = st.at[i, nid_col]
    if "lat" in updates or "lon" in updates:
        try:
            lat_val = float(updates.get("lat") or st.at[i, "lat"])
            lon_val = float(updates.get("lon") or st.at[i, "lon"])
            con = db.connect()
            try:
                locdb.override_set(con, lat=lat_val, lon=lon_val, nid=nid, note=f"تعديل طالب صف {row_no}")
                db.audit(con, user, "student_update", f"run:{run_id}:row:{row_no}", updates)
            finally:
                con.close()
        except (ValueError, TypeError):
            pass
    return {"ok": True, "student": st.iloc[i].to_dict()}


# ---------------- فحص وتصحيح إحداثيات الطلاب المشبوهين (coord_suspect) ----------------
def get_suspect_students(run_id, page=1, per_page=50):
    """جلب الطلاب المشبوهين (coord_suspect == 'نعم') مع سبب الشبهة وتفاصيل موقعهم."""
    st = load_students(run_id)
    if st is None:
        raise ValueError("ملف الطلاب غير موجود")
    if "coord_suspect" not in st.columns:
        return {"total": 0, "page": 1, "per_page": per_page, "rows": []}
    sub = st[st["coord_suspect"] == "نعم"].copy()
    total = len(sub)
    start = max(0, (page - 1) * per_page)
    end = start + per_page
    rows = sub.iloc[start:end].to_dict("records")
    return {"total": total, "page": page, "per_page": per_page, "rows": rows}


def smart_fix_suspects(run_id, save_overrides=True, user=""):
    """تصحيح ذكي شامل لجميع الطلاب المشبوهين:
    1. التحقق من مطابقة الإحداثيات لمنطقتهم في المحافظة (مثل الخارجة في الوادي الجديد أو رأس سدر في جنوب سيناء).
    2. تصحيح أي إحداثيات خارج النطاق إلى مركز المنطقة الدقيق.
    3. حفظ التصحيحات في locdb.overrides بالرقم القومي ليعتمد عليها السيستم دائمًا في أي تشغيلات قادمة.
    4. تحديث ملف الطلاب والملخص لإزالة علامة الشك والتحذيرات.
    """
    d = run_dir(run_id)
    p = d / FILES["students"]
    if not p.exists():
        raise ValueError("ملف الطلاب غير موجود")
    with gzip.open(p, "rt", encoding="utf-8") as f:
        st = pd.read_csv(f, dtype=str, keep_default_na=False)

    if "coord_suspect" not in st.columns:
        return {"total": 0, "confirmed_valid": 0, "corrected": 0, "saved_to_overrides": 0}

    sus_mask = st["coord_suspect"] == "نعم"
    sus_idx = st.index[sus_mask].tolist()
    total = len(sus_idx)
    if total == 0:
        return {"total": 0, "confirmed_valid": 0, "corrected": 0, "saved_to_overrides": 0}

    con = db.connect()
    gaz = locdb.find_gazetteer()
    gz = None
    if gaz:
        try:
            from distributor.core import geocoder as gc
            gz = gc.Geocoder(gaz)
        except Exception:
            gz = None

    nid_col = next((c for c in st.columns if c.strip().lower() in ("student nid", "nid")), "Student NID")
    gov_col = next((c for c in st.columns if c.strip().lower() in ("gov", "governorate", "المحافظة", "المحافظه")), "gov")
    area_col = next((c for c in st.columns if c.strip().lower() in ("area", "المنطقة", "المنطقه")), "Area")
    addr_col = next((c for c in st.columns if c.strip().lower() in ("adress", "address", "العنوان", "عنوان")), "Adress")

    confirmed_valid = 0
    corrected = 0

    try:
        for i in sus_idx:
            nid = st.at[i, nid_col]
            g = st.at[i, gov_col]
            area = st.at[i, area_col]
            addr = st.at[i, addr_col]
            try:
                cur_lat = float(st.at[i, "lat"]) if st.at[i, "lat"] else None
                cur_lon = float(st.at[i, "lon"]) if st.at[i, "lon"] else None
            except (ValueError, TypeError):
                cur_lat, cur_lon = None, None

            target_lat, target_lon = cur_lat, cur_lon
            is_already_valid = False

            if gz and (area or g):
                area_res = gz.geocode(g, area, addr)
                if area_res and area_res.get("lat") is not None:
                    dist_to_area_km = locdb.km(cur_lat, cur_lon, area_res["lat"], area_res["lon"]) if cur_lat and cur_lon else 9999
                    if dist_to_area_km <= 35:
                        is_already_valid = True
                    else:
                        target_lat, target_lon = area_res["lat"], area_res["lon"]
                        st.at[i, "lat"] = str(target_lat)
                        st.at[i, "lon"] = str(target_lon)
                        if "Latitude" in st.columns:
                            st.at[i, "Latitude"] = str(target_lat)
                        if "Longitude" in st.columns:
                            st.at[i, "Longitude"] = str(target_lon)
                else:
                    is_already_valid = True
            else:
                is_already_valid = True

            if is_already_valid:
                confirmed_valid += 1
            else:
                corrected += 1

            st.at[i, "coord_suspect"] = ""

            if save_overrides and nid and target_lat is not None and target_lon is not None:
                note = "تأكيد إحداثيات (منطقة صحيحة)" if is_already_valid else "تصحيح إحداثيات مشبوهة إلى مركز المنطقة"
                locdb.override_set(con, lat=target_lat, lon=target_lon, nid=nid, note=note)

        with gzip.open(p, "wt", encoding="utf-8", newline="") as f:
            st.to_csv(f, index=False)

        res_path = d / FILES["result"]
        if res_path.exists():
            res_data = json.loads(res_path.read_text(encoding="utf-8"))
            if "summary" in res_data:
                if "warnings" in res_data["summary"]:
                    res_data["summary"]["warnings"] = [w for w in res_data["summary"]["warnings"] if "coord_suspect" not in w and "إحداثياتهم بعيدة جدًا" not in w]
                res_data["summary"]["suspect"] = 0
            res_data["sus_students"] = []
            res_path.write_text(json.dumps(res_data, ensure_ascii=False, default=str), encoding="utf-8")

        db.audit(con, user, "smart_fix_suspects", run_id, {
            "total": total,
            "confirmed_valid": confirmed_valid,
            "corrected": corrected,
            "saved_to_overrides": total if save_overrides else 0
        })

        return {
            "total": total,
            "confirmed_valid": confirmed_valid,
            "corrected": corrected,
            "saved_to_overrides": total if save_overrides else 0
        }
    finally:
        con.close()


# ---------------- استعلام الطلاب الأبعد من الحد الأقصى للمسافة (50 كم) ----------------
def get_far_students(run_id, page=1, per_page=50, search="", filter_type="all"):
    """جلب قائمة الطلاب الأبعد من الحد الأقصى للمسافة (أقرب مركز في محافظتهم > max_km)."""
    st = load_students(run_id)
    if st is None:
        raise ValueError("ملف الطلاب غير موجود")
    if "note" not in st.columns:
        return {"total": 0, "page": 1, "per_page": per_page, "max_km": 50.0, "rows": []}

    res = load_result(run_id) or {}
    max_km = float((res.get("summary", {}).get("params") or {}).get("max_km", 50.0))

    mask = st["note"].str.contains("أبعد من", na=False)
    if filter_type == "gov":
        mask &= ~st["note"].str.contains("غالبًا إحداثيات|مشكوك", na=False)
    elif filter_type == "suspect":
        mask &= st["note"].str.contains("غالبًا إحداثيات|مشكوك", na=False)

    sub = st[mask].copy()

    nid_col = next((c for c in sub.columns if c.strip().lower() in ("student nid", "nid")), "Student NID")
    if search:
        s = search.strip().lower()
        s_mask = sub["row_no"].str.contains(s, case=False, na=False)
        if nid_col in sub.columns:
            s_mask |= sub[nid_col].str.contains(s, case=False, na=False)
        if "gov" in sub.columns:
            s_mask |= sub["gov"].str.contains(s, case=False, na=False)
        if "Area" in sub.columns:
            s_mask |= sub["Area"].str.contains(s, case=False, na=False)
        if "nearest_center" in sub.columns:
            s_mask |= sub["nearest_center"].str.contains(s, case=False, na=False)
        if "Adress" in sub.columns:
            s_mask |= sub["Adress"].str.contains(s, case=False, na=False)
        sub = sub[s_mask]

    c_names = {}
    c_lats = {}
    c_lons = {}
    d = run_dir(run_id)
    gp = d / FILES["groups"]
    if gp.exists():
        try:
            gr = pd.read_csv(gp)
            c_info = gr.drop_duplicates("center_code").set_index("center_code")
            if "center_name" in c_info.columns:
                c_names = c_info["center_name"].to_dict()
            if "lat" in c_info.columns and "lon" in c_info.columns:
                c_lats = c_info["lat"].to_dict()
                c_lons = c_info["lon"].to_dict()
        except Exception:
            pass

    sub["dist_num"] = pd.to_numeric(sub["distance_km"], errors="coerce").fillna(0.0)
    sub = sub.sort_values("dist_num", ascending=False)

    total = len(sub)
    start = max(0, (page - 1) * per_page)
    end = start + per_page
    page_df = sub.iloc[start:end].copy()

    rows = []
    for _, r in page_df.iterrows():
        c_code = str(r.get("nearest_center") or "").strip()
        dist_val = round(float(r["dist_num"]), 1)
        over_val = round(max(0.0, dist_val - max_km), 1)
        rows.append({
            "row_no": str(r.get("row_no", "")),
            "nid": str(r.get(nid_col, "")),
            "gov": str(r.get("gov", "")),
            "area": str(r.get("Area", "")),
            "addr": str(r.get("Adress", "")),
            "lat": float(r["lat"]) if r.get("lat") else None,
            "lon": float(r["lon"]) if r.get("lon") else None,
            "level": str(r.get("Level(25-26)") or r.get("level_code") or ""),
            "status": str(r.get("status", "VP")),
            "nearest_center": c_code,
            "nearest_name": str(c_names.get(c_code, "")),
            "nearest_lat": float(c_lats[c_code]) if c_code in c_lats and pd.notna(c_lats[c_code]) else None,
            "nearest_lon": float(c_lons[c_code]) if c_code in c_lons and pd.notna(c_lons[c_code]) else None,
            "distance_km": dist_val,
            "over_km": over_val,
            "note": str(r.get("note", "")),
            "coord_suspect": str(r.get("coord_suspect", "")),
            "is_suspect": "غالبًا إحداثيات" in str(r.get("note", "")) or r.get("coord_suspect") == "نعم",
        })

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "max_km": max_km,
        "rows": rows
    }


def get_far_students_points(run_id, filter_type="all", limit=3000):
    """جلب نقاط الطلاب الأبعد من 50 كم ومراكزهم الأقرب لرسمها على الخريطة."""
    st = load_students(run_id)
    if st is None:
        raise ValueError("ملف الطلاب غير موجود")
    if "note" not in st.columns:
        return {"total": 0, "points": [], "centers": [], "max_km": 50.0}

    res = load_result(run_id) or {}
    max_km = float((res.get("summary", {}).get("params") or {}).get("max_km", 50.0))

    mask = st["note"].str.contains("أبعد من", na=False)
    if filter_type == "gov":
        mask &= ~st["note"].str.contains("غالبًا إحداثيات|مشكوك", na=False)
    elif filter_type == "suspect":
        mask &= st["note"].str.contains("غالبًا إحداثيات|مشكوك", na=False)

    sub = st[mask].copy()

    c_names = {}
    c_lats = {}
    c_lons = {}
    c_govs = {}
    c_areas = {}
    d = run_dir(run_id)
    gp = d / FILES["groups"]
    if gp.exists():
        try:
            gr = pd.read_csv(gp)
            c_info = gr.drop_duplicates("center_code").set_index("center_code")
            c_names = c_info["center_name"].to_dict() if "center_name" in c_info.columns else {}
            c_govs = c_info["gov"].to_dict() if "gov" in c_info.columns else {}
            c_areas = c_info["area"].to_dict() if "area" in c_info.columns else {}
            if "lat" in c_info.columns and "lon" in c_info.columns:
                c_lats = pd.to_numeric(c_info["lat"], errors="coerce").to_dict()
                c_lons = pd.to_numeric(c_info["lon"], errors="coerce").to_dict()
        except Exception:
            pass

    sub["lat_num"] = pd.to_numeric(sub["lat"], errors="coerce")
    sub["lon_num"] = pd.to_numeric(sub["lon"], errors="coerce")
    sub["dist_num"] = pd.to_numeric(sub["distance_km"], errors="coerce").fillna(0.0)

    valid_mask = sub["lat_num"].notna() & sub["lon_num"].notna()
    sub = sub[valid_mask]
    total = len(sub)
    if total > limit:
        sub = sub.iloc[:limit]

    nid_col = next((c for c in sub.columns if c.strip().lower() in ("student nid", "nid")), "Student NID")

    pts = []
    used_centers = set()
    for _, r in sub.iterrows():
        c_code = str(r.get("nearest_center") or "").strip()
        dist_val = round(float(r["dist_num"]), 1)
        over_val = round(max(0.0, dist_val - max_km), 1)
        c_lat = c_lats.get(c_code)
        c_lon = c_lons.get(c_code)
        if c_code:
            used_centers.add(c_code)

        pts.append({
            "row_no": str(r.get("row_no", "")),
            "nid": str(r.get(nid_col, "")),
            "gov": str(r.get("gov", "")),
            "area": str(r.get("Area", "")),
            "addr": str(r.get("Adress", "")),
            "lat": round(float(r["lat_num"]), 5),
            "lon": round(float(r["lon_num"]), 5),
            "c_code": c_code,
            "c_name": str(c_names.get(c_code, "")),
            "c_lat": round(float(c_lat), 5) if c_lat and pd.notna(c_lat) else None,
            "c_lon": round(float(c_lon), 5) if c_lon and pd.notna(c_lon) else None,
            "dist": dist_val,
            "over": over_val,
            "note": str(r.get("note", "")),
            "suspect": bool("غالبًا إحداثيات" in str(r.get("note", "")) or r.get("coord_suspect") == "نعم")
        })

    centers_list = []
    for cc in used_centers:
        clat = c_lats.get(cc)
        clon = c_lons.get(cc)
        if clat and clon and pd.notna(clat) and pd.notna(clon):
            centers_list.append({
                "center_code": cc,
                "center_name": str(c_names.get(cc, "")),
                "gov": str(c_govs.get(cc, "")),
                "area": str(c_areas.get(cc, "")),
                "lat": round(float(clat), 5),
                "lon": round(float(clon), 5)
            })

    return {
        "points": pts,
        "centers": centers_list,
        "total": total,
        "shown": len(pts),
        "max_km": max_km
    }



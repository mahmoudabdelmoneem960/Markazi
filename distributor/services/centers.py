"""عمليات المراكز: تعديل موقع، استيراد شيت، فحص لينكات Google، فحص Google API، التصدير."""
import csv
import io
import time

from distributor import db, jobs
from distributor.core import engine, googlegeo, locdb


def parse_input(text):
    """إحداثيات 'lat, lon' أو لينك Google (طويل أو مختصر) -> (lat, lon, note)."""
    got = locdb.parse_coords(text)
    if got:
        return got
    t = (text or "").strip()
    if t.startswith("http"):
        r = locdb.resolve_gmaps(t)
        if r:
            return r[0], r[1], "من لينك Google"
    raise ValueError("مش قادر أقرا إحداثيات من اللي كتبته. الصق إحداثيات زي 30.0444, 31.2357 أو لينك Google Maps طويل.")


def list_centers():
    con = db.connect()
    try:
        return [dict(r) for r in con.execute("SELECT * FROM centers ORDER BY gov, code")]
    finally:
        con.close()


def set_center(code, text, note=None, user="", run_id=""):
    con = db.connect()
    try:
        lat, lon, n = parse_input(text)
        flags = locdb.center_set(con, code, lat, lon, note=(note or n))
        row = con.execute("SELECT flags FROM centers WHERE code=?", (code,)).fetchone()
        if row and row["flags"]:
            parts = [p.strip() for p in row["flags"].split("|") if "بعيد" not in p and "كم عن موقع عنوانه" not in p]
            new_fl = " | ".join(parts) if parts else None
            con.execute("UPDATE centers SET flags=? WHERE code=?", (new_fl, code))
            con.commit()
            flags = parts
        db.audit(con, user, "center_set", code, {"lat": lat, "lon": lon, "note": note or n})
        _clean_center_from_run(run_id, code)
        return {"ok": True, "lat": lat, "lon": lon, "flags": flags}
    finally:
        con.close()


def import_sheet(df, user=""):
    df.columns = [str(c).strip() for c in df.columns]
    if "Center Code" not in df.columns:
        raise ValueError("الملف ده مش شيت مراكز: مفيش فيه عمود Center Code.")
    con = db.connect()
    try:
        rep = locdb.centers_upsert(df, source="sheet", con=con)
        db.audit(con, user, "centers_import", "", rep)
        return rep
    finally:
        con.close()


def centers_csv():
    con = db.connect()
    try:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["code", "name", "gov", "area", "lat", "lon", "source", "verified", "flags",
                    "link_lat", "link_lon", "link_diff_m", "link_place_name", "gmaps_url", "google_verdict"])
        for r in con.execute("SELECT code,name,gov,area,lat,lon,source,verified,flags,link_lat,link_lon,"
                             "ROUND(link_diff_m),link_name,gmaps_url,google_verdict FROM centers ORDER BY gov, code"):
            w.writerow(list(r))
        return "﻿" + buf.getvalue()
    finally:
        con.close()


# ---------------- فحص لينكات Google (مجاني، بطيء) ----------------
def _verify_links(job):
    con = None
    got_lock = googlegeo._running.acquire(blocking=False)       # نفس القفل بتاع فحص جوجل: مايشتغلوش مع بعض
    try:
        if not got_lock:
            raise RuntimeError("في فحص جوجل شغال دلوقتي. استنى لما يخلص.")
        con = db.connect()
        rows = [dict(r) for r in con.execute("SELECT code,gmaps_url FROM centers WHERE gmaps_url IS NOT NULL AND gmaps_url<>''")]
        mismatch, no_coords, checked = [], [], 0
        for i, r in enumerate(rows):
            if job.get("cancel"):
                raise engine.JobCancelled()
            job["progress"], job["message"] = int(100 * i / max(len(rows), 1)), f"قراءة لينك المركز {r['code']} ({i + 1} / {len(rows)})"
            got = locdb.resolve_gmaps_ex(r["gmaps_url"])
            time.sleep(locdb.GMAPS_GAP)
            if not got:                      # اللينك مفيهوش إحداثيات واضحة (صفحة بحث/مكان بالاسم): مش بنخمّن
                no_coords.append(r["code"])
                con.execute("UPDATE centers SET link_lat=NULL, link_lon=NULL, link_diff_m=NULL, link_name=NULL WHERE code=?", (r["code"],))
                con.commit()
                continue
            checked += 1
            cur = con.execute("SELECT lat, lon FROM centers WHERE code=?", (r["code"],)).fetchone()
            diff = locdb.km(cur["lat"], cur["lon"], got[0], got[1]) * 1000 if (cur and cur["lat"] is not None) else None
            con.execute("UPDATE centers SET link_lat=?, link_lon=?, link_diff_m=?, link_name=? WHERE code=?",
                        (got[0], got[1], diff, got[2], r["code"]))
            if diff is not None and diff <= 150:
                # مركز موقعه منسوخ من اللينك نفسه (أو من العنوان) مايتأكدش باللينك (مقارنة دايرية)، وجوجل لو قال "مختلف" مانرفعش العلامة
                con.execute("UPDATE centers SET verified=1 WHERE code=? AND source NOT IN ('manual','gmaps_link','geocoded') "
                            "AND COALESCE(google_verdict,'') NOT IN ('differs','conflict','approx_far')", (r["code"],))
            else:
                con.execute("UPDATE centers SET verified=0 WHERE code=? AND source NOT IN ('manual','google') "
                            "AND COALESCE(google_verdict,'') NOT IN ('same_current','same_link')", (r["code"],))
                mismatch.append({"code": r["code"], "diff_m": None if diff is None else int(diff)})
            con.commit()
        mismatch.sort(key=lambda m: -(m["diff_m"] if m["diff_m"] is not None else 10 ** 12))
        job["result"] = {"checked": checked, "of": len(rows), "no_coords": no_coords, "mismatch": mismatch}
    finally:
        if con is not None:
            con.close()
        if got_lock:
            googlegeo._running.release()


def start_verify_links():
    if googlegeo.running():
        raise ValueError("في فحص شغال دلوقتي (جوجل أو لينكات). استنى لما يخلص.")
    return jobs.submit("verify_links", _verify_links)


# ---------------- Google Geocoding API (اختياري) ----------------
def google_status():
    con = db.connect()
    try:
        return googlegeo.status(con)
    finally:
        con.close()


def google_plan(scope):
    con = db.connect()
    try:
        codes, nreq = googlegeo.plan(con, scope)
        return codes, {**googlegeo.status(con), "centers": len(codes), "requests": nreq}
    finally:
        con.close()


def start_google(scope, user=""):
    codes, info = google_plan(scope)
    key = googlegeo.get_key()
    if not key:
        raise ValueError("مفيش مفتاح جوجل. حط المفتاح (سطر واحد) في ملف data\\google_key.txt وأعد تشغيل السيستم.")
    if googlegeo.running():
        raise ValueError("في فحص جوجل شغال دلوقتي.")
    if not codes:
        raise ValueError("مفيش مراكز تتفحص (كلهم متأكد منهم). اختار 'كل المراكز' لو عايز تعيد.")
    if info["used"] + info["requests"] > info["cap"]:
        raise ValueError(f"الفحص محتاج {info['requests']} طلب والمستخدم الشهر ده {info['used']} من حد {info['cap']}. هتعدّي الحد المجاني فمش هبعت.")

    def _job(job):
        con = db.connect()
        partial = {}
        try:
            def progress(pct, msg):
                job["progress"], job["message"] = pct, msg
            job["result"] = googlegeo.verify_centers(con, codes, key, progress=progress, cancelled=lambda: job.get("cancel"), out=partial)
            db.audit(con, user, "google_verify", "", {"checked": job["result"].get("checked"), "applied": job["result"].get("applied")})
        except googlegeo.Cancelled:
            job["state"], job["message"], job["result"] = "cancelled", "اتلغى (اللي اتفحص اتحفظ)", partial
        except googlegeo.GoogleError as e:
            job["state"], job["error"], job["result"] = "error", str(e).replace(key, "***"), partial
        except Exception as e:  # noqa  (ما بنطبعش الـtraceback: ممكن يحمل الـURL وفيه المفتاح)
            job["state"], job["error"], job["result"] = "error", type(e).__name__ + ": " + str(e).replace(key, "***"), partial
        finally:
            con.close()
    return jobs.submit("google_verify", _job), info


# ---------------- اقتراح وتصحيح موقع المركز المشكوك فيه ----------------
def suggest_center_coords(code):
    """جلب إحداثيات مقترحة للمركز (من اللينك ومن الـ Geocoder مقابل العنوان) مع مقارنتها بالموقع الحالي."""
    con = db.connect()
    try:
        r = con.execute("SELECT * FROM centers WHERE code=?", (code,)).fetchone()
        if not r:
            raise KeyError(code)
        c = dict(r)

        link_info = None
        if c.get("gmaps_url"):
            link_lat, link_lon = c.get("link_lat"), c.get("link_lon")
            if link_lat is None or link_lon is None:
                resolved = locdb.resolve_gmaps(c["gmaps_url"])
                if resolved:
                    link_lat, link_lon = resolved[0], resolved[1]
                    con.execute("UPDATE centers SET link_lat=?, link_lon=? WHERE code=?", (link_lat, link_lon, code))
                    con.commit()
            if link_lat is not None and link_lon is not None:
                gap_km = round(locdb.km(c["lat"], c["lon"], link_lat, link_lon), 1) if c.get("lat") is not None else None
                link_info = {
                    "url": c["gmaps_url"],
                    "lat": link_lat,
                    "lon": link_lon,
                    "diff_km": gap_km
                }

        addr_info = None
        gaz = locdb.find_gazetteer()
        if gaz and c.get("address") and c.get("gov"):
            try:
                from distributor.core import geocoder as gc
                gz = gc.Geocoder(gaz)
                res = gz.geocode(c["gov"], c.get("area") or "", c["address"], exclude_center=code)
                if res and res.get("lat") is not None:
                    gap_km = round(locdb.km(c["lat"], c["lon"], res["lat"], res["lon"]), 1) if c.get("lat") is not None else None
                    addr_info = {
                        "lat": res["lat"],
                        "lon": res["lon"],
                        "level": res.get("level"),
                        "matched": res.get("matched"),
                        "diff_km": gap_km
                    }
            except Exception:
                pass

        return {
            "center": c,
            "link": link_info,
            "address": addr_info,
        }
    finally:
        con.close()


def _clean_center_from_run(run_id, code):
    """إزالة المركز من تحذيرات وتنبيهات التشغيلة المعروضة في الملخص."""
    if not run_id:
        return
    try:
        from distributor.services import runs
        d = runs.run_dir(run_id)
        p = d / runs.FILES["result"]
        if p.exists():
            import json
            data = json.loads(p.read_text(encoding="utf-8"))
            s = data.get("summary", {})
            if "alerts" in s:
                s["alerts"] = [a for a in s["alerts"] if code not in a]
            if "warnings" in s:
                s["warnings"] = [w for w in s["warnings"] if code not in w]
            ag = s.get("geo", {}).get("centers", {}).get("address_gap")
            if ag:
                s["geo"]["centers"]["address_gap"] = [x for x in ag if x.get("code") != code]
            if "sus_centers" in data:
                data["sus_centers"] = [x for x in data["sus_centers"] if x.get("center_code") != code]
            p.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
    except Exception:
        pass


def apply_center_coords(code, lat, lon, source="manual", note="", clear_flags=True, user="", run_id=""):
    """تطبيق إحداثيات جديدة للمركز وتأكيده مع إزالة علامة الشك والمسافة وتحديث نتيجة التشغيلة إن وجدت."""
    con = db.connect()
    try:
        fl = locdb.center_set(con, code, float(lat), float(lon), note=note, source=source)
        if clear_flags:
            row = con.execute("SELECT flags FROM centers WHERE code=?", (code,)).fetchone()
            if row and row["flags"]:
                parts = [p.strip() for p in row["flags"].split("|") if "بعيد" not in p and "كم عن موقع عنوانه" not in p]
                new_fl = " | ".join(parts) if parts else None
                con.execute("UPDATE centers SET flags=? WHERE code=?", (new_fl, code))
                con.commit()
                fl = parts
        db.audit(con, user, "center_apply_coords", code, {"lat": lat, "lon": lon, "source": source, "note": note})
        _clean_center_from_run(run_id, code)
        return {"ok": True, "lat": lat, "lon": lon, "flags": fl}
    finally:
        con.close()


def verify_center_current(code, user="", run_id=""):
    """تأكيد أن موقع المركز الحالي صحيح وإزالة علامة الشك والمسافة عنه وتحديث نتيجة التشغيلة."""
    con = db.connect()
    try:
        row = con.execute("SELECT lat, lon, flags FROM centers WHERE code=?", (code,)).fetchone()
        if not row:
            raise KeyError(code)
        parts = []
        if row["flags"]:
            parts = [p.strip() for p in row["flags"].split("|") if "بعيد" not in p and "كم عن موقع عنوانه" not in p]
        new_fl = " | ".join(parts) if parts else None
        con.execute("UPDATE centers SET verified=1, flags=? WHERE code=?", (new_fl, code))
        con.commit()
        db.audit(con, user, "center_verify_current", code, {"flags": new_fl})
        _clean_center_from_run(run_id, code)
        return {"ok": True, "flags": new_fl}
    finally:
        con.close()



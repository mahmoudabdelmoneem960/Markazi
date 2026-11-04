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
        con.execute("UPDATE centers SET verified=1, review='confirmed', review_at=?, review_by=?, correct_url=? WHERE code=?",
                    (locdb.now(), user or "", maps_link(lat, lon), code))
        con.commit()
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


# ---------------- مراجعة مواقع المراكز (لينك الشيت مقابل العنوان) ----------------
# التقدير من العنوان بالـgeocoder المحلي دقته على حسب المستوى: كل ما المستوى أوسع، نسمح بمسافة أكبر قبل ما نقول «اللينك غلط»
ADDR_TOLERANCE_KM = {"poi": 1.0, "street": 1.5, "locality": 3.0, "area": 5.0, "markaz": 10.0}
REVIEW_LABELS = {
    "confirmed": "متأكد (اتراجع)", "recheck": "محتاج مراجعة يدوية", "far": "اللينك بعيد عن العنوان", "link_unreadable": "اللينك مش بيتقري",
    "no_link": "مفيش لينك", "vague": "العنوان مش كفاية للحكم", "ok": "اللينك قريب من العنوان", "pending": "لسه ما اتفحصش",
}
REVIEW_ORDER = ["recheck", "far", "link_unreadable", "no_link", "vague", "pending", "ok", "confirmed"]


def maps_link(lat, lon):
    return f"https://www.google.com/maps?q={lat:.6f},{lon:.6f}"


def _km(a, b, c, d):
    return locdb.km(a, b, c, d)


def review_state(c):
    """حالة مراجعة موقع المركز من البيانات المحفوظة. c: صف من جدول centers (dict)."""
    tol = ADDR_TOLERANCE_KM.get(c.get("addr_level"))
    link = (c["link_lat"], c["link_lon"]) if c.get("link_lat") is not None else None
    addr = (c["addr_lat"], c["addr_lon"]) if c.get("addr_lat") is not None else None
    gap = round(_km(link[0], link[1], addr[0], addr[1]), 1) if link and addr else None
    out = {"addr_gap_km": gap, "tolerance_km": tol}
    if c.get("review") == "confirmed":
        out["status"] = "confirmed"
    elif c.get("review") == "recheck":                 # اترجع للمراجعة بإيد المستخدم: يفضل في القايمة لحد ما يتحسم
        out["status"] = "recheck"
    elif not c.get("gmaps_url"):
        out["status"] = "no_link"
    elif not link:
        out["status"] = "link_unreadable" if c.get("link_tried") else "pending"
    elif not addr and c.get("addr_level") is None:
        out["status"] = "pending"
    elif not addr or tol is None:
        out["status"] = "vague"
    else:
        out["status"] = "far" if gap > tol else "ok"
    out["status_ar"] = REVIEW_LABELS[out["status"]]
    return out


def _geocoder():
    gaz = locdb.find_gazetteer()
    if not gaz:
        return None
    from distributor.core import geocoder as gc
    return gc.Geocoder(gaz)


def _addr_estimate(gz, c):
    """تقدير موقع المركز من عنوانه. مستوى «محافظة» بس مش كفاية نحكم بيه على اللينك."""
    if not gz or not c.get("address") or not c.get("gov"):
        return {"lat": None, "lon": None, "level": "none", "matched": None}
    res = gz.geocode(c["gov"], c.get("area") or "", c["address"], exclude_center=c["code"])
    if not res or res.get("lat") is None or res.get("level") not in ADDR_TOLERANCE_KM:
        return {"lat": None, "lon": None, "level": (res or {}).get("level") or "none", "matched": (res or {}).get("matched")}
    return {"lat": res["lat"], "lon": res["lon"], "level": res["level"], "matched": res.get("matched")}


def _review_locations(job):
    """لكل مركز: يقرا لينك الشيت (لو لسه ما اتقراش)، يقدّر الموقع من العنوان، ويحدد الحالة.
    المراكز اللي مالهاش موقع خالص بتاخد موقع اللينك (زي ما التوزيع بيعمل)."""
    con = None
    got_lock = googlegeo._running.acquire(blocking=False)
    try:
        if not got_lock:
            raise RuntimeError("في فحص جوجل أو فحص لينكات شغال دلوقتي. استنى لما يخلص.")
        con = db.connect()
        gz = _geocoder()
        rows = [dict(r) for r in con.execute("SELECT * FROM centers ORDER BY gov, code")]
        need_net = sum(1 for r in rows if r.get("gmaps_url") and r.get("link_lat") is None and not r.get("link_tried"))
        done_net = 0
        for i, r in enumerate(rows):
            if job.get("cancel"):
                raise engine.JobCancelled()
            job["progress"] = int(100 * i / max(len(rows), 1))
            job["message"] = f"المركز {r['code']} ({i + 1} / {len(rows)})" + (
                f" — قراءة لينكات Google: {done_net} / {need_net} (كل لينك ~{locdb.GMAPS_GAP} ثواني)" if need_net else "")
            if r.get("gmaps_url") and r.get("link_lat") is None and not r.get("link_tried"):
                used_net = locdb.parse_coords(r["gmaps_url"]) is None      # لينك فيه الإحداثيات نفسها: مفيش نت ولا انتظار
                got = locdb.resolve_gmaps_ex(r["gmaps_url"])
                done_net += 1
                if got:
                    diff = _km(r["lat"], r["lon"], got[0], got[1]) * 1000 if r.get("lat") is not None else None
                    con.execute("UPDATE centers SET link_lat=?, link_lon=?, link_diff_m=?, link_name=?, link_tried=1 WHERE code=?",
                                (got[0], got[1], diff, got[2], r["code"]))
                    if r.get("lat") is None:
                        con.execute("UPDATE centers SET lat=?, lon=?, source='gmaps_link', updated_at=? WHERE code=?",
                                    (got[0], got[1], locdb.now(), r["code"]))
                else:
                    con.execute("UPDATE centers SET link_tried=1 WHERE code=?", (r["code"],))
                con.commit()
                if used_net and done_net < need_net:
                    time.sleep(locdb.GMAPS_GAP)
            if r.get("addr_level") is None:
                est = _addr_estimate(gz, r)
                con.execute("UPDATE centers SET addr_lat=?, addr_lon=?, addr_level=?, addr_matched=? WHERE code=?",
                            (est["lat"], est["lon"], est["level"], est["matched"], r["code"]))
                con.commit()
        rows = [dict(r) for r in con.execute("SELECT * FROM centers")]
        counts = {}
        for r in rows:
            st = review_state(r)["status"]
            counts[st] = counts.get(st, 0) + 1
        job["result"] = {"centers": len(rows), "counts": counts, "links_read": done_net, "gazetteer": gz is not None}
    finally:
        if con is not None:
            con.close()
        if got_lock:
            googlegeo._running.release()


def start_review_locations():
    if googlegeo.running():
        raise ValueError("في فحص شغال دلوقتي (جوجل أو لينكات). استنى لما يخلص.")
    return jobs.submit("review_locations", _review_locations)


def list_centers_review():
    """المراكز + حالة المراجعة محسوبة."""
    out = []
    for c in list_centers():
        c.update(review_state(c))
        out.append(c)
    return out


def preview_location(code, text):
    """معاينة لينك/إحداثيات قبل الحفظ: الموقع وبعده عن تقدير العنوان وعن لينك الشيت وعن الموقع الحالي. مفيش حفظ."""
    con = db.connect()
    try:
        r = con.execute("SELECT * FROM centers WHERE code=?", (code,)).fetchone()
        if not r:
            raise KeyError(code)
        c = dict(r)
    finally:
        con.close()
    t = (text or "").strip()
    got = locdb.parse_coords(t)
    name = ""
    if not got and t.startswith("http"):
        ex = locdb.resolve_gmaps_ex(t)
        if ex:
            got, name = (ex[0], ex[1], ""), ex[2]
    if not got:
        raise ValueError("مش قادر أقرا موقع من اللي كتبته. الصق لينك Google Maps للمكان (أو إحداثيات زي 30.0444, 31.2357).")
    lat, lon = got[0], got[1]

    def d(a, b):
        return round(_km(lat, lon, a, b), 2) if a is not None else None
    return {"lat": lat, "lon": lon, "place_name": name, "url": t if t.startswith("http") else maps_link(lat, lon),
            "from_address_km": d(c.get("addr_lat"), c.get("addr_lon")), "address_level": c.get("addr_level"),
            "tolerance_km": ADDR_TOLERANCE_KM.get(c.get("addr_level")),
            "from_link_km": d(c.get("link_lat"), c.get("link_lon")), "from_current_km": d(c.get("lat"), c.get("lon"))}


def confirm_location(code, lat, lon, url="", how="", user=""):
    """قرار المستخدم: الموقع ده هو الصح. بيتحفظ كتصحيح يدوي (الشيت ما بيغيّرهوش بعد كده) + اللينك الصح."""
    lat, lon = float(lat), float(lon)
    if not locdb.in_egypt(lat, lon):
        raise ValueError("الموقع ده برّه مصر.")
    res = apply_center_coords(code, lat, lon, source="manual", note=how or "مراجعة المواقع", user=user)
    good = (url or "").strip() or maps_link(lat, lon)
    con = db.connect()
    try:
        con.execute("UPDATE centers SET verified=1, review='confirmed', review_at=?, review_by=?, correct_url=? WHERE code=?",
                    (locdb.now(), user or "", good, code))
        con.commit()
        db.audit(con, user, "center_review_confirm", code, {"lat": lat, "lon": lon, "url": good, "how": how})
    finally:
        con.close()
    return {**res, "correct_url": good}


def reopen_review(code, user=""):
    con = db.connect()
    try:
        if not con.execute("SELECT 1 FROM centers WHERE code=?", (code,)).fetchone():
            raise KeyError(code)
        con.execute("UPDATE centers SET review='recheck', review_at=? WHERE code=?", (locdb.now(), code))
        con.commit()
        db.audit(con, user, "center_review_reopen", code, None)
    finally:
        con.close()
    return {"ok": True}


def master_xlsx():
    """شيت المواقع المعتمدة بنفس أسماء أعمدة شيت المراكز (Center Code / Center Name / City / Area / Center Address / Location
    / Latitude / Longitude) عشان ينفع يتستورد تاني أو يتنسخ في الشيت الأصلي + أعمدة المراجعة."""
    import pandas as pd
    rows = []
    for c in sorted(list_centers_review(), key=lambda x: (REVIEW_ORDER.index(x["status"]), x.get("gov") or "", x["code"])):
        good = c.get("correct_url") or (maps_link(c["lat"], c["lon"]) if c.get("lat") is not None else "")
        rows.append({"Center Code": c["code"], "Center Name": c.get("name") or "", "City": c.get("gov") or "",
                     "Area": c.get("area") or "", "Center Address": c.get("address") or "",
                     "Location": good, "Latitude": c.get("lat"), "Longitude": c.get("lon"),
                     "حالة المراجعة": c["status_ar"], "لينك الشيت": c.get("gmaps_url") or "",
                     "بعد لينك الشيت عن العنوان (كم)": c.get("addr_gap_km"), "دقة تقدير العنوان": c.get("addr_level") or "",
                     "مصدر الموقع": c.get("source") or "", "اتراجع بواسطة": c.get("review_by") or "",
                     "تاريخ المراجعة": c.get("review_at") or ""})
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="xlsxwriter") as xw:
        pd.DataFrame(rows).to_excel(xw, sheet_name="مواقع المراكز", index=False)
        ws = xw.sheets["مواقع المراكز"]
        ws.right_to_left()
        ws.set_column(0, 4, 18)
        ws.set_column(5, 5, 45)
        ws.set_column(6, 14, 16)
    out.seek(0)
    return out


# ---------------- مراكز محطوط لها موقع مركز تاني ----------------
DUP_KM = 0.03      # أقل من 30 متر = نفس المكان (اللينكات المختلفة لنفس المبنى بتفرق أمتار قليلة)


def _same_name(a, b):
    """مركزين بنفس الاسم = نفس المبنى بكودين (مش غلط)."""
    from distributor.core import engine
    na, nb = engine.norm_ar(a.get("name") or ""), engine.norm_ar(b.get("name") or "")
    return bool(na) and na == nb


def _dup_ok(r):
    return {x for x in (r.get("dup_ok") or "").split(",") if x}


def center_issues():
    """المراكز اللي موقعها هو موقع مركز تاني (غالبًا لينك مركز اتلصق لمركز تاني).
    مستثنى: نفس الاسم (نفس المبنى بكودين)، والأزواج اللي المستخدم أكّد إنها نفس المبنى فعلاً."""
    con = db.connect()
    try:
        rows = [dict(r) for r in con.execute("SELECT * FROM centers WHERE lat IS NOT NULL ORDER BY gov, code")]
    finally:
        con.close()
    out = []
    for r in rows:
        others = []
        for o in rows:
            if o["code"] == r["code"] or abs(o["lat"] - r["lat"]) > 0.001 or abs(o["lon"] - r["lon"]) > 0.001:
                continue
            d = _km(r["lat"], r["lon"], o["lat"], o["lon"])
            if d < DUP_KM and not _same_name(r, o) and o["code"] not in _dup_ok(r):
                others.append({"code": o["code"], "name": o.get("name") or "", "area": o.get("area") or "", "m": round(d * 1000)})
        if others:
            text = "؛ ".join(f"{o['code']} ({o['name']} — {o['area']})" + (f" على بعد {o['m']} م" if o["m"] else " نفس الإحداثيات بالظبط")
                            for o in others)
            out.append({"code": r["code"], "name": r.get("name") or "", "gov": r.get("gov") or "", "area": r.get("area") or "",
                        "address": r.get("address") or "", "lat": r["lat"], "lon": r["lon"],
                        "correct_url": r.get("correct_url") or "", "others": others,
                        "diff_area": any(o["area"] != (r.get("area") or "") for o in others),
                        "text": f"موقعه هو موقع: {text}"})
    out.sort(key=lambda x: (not x["diff_area"], x["gov"], x["code"]))
    return {"centers": out, "total": len(rows), "count": len(out)}


def center_issues_xlsx():
    import pandas as pd
    rows = [{"Center Code": c["code"], "Center Name": c["name"], "City": c["gov"], "Area": c["area"],
             "Center Address": c["address"], "موقعه هو موقع": "، ".join(o["code"] for o in c["others"]),
             "التفاصيل": c["text"], "منطقة مختلفة": "أيوه" if c["diff_area"] else "",
             "الموقع الحالي": c["correct_url"]} for c in center_issues()["centers"]]
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="xlsxwriter") as xw:
        pd.DataFrame(rows).to_excel(xw, sheet_name="مراكز بموقع مركز تاني", index=False)
        ws = xw.sheets["مراكز بموقع مركز تاني"]
        ws.right_to_left()
        ws.set_column(0, 5, 18)
        ws.set_column(6, 6, 70, xw.book.add_format({"text_wrap": True, "valign": "top"}))
        ws.set_column(7, 8, 20)
    out.seek(0)
    return out


def mark_same_place(code, other, user=""):
    """المستخدم أكّد إن المركزين دول في نفس المكان فعلاً (نفس المبنى): يخرجوا من القايمة."""
    con = db.connect()
    try:
        rows = {r["code"]: dict(r) for r in con.execute("SELECT code, dup_ok FROM centers WHERE code IN (?, ?)", (code, other))}
        if code not in rows or other not in rows or code == other:
            raise KeyError(code if code not in rows else other)
        for a, b in ((code, other), (other, code)):
            ok = _dup_ok(rows[a]) | {b}
            con.execute("UPDATE centers SET dup_ok=? WHERE code=?", (",".join(sorted(ok)), a))
        con.commit()
        db.audit(con, user, "center_same_place", code, {"other": other})
    finally:
        con.close()
    return {"ok": True}


def edit_center(code, address=None, user=""):
    """تعديل بيانات المركز من السيستم (العنوان). العنوان المعدّل ما يتغيرش لما الشيت يترفع تاني."""
    con = db.connect()
    try:
        r = con.execute("SELECT address FROM centers WHERE code=?", (code,)).fetchone()
        if not r:
            raise KeyError(code)
        if address is not None:
            address = str(address).strip()
            if address != (r["address"] or ""):
                con.execute("UPDATE centers SET address=?, addr_manual=1, addr_lat=NULL, addr_lon=NULL, addr_level=NULL, "
                            "addr_matched=NULL WHERE code=?", (address, code))
                con.commit()
                db.audit(con, user, "center_edit", code, {"address": address, "old": r["address"]})
        return {"ok": True}
    finally:
        con.close()

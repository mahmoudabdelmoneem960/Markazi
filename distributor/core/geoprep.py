"""تجهيز إحداثيات المراكز والطلاب قبل التوزيع.

الطريقتين:
  1) الشيت فيه إحداثيات  -> بتتستخدم (وبتتسجل في الـ DB للمراكز)
  2) عناوين بس           -> المراكز: من الـ DB / لينك Google / العنوان، والطلاب: من الـ DB (كاش) / الـ geocoder المحلي

مصادر كل موقع بتتسجل في أعمدة: geo_source (sheet | manual | osm | google | missing)، geo_level (دقة)، geo_conf (ثقة).
"""
import hashlib
import re
import time

import numpy as np
import pandas as pd

from distributor.core import engine
from distributor.core import geocoder as gc
from distributor.core import geonorm as gn
from distributor.core import locdb

LOW_LEVELS = ("area", "markaz", "gov", "none")
CENTER_ADDR_GAP_KM = 15       # مركز موقعه بعيد أكتر من كده عن موقع عنوانه = تحذير
UNCERT_KM = {"poi": 0.1, "street": 0.4, "locality": 1.5, "area": 4.0, "markaz": 6.0, "gov": 40.0, "none": 40.0}


class GeoPrepError(ValueError):
    pass


def _bbox_ok(lat, lon):
    b = locdb.EGYPT_BBOX
    return np.isfinite(lat) & np.isfinite(lon) & (lat >= b[0]) & (lat <= b[1]) & (lon >= b[2]) & (lon <= b[3])


def _fill_center_columns(C, coords):
    """بيكتب Latitude/Longitude على كل رومز المركز من القاموس coords[code]=(lat, lon)."""
    lat_c = engine._find(C, names=engine.LAT) or "Latitude"
    lon_c = engine._find(C, names=engine.LON) or "Longitude"
    codes = C["Center Code"].astype(str)
    C[lat_c] = [coords.get(c, (np.nan, np.nan))[0] if coords.get(c, (None, None))[0] is not None else np.nan for c in codes]
    C[lon_c] = [coords.get(c, (np.nan, np.nan))[1] if coords.get(c, (None, None))[1] is not None else np.nan for c in codes]
    return lat_c, lon_c


def prepare(S, C, mode="auto", max_km=50, progress=lambda p, m: None, con=None, gaz_path=None, use_google_links=True):
    """mode: 'auto' (إحداثيات الشيت لو موجودة وإلا من العناوين) | 'addr' (تجاهل إحداثيات الطلاب في الشيت) | 'sheet' (من الشيت بس)."""
    S = S.copy().reset_index(drop=True)
    C = C.copy().reset_index(drop=True)
    S.columns = [str(c).strip() for c in S.columns]
    C.columns = [str(c).strip() for c in C.columns]
    own = con is None
    con = con or locdb.connect()
    gaz = gaz_path or locdb.find_gazetteer()
    warnings, alerts = [], []
    info = {"mode": mode}
    try:
        # ====================== المراكز ======================
        progress(1, "تجهيز مواقع المراكز")
        if "Center Code" not in C.columns:
            raise GeoPrepError("شيت المراكز ناقص عمود Center Code")
        rep = locdb.centers_upsert(C, source="sheet", con=con)
        codes = sorted({str(c) for c in C["Center Code"]})
        rows = {r["code"]: r for r in con.execute(f"SELECT * FROM centers WHERE code IN ({','.join('?' * len(codes))})", codes)}
        need = [c for c in codes if rows[c]["lat"] is None]
        gz = None
        if need:
            from_link = from_addr = failed = 0
            for i, code in enumerate(need):
                r = rows[code]
                progress(1 + int(1.5 * i / max(len(need), 1)), f"تحديد مواقع المراكز اللي ناقصة ({i + 1} / {len(need)})")
                got = None
                if use_google_links and r["gmaps_url"]:
                    got = locdb.resolve_gmaps(r["gmaps_url"])
                    if got:
                        locdb.center_set(con, code, got[0], got[1], note="من لينك Google", source="gmaps_link")
                        con.execute("UPDATE centers SET verified=0 WHERE code=?", (code,))
                        from_link += 1
                        time.sleep(locdb.GMAPS_GAP)
                        continue
                    time.sleep(locdb.GMAPS_GAP)
                if gaz and r["address"] and r["gov"]:       # من غير محافظة الـgeocoder بيدوّر في كل مصر: مانحددش موقع تقريبي غلط
                    if gz is None:               # المراكز اللي ليها موقع بتبقى نقط ارتكاز للمناطق (نستبعد المركز نفسه)
                        anchors = [{"code": x["code"], "gov": x["gov"], "area": x["area"], "lat": x["lat"], "lon": x["lon"]}
                                   for x in con.execute("SELECT code,gov,area,lat,lon FROM centers WHERE lat IS NOT NULL")]
                        gz = gc.Geocoder(gaz, centers=anchors)
                    res = gz.geocode(r["gov"], r["area"], r["address"], exclude_center=code)
                    if res["lat"] is not None:
                        locdb.center_set(con, code, res["lat"], res["lon"],
                                         note=f"geocoder ({res['level']}) - راجعه", source="geocoded")
                        con.execute("UPDATE centers SET verified=0 WHERE code=?", (code,))
                        from_addr += 1
                        continue
                failed += 1
            con.commit()
            rows = {r["code"]: r for r in con.execute(f"SELECT * FROM centers WHERE code IN ({','.join('?' * len(codes))})", codes)}
            if from_link:
                warnings.append(f"{from_link} مركز اتحدد موقعه من لينك Google (راجعهم في صفحة المواقع).")
            if from_addr:
                alerts.append(f"{from_addr} مركز اتحدد موقعه من العنوان بس (دقة تقريبية): راجعهم في صفحة المواقع وأكّد مواقعهم.")
            if failed:
                alerts.append(f"{failed} مركز مالوش موقع (مفيش إحداثيات ولا لينك ولا عنوان يتحدد): هيتتجاهلوا في التوزيع.")
        if rep["differs_from_manual"]:
            warnings.append("إحداثيات الشيت مختلفة عن التصحيح المحفوظ في الـDB (يدوي أو متأكد بجوجل) لمراكز: "
                            + "، ".join(rep["differs_from_manual"][:15]) + " (اتستخدم التصحيح المحفوظ).")
        if rep.get("moved"):
            warnings.append(f"{len(rep['moved'])} مركز اتغيّر موقعه في قاعدة المواقع من الشيت ده (كان مسجل بموقع مختلف): "
                            + "، ".join(rep["moved"][:12]) + ". كاش العناوين اتجدّد بسبب كده.")
        # فحص مقابل العنوان: مركز موقعه (شيت/لينك) بعيد جدًا عن موقع عنوانه = غالبًا لينك أو إحداثيات غلط. تعليم بس من غير تغيير.
        gz_all, gap_list = None, []
        if gaz:
            try:
                chk = [c for c in codes if rows[c]["lat"] is not None and not rows[c]["verified"]
                       and rows[c]["source"] not in ("geocoded", "manual", "google") and (rows[c]["address"] or "").strip() and rows[c]["gov"]]
                anchors = [{"code": x["code"], "gov": x["gov"], "area": x["area"], "lat": x["lat"], "lon": x["lon"]}
                           for x in con.execute("SELECT code,gov,area,lat,lon FROM centers WHERE lat IS NOT NULL")]
                gz_all = gc.Geocoder(gaz, centers=anchors)
                for c in chk:
                    res = gz_all.geocode(rows[c]["gov"], rows[c]["area"], rows[c]["address"], exclude_center=c)
                    if res["lat"] is not None:
                        gap = locdb.km(rows[c]["lat"], rows[c]["lon"], res["lat"], res["lon"])
                        if gap > CENTER_ADDR_GAP_KM:
                            gap_list.append((c, gap))
                for c, gap in gap_list:
                    fl = rows[c]["flags"] or ""
                    con.execute("UPDATE centers SET flags=? WHERE code=?",
                                ((fl + " | " if fl else "") + f"بعيد {gap:.0f} كم عن موقع عنوانه", c))
                con.commit()
                if gap_list:
                    rows = {r["code"]: r for r in con.execute(f"SELECT * FROM centers WHERE code IN ({','.join('?' * len(codes))})", codes)}
                    alerts.append(f"{len(gap_list)} مركز موقعه (من الشيت أو لينك Google) بعيد أكتر من {CENTER_ADDR_GAP_KM} كم عن موقع عنوانه: "
                                  + "، ".join(f"{c} ({g:.0f} كم)" for c, g in sorted(gap_list, key=lambda x: -x[1])[:12])
                                  + " — غالبًا لينك أو إحداثيات غلط وده بيأثر على توزيع طلابه. راجعهم في صفحة المواقع.")
            except Exception as e:  # noqa  (فحص إضافي: ما نوقفش التوزيع بسببه)
                gz_all = None
                warnings.append(f"تعذر فحص المراكز مقابل عناوينها: {e}")
        coords = {c: (rows[c]["lat"], rows[c]["lon"]) for c in codes if rows[c]["lat"] is not None}
        _fill_center_columns(C, coords)
        bad_flags = [(c, rows[c]["flags"]) for c in codes if rows[c]["flags"]]
        if bad_flags:
            warnings.append("مراكز بمواقع مشكوك فيها: " + "؛ ".join(f"{c}: {f}" for c, f in bad_flags[:12]))
        info["centers"] = {"total": len(codes), "with_coords": len(coords),
                           "by_source": pd.Series([rows[c]["source"] or "none" for c in codes]).value_counts().to_dict(),
                           "flagged": len(bad_flags), "address_gap": [{"code": c, "km": round(g, 1)} for c, g in gap_list]}

        # ====================== الطلاب ======================
        progress(2, "تجهيز مواقع الطلاب")
        gov_col = engine._find(S, names={"gov", "governorate", "المحافظة", "المحافظه"})
        area_col = engine._find(S, names={"area", "المنطقة", "المنطقه"})
        addr_col = engine._find(S, names={"adress", "address", "العنوان", "عنوان"}, contains=["adress", "address", "عنوان"])
        nid_col = engine._find(S, names={"student nid", "nid", "national id", "الرقم القومي", "رقم قومي"}, contains=["nid"])
        lat_col, lon_col = engine._find(S, names=engine.LAT), engine._find(S, names=engine.LON)
        n = len(S)
        sheet_lat = pd.to_numeric(S[lat_col], errors="coerce").to_numpy(float) if lat_col else np.full(n, np.nan)
        sheet_lon = pd.to_numeric(S[lon_col], errors="coerce").to_numpy(float) if lon_col else np.full(n, np.nan)
        sheet_ok = _bbox_ok(sheet_lat, sheet_lon)
        gcache = {}
        gcode = np.empty(n, dtype=object)
        if gov_col:
            for i, g in enumerate(S[gov_col]):
                if g not in gcache:
                    gcache[g] = gn.gov_code(g)
                gcode[i] = gcache[g]
        areas = S[area_col].fillna("").astype(str).to_numpy(object) if area_col else np.full(n, "", dtype=object)
        addrs = S[addr_col].fillna("").astype(str).to_numpy(object) if addr_col else np.full(n, "", dtype=object)
        nid = None
        nid_ok = np.zeros(n, bool)
        if nid_col:
            nid = engine._clean_id(S[nid_col]).to_numpy(object)
            nid_ok = np.array([bool(re.fullmatch(r"\d{14}", x)) for x in nid])
        if mode != "sheet" and (not gov_col or not area_col):
            if not sheet_ok.all():
                raise GeoPrepError("عشان الإحداثيات تتجاب من العناوين، شيت الطلاب لازم فيه الأعمدة: gov (المحافظة) وArea (المنطقة) "
                                   "وAdress (العنوان).")
        lat = np.full(n, np.nan)
        lon = np.full(n, np.nan)
        level = np.full(n, "", dtype=object)
        conf = np.full(n, np.nan)
        src = np.full(n, "missing", dtype=object)
        matched = np.full(n, "", dtype=object)

        # 1) تصحيحات يدوية بالرقم القومي
        ov = locdb.overrides_get(con, [nid[i] for i in range(n) if nid_ok[i]]) if nid is not None else {}
        for i in range(n):
            if nid_ok[i] and nid[i] in ov:
                lat[i], lon[i] = ov[nid[i]]
                level[i], conf[i], src[i], matched[i] = "manual", 1.0, "manual", "تصحيح يدوي"
        # 2) إحداثيات الشيت
        if mode in ("auto", "sheet"):
            take = sheet_ok & (src == "missing")
            lat[take], lon[take] = sheet_lat[take], sheet_lon[take]
            level[take], src[take] = "sheet", "sheet"
            idx = np.nonzero(take)[0]
            if len(idx) >= 300:
                sus = _sheet_coords_suspect(gcode[idx], areas[idx], addrs[idx], lat[idx], lon[idx])
                if sus:
                    info["sheet_coords_suspect"] = sus
                    alerts.append("إحداثيات الطلاب في الشيت شكلها تقريبية (مش حقيقية)، والتوزيع ده بيستخدمها زي ما هي. "
                                  + " | ".join(sus) + " — للموقع الأدق من العناوين اختار 'من العناوين دايمًا'.")
        # 3) من العناوين
        todo = np.nonzero((src == "missing") & np.array([g is not None for g in gcode]))[0] if mode != "sheet" else np.array([], int)
        n_cache = n_new = 0
        if len(todo):
            if not gaz:
                raise GeoPrepError("قاعدة الأماكن (gazetteer.sqlite) مش موجودة. حطها في فولدر data جنب السيستم أو اضبط GAZETTEER_DB، "
                                   "أو اختار 'إحداثيات من الشيت'.")
            centers_rows = [{"code": r["code"], "gov": r["gov"], "area": r["area"], "lat": r["lat"], "lon": r["lon"]}
                            for r in con.execute("SELECT code,gov,area,lat,lon FROM centers WHERE lat IS NOT NULL")]
            anchors_fp = hashlib.md5(str(sorted((c["code"], round(c["lat"], 5), round(c["lon"], 5)) for c in centers_rows)).encode()).hexdigest()
            locdb.cache_prepare(con, f"{locdb.gazetteer_fingerprint(gaz)}|{anchors_fp}")
            gz = gz_all if gz_all is not None else gc.Geocoder(gaz, centers=centers_rows)   # نفس نقط الارتكاز اللي في فحص المراكز
            keys = [locdb.geo_key(gcode[i], areas[i], addrs[i]) for i in todo]
            uniq = list(dict.fromkeys(keys))
            ao = locdb.addr_overrides_get(con, uniq)
            cached = locdb.cache_get_many(con, uniq)
            first_idx = {}                           # أول صف لكل مفتاح (بنحتاج النصوص الأصلية للـ geocoder)
            for i, k in zip(todo, keys):
                first_idx.setdefault(k, i)
            res_by_key, new_rows = {}, []
            for j, k in enumerate(uniq):
                if j % 1500 == 0:
                    progress(2 + int(10 * j / max(len(uniq), 1)), f"تحديد مواقع الطلاب من العناوين ({j:,} / {len(uniq):,} عنوان مميز)")
                if k in ao:
                    res_by_key[k] = (ao[k][0], ao[k][1], "manual", 1.0, "تصحيح يدوي للعنوان", "manual")
                elif k in cached:
                    res_by_key[k] = cached[k]
                    n_cache += 1
                else:
                    i = first_idx[k]
                    r = gz.geocode(gcode[i], areas[i], addrs[i])
                    if r["lat"] is None:
                        res_by_key[k] = (None, None, "none", 0.0, "", "osm")
                    else:
                        res_by_key[k] = (r["lat"], r["lon"], r["level"], r["confidence"], r["matched"], "osm")
                        new_rows.append((k, gcode[i], areas[i], addrs[i], r["lat"], r["lon"], r["level"], r["confidence"],
                                         r["matched"], "osm"))
                    n_new += 1
            if new_rows:
                locdb.cache_put_many(con, new_rows)
            for i, k in zip(todo, keys):
                v = res_by_key[k]
                if v[0] is None:
                    continue
                lat[i], lon[i], level[i], conf[i], matched[i], src[i] = v[0], v[1], v[2], v[3], v[4], v[5]
        # 4) تصحيحات يدوية على مستوى المجموعة (حي/شارع في منطقة) للي موقعهم لسه على مستوى منطقة أو أعم
        gkeys = np.array([group_key_of(gcode[i], areas[i], addrs[i]) if gcode[i] else "" for i in range(n)], dtype=object)
        cand = np.nonzero(np.isin(level, LOW_LEVELS) & (src == "osm") & (gkeys != ""))[0]
        n_grp = 0
        if len(cand):
            go = locdb.group_overrides_get(con, list(dict.fromkeys(gkeys[cand])))
            for i in cand:
                v = go.get(gkeys[i])
                if v:
                    lat[i], lon[i] = v
                    level[i], conf[i], src[i], matched[i] = "manual", 1.0, "manual", "تصحيح يدوي (مجموعة)"
                    n_grp += 1
        info["students_geocoded_unique"] = n_new
        info["students_cache_hits"] = n_cache
        info["students_group_overrides"] = n_grp

        # كتابة الأعمدة
        lat_name, lon_name = lat_col or "Latitude", lon_col or "Longitude"
        if lat_col and mode == "addr":
            S["Latitude_sheet"], S["Longitude_sheet"] = sheet_lat, sheet_lon
        S[lat_name], S[lon_name] = lat, lon
        S["geo_level"], S["geo_conf"], S["geo_source"], S["geo_matched"] = level, conf, src, matched
        info["students"] = {"total": n, "by_source": pd.Series(src).value_counts().to_dict(),
                            "by_level": pd.Series(level[level != ""]).value_counts().to_dict()}
        n_missing = int((src == "missing").sum())
        if n_missing:
            alerts.append(f"{n_missing} طالب مالهمش موقع (إحداثيات ناقصة أو محافظة غير معروفة): هيتحولوا VP.")
        n_low = int(np.isin(level, LOW_LEVELS).sum())
        if n_low:
            warnings.append(f"{n_low:,} طالب موقعهم على مستوى المنطقة أو أعم (دقة 2-5 كم أو أقل). الأهم فيهم في صفحة مراجعة المواقع.")

        # ====================== قايمة المراجعة (مجموعات حي/شارع) ======================
        progress(12, "تجهيز قايمة مراجعة المواقع")
        review = _review_groups(gcode, areas, addrs, lat, lon, level, conf, src, matched, gkeys)
        progress(13, "جاهز")
        return {"S": S, "C": C, "info": info, "review": review, "gkeys": gkeys, "warnings": warnings, "alerts": alerts}
    finally:
        if own:
            con.close()


def _sheet_coords_suspect(gcode, areas, addrs, lat, lon):
    """هل إحداثيات الطلاب في الشيت حقيقية؟ بنقيس حاجتين:
    1) طلاب محافظة كبيرة (300+) كل 90% منهم جوه نطاق أقل من 1.5 كم = غالبًا مولّدة حوالين وسط المدينة.
    2) نفس العنوان بالظبط بإحداثيات مختلفة (بعيدين أكتر من 200 م عن بقية نفس العنوان).
    بيرجّع قايمة جمل (فاضية = مفيش اشتباه)."""
    df = pd.DataFrame({"g": gcode, "lat": lat, "lon": lon,
                       "k": [f"{g}|{gn.norm(a)}|{gn.norm(t)}" for g, a, t in zip(gcode, areas, addrs)],
                       "t": [len(gn.norm(t)) for t in addrs]})
    df = df[df["g"].notna()]
    msgs, tight = [], []
    for g, d in df.groupby("g"):
        if len(d) < 300:
            continue
        mla, mlo = d["lat"].median(), d["lon"].median()
        dist = np.hypot((d["lat"] - mla) * 110.57, (d["lon"] - mlo) * 111.32 * np.cos(np.radians(mla)))
        p90 = float(np.percentile(dist, 90))
        if p90 < 1.5:
            tight.append(f"{g} (90% من {len(d):,} طالب جوه {p90:.1f} كم)")
    if tight:
        msgs.append("طلاب محافظة كاملة متكتلين في نطاق ضيق جدًا: " + "، ".join(tight[:6])
                    + (f" و{len(tight) - 6} محافظة كمان" if len(tight) > 6 else ""))
    d = df[df["t"] >= 6]
    d = d[d.groupby("k")["lat"].transform("size") >= 2]
    if len(d) >= 300:
        mla = d.groupby("k")["lat"].transform("median")
        mlo = d.groupby("k")["lon"].transform("median")
        dist = np.hypot((d["lat"] - mla) * 110.57, (d["lon"] - mlo) * 111.32 * np.cos(np.radians(mla)))
        frac = float((dist > 0.2).mean())
        if frac > 0.5:
            msgs.append(f"نفس العنوان بإحداثيات مختلفة ({frac:.0%} من الطلاب اللي عناوينهم متكررة بعيدين أكتر من 200 م عن بقية نفس العنوان)")
    return msgs


def loc_text_of(address):
    """نص بيحدد "الحي/الشارع" للتجميع: الجزء التاني في العنوان المرتب، أو أول شارع مذكور، أو أول 3 كلمات."""
    a = (address or "").strip()
    if not a:
        return ""
    segs = [s.strip() for s in re.split(r"\s+[-–]\s+|[،,;/|]", a) if s.strip()]
    if len(segs) >= 3:
        k = gn.key(segs[1], "place")
        if k:
            return k
    ph = gc.Geocoder._street_phrases(a)
    if ph:
        return "ش:" + gn.key(ph[0], "street")
    t = [x for x in gn.tokens(a) if not re.fullmatch(r"\d+", x)]
    return ("نص:" + " ".join(t[:3])) if t else ""


def group_key_of(gov, area, address):
    lt = loc_text_of(address)
    return f"{gov}|{gn.norm(area)}|{lt}" if lt else ""


def _review_groups(gcode, areas, addrs, lat, lon, level, conf, src, matched, gkeys, cap=3000):
    """مجموعات طلاب (حي/شارع داخل منطقة) موقعهم أوتوماتيك وعلى مستوى منطقة أو أعم، الأكبر الأول.
    تصحيح واحد للمجموعة بيتطبق على كل طلابها. الطلاب من غير عنوان مش قابلين للتصحيح هنا."""
    idx = np.nonzero(np.isin(level, LOW_LEVELS) & (src == "osm") & np.isfinite(lat))[0]
    empty, groups = 0, {}
    for i in idx:
        k = gkeys[i]
        if not k:
            empty += 1
            continue
        g = groups.get(k)
        if g is None:
            g = groups[k] = {"gkey": k, "gov": gcode[i], "area": areas[i], "text": loc_text_of(addrs[i]), "n": 0, "rows": [],
                             "lat": [], "lon": [], "levels": {}, "conf": [], "sample": addrs[i], "matched": matched[i]}
        g["n"] += 1
        if len(g["rows"]) < 3:
            g["rows"].append(int(i) + 2)
        g["lat"].append(lat[i])
        g["lon"].append(lon[i])
        g["conf"].append(conf[i])
        g["levels"][level[i]] = g["levels"].get(level[i], 0) + 1
    items = []
    for g in groups.values():
        items.append({"gkey": g["gkey"], "gov": g["gov"], "area": g["area"], "text": g["text"], "n": g["n"], "rows": g["rows"],
                      "level": max(g["levels"], key=g["levels"].get), "conf": round(float(np.nanmean(g["conf"])), 3),
                      "lat": round(float(np.median(g["lat"])), 6), "lon": round(float(np.median(g["lon"])), 6),
                      "sample": g["sample"], "matched": g["matched"]})
    items.sort(key=lambda x: -x["n"])
    return {"total_low": int(len(idx)), "empty_low": int(empty), "groups_total": len(items), "items": items[:cap]}

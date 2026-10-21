"""Geocoder محلي لعناوين مصرية (بدون إنترنت): بيستخدم قاعدة أماكن مبنية من OSM.

المستويات (من الأدق للأعم): poi (معلم) > street (شارع) > locality (حي/قرية) > area (منطقة/مدينة) > gov (عاصمة المحافظة)
كل نتيجة فيها: lat, lon, level, confidence, est_error_m, matched (إيه اللي اتطابق).
"""
import difflib
import math
import os
import re
import sqlite3
import sys

from distributor.core import geonorm as gn

GOV_CAPITALS = {
    "cairo": "القاهرة", "alexandria": "الإسكندرية", "giza": "الجيزة", "qalyubia": "بنها", "sharqia": "الزقازيق",
    "dakahlia": "المنصورة", "beheira": "دمنهور", "gharbia": "طنطا", "monufia": "شبين الكوم", "kafr_el_sheikh": "كفر الشيخ",
    "damietta": "دمياط", "port_said": "بورسعيد", "ismailia": "الإسماعيلية", "suez": "السويس", "fayoum": "الفيوم",
    "beni_suef": "بني سويف", "minya": "المنيا", "assiut": "أسيوط", "sohag": "سوهاج", "qena": "قنا", "luxor": "الأقصر",
    "aswan": "أسوان", "red_sea": "الغردقة", "new_valley": "الخارجة", "matrouh": "مرسى مطروح", "north_sinai": "العريش",
    "south_sinai": "الطور",
}
STREET_STRATEGY = os.environ.get("GEO_STREET", "blend")        # nearest | medoid | blend (blend بيقلل الأخطاء الكبيرة)
EST_ERR = {"poi": 100, "street": 300, "locality": 1500, "area": 4000, "markaz": 6000, "gov": 40000}
LEVEL_CONF = {"poi": 0.90, "street": 0.80, "locality": 0.55, "area": 0.35, "markaz": 0.30, "gov": 0.10}

_STREET_KW = r"(?:شارع|ش|طريق|حاره|ممر|ميدان|كوبري|محور|سكه|نفق)"
_LANDMARK_KW = r"(?:بجوار|جوار|امام|خلف|بالقرب\s+من|قرب|مقابل|عند|بجانب|بحري|قبلي)"
_STOP_AFTER = {"من", "بجوار", "جوار", "امام", "خلف", "بالقرب", "قرب", "مقابل", "عند", "بعد", "قبل", "تقاطع", "بجانب",
               "ب", "و", "علي", "في", "داخل", "الدور", "عمارة", "برج", "منزل", "رقم", "شقه", "بلوك", "عماره"}
_SPLIT = re.compile(r"\s+[-–—]\s+|[،,;/|\n\r]+|\s{2,}")
_GENERIC = {"جمهوريه", "نيل", "جيش", "تحرير", "سلام", "مدرسه", "مسجد", "جامع", "صيدليه", "بنك"}


def _km(lat1, lon1, lat2, lon2):
    dy = (lat2 - lat1) * 110.57
    dx = (lon2 - lon1) * 111.32 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)


def _tok_close(a, b):
    if a == b:
        return True
    if len(a) < 4 or len(b) < 4 or abs(len(a) - len(b)) > 2:
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.85


def _dice(q, n):
    """تشابه كلمات مع تسامح في الأخطاء الإملائية البسيطة. q, n: قوايم توكنز."""
    if not q or not n:
        return 0.0
    used, m = set(), 0
    for t in q:
        for j, u in enumerate(n):
            if j not in used and _tok_close(t, u):
                used.add(j)
                m += 1
                break
    return 2.0 * m / (len(q) + len(n))


def _areaset(area):
    return frozenset(gn.tokens(area, drop=gn.PLACE_WORDS))


class Geocoder:
    def __init__(self, db_path, centers=None):
        """centers: قايمة مراكز [{'code','gov','area','lat','lon'}] لاستخدامها كمرساة للمنطقة لو مش موجودة في OSM/GeoNames."""
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self._gov = {}
        self._cap = {}
        self._center_areas = {}
        for c in (centers or []):
            self._center_areas.setdefault((c["gov"], _areaset(c["area"])), []).append((c["code"], c["lat"], c["lon"]))

    def _center_anchor(self, gov, area, exclude=None):
        """متوسط مواقع المراكز اللي في نفس المنطقة (بنفس اسم المنطقة بغض النظر عن الترتيب)."""
        pts = [(la, lo) for code, la, lo in self._center_areas.get((gov, _areaset(area)), []) if code != exclude]
        if not pts:
            return None
        return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts), len(pts)

    # ---------- تحميل بيانات المحافظة ----------
    def _load(self, gov):
        if gov in self._gov:
            return self._gov[gov]
        d = {"places": {}, "places_full": {}, "place_set": {}, "place_tok": {}, "streets": {}, "street_tok": {},
             "pois": {}, "poi_tok": {}}
        q = " (gov=? OR gov IS NULL)" if gov else " 1=1"
        args = (gov,) if gov else ()
        for kind, rank, name, key, kfull, lat, lon, pop in self.db.execute(
                f"SELECT kind,rank,name,key,kfull,lat,lon,pop FROM places WHERE{q}", args):
            e = (lat, lon, rank, kind, name, pop or 0)
            d["places"].setdefault(key, []).append(e)
            d["places_full"].setdefault(kfull, []).append(e)
            d["place_set"].setdefault(frozenset(key.split()), []).append(e)      # مطابقة بدون اعتبار الترتيب
            for t in key.split():
                d["place_tok"].setdefault(t, set()).add(key)
        for name, key, lat, lon in self.db.execute(f"SELECT name,key,lat,lon FROM streets WHERE{q}", args):
            d["streets"].setdefault(key, []).append((lat, lon, name))
        for k in d["streets"]:
            for t in k.split():
                d["street_tok"].setdefault(t, set()).add(k)
        for kind, sub, name, key, lat, lon in self.db.execute(f"SELECT kind,subkind,name,key,lat,lon FROM pois WHERE{q}", args):
            d["pois"].setdefault(key, []).append((lat, lon, name, kind, sub))
        for k in d["pois"]:
            for t in k.split():
                d["poi_tok"].setdefault(t, set()).add(k)
        self._gov[gov] = d
        return d

    def _radius(self, gov):
        """نصف قطر منطقي حوالين عاصمة المحافظة (من حجم حدودها)."""
        r = self.db.execute("SELECT bbox FROM govs WHERE code=?", (gov,)).fetchone()
        if not r:
            return 80
        import json as _j
        b = _j.loads(r[0])
        diag = _km(b[1], b[0], b[3], b[2])
        return max(25.0, min(150.0, 0.5 * diag))

    def capital(self, gov):
        if gov in self._cap:
            return self._cap[gov]
        pt = None
        nm = GOV_CAPITALS.get(gov)
        if nm:
            r = self.db.execute("SELECT lat,lon FROM places WHERE key=? AND rank<=2 AND (gov=? OR gov IS NULL) ORDER BY rank,"
                                " COALESCE(pop,0) DESC LIMIT 1", (gn.key(nm, "place"), gov)).fetchone()
            if r:
                pt = (r[0], r[1])
        self._cap[gov] = pt
        return pt

    # ---------- بحث عن اسم ----------
    def _fuzzy_keys(self, q_tokens, index, thr=0.78, limit=400):
        """مفاتيح مرشحة من الفهرس بالتوكنز النادرة، مع تقييم Dice."""
        cands = set()
        for t in q_tokens:
            ks = index.get(t)
            if ks and len(ks) <= limit:
                cands |= ks
        out = []
        for k in cands:
            s = _dice(q_tokens, k.split())
            if s >= thr:
                out.append((s, k))
        out.sort(reverse=True)
        return out

    def _find_place(self, text, G, near=None, max_km=None, exact_only=False):
        """بيرجّع (entry, sim) لأحسن مكان بيطابق النص، (اختياريًا) في حدود max_km من نقطة near."""
        full = gn.key_full(text)
        k = gn.key(text, "place")
        if not k:
            return None
        cand = []
        for e in G["places_full"].get(full, []):
            cand.append((1.0, e))
        if not cand:
            for e in G["places"].get(k, []):
                cand.append((0.97, e))
        if not cand and len(k.split()) >= 2:
            for e in G["place_set"].get(frozenset(k.split()), []):          # نفس الكلمات بترتيب مختلف
                cand.append((0.95, e))
        if not cand and not exact_only:
            for s, kk in self._fuzzy_keys(k.split(), G["place_tok"], thr=0.85):
                for e in G["places"].get(kk, []):
                    cand.append((s * 0.95, e))
        if near and max_km:
            cand = [(s, e) for s, e in cand if _km(near[0], near[1], e[0], e[1]) <= max_km]
        if not cand:
            return None
        # الأعلى تشابهًا، ثم الأقرب لـ near، ثم الأعلى رتبة (مدينة>حي>قرية) ثم الأكبر سكانًا
        def score(c):
            s, e = c
            dist = _km(near[0], near[1], e[0], e[1]) if near else 0
            return (-s, dist if near else 0, e[2], -e[5])
        cand.sort(key=score)
        s, e = cand[0]
        return e, s

    def _find_named(self, text, G, which, near, max_km):
        """شارع/معلم: أحسن مطابقة للاسم في حدود max_km من near، ويرجّع (lat, lon, name, sim)."""
        kind = "street" if which == "streets" else "poi"
        k = gn.key(text, kind)
        if not k:
            return None
        toks = k.split()
        if not toks or (len(toks) == 1 and toks[0] in _GENERIC and kind == "poi"):
            return None
        table = G[which]
        keys = []
        if k in table:
            keys.append((1.0, k))
        else:
            tokidx = G["street_tok"] if which == "streets" else G["poi_tok"]
            keys = [(s, kk) for s, kk in self._fuzzy_keys(toks, tokidx, thr=0.80)][:8]
        best = None
        for s, kk in keys:
            for ent in table.get(kk, []):
                d = _km(near[0], near[1], ent[0], ent[1]) if near else 0
                if near and d > max_km:
                    continue
                cand = (-s, d, ent)
                if best is None or cand[:2] < best[:2]:
                    best = cand
        if not best:
            return None
        ent = best[2]
        return ent[0], ent[1], ent[2], -best[0], best[1]

    # ---------- تحليل العنوان ----------
    @staticmethod
    def _segments(addr):
        return [s.strip() for s in _SPLIT.split(str(addr or "")) if s and s.strip()]

    @staticmethod
    def _street_phrases(addr):
        """عبارات الشوارع: بعد كلمة شارع/ش/طريق... لحد كلمة وقف (من/بجوار/...) أو 4 كلمات."""
        t = gn.norm(addr)
        out = []
        for m in re.finditer(rf"(?:^|\s){_STREET_KW}\s+(.+)", t):
            words = m.group(1).split()
            phrase = []
            for w in words:
                if w in _STOP_AFTER or re.fullmatch(r"\d+", w):
                    if phrase:
                        break
                    continue
                phrase.append(w)
                if len(phrase) >= 4:
                    break
            if phrase:
                out.append(" ".join(phrase))
        return out

    @staticmethod
    def _landmark_phrases(addr):
        t = gn.norm(addr)
        out = []
        for m in re.finditer(rf"(?:^|\s){_LANDMARK_KW}\s+(.+)", t):
            words = m.group(1).split()
            phrase = []
            for w in words:
                if w in _STOP_AFTER and phrase:
                    break
                if re.fullmatch(r"\d+", w):
                    continue
                phrase.append(w)
                if len(phrase) >= 4:
                    break
            if phrase:
                out.append(" ".join(phrase))
        return out

    # ---------- التحديد ----------
    def _ngram_places(self, addr, G, near, max_km, min_rank=1):
        """أسماء أماكن جوه النص الحر. شروط منعًا للمطابقات العشوائية:
        الاسم يكون كلمتين فأكتر، أو جزء مستقل من العنوان، أو بعد كلمة (حي/مدينة/قرية/منطقة/مركز/قسم...)."""
        found = []
        for seg in self._segments(addr):
            toks = [t for t in gn.tokens(seg) if not re.fullmatch(r"\d+", t)]
            core = [t for t in toks if t not in gn.PLACE_WORDS and t not in _STOP_AFTER and t not in gn.STREET_WORDS]
            for n in range(min(4, len(toks)), 0, -1):
                for i in range(0, len(toks) - n + 1):
                    w = toks[i:i + n]
                    kd = " ".join(t for t in w if t not in gn.PLACE_WORDS)
                    if not kd or all(t in _STOP_AFTER or t in gn.STREET_WORDS for t in w):
                        continue
                    standalone = (len(core) == len([t for t in w if t not in gn.PLACE_WORDS]) and len(core) > 0)
                    prefixed = i > 0 and toks[i - 1] in gn.PLACE_WORDS
                    if n == 1 and not (standalone or prefixed):
                        continue
                    if n == 1 and (kd in _GENERIC or len(kd) < 3):
                        continue
                    ents = G["places_full"].get(" ".join(w)) or G["places"].get(kd) or []
                    for e in ents:
                        if e[2] < min_rank:
                            continue
                        d = _km(near[0], near[1], e[0], e[1]) if near else 0
                        if near and max_km and d > max_km:
                            continue
                        found.append((n, e, d))
        return found
    def _segments_of(self, name_phrase, G, kind, near, max_km):
        """كل المقاطع (lat, lon, name, sim) لاسم شارع/معلم في حدود max_km من near."""
        k = gn.key(name_phrase, "street" if kind == "streets" else "poi")
        if not k:
            return []
        toks = k.split()
        table = G[kind]
        if k in table:
            keys = [(1.0, k)]
        else:
            idx = G["street_tok"] if kind == "streets" else G["poi_tok"]
            keys = self._fuzzy_keys(toks, idx, thr=0.80)[:6]
        out = []
        for s, kk in keys:
            for ent in table.get(kk, []):
                if near and _km(near[0], near[1], ent[0], ent[1]) > max_km:
                    continue
                out.append((ent[0], ent[1], ent[2], s))
        return out

    def geocode(self, gov, area, addr, exclude_center=None):
        """gov: كود المحافظة (مثلا 'cairo')، area: اسم المنطقة، addr: نص العنوان.
        exclude_center: كود مركز نستبعده من مرساة المنطقة (لقياس الدقة بدون غش)."""
        G = self._load(gov)
        segs = self._segments(addr)
        cap = self.capital(gov)
        big = gov in ("matrouh", "red_sea", "new_valley", "south_sinai", "north_sinai")
        max_cap = None if big else self._radius(gov)
        full_text = " ".join(([area] if area else []) + segs)
        # 1) المرساة: اسم المنطقة (من الشيت) ثم أول أجزاء العنوان
        anchor, a_name, a_level = None, None, None
        if area:
            r = self._find_place(area, G, near=cap, max_km=max_cap)
            if r:
                anchor, a_name, a_level = (r[0][0], r[0][1]), r[0][4], "area"
            else:                                   # المنطقة مش في OSM/GeoNames: متوسط مراكز نفس المنطقة
                ca = self._center_anchor(gov, area, exclude_center)
                if ca:
                    anchor, a_name, a_level = (ca[0], ca[1]), f"{area} (متوسط {ca[2]} مركز)", "area"
        # لو المنطقة طلعت مدينة المحافظة نفسها (واسعة)، دوّر على اسم أدق جوه العنوان
        grams = self._ngram_places(addr, G, cap, max_cap, min_rank=1) if (cap or big) else []
        grams = [g for g in grams if not a_name or gn.key_full(g[1][4]) != gn.key_full(a_name)]
        if grams:
            # الأطول نافذة، بعدين الأعلى تخصصًا (حي/قرية قبل مدينة)، بعدين الأقرب
            grams.sort(key=lambda g: (-g[0], 0 if g[1][2] >= 3 else 1, g[2]))
        capital_like = (anchor is None) or (a_level == "area" and any(
            gn.key(a_name, "place") == gn.key(GOV_CAPITALS.get(gov, ""), "place") for _ in [0]))
        if capital_like and grams:
            n, e, d = grams[0]
            anchor, a_name, a_level = (e[0], e[1]), e[4], "markaz" if e[2] <= 2 else "area"
        if anchor is None:
            for s in segs[:3]:
                r = self._find_place(s, G, near=cap, max_km=max_cap)
                if r:
                    anchor, a_name, a_level = (r[0][0], r[0][1]), r[0][4], "markaz"
                    break
        base = anchor or cap
        # 2) الحي/القرية (جوه النص) قريب من المرساة
        loc, l_name = None, None
        cand = [g for g in self._ngram_places(addr, G, base, 15, min_rank=3)
                if not a_name or gn.key_full(g[1][4]) != gn.key_full(a_name)] if base else []
        if cand:
            cand.sort(key=lambda g: (-g[0], g[2]))
            loc, l_name = (cand[0][1][0], cand[0][1][1]), cand[0][1][4]
        center = loc or base
        # 3) الشوارع (ممكن أكتر من واحد) -> تقاطع لو اتنين
        phrases = self._street_phrases(addr)
        street_sets = []
        for ph in phrases:
            for kk in range(len(ph.split()), 0, -1):
                sub = " ".join(ph.split()[:kk])
                segsx = self._segments_of(sub, G, "streets", center, 5.0 if loc else 9.0)
                if segsx:
                    street_sets.append((sub, segsx))
                    break
        street = None          # (lat, lon, name, sim, is_intersection)
        if len(street_sets) >= 2:
            best = None
            for i in range(len(street_sets)):
                for j in range(i + 1, len(street_sets)):
                    for a in street_sets[i][1]:
                        for b in street_sets[j][1]:
                            d = _km(a[0], a[1], b[0], b[1])
                            if d <= 1.2 and (best is None or d < best[0]):
                                best = (d, a, b, street_sets[i][0], street_sets[j][0])
            if best:
                _, a, b, n1, n2 = best
                street = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, f"{n1} × {n2}", min(a[3], b[3]), True)
        if street is None and street_sets:
            segsx = street_sets[0][1]
            ref = center
            top = max(s[3] for s in segsx)
            segsx = [s for s in segsx if s[3] >= top - 1e-9]
            e = min(segsx, key=lambda s: _km(ref[0], ref[1], s[0], s[1]) if ref else 0)         # الأقرب لمركز المنطقة
            if STREET_STRATEGY != "nearest" and len(segsx) > 2:
                med = min(segsx, key=lambda s: sum(_km(s[0], s[1], t[0], t[1]) for t in segsx))     # القطعة الوسطى من الشارع
                if STREET_STRATEGY == "medoid":
                    e = med
                else:                                                                              # blend
                    e = (( e[0] + med[0]) / 2, (e[1] + med[1]) / 2, e[2], e[3])
            street = (e[0], e[1], e[2], e[3], False)
        # 4) المعلم: أصرم (اسم مميز، قريب)
        poi = None
        ref = (street[0], street[1]) if street else center
        for ph in self._landmark_phrases(addr):
            for kk in range(len(ph.split()), 0, -1):
                sub = " ".join(ph.split()[:kk])
                tk = gn.key(sub, "poi").split()
                if not tk or (len(tk) == 1 and (tk[0] in _GENERIC or len(tk[0]) < 5)):
                    continue
                segsx = [s for s in self._segments_of(sub, G, "pois", ref, 1.5 if street else 3.0) if s[3] >= 0.85]
                if segsx:
                    e = min(segsx, key=lambda s: _km(ref[0], ref[1], s[0], s[1]) if ref else 0)
                    poi = e
                    break
            if poi:
                break
        # 5) القرار
        if poi:
            lat, lon, level, matched, sim = poi[0], poi[1], "poi", poi[2], poi[3]
        elif street:
            lat, lon, level, matched, sim = street[0], street[1], "street", street[2], street[3]
        elif loc:
            lat, lon, level, matched, sim = loc[0], loc[1], "locality", l_name, 1.0
        elif anchor:
            lat, lon, level, matched, sim = anchor[0], anchor[1], a_level, a_name, 1.0
        elif cap:
            lat, lon, level, matched, sim = cap[0], cap[1], "gov", GOV_CAPITALS.get(gov), 1.0
        else:
            return {"lat": None, "lon": None, "level": "none", "confidence": 0.0, "est_error_m": None, "matched": ""}
        conf = LEVEL_CONF[level] * (0.6 + 0.4 * sim)
        if level == "street" and street and street[4]:
            conf = min(0.95, conf + 0.1)                 # تقاطع شارعين: أدق
        return {"lat": lat, "lon": lon, "level": level, "confidence": round(conf, 3), "est_error_m": EST_ERR[level],
                "matched": matched or "", "anchor": a_name or "", "locality": l_name or ""}


"""شيت طلاب تجريبي (100 طالب على مستوى الجمهورية) لاختبار تحديد المواقع، بنفس أعمدة «Student DB».
كل البيانات وهمية. العناوين مبنية من قاعدة الأماكن، فكل صف ليه موقع صحيح معروف (في شيت الإجابات، مش في شيت الطلاب).

السيناريوهات:
  amb_street            شارع اسمه متكرر في محافظات كتير (بورسعيد/السويس/دمياط/الجلاء...) + منطقة صح
  amb_street_in_addr    نفس الكلام بس المنطقة فاضية واسمها جوه العنوان
  amb_street_no_context شارع متكرر لوحده (من غير منطقة): المطلوب يفضل جوه المحافظة الصح
  dup_area_name         اسم منطقة موجود في كذا محافظة (المنشية، الروضة، عزبة العرب...)
  clean                 منطقة + شارع عادي (مرجع)
  (كل الطلاب في محافظات البند الأول والتالت عشان كلهم يدخلوا التوزيع)
  missing_address       منطقة بس، العنوان فاضي
  missing_area          المنطقة فاضية، والعنوان فيه الشارع والمنطقة
  missing_both          المنطقة والعنوان فاضيين
  junk_address          عنوان مالوش معنى (بجوار الجامع، -، لا يوجد...)
  variant_spelling      كتابة مختلفة للشارع (ش.، بور سعيد، أرقام هندي، من غير "شارع")

الملف واحد (sample/students_geo_test.xlsx):
  Student DB   الشيت اللي بيترفع (أول شيت: السيستم بيقرا أول شيت بس)
  Answer Key   الموقع الصحيح لكل طالب + السيناريو + لينك خريطة
  Results/Summary  بيضيفهم check_geo_testset.py بعد الفحص

الاستخدام: python tools/make_geo_testset.py [gazetteer.sqlite] [out_dir]
الفحص بعدها: python tools/check_geo_testset.py"""
import math
import os
import random
import re
import sqlite3
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from distributor.core import engine  # noqa: E402
from distributor.core import geonorm as gn  # noqa: E402
from check_geo_testset import gov_of  # noqa: E402

GAZ = sys.argv[1] if len(sys.argv) > 1 else os.path.join("data", "gazetteer.sqlite")
OUT = sys.argv[2] if len(sys.argv) > 2 else "sample"
APP_DB = os.path.join("data", "app.db")
random.seed(2026)

# توزيع الـ100 طالب على المحافظات: البند الأول والتالت بس (البند التاني مالوش مراكز في التشغيل)
GOV_WEIGHTS = {"cairo": 20, "alexandria": 11, "dakahlia": 11, "sharqia": 8, "gharbia": 7, "beheira": 6, "kafr_el_sheikh": 4,
               "damietta": 4, "fayoum": 4, "beni_suef": 4, "minya": 4, "sohag": 4, "port_said": 3, "ismailia": 3, "suez": 3,
               "new_valley": 2, "south_sinai": 1, "north_sinai": 1}
assert sum(GOV_WEIGHTS.values()) == 100
assert not [g for g in GOV_WEIGHTS if engine.CITY_BANDS.get(g) == engine.BAND_2], "محافظة من البند التاني"
SCENARIOS = [("amb_street", 30), ("amb_street_in_addr", 8), ("amb_street_no_context", 7), ("dup_area_name", 10),
             ("clean", 15), ("missing_address", 8), ("missing_area", 7), ("missing_both", 5), ("junk_address", 5),
             ("variant_spelling", 5)]
TOL_KM = {"amb_street": 2.5, "amb_street_in_addr": 3.0, "dup_area_name": 4.0, "clean": 2.5, "missing_address": 5.0,
          "missing_area": 3.0, "variant_spelling": 3.0}          # الباقي: فحص "جوه المحافظة الصح" بس
# شوارع على اسم محافظة تانية: الفخ الأساسي (الشارع في gov، والاسم بيشاور على trap)
GOV_NAMED = [("dakahlia", "بورسعيد", "port_said"), ("cairo", "بورسعيد", "port_said"), ("ismailia", "بورسعيد", "port_said"),
             ("sharqia", "بورسعيد", "port_said"), ("kafr_el_sheikh", "بورسعيد", "port_said"), ("alexandria", "بورسعيد", "port_said"),
             ("beheira", "بورسعيد", "port_said"), ("minya", "بورسعيد", "port_said"), ("cairo", "السويس", "suez"),
             ("ismailia", "السويس", "suez"), ("alexandria", "السويس", "suez"), ("sharqia", "السويس", "suez"),
             ("dakahlia", "دمياط", "damietta"), ("gharbia", "دمياط", "damietta"), ("beheira", "القاهره", "cairo"),
             ("fayoum", "القاهره", "cairo"), ("beni_suef", "القاهره", "cairo"), ("cairo", "الاسكندريه", "alexandria")]
AMB_NAMES = ["الجلاء", "طلعت حرب", "26 يوليو", "الثوره", "جمال عبد الناصر", "الجيش", "سعد زغلول", "الجمهوريه",
             "بورسعيد", "السويس", "دمياط", "التحرير", "الجمهوريه", "المحطه", "البحر", "النيل", "عرابي", "مصطفي كامل"]
TRAP = {gn.key(n, "street"): t for _, n, t in GOV_NAMED}
JUNK = ["بجوار الجامع", "-", "لا يوجد", "عند الموقف", "0", "مصر", "البيت", "نفس العنوان"]
GOV3 = {"cairo": "CAI", "giza": "GIZ", "alexandria": "ALX", "dakahlia": "DKH", "sharqia": "SHR", "qalyubia": "QLY",
        "gharbia": "GHR", "monufia": "MNF", "beheira": "BHR", "kafr_el_sheikh": "KFS", "damietta": "DMT", "port_said": "PSD",
        "ismailia": "ISM", "suez": "SWS", "fayoum": "FYM", "beni_suef": "BNS", "minya": "MNY", "assiut": "AST",
        "sohag": "SOH", "qena": "QNA", "luxor": "LXR", "aswan": "ASW", "red_sea": "RSE", "matrouh": "MTR", "new_valley": "WJD", "south_sinai": "JSN",
        "north_sinai": "NSN"}
NID_GOV = {"cairo": "01", "alexandria": "02", "port_said": "03", "suez": "04", "damietta": "11", "dakahlia": "12",
           "sharqia": "13", "qalyubia": "14", "kafr_el_sheikh": "15", "gharbia": "16", "monufia": "17", "beheira": "18",
           "ismailia": "19", "giza": "21", "beni_suef": "22", "fayoum": "23", "minya": "24", "assiut": "25", "sohag": "26",
           "qena": "27", "aswan": "28", "luxor": "29", "red_sea": "31", "new_valley": "32", "matrouh": "33", "north_sinai": "34",
           "south_sinai": "35"}
LEVELS = ["first"] * 4 + ["second"] * 3 + ["third_web", "third_ai", "third_cyber_security", "forth_web"]
LEVEL_CODE = {"first": "L1-GEN", "second": "L2-GEN", "third_web": "L3-WB", "third_ai": "L3-DT",
              "third_cyber_security": "L3-CY", "forth_web": "L4-WB"}

MALE = [("محمد", "Mohamed"), ("أحمد", "Ahmed"), ("محمود", "Mahmoud"), ("عمر", "Omar"), ("يوسف", "Youssef"), ("علي", "Ali"),
        ("مصطفى", "Mostafa"), ("كريم", "Karim"), ("حسن", "Hassan"), ("خالد", "Khaled"), ("إبراهيم", "Ibrahim"),
        ("عبد الرحمن", "Abdelrahman"), ("زياد", "Ziad"), ("مازن", "Mazen"), ("سيف", "Seif"), ("آدم", "Adam")]
FEMALE = [("مريم", "Mariam"), ("نور", "Nour"), ("سارة", "Sara"), ("فاطمة", "Fatma"), ("هنا", "Hana"), ("جنى", "Jana"),
          ("ملك", "Malak"), ("رنا", "Rana"), ("ياسمين", "Yasmin"), ("آية", "Aya"), ("سلمى", "Salma"), ("حبيبة", "Habiba")]
FATHERS = MALE + [("سعيد", "Saeed"), ("طارق", "Tarek"), ("أشرف", "Ashraf"), ("هشام", "Hesham"), ("وليد", "Walid"),
                  ("عادل", "Adel"), ("جمال", "Gamal"), ("سامي", "Samy")]
FAMILY = [("السيد", "Elsayed"), ("عبد الله", "Abdallah"), ("الشافعي", "Elshafei"), ("حسين", "Hussein"), ("منصور", "Mansour"),
          ("عثمان", "Osman"), ("النجار", "Elnaggar"), ("فرج", "Farag"), ("سليمان", "Soliman"), ("الشريف", "Elsherif")]

AR_OK = re.compile(r"^[ء-ي0-9 ]+$")
db = sqlite3.connect(GAZ)


def km(a, b):
    p = math.pi / 180
    x = (b[1] - a[1]) * p * math.cos((a[0] + b[0]) / 2 * p)
    return 6371 * math.hypot(x, (b[0] - a[0]) * p)


_seg_cache, _place_cache = {}, {}


def segs(gov, k):
    if (gov, k) not in _seg_cache:
        _seg_cache[gov, k] = [(r[0], r[1]) for r in db.execute("SELECT lat, lon FROM streets WHERE gov=? AND key=?", (gov, k))]
    return _seg_cache[gov, k]


def places(gov):
    """أماكن عربي سليم في المحافظة: (name, lat, lon, rank, kfull)."""
    if gov not in _place_cache:
        rows = db.execute("SELECT name, lat, lon, rank, kfull FROM places WHERE gov=? AND rank<=4", (gov,)).fetchall()
        _place_cache[gov] = [r for r in rows if AR_OK.match(r[0].strip()) and len(r[0].strip()) >= 3]
    return _place_cache[gov]


def unique_in_gov(gov, kfull):
    return sum(1 for p in places(gov) if p[4] == kfull) == 1


def area_near(gov, pt, street_key):
    """أقرب منطقة معروفة (ومش متكررة جوه المحافظة) للنقطة: مدينة/حي (rank<=3) لحد 5 كم، وإلا حي صغير/قرية لحد 2 كم."""
    best = None
    for p in places(gov):
        d = km(pt, (p[1], p[2]))
        lim = 5.0 if p[3] <= 3 else 2.0
        if d > lim or gn.key(p[0], "street") == street_key or not unique_in_gov(gov, p[4]):
            continue
        score = d + (0 if p[3] <= 3 else 1.5)
        if best is None or score < best[0]:
            best = (score, p)
    return best[1] if best else None


def medoid(pts):
    return min(pts, key=lambda s: sum(km(s, t) for t in pts))


def street_case(gov, k, need_area=True):
    """مقطع من شارع k في المحافظة + منطقة قريبة + الموقع الصحيح. لازم أقرب مقطع بنفس الاسم للمنطقة يكون من نفس الشارع
    (عشان الإجابة تبقى عادلة: لو في شارع تاني بنفس الاسم أقرب للمنطقة، النظام مش غلطان)."""
    S = segs(gov, k)
    if len(S) < 2:
        return None
    for seed in random.sample(S, min(len(S), 12)):
        cluster = [s for s in S if km(seed, s) <= 1.5]
        truth = medoid(cluster)
        if not need_area:
            return {"truth": truth, "area": None}
        a = area_near(gov, truth, k)
        if not a:
            continue
        nearest = min(S, key=lambda s: km((a[1], a[2]), s))
        if km(nearest, truth) <= 1.6:
            return {"truth": truth, "area": a}
    return None


def street_govs(k):
    return {r[0] for r in db.execute("SELECT DISTINCT gov FROM streets WHERE key=?", (k,))}


_clean_cache = {}


def clean_streets(gov):
    """شوارع اسمها موجود في المحافظة دي بس (غير متكرر في أي محافظة تانية)."""
    if gov not in _clean_cache:
        rows = db.execute("""SELECT s.key, MIN(s.name), COUNT(*) FROM streets s WHERE s.gov=? AND s.name LIKE 'شارع %'
                             GROUP BY s.key HAVING COUNT(*) BETWEEN 2 AND 40""", (gov,)).fetchall()
        rows = [r for r in rows if AR_OK.match(r[1]) and len(r[0]) >= 4 and not r[0].isdigit()]
        random.shuffle(rows)
        _clean_cache[gov] = rows[:300]
    return [r for r in _clean_cache[gov] if street_govs(r[0]) == {gov}]


def street_label(k):
    """اسم الشارع زي ما الناس بتكتبه (من اسم أول مقطع)."""
    r = db.execute("SELECT name FROM streets WHERE key=? AND name LIKE 'شارع %' LIMIT 1", (k,)).fetchone()
    return re.sub(r"^شارع\s+", "", r[0]) if r else k


def variant(name):
    """كتابة مختلفة لاسم الشارع."""
    opts = [f"ش.{name}", f"ش {name}", f"{name}"]
    if "بورسعيد" in name:
        opts.append(name.replace("بورسعيد", "بور سعيد"))
    if re.search(r"\d", name):
        opts.append(name.translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")))
    if "ة" in name or "ى" in name:
        opts.append(name.replace("ة", "ه").replace("ى", "ي"))
    return random.choice(opts)


def addr_fmt(street, area):
    n = random.randint(1, 140)
    return random.choice([f"{n} شارع {street} - {area}", f"شارع {street} - {area}", f"{area} - {n} ش {street}",
                          f"{n} شارع {street} متفرع من الرئيسي - {area}", f"{area}، شارع {street}، الدور {random.randint(1, 6)}"])


rows, used_keys = [], set()


def add(gov, scen, area, addr, truth, note="", trap=None):
    if truth and gov_of(truth[0], truth[1], GAZ) != gov:     # قاعدة الأماكن ساعات بتحط مكان في محافظة غلط (مركز تلا → الغربية)
        return False
    if trap == gov:                                       # شارع دمياط في دمياط مش فخ
        trap = None
    rows.append({"gov": gov, "scenario": scen, "area": area or "", "address": addr or "", "truth": truth, "note": note,
                 "trap": trap or ""})
    return True


def gen(scen, gov):
    if scen in ("amb_street", "amb_street_in_addr", "amb_street_no_context", "variant_spelling"):
        names = AMB_NAMES[:]
        random.shuffle(names)
        for nm in names:
            k = gn.key(nm, "street")
            if (gov, k, scen) in used_keys or len(street_govs(k)) < 3:
                continue
            c = street_case(gov, k, need_area=scen != "amb_street_no_context")
            if not c:
                continue
            used_keys.add((gov, k, scen))
            label = street_label(k)
            others = sorted(street_govs(k) - {gov})
            note = f"«{label}» موجود كمان في: {', '.join(others[:6])}{'...' if len(others) > 6 else ''}"
            if scen == "amb_street":
                ok = add(gov, scen, c["area"][0], addr_fmt(label, c["area"][0]), c["truth"], note, TRAP.get(k))
            elif scen == "amb_street_in_addr":
                ok = add(gov, scen, "", addr_fmt(label, c["area"][0]), c["truth"], note + f" | المنطقة «{c['area'][0]}» جوه العنوان بس", TRAP.get(k))
            elif scen == "variant_spelling":
                v = variant(label)
                ok = add(gov, scen, c["area"][0], f"{v} - {c['area'][0]}", c["truth"], f"كتابة «{v}» بدل «شارع {label}»", TRAP.get(k))
            else:
                ok = add(gov, scen, "", f"شارع {label}", None, note + " | من غير أي سياق: المطلوب يفضل في المحافظة الصح", TRAP.get(k))
            if ok:
                return True
        return False
    if scen == "dup_area_name":
        dup = db.execute("""SELECT kfull FROM places WHERE rank<=4 GROUP BY kfull HAVING COUNT(DISTINCT gov)>=2""").fetchall()
        dup = {r[0] for r in dup}
        cand = [p for p in places(gov) if p[4] in dup and unique_in_gov(gov, p[4]) and (gov, p[4]) not in used_keys]
        if not cand:
            return False
        p = random.choice(cand[:200])
        used_keys.add((gov, p[4]))
        others = sorted({r[0] for r in db.execute("SELECT DISTINCT gov FROM places WHERE kfull=? AND gov<>?", (p[4], gov))})
        return add(gov, scen, p[0], random.choice(["", f"{p[0]} بجوار المسجد", f"{p[0]} - منزل {random.randint(1, 50)}"]),
                   (p[1], p[2]), f"اسم المنطقة «{p[0]}» موجود كمان في: {', '.join(others[:6])}")
    if scen in ("clean", "missing_area"):
        for k, name, _ in clean_streets(gov)[:60]:
            if (gov, k) in used_keys:
                continue
            c = street_case(gov, k)
            if not c:
                continue
            used_keys.add((gov, k))
            label = re.sub(r"^شارع\s+", "", name)
            if scen == "clean":
                if add(gov, scen, c["area"][0], addr_fmt(label, c["area"][0]), c["truth"], "شارع اسمه مش متكرر في أي محافظة تانية"):
                    return True
            elif add(gov, scen, "", addr_fmt(label, c["area"][0]), c["truth"], f"المنطقة فاضية، «{c['area'][0]}» جوه العنوان"):
                return True
        return False
    if scen == "missing_address":
        cand = [p for p in places(gov) if p[3] <= 3 and unique_in_gov(gov, p[4]) and (gov, p[4]) not in used_keys]
        if not cand:
            return False
        p = random.choice(cand)
        used_keys.add((gov, p[4]))
        return add(gov, scen, p[0], "", (p[1], p[2]), "العنوان فاضي: المتوقع موقع المنطقة")

    if scen == "missing_both":
        return add(gov, scen, "", "", None, "مفيش منطقة ولا عنوان: المتوقع عاصمة المحافظة (ثقة قليلة)")

    if scen == "junk_address":
        return add(gov, scen, "", random.choice(JUNK), None, "عنوان مالوش معنى: المتوقع عاصمة المحافظة (ثقة قليلة)")

    raise ValueError(scen)


# 1) الفخ الأساسي: شوارع على اسم محافظة تانية
for gov, nm, trap in GOV_NAMED:
    k = gn.key(nm, "street")
    c = street_case(gov, k)
    if c:
        used_keys.add((gov, k, "amb_street"))
        label = street_label(k)
        add(gov, "amb_street", c["area"][0], addr_fmt(label, c["area"][0]), c["truth"],
            f"شارع على اسم محافظة تانية ({trap}): لازم الطالب يفضل في {gov}", trap)
# 2) باقي السيناريوهات على المحافظات بالأوزان
govs = [g for g, w in GOV_WEIGHTS.items() for _ in range(w)]
random.shuffle(govs)
order = sorted(GOV_WEIGHTS, key=lambda g: -GOV_WEIGHTS[g])
gi = 0
for scen, n in SCENARIOS:
    have = sum(1 for r in rows if r["scenario"] == scen)
    for _ in range(n - have):
        tries = [govs[gi % len(govs)]] + order
        gi += 1
        if not any(gen(scen, g) for g in tries):
            print("مقدرتش أعمل حالة:", scen)
random.shuffle(rows)

# ====================== باقي أعمدة Student DB (وهمية) ======================
centers = {}
if os.path.exists(APP_DB):
    try:
        for c in sqlite3.connect(APP_DB).execute("SELECT gov, name, address FROM centers WHERE name IS NOT NULL"):
            centers.setdefault(c[0], []).append((c[1], c[2] or ""))
    except sqlite3.Error:
        pass
used_nid, counters = set(), {}


def nid(gov, year, male):
    while True:
        cent = "3" if year >= 2000 else "2"
        d = f"{year % 100:02d}{random.randint(1, 12):02d}{random.randint(1, 28):02d}"
        serial = f"{random.randint(0, 99):02d}{random.choice('13579' if male else '02468')}"
        x = cent + d + NID_GOV.get(gov, "88") + f"{random.randint(0, 9)}{serial}" + str(random.randint(1, 9))
        x = x[:14]
        if x not in used_nid:
            used_nid.add(x)
            return x


def person(male):
    f = random.choice(MALE if male else FEMALE)
    fa, gf, fam = random.choice(FATHERS), random.choice(FATHERS), random.choice(FAMILY)
    return f, fa, gf, fam


students, key = [], []
for i, r in enumerate(rows):
    sid = 900001 + i
    male = random.random() < 0.55
    f, fa, gf, fam = person(male)
    level = random.choice(LEVELS)
    physical = r["gov"] in centers and random.random() < 0.6
    counters[(r["gov"], level)] = counters.get((r["gov"], level), 0) + 1
    day = random.choice("AB")
    if physical:
        group = f"PH-{day}-{GOV3[r['gov']]}-V01-{LEVEL_CODE[level]}-C{counters[(r['gov'], level)]:03d}"
        cname, caddr = random.choice(centers[r["gov"]])
    else:
        group = f"ON-{day}-S1-{LEVEL_CODE[level]}-C{counters[(r['gov'], level)]:03d}"
        cname, caddr = "", ""
    students.append({
        "id": sid, "full_name": f"{f[1]} {fa[1]} {gf[1]} {fam[1]}", "full_name_ar": f"{f[0]} {fa[0]} {gf[0]} {fam[0]}",
        "email": f"{f[1].lower()}.{fam[1].lower()}{sid % 1000}@example.com",
        "phone": random.choice(["010", "011", "012", "015"]) + f"{random.randint(0, 99999999):08d}",
        "gender": "male" if male else "female", "national_id": nid(r["gov"], random.randint(2008, 2014), male),
        "city": r["gov"], "area": r["area"], "address": r["address"],
        "school_language": random.choice(["arabic", "arabic", "english", "french"]),
        "program_type": "Physical" if physical else "Online", "level": level, "group": group,
        "Center Name": cname, "Center Address": caddr,
        "parent_name": f"{fa[0]} {gf[0]} {random.choice(FATHERS)[0]} {fam[0]}",
        "parent_national_id": nid(r["gov"], random.randint(1968, 1990), True),
    })
    key.append({"id": sid, "national_id": students[-1]["national_id"], "full_name_ar": students[-1]["full_name_ar"],
                "scenario": r["scenario"], "gov": r["gov"], "area": r["area"], "address": r["address"],
                "check": "distance" if r["truth"] else "in_gov",
                "expected_lat": round(r["truth"][0], 6) if r["truth"] else None,
                "expected_lon": round(r["truth"][1], 6) if r["truth"] else None,
                "tolerance_km": TOL_KM.get(r["scenario"]) if r["truth"] else None,
                "expected_map": f"https://www.google.com/maps?q={r['truth'][0]:.6f},{r['truth'][1]:.6f}" if r["truth"] else "",
                "trap_gov": r["trap"], "note": r["note"]})

os.makedirs(OUT, exist_ok=True)
path = os.path.join(OUT, "students_geo_test.xlsx")
try:
    open(path, "ab").close()
except PermissionError:                                   # الملف مفتوح في Excel: نحفظ باسم تاني بدل ما نفشل بعد دقيقتين شغل
    path = os.path.join(OUT, f"students_geo_test_{time.strftime('%Y%m%d-%H%M%S')}.xlsx")
with pd.ExcelWriter(path) as w:
    pd.DataFrame(students).astype({"national_id": str, "parent_national_id": str, "phone": str}).to_excel(
        w, index=False, sheet_name="Student DB")
    pd.DataFrame(key).to_excel(w, index=False, sheet_name="Answer Key")
print("students:", len(students), "->", path)
print(pd.DataFrame(key).groupby("scenario").size().to_string())
print("govs:", pd.DataFrame(key)["gov"].nunique())

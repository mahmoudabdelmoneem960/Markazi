"""توليد شيتات تجريبية (بيانات وهمية بالكامل: أرقام قومية عشوائية) بنفس شكل الشيتات الحقيقية.
العناوين وأسماء الشوارع بتتاخد من قاعدة الأماكن عشان تحديد المواقع يشتغل عليها.
الاستخدام: python tools/make_sample.py [gazetteer.sqlite] [out_dir]"""
import os
import random
import sqlite3
import sys

import pandas as pd

GAZ = sys.argv[1] if len(sys.argv) > 1 else os.path.join("data", "gazetteer.sqlite")
OUT = sys.argv[2] if len(sys.argv) > 2 else "sample"
random.seed(7)
GOVS = {"cairo": "القاهرة", "alexandria": "الإسكندرية", "dakahlia": "الدقهلية"}
LEVELS = ["Level (1)"] * 5 + ["Level (2)"] * 4 + ["Level (3): Cyber", "Level (3): Web", "Level (3): Data", "Level (4): Web"]
LEVEL_CODES = {"Level (1)": "L1", "Level (2)": "L2", "Level (3): Cyber": "L3CY", "Level (3): Web": "L3WB", "Level (3): Data": "L3DT", "Level (4): Web": "L4WB"}
DAYS = ["Friday", "Saturday", "Sunday", "Monday"]

import re

AR = re.compile(r"^[ء-ي0-9 ]+$")           # عربي قياسي بس (من غير حروف فارسية/أردو زي ک ی ہ)


def arabic(rows, idx=0):
    return [r for r in rows if AR.match(str(r[idx]).strip()) and len(str(r[idx]).strip()) >= 3]


db = sqlite3.connect(GAZ)
areas, streets, places = {}, {}, {}
for g in GOVS:
    areas[g] = arabic(db.execute("SELECT name, lat, lon FROM places WHERE gov=? AND rank<=3 ORDER BY COALESCE(pop,0) DESC LIMIT 60", (g,)).fetchall())[:12]
    streets[g] = [re.sub(r"^(شارع|ش)\s+", "", r[0]).strip() for r in arabic(db.execute("SELECT DISTINCT name FROM streets WHERE gov=? LIMIT 1500", (g,)).fetchall())][:400]
    places[g] = arabic(db.execute("SELECT name, lat, lon FROM places WHERE gov=? AND rank<=4 LIMIT 600", (g,)).fetchall())[:200]
    if not areas[g]:
        raise SystemExit(f"مفيش أماكن للمحافظة {g} في قاعدة الأماكن")

used = set()


def nid():
    while True:
        x = "3" + "".join(random.choice("0123456789") for _ in range(13))
        if x not in used:
            used.add(x)
            return x


students = []
for g, ar in GOVS.items():
    for _ in range(200):
        area = random.choice(areas[g])[0]
        st = random.choice(streets[g]) if streets[g] else "شارع الجمهورية"
        addr = random.choice([f"{area} - {random.randint(1, 120)} شارع {st}", f"شارع {st} - {area}", f"{area}", f"{st} بجوار {random.choice(places[g])[0]}"])
        students.append({"Student NID": nid(), "gov": ar, "Area": area, "Adress": addr, "Level(25-26)": random.choice(LEVELS)})
random.shuffle(students)
pd.DataFrame(students).to_excel(os.path.join(OUT, "students_sample.xlsx"), index=False)

centers, k = [], 0
codes = {"cairo": "SMP_CAI", "alexandria": "SMP_ALX", "dakahlia": "SMP_DKH"}      # أكواد مميزة عشان ما تتلخبطش مع مراكز حقيقية في قاعدة المواقع
for g in GOVS:
    for i in range(4):
        a = areas[g][i % len(areas[g])]
        code = f"{codes[g]}_C{i + 1:02d}"
        lat, lon = a[1] + random.uniform(-0.01, 0.01), a[2] + random.uniform(-0.01, 0.01)
        name = f"مركز {a[0]} للتدريب {i + 1}"
        addr = f"{a[0]} - شارع {random.choice(streets[g]) if streets[g] else 'الجمهورية'} - الدور {random.randint(1, 4)}"
        for lv in random.sample(list(LEVEL_CODES.values()), k=random.randint(2, 4)):
            k += 1
            centers.append({"Group Code": f"{lv}_{k:04d}", "Center Code": code, "Area": a[0], "City": g, "Day": random.choice(DAYS),
                            "Time": f"{random.choice([10, 12, 14, 16])}:00:00", "Center Name": name, "العنوان التفصيلي للمركز": addr,
                            "سعة القاعة": random.choice([20, 25]), "لوكيشن المكان": f"https://www.google.com/maps?q={lat:.6f},{lon:.6f}",
                            "Latitude": round(lat, 6) if i != 3 else "", "Longitude": round(lon, 6) if i != 3 else ""})
pd.DataFrame(centers).to_csv(os.path.join(OUT, "centers_sample.csv"), index=False, encoding="utf-8-sig")
print("students:", len(students), "| groups:", len(centers), "| centers:", len({c['Center Code'] for c in centers}), "->", OUT)

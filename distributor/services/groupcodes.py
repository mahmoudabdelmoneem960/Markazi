"""أكواد الجروبات من نتيجة التخطيط.

Online  : ON-{Day}-{Slot}-L{n}-{Track}-C{NNN}            مثال ON-A-S1-L2-GEN-C047
Physical: PH-{Day}-{GOV}-V{NN}-L{n}-{Track}-C{NNN}        مثال PH-B-CAI-V01-L2-GEN-C008

- Day: A = الجمعة، B = السبت. جروبات كل ليفيل بتتقسم نص ونص (ولو العدد فردي، الجروب الزيادة بيتبادل بين اليومين
  عشان الإجمالي يفضل 50% / 50%).
- Slot: S1 للكل لحد ما المواعيد تتظبط.
- Track: GEN لليفيل من غير تراك (L1/L2)، وغير كده اختصار التراك (CY/DT/WB/DA/EM) زي ما هو في الداتا.
- Online على مستوى الجمهورية (الجروب ليفيل واحد من أي محافظة). Physical لكل محافظة لوحدها، وكود المحافظة
  3 حروف من أكواد المراكز (CAI, ALX, ...) — محافظات البند الأول والتالت بس (البند التاني مالوش مراكز).
- Venue: V01 لحد ما المراكز تتحدد.
- الترقيم C001.. لكل ليفيل/تراك (Online) أو لكل محافظة + ليفيل/تراك (Physical)، مكمّل بين اليومين.
"""
import io
import re
from collections import Counter

from distributor import db
from distributor.core import engine

DAYS = {"A": ("الجمعة", "Friday"), "B": ("السبت", "Saturday")}
SLOT = "S1"
VENUE = "V01"
UNKNOWN_GOV = "غير معروفة"
UNKNOWN_LEVEL = "غير محدد"
# احتياطي لو جدول المراكز فاضي (نفس البادئات اللي في أكواد المراكز)
GOV3_DEFAULT = {"cairo": "CAI", "suez": "SWS", "ismailia": "ISM", "port_said": "PSD", "beheira": "BHR", "beni_suef": "BNS",
                "minya": "MNY", "south_sinai": "JSN", "north_sinai": "NSN", "alexandria": "ALX", "sharqia": "SHR",
                "dakahlia": "DKH", "gharbia": "GHR", "damietta": "DMT", "kafr_el_sheikh": "KFS", "fayoum": "FYM",
                "sohag": "SOH", "new_valley": "WJD"}


def gov_codes():
    """كود المحافظة (3 حروف) = بادئة أكواد مراكزها الأكثر تكرارًا (ALX_C01 → ALX)."""
    out = dict(GOV3_DEFAULT)
    try:
        con = db.connect()
        try:
            rows = con.execute("SELECT gov, code FROM centers").fetchall()
        finally:
            con.close()
    except Exception:  # noqa
        return out
    by_gov = {}
    for gov, code in rows:
        # الـ3 حروف اللي قبل رقم المركز على طول: ALX_C01 → ALX ، SMP_CAI_C01 → CAI
        m = re.search(r"(?:^|_)([A-Za-z]{3})_C\d+\s*$", str(code or "").strip())
        if gov and m:
            by_gov.setdefault(gov, Counter())[m.group(1).upper()] += 1
    for gov, cnt in by_gov.items():
        out[gov] = cnt.most_common(1)[0][0]
    # كود واحد لمحافظتين = غلط في أكواد المراكز → نرجع للكود الافتراضي
    dup = Counter(out.values())
    for gov, c3 in list(out.items()):
        if dup[c3] > 1 and gov in GOV3_DEFAULT:
            out[gov] = GOV3_DEFAULT[gov]
    return out


def split_level(code):
    """L3CY → ('L3', 'CY') ، L1 → ('L1', 'GEN')."""
    m = re.match(r"L(\d+)([A-Z]*)$", str(code))
    if not m:
        return None, None
    return f"L{m.group(1)}", (m.group(2) or "GEN")


def _sizes(students, groups):
    """توزيع الطلاب على الجروبات بالتساوي (الفرق بين أي جروبين طالب واحد بالكتير)."""
    if not groups:
        return []
    q, r = divmod(int(students), int(groups))
    return [q + 1 if i < r else q for i in range(groups)]


class _Balance:
    """نص الجروبات الجمعة ونصها السبت. لما العدد فردي، الجروب الزيادة بيروح لليوم المتأخر في نفس الليفيل،
    ولو متساويين لليوم المتأخر في الإجمالي — فكل ليفيل والإجمالي يفضلوا بفرق جروب واحد بالكتير تقريبًا."""
    def __init__(self):
        self.total = {"A": 0, "B": 0}
        self.level = {}

    def split(self, lv, g):
        a = b = g // 2
        if g % 2:
            lc = self.level.setdefault(lv, {"A": 0, "B": 0})
            if lc["A"] != lc["B"]:
                extra = "A" if lc["A"] < lc["B"] else "B"
            else:
                extra = "A" if self.total["A"] <= self.total["B"] else "B"
            a, b = (a + 1, b) if extra == "A" else (a, b + 1)
        lc = self.level.setdefault(lv, {"A": 0, "B": 0})
        lc["A"] += a
        lc["B"] += b
        self.total["A"] += a
        self.total["B"] += b
        return a, b


def _level_order(rep):
    return [lv for lv in rep.get("levels_order") or [] if lv != UNKNOWN_LEVEL and split_level(lv)[0]]


def generate(rep, mode, cap):
    """بيرجع {rows, summary, excluded}. كل صف = جروب واحد بكوده وعدد طلابه المتوقع."""
    mode = mode if mode in ("physical", "online") else "physical"
    cap = int(cap or 0)
    if not rep or cap <= 0:
        return {"mode": mode, "cap": cap, "rows": [], "summary": _summary([], mode), "excluded": []}
    rows, excluded = [], []
    bal = _Balance()

    def add(lv, students, gov_code="", gov="", gov3=""):
        groups = -(-students // cap)
        level, track = split_level(lv)
        a, _b = bal.split(lv, groups)
        for i, size in enumerate(_sizes(students, groups)):
            day = "A" if i < a else "B"
            num = f"C{i + 1:03d}"
            code = (f"ON-{day}-{SLOT}-{level}-{track}-{num}" if mode == "online"
                    else f"PH-{day}-{gov3}-{VENUE}-{level}-{track}-{num}")
            rows.append({"code": code, "type": "Online" if mode == "online" else "Physical", "day": day,
                         "day_ar": DAYS[day][0], "day_en": DAYS[day][1], "slot": SLOT, "level": level, "track": track,
                         "level_code": lv, "gov_code": gov_code, "gov": gov, "gov3": gov3,
                         "venue": "" if mode == "online" else VENUE, "class": num, "students": size})

    levels = _level_order(rep)
    if mode == "online":
        for lv in levels:
            students = sum(int(g["levels"].get(lv) or 0) for g in rep["govs"])
            if students:
                add(lv, students)
    else:
        g3 = gov_codes()
        for g in rep["govs"]:
            n_ok = sum(int(g["levels"].get(lv) or 0) for lv in levels)
            if g["gov_code"] == UNKNOWN_GOV:
                if n_ok:
                    excluded.append({"gov": g["gov"], "band": g.get("band", ""), "students": n_ok, "reason": "محافظة غير معروفة"})
                continue
            code3 = g3.get(g["gov_code"])
            if not code3 or g.get("band") == engine.BAND_2:
                if n_ok:
                    excluded.append({"gov": g["gov"], "band": g.get("band", ""), "students": n_ok,
                                     "reason": "البند التاني: مفيش مراكز ولا كود محافظة" if g.get("band") == engine.BAND_2
                                     else "مفيش كود محافظة"})
                continue
            for lv in levels:
                students = int(g["levels"].get(lv) or 0)
                if students:
                    add(lv, students, g["gov_code"], g["gov"], code3)
    return {"mode": mode, "cap": cap, "rows": rows, "summary": _summary(rows, mode, levels), "excluded": excluded}


def _summary(rows, mode, levels=()):
    by_day = {d: {"day": d, "day_ar": DAYS[d][0], "groups": 0, "students": 0} for d in DAYS}
    by_level = {}
    by_gov = {}
    for r in rows:
        by_day[r["day"]]["groups"] += 1
        by_day[r["day"]]["students"] += r["students"]
        lv = by_level.setdefault(r["level_code"], {"level_code": r["level_code"], "level": r["level"], "track": r["track"],
                                                   "A": 0, "B": 0, "students": 0, "groups": 0})
        lv[r["day"]] += 1
        lv["groups"] += 1
        lv["students"] += r["students"]
        if mode == "physical":
            gv = by_gov.setdefault(r["gov3"], {"gov3": r["gov3"], "gov": r["gov"], "A": 0, "B": 0, "students": 0, "groups": 0})
            gv[r["day"]] += 1
            gv["groups"] += 1
            gv["students"] += r["students"]
    order = {lv: i for i, lv in enumerate(levels)}
    return {"groups": len(rows), "students": sum(r["students"] for r in rows), "days": list(by_day.values()),
            "levels": sorted(by_level.values(), key=lambda x: order.get(x["level_code"], 999)),
            "govs": sorted(by_gov.values(), key=lambda x: -x["students"])}


def excel_sheets(res, prefix=""):
    """شيتات الأكواد: [(اسم الشيت, DataFrame)] — prefix بيتحط قبل الأسماء لما تتضاف لتقرير تاني."""
    import pandas as pd
    mode = res["mode"]
    cols = [("code", "Group Code"), ("type", "Type"), ("day", "Day"), ("day_en", "Day Name"), ("day_ar", "اليوم"),
            ("slot", "Slot")]
    if mode == "physical":
        cols += [("gov3", "Gov"), ("gov", "المحافظة"), ("venue", "Venue")]
    cols += [("level", "Level"), ("track", "Track"), ("class", "Class"), ("students", "عدد الطلاب المتوقع")]
    codes = pd.DataFrame([{h: r[k] for k, h in cols} for r in res["rows"]], columns=[h for _, h in cols])
    s = res["summary"]
    summary = pd.DataFrame([("نوع الجروبات", "Online" if mode == "online" else "Physical"), ("سعة الجروب", res["cap"]),
                            ("إجمالي الجروبات", s["groups"]), ("إجمالي الطلاب", s["students"])]
                           + [(f"جروبات {d['day_ar']} ({d['day']})", d["groups"]) for d in s["days"]]
                           + [(f"طلاب {d['day_ar']} ({d['day']})", d["students"]) for d in s["days"]]
                           + [("Slot", SLOT)] + ([("Venue", VENUE)] if mode == "physical" else []),
                           columns=["البند", "القيمة"])
    levels = pd.DataFrame([{"Level": x["level"], "Track": x["track"], "جروبات الجمعة (A)": x["A"], "جروبات السبت (B)": x["B"],
                            "إجمالي الجروبات": x["groups"], "الطلاب": x["students"]} for x in s["levels"]])
    sheets = [(prefix + "ملخص" if prefix else "ملخص", summary), ("أكواد الجروبات", codes),
              (prefix + "الليفيلات × الأيام", levels)]
    if mode == "physical":
        sheets.append((prefix + "المحافظات", pd.DataFrame([{"Gov": x["gov3"], "المحافظة": x["gov"], "جروبات الجمعة (A)": x["A"],
                                                            "جروبات السبت (B)": x["B"], "إجمالي الجروبات": x["groups"],
                                                            "الطلاب": x["students"]} for x in s["govs"]])))
    if res["excluded"]:
        sheets.append((prefix + "مستبعد", pd.DataFrame([{"المحافظة": x["gov"], "البند": x["band"], "الطلاب": x["students"],
                                                         "السبب": x["reason"]} for x in res["excluded"]])))
    return sheets


def format_sheet(xw, name, df):
    ws = xw.sheets[name]
    ws.right_to_left()
    ws.set_column(0, max(0, len(df.columns) - 1), 16)
    if name == "أكواد الجروبات":
        ws.set_column(0, 0, 30)
        ws.freeze_panes(1, 0)


def to_excel(res, title=""):
    import pandas as pd
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="xlsxwriter") as xw:
        for name, df in excel_sheets(res):
            df.to_excel(xw, sheet_name=name, index=False)
            format_sheet(xw, name, df)
    out.seek(0)
    return out

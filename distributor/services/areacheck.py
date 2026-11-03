"""فحص عمود المنطقة في شيت الطلاب (للتخطيط):
- من غير منطقة.
- المنطقة مكتوب فيها شارع/ميدان/طريق بدل اسم منطقة.
- المنطقة تابعة لمحافظة تانية غير محافظة الطالب:
    «أكيد»   = المنطقة هي اسم محافظة تانية بالظبط (المحافظة: الإسكندرية / المنطقة: السويس).
    «مشتبه» = فيها اسم محافظة تانية، أو اسم مدينة معروفة في محافظة تانية بس (الغربية / المنصورة)،
               والاسم مش معروف في محافظة الطالب في قاعدة الأماكن.
القواعد اتظبطت على شيتات حقيقية عشان تقلل الإنذارات الغلط (جسر السويس في القاهرة، منيا القمح في الشرقية،
عين شمس الشرقية، حي ثان...)."""
import re
import sqlite3

from distributor.core import engine, geonorm as gn, locdb
from distributor.services.compare import GOV_AR

NO_AREA, STREET, OTHER_GOV = "no_area", "street", "other_gov"
KIND_AR = {NO_AREA: "بدون منطقة", STREET: "شارع بدل منطقة", OTHER_GOV: "منطقة في محافظة تانية"}

_STREET_RE = re.compile(r"^\d*\s*(شارع|ش|طريق|ميدان|حاره|عطفه|زقاق|ممر|كوبري)(\s|\d|$)")
_STREET_BEFORE = {"شارع", "ش", "طريق", "ميدان", "كوبري", "محور", "جسر", "قناه", "ترعه", "مساكن", "شارع", "حاره"}
# أسماء محافظات بتيجي كصفة (اتجاه) جوّه أسماء مناطق: «عين شمس الشرقية»، «المرج الغربية» ← ما نعتبرهاش غير لو المنطقة هي الاسم بالظبط
_DIRECTIONAL = {"شرقيه", "غربيه"}
# كلمات عامة بتتكرر في كل المحافظات (حي ثان، الحي السادس، المنطقة الخامسة...) ← ما ندوّرش عليها في محافظات تانية
_GENERIC = {"اول", "اولي", "ثان", "ثاني", "ثانيه", "ثالث", "ثالثه", "رابع", "رابعه", "خامس", "خامسه", "سادس", "سادسه",
            "سابع", "سابعه", "ثامن", "ثامنه", "تاسع", "تاسعه", "عاشر", "عاشره", "شرق", "غرب", "وسط", "شمال", "جنوب",
            "بحري", "قبلي", "جديد", "جديده", "قديم", "قديمه", "الحي", "حي", "منطقه", "مدينه", "قسم", "مركز", "بندر"}

_GOV_KEYS = {}
for _n, _c in list(gn.GOV_NAMES.items()) + list(engine.GOV_MAP.items()):
    _k = gn.key(_n, "place")
    if _k:
        _GOV_KEYS[_k] = _c


class _Gaz:
    """استعلامات قاعدة الأماكن مع كاش. لو القاعدة مش موجودة: بنعتمد على أسماء المحافظات بس."""

    def __init__(self):
        p = locdb.find_gazetteer()
        self.db = sqlite3.connect(p, check_same_thread=False) if p else None
        self._known, self._cities = {}, {}

    def known_in(self, gov, k):
        """الاسم معروف في المحافظة دي (مكان أو شارع أو معلم)؟"""
        if not self.db or not k:
            return False
        ck = (gov, k)
        if ck not in self._known:
            self._known[ck] = any(self.db.execute(f"SELECT 1 FROM {t} WHERE gov=? AND key=? LIMIT 1", (gov, k)).fetchone()
                                  for t in ("places", "streets", "pois"))
        return self._known[ck]

    def city_govs(self, k):
        """المحافظات اللي فيها مدينة/بلد (rank ≤ 2) بالاسم ده."""
        if not self.db or not k:
            return set()
        if k not in self._cities:
            self._cities[k] = {r[0] for r in self.db.execute(
                "SELECT DISTINCT gov FROM places WHERE key=? AND rank<=2 AND gov IS NOT NULL", (k,))}
        return self._cities[k]


def _gov_in_text(k, gov, gaz):
    """اسم محافظة تانية جوّه المنطقة؟ بيرجّع (الكود، exact) أو None.
    بيتجاهل «شارع بورسعيد»، والأسماء اللي الاسم ده جزء من مكان معروف في محافظة الطالب («منيا القمح ...» في الشرقية)."""
    if k in _GOV_KEYS and _GOV_KEYS[k] != gov:
        return _GOV_KEYS[k], True
    toks = k.split()
    for gk, gc in _GOV_KEYS.items():
        if gc == gov or gk in _DIRECTIONAL:
            continue
        gt = gk.split()
        L = len(gt)
        for i in range(len(toks) - L + 1):
            if toks[i:i + L] != gt or (i > 0 and toks[i - 1] in _STREET_BEFORE):
                continue
            windows = [" ".join(toks[a:b]) for a in range(max(0, i - 2), i + 1)
                       for b in range(i + L, min(len(toks), i + L + 2) + 1) if b - a > L]
            if any(gaz.known_in(gov, w) for w in windows):
                continue
            return gc, False
    return None


def classify(gov, area, gaz):
    """بيرجّع None لو المنطقة سليمة، أو dict فيه kind/conf/other_gov/reason."""
    if not gov:
        return None                                   # المحافظة نفسها مش معروفة: ليها تنبيه لوحدها
    if not area:
        return {"kind": NO_AREA, "conf": "أكيد", "other_gov": "", "reason": "عمود المنطقة فاضي"}
    n = gn.norm(area)
    if _STREET_RE.match(n):
        return {"kind": STREET, "conf": "أكيد", "other_gov": "", "reason": "المنطقة مكتوب فيها شارع/ميدان/طريق مش اسم منطقة"}
    k = gn.key(area, "place")
    if not k:
        return None
    hit = _gov_in_text(k, gov, gaz)
    if hit:
        other, exact = hit
        if exact:
            return {"kind": OTHER_GOV, "conf": "أكيد", "other_gov": other,
                    "reason": f"المنطقة هي اسم محافظة {GOV_AR.get(other, other)}"}
        if not gaz.known_in(gov, k):
            return {"kind": OTHER_GOV, "conf": "مشتبه", "other_gov": other,
                    "reason": f"المنطقة فيها اسم محافظة {GOV_AR.get(other, other)}"}
        return None
    if set(k.split()) <= _GENERIC or k.isdigit():
        return None
    cg = gaz.city_govs(k)
    if cg and gov not in cg and not gaz.known_in(gov, k):
        other = sorted(cg)[0] if len(cg) == 1 else ""
        names = "، ".join(GOV_AR.get(g, g) for g in sorted(cg))
        return {"kind": OTHER_GOV, "conf": "مشتبه", "other_gov": other,
                "reason": f"«{area}» مدينة معروفة في {names} ومش معروفة في {GOV_AR.get(gov, gov)}"}
    return None


def check(df, S, sc):
    """df: فيه gov (كود) و level. S/sc: الشيت والأعمدة. بيرجّع (ملخص للتقرير، صفوف الطلاب المتعلّمين)."""
    from distributor.services import sheets
    area = S[sc["area"]].map(sheets.clean) if sc["area"] else None
    if area is None:
        return {"available": False}, []
    nid = engine._clean_id(S[sc["nid"]]) if sc["nid"] else None
    addr = S[sc["addr"]].map(sheets.clean) if sc["addr"] else None
    gov_raw = S[sc["gov"]].map(sheets.clean)
    gaz = _Gaz()
    cache, rows = {}, []
    govs = df["gov"].to_numpy()
    for i, (g, a) in enumerate(zip(govs, area)):
        g = None if g == "غير معروفة" else g
        ck = (g, a)
        if ck not in cache:
            cache[ck] = classify(g, a, gaz)
        r = cache[ck]
        if not r:
            continue
        rows.append({"row_no": i + 2, "nid": "" if nid is None else nid.iat[i], "gov": GOV_AR.get(g, g), "gov_code": g,
                     "gov_raw": gov_raw.iat[i], "area": a, "address": "" if addr is None else addr.iat[i],
                     "level": df["level"].iat[i], **r, "other_gov_ar": GOV_AR.get(r["other_gov"], r["other_gov"])})
    # ملخص: العدد لكل نوع + المجموعات (محافظة × منطقة) مرتبة بالعدد
    counts = {NO_AREA: 0, STREET: 0, OTHER_GOV: 0}
    groups = {}
    for r in rows:
        counts[r["kind"]] += 1
        gk = (r["kind"], r["gov_code"], r["area"])
        if gk not in groups:
            groups[gk] = {"kind": r["kind"], "gov": r["gov"], "gov_code": r["gov_code"], "area": r["area"] or "—",
                          "conf": r["conf"], "other_gov": r["other_gov_ar"], "reason": r["reason"], "students": 0}
        groups[gk]["students"] += 1
    by_gov = {}
    for r in rows:
        by_gov.setdefault(r["gov_code"], {NO_AREA: 0, STREET: 0, OTHER_GOV: 0})[r["kind"]] += 1
    summary = {"available": True, "gazetteer": gaz.db is not None, "counts": counts, "total": len(rows),
               "by_gov": by_gov,
               "groups": sorted((g for g in groups.values() if g["kind"] != NO_AREA), key=lambda x: (x["conf"] != "أكيد", -x["students"]))[:500]}
    if gaz.db:
        gaz.db.close()
    return summary, rows

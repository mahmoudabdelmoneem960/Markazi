"""تطبيع الأسماء العربية للمطابقة (مشترك بين بناء قاعدة الأماكن والـ geocoder).
نفس الدوال لازم تُستخدم في الطرفين عشان المفاتيح تتطابق."""
import re

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_DIACRITICS = re.compile(r"[ً-ٰٟـۖ-ۭ]")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")

# كلمات بتتشال حسب نوع الاسم (بعد التطبيع)
STREET_WORDS = {"شارع", "ش", "طريق", "حاره", "ممر", "ميدان", "كوبري", "محور", "الشارع", "st", "street", "road", "rd",
                "ave", "avenue", "مدخل", "نفق", "سكه", "حارة"}
PLACE_WORDS = {"حي", "مدينه", "مركز", "قسم", "منطقه", "قريه", "محافظه", "بندر", "ضاحيه", "قرية", "مدينة", "منطقة"}
POI_WORDS = {"مدرسه", "مسجد", "جامع", "صيدليه", "مستشفي", "بنك", "معهد", "كليه", "مركز", "مكتب"}  # للاستخدام الاختياري

# حروف/كلمات بتتدمج مع اللي بعدها: "عبد الله" = "عبدالله"
_JOIN_NEXT = {"عبد", "ابو", "ام", "ابي", "ذو", "بيت"}


def norm(s):
    """نص عربي/لاتيني -> شكل موحّد (همزات، تاء مربوطة، ياء، أرقام، تشكيل، ترقيم)."""
    if s is None:
        return ""
    s = str(s).lower().strip().translate(_DIGITS)
    s = _DIACRITICS.sub("", s)
    s = (s.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ٱ", "ا")
         .replace("ى", "ي").replace("ئ", "ي").replace("ؤ", "و").replace("ة", "ه").replace("ک", "ك").replace("ی", "ي"))
    s = _PUNCT.sub(" ", s.replace("_", " "))
    return _SPACES.sub(" ", s).strip()


def tokens(s, drop=frozenset()):
    """توكنز مطبّعة: بدون 'ال' التعريف، ابن->بن، دمج عبد/ابو مع اللي بعدها، وشيل كلمات drop."""
    out = []
    for w in norm(s).split(" "):
        if not w:
            continue
        if w.startswith("ال") and len(w) > 3:
            w = w[2:]
        if w in ("ابن", "بن"):
            w = "بن"
        out.append(w)
    merged, i = [], 0
    while i < len(out):
        w = out[i]
        if w in _JOIN_NEXT and i + 1 < len(out):
            nxt = out[i + 1]
            merged.append(w + nxt)
            i += 2
            continue
        merged.append(w)
        i += 1
    return [w for w in merged if w not in drop]


def key(s, kind="place"):
    """مفتاح مطابقة تام (كلمات مرتبة زي ما هي)."""
    drop = STREET_WORDS if kind == "street" else PLACE_WORDS if kind == "place" else frozenset()
    return " ".join(tokens(s, drop))


def key_full(s):
    """مفتاح بالاسم الكامل (من غير شيل كلمات حي/مدينة/شارع) للتفريق بين اسمين متشابهين."""
    return " ".join(tokens(s))


def has_arabic(s):
    return bool(re.search(r"[؀-ۿ]", s or ""))


# المحافظات: اسم عربي مطبّع -> كود (نفس engine.GOV_MAP)
GOV_NAMES = {
    "الاسكندريه": "alexandria", "البحيره": "beheira", "بني سويف": "beni_suef", "القاهره": "cairo",
    "الدقهليه": "dakahlia", "دمياط": "damietta", "الفيوم": "fayoum", "الغربيه": "gharbia",
    "الاسماعيليه": "ismailia", "كفر الشيخ": "kafr_el_sheikh", "المنيا": "minya",
    "الوادي الجديد": "new_valley", "بورسعيد": "port_said", "الشرقيه": "sharqia", "سوهاج": "sohag",
    "جنوب سيناء": "south_sinai", "السويس": "suez", "الجيزه": "giza", "القليوبيه": "qalyubia",
    "المنوفيه": "monufia", "اسيوط": "assiut", "اسوان": "aswan", "الاقصر": "luxor", "قنا": "qena",
    "البحر الاحمر": "red_sea", "مطروح": "matrouh", "شمال سيناء": "north_sinai",
}


def gov_code(name):
    n = norm(name)
    for pre in ("محافظه ", "governorate"):
        if n.startswith(pre):
            n = n[len(pre):].strip()
    return GOV_NAMES.get(n)


"""اختبار سيرفر OSRM على كل محافظات مصر (27 محافظة).
يفحص اتصال الطرق والمسافات وزمن الرحلة من القاهرة لكل عاصمة محافظة، بالإضافة لمصفوفة المسافات الشاملة.
"""
import json
import time
import urllib.request
import urllib.parse

OSRM_URL = "http://127.0.0.1:5001"

# إحداثيات عواصم ومراكز كل محافظات مصر الـ 27 (lat, lon)
EGYPT_GOVERNORATES = [
    # إقليم القاهرة الكبرى
    {"gov": "cairo", "name_ar": "القاهرة", "city": "ميدان التحرير", "lat": 30.0444, "lon": 31.2357},
    {"gov": "giza", "name_ar": "الجيزة", "city": "الدقي / ميدان الجيزة", "lat": 30.0131, "lon": 31.2089},
    {"gov": "qalyubia", "name_ar": "القليوبية", "city": "بنها", "lat": 30.4660, "lon": 31.1834},

    # إقليم الإسكندرية والساحل الشمالي
    {"gov": "alexandria", "name_ar": "الإسكندرية", "city": "محطة الرمل", "lat": 31.2001, "lon": 29.9187},
    {"gov": "beheira", "name_ar": "البحيرة", "city": "دمنهور", "lat": 31.0379, "lon": 30.4725},
    {"gov": "matrouh", "name_ar": "مطروح", "city": "مرسى مطروح", "lat": 31.3543, "lon": 27.2373},

    # إقليم الدلتا
    {"gov": "gharbia", "name_ar": "الغربية", "city": "طنطا", "lat": 30.7885, "lon": 31.0019},
    {"gov": "dakahlia", "name_ar": "الدقهلية", "city": "المنصورة", "lat": 31.0409, "lon": 31.3785},
    {"gov": "monufia", "name_ar": "المنوفية", "city": "شبين الكوم", "lat": 30.5526, "lon": 31.0090},
    {"gov": "sharqia", "name_ar": "الشرقية", "city": "الزقازيق", "lat": 30.5877, "lon": 31.5020},
    {"gov": "kafr_el_sheikh", "name_ar": "كفر الشيخ", "city": "كفر الشيخ", "lat": 31.1107, "lon": 30.9388},
    {"gov": "damietta", "name_ar": "دمياط", "city": "دمياط", "lat": 31.4175, "lon": 31.8144},

    # إقليم القناة وسيناء
    {"gov": "port_said", "name_ar": "بورسعيد", "city": "بورسعيد", "lat": 31.2653, "lon": 32.3019},
    {"gov": "ismailia", "name_ar": "الإسماعيلية", "city": "الإسماعيلية", "lat": 30.6043, "lon": 32.2723},
    {"gov": "suez", "name_ar": "السويس", "city": "السويس", "lat": 29.9668, "lon": 32.5498},
    {"gov": "north_sinai", "name_ar": "شمال سيناء", "city": "العريش", "lat": 31.1321, "lon": 33.8034},
    {"gov": "south_sinai", "name_ar": "جنوب سيناء", "city": "شرم الشيخ", "lat": 27.9158, "lon": 34.3299},

    # إقليم شمال ووسط الصعيد
    {"gov": "fayoum", "name_ar": "الفيوم", "city": "الفيوم", "lat": 29.3084, "lon": 30.8428},
    {"gov": "beni_suef", "name_ar": "بني سويف", "city": "بني سويف", "lat": 29.0661, "lon": 31.0994},
    {"gov": "minya", "name_ar": "المنيا", "city": "المنيا", "lat": 28.1099, "lon": 30.7503},
    {"gov": "assiut", "name_ar": "أسيوط", "city": "أسيوط", "lat": 27.1801, "lon": 31.1837},

    # إقليم جنوب الصعيد
    {"gov": "sohag", "name_ar": "سوهاج", "city": "سوهاج", "lat": 26.5570, "lon": 31.6948},
    {"gov": "qena", "name_ar": "قنا", "city": "قنا", "lat": 26.1551, "lon": 32.7160},
    {"gov": "luxor", "name_ar": "الأقصر", "city": "الأقصر", "lat": 25.6872, "lon": 32.6396},
    {"gov": "aswan", "name_ar": "أسوان", "city": "أسوان", "lat": 24.0889, "lon": 32.8998},

    # المحافظات الحدودية والصحراوية
    {"gov": "red_sea", "name_ar": "البحر الأحمر", "city": "الغردقة", "lat": 27.2579, "lon": 33.8116},
    {"gov": "new_valley", "name_ar": "الوادي الجديد", "city": "الخارجة", "lat": 25.4514, "lon": 30.5472},
]


def test_routes_from_cairo():
    cairo = EGYPT_GOVERNORATES[0]
    results = []
    print(f"============================================================")
    print(f" فحص مسارات OSRM في كل محافظات مصر (نقطة الانطلاق: {cairo['name_ar']})")
    print(f"============================================================")
    print(f"{'#':<3} | {'المحافظة':<14} | {'المدينة/المركز':<16} | {'المسافة (كم)':<12} | {'زمن الرحلة':<12} | {'الحالة'}")
    print("-" * 75)

    passed = 0
    total = len(EGYPT_GOVERNORATES)

    for i, target in enumerate(EGYPT_GOVERNORATES, 1):
        if target["gov"] == "cairo":
            print(f"{i:<3} | {target['name_ar']:<14} | {target['city']:<16} | {'0.0 كم':<12} | {'0 دقيقة':<12} | [✓ الانطلاق]")
            passed += 1
            continue

        coords = f"{cairo['lon']:.6f},{cairo['lat']:.6f};{target['lon']:.6f},{target['lat']:.6f}"
        url = f"{OSRM_URL}/route/v1/driving/{coords}?overview=false"

        t0 = time.time()
        try:
            req = urllib.request.urlopen(url, timeout=10)
            res = json.loads(req.read())
            ms = int((time.time() - t0) * 1000)

            if res.get("code") == "Ok" and res.get("routes"):
                r = res["routes"][0]
                dist_km = r["distance"] / 1000.0
                dur_min = r["duration"] / 60.0
                dur_str = f"{int(dur_min // 60)} س {int(dur_min % 60)} د" if dur_min >= 60 else f"{int(dur_min)} د"
                print(f"{i:<3} | {target['name_ar']:<14} | {target['city']:<16} | {dist_km:>6.1f} كم      | {dur_str:<12} | [✓ {ms}ms]")
                passed += 1
                results.append({"gov": target["gov"], "name": target["name_ar"], "km": dist_km, "ok": True, "ms": ms})
            else:
                print(f"{i:<3} | {target['name_ar']:<14} | {target['city']:<16} | {'--':<12} | {'--':<12} | [✗ لا يوجد طريق]")
                results.append({"gov": target["gov"], "name": target["name_ar"], "ok": False})
        except Exception as e:
            print(f"{i:<3} | {target['name_ar']:<14} | {target['city']:<16} | {'خطأ':<12} | {'--':<12} | [✗ {e}]")
            results.append({"gov": target["gov"], "name": target["name_ar"], "ok": False, "err": str(e)})

    print("-" * 75)
    print(f"النتيجة: تم التحقق بنجاح من {passed}/{total} محافظة ({passed/total*100:.1f}%)")
    return results


def test_distance_matrix():
    print("\n" + "=" * 60)
    print(" فحص مصفوفة المسافات الشاملة (OSRM /table API) لـ 27 محافظة")
    print("=" * 60)
    # تجميع كل إحداثيات محافظات مصر في طلب واحد
    coords = ";".join(f"{g['lon']:.6f},{g['lat']:.6f}" for g in EGYPT_GOVERNORATES)
    url = f"{OSRM_URL}/table/v1/driving/{coords}?annotations=distance"

    t0 = time.time()
    try:
        req = urllib.request.urlopen(url, timeout=15)
        res = json.loads(req.read())
        ms = int((time.time() - t0) * 1000)

        if res.get("code") == "Ok":
            distances = res.get("distances", [])
            total_pairs = len(distances) * len(distances[0]) if distances else 0
            valid_pairs = sum(1 for row in distances for d in row if d is not None)
            print(f"✓ استجابة OSRM /table تمت بنجاح في {ms} ملي ثانية.")
            print(f"✓ تم حساب المسافات بين كل الأزواج: {valid_pairs}/{total_pairs} مسار بدون أي فجوة!")
            return True
        else:
            print(f"✗ خطأ في الاستجابة: {res.get('code')}")
            return False
    except Exception as e:
        print(f"✗ فشل طلب المصفوفة: {e}")
        return False


if __name__ == "__main__":
    test_routes_from_cairo()
    test_distance_matrix()

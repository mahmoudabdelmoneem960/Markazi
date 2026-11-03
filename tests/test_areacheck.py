"""فحص عمود المنطقة في التخطيط: بدون منطقة / شارع بدل منطقة / منطقة في محافظة تانية."""
import io

import pandas as pd

from distributor.services import areacheck as A
from tests.conftest import HAS_GAZ


class _NoGaz:
    db = None

    def known_in(self, gov, k):
        return False

    def city_govs(self, k):
        return set()


def test_classify_without_gazetteer():
    g = _NoGaz()
    assert A.classify("cairo", "", g)["kind"] == A.NO_AREA
    assert A.classify("cairo", "شارع العشرين", g)["kind"] == A.STREET
    assert A.classify("cairo", "69شارع حسن", g)["kind"] == A.STREET
    r = A.classify("alexandria", "السويس", g)
    assert r["kind"] == A.OTHER_GOV and r["conf"] == "أكيد" and r["other_gov"] == "suez"
    assert A.classify("dakahlia", "بورسعيد", g)["other_gov"] == "port_said"
    # اتجاهات مش محافظات، والأسماء العامة، واسم المحافظة نفسها
    assert A.classify("cairo", "عين شمس الشرقية", g) is None
    assert A.classify("cairo", "حي ثان", g) is None
    assert A.classify("alexandria", "اسكندرية", g) is None
    assert A.classify("dakahlia", "المطرية دقهليه. شارع بورسعيد", g) is None   # «شارع بورسعيد» اسم شارع
    assert A.classify(None, "السويس", g) is None                               # المحافظة نفسها مش معروفة


def test_classify_with_gazetteer():
    if not HAS_GAZ:
        return
    g = A._Gaz()
    assert A.classify("cairo", "جسر السويس", g) is None               # حي في القاهرة
    assert A.classify("sharqia", "منيا القمح", g) is None             # مدينة في الشرقية
    assert A.classify("sharqia", "قرية بنى قريش مركز منيا القمح محافظة الشرقية", g) is None
    r = A.classify("gharbia", "المنصورة", g)
    assert r and r["kind"] == A.OTHER_GOV and r["other_gov"] == "dakahlia"
    r = A.classify("cairo", "شبرا الخيمة", g)
    assert r and r["other_gov"] == "qalyubia"


def _sheet():
    rows = [("29801010100011", "القاهرة", "مدينة نصر", "ش عباس العقاد", "Level (1)"),
            ("29801010100022", "القاهرة", "", "شارع التحرير", "Level (1)"),
            ("29801010100033", "القاهرة", "شارع العشرين", "عمارة 5", "Level (2)"),
            ("29801010100044", "الإسكندرية", "السويس", "سيدي جابر", "Level (3): Web"),
            ("29801010100055", "الإسكندرية", "سموحة", "شارع فوزي معاذ", "Level (1)")]
    df = pd.DataFrame(rows, columns=["Student NID", "gov", "Area", "Adress", "Level(25-26)"])
    b = io.BytesIO()
    df.to_excel(b, index=False)
    return b.getvalue()


def test_planning_flags_api(client):
    r = client.post("/api/planning/analyze", data={"students": (io.BytesIO(_sheet()), "flags.xlsx")},
                    content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    j = r.get_json()
    pid, ac = j["id"], j["report"]["area_check"]
    assert "_flags" not in j["report"]
    assert ac["available"] and ac["counts"] == {"no_area": 1, "street": 1, "other_gov": 1}
    assert ac["by_gov"]["cairo"] == {"no_area": 1, "street": 1, "other_gov": 0}

    d = client.get(f"/api/planning/{pid}/flags?kind=no_area").get_json()
    assert d["total"] == 1 and d["rows"][0]["row_no"] == 3 and d["rows"][0]["nid"] == "29801010100022"
    d = client.get(f"/api/planning/{pid}/flags?kind=other_gov&gov=alexandria").get_json()
    assert d["total"] == 1 and d["rows"][0]["other_gov_ar"] == "السويس" and d["rows"][0]["row_no"] == 5
    d = client.get(f"/api/planning/{pid}/flags?kind=street&area=" + "شارع العشرين").get_json()
    assert d["total"] == 1
    d = client.get(f"/api/planning/{pid}/flags?q=29801010100044").get_json()
    assert d["total"] == 1
    assert client.get(f"/api/planning/{pid}/flags").get_json()["total"] == 3

    r = client.get(f"/api/planning/{pid}/flags.xlsx")
    assert r.status_code == 200 and r.data[:2] == b"PK"
    x = pd.read_excel(io.BytesIO(r.data), sheet_name=None, dtype=str)
    assert len(x["كل المشاكل"]) == 3 and x["كل المشاكل"]["الرقم القومي"].str.len().eq(14).all()

    # التحليل لسه خفيف في السجل (الصفوف مش جوّه التقرير)
    h = client.get("/api/planning/history").get_json()
    assert any(p["id"] == pid for p in h)
    assert client.delete(f"/api/planning/{pid}").status_code == 200
    assert client.get(f"/api/planning/{pid}/flags").status_code == 404

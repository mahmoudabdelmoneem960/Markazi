"""مراجعة مواقع المراكز: لينك الشيت مقابل العنوان، المعاينة، الاعتماد، والتصدير."""
import io
import time

import pandas as pd

from distributor.core import locdb
from distributor.services import centers as cs
from tests.conftest import HAS_GAZ


def test_center_address_column_is_read():
    C = pd.DataFrame({"Center Code": ["X1"], "City": ["cairo"], " Area": ["وسط البلد"],
                      "Center Address": ["شارع الفلكي 28، عابدين، القاهرة"], "Location": ["https://maps.app.goo.gl/abc"]})
    rec = locdb.center_records(C)["X1"]
    assert rec["address"].startswith("شارع الفلكي") and rec["area"] == "وسط البلد" and rec["url"].startswith("https://")


def test_review_state_rules():
    base = {"gmaps_url": "https://x", "link_lat": 30.0, "link_lon": 31.0, "addr_lat": 30.0, "addr_lon": 31.0,
            "addr_level": "street", "link_tried": 1, "review": None}
    assert cs.review_state(base)["status"] == "ok"
    assert cs.review_state({**base, "addr_lat": 30.05})["status"] == "far"                       # ~5.5 كم > 1.5 كم
    assert cs.review_state({**base, "addr_lat": 30.03, "addr_level": "area"})["status"] == "ok"  # ~3.3 كم < 5 كم
    assert cs.review_state({**base, "addr_lat": None, "addr_level": "gov"})["status"] == "vague"
    assert cs.review_state({**base, "link_lat": None})["status"] == "link_unreadable"
    assert cs.review_state({**base, "link_lat": None, "link_tried": None})["status"] == "pending"
    assert cs.review_state({**base, "gmaps_url": ""})["status"] == "no_link"
    assert cs.review_state({**base, "addr_lat": 30.5, "review": "confirmed"})["status"] == "confirmed"


def _wait(client, jid, timeout=120):
    t = time.time()
    while time.time() - t < timeout:
        j = client.get("/api/jobs/" + jid).get_json()
        if j["state"] in ("done", "error", "cancelled"):
            return j
        time.sleep(0.5)
    raise AssertionError("job timeout")


def _upload(client, rows):
    df = pd.DataFrame(rows, columns=["Group Code", "City", "Center Name", "Center Code", "Area", "Center Address", "Location"])
    b = io.BytesIO()
    df.to_excel(b, index=False)
    r = client.post("/api/centers/import", data={"file": (io.BytesIO(b.getvalue()), "c.xlsx")}, content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def test_review_flow(client):
    # لينك فيه الإحداثيات نفسها (من غير نت): TRV_1 صح في وسط البلد، TRV_2 لينكه في الإسكندرية وعنوانه في القاهرة
    _upload(client, [
        ("L1_9001", "cairo", "مدرسة السينما", "TRV_1", "وسط البلد", "شارع الفلكي 28، عابدين، القاهرة",
         "https://www.google.com/maps?q=30.0444,31.2400"),
        ("L1_9002", "cairo", "مركز تجربة", "TRV_2", "وسط البلد", "شارع الفلكي 28، عابدين، القاهرة",
         "https://www.google.com/maps?q=31.2001,29.9187"),
        ("L1_9003", "cairo", "مركز من غير لينك", "TRV_3", "وسط البلد", "شارع الفلكي، عابدين", ""),
    ])
    r = client.post("/api/centers/review_locations")
    assert r.status_code == 200, r.get_json()
    j = _wait(client, r.get_json()["id"])
    assert j["state"] == "done", j
    by = {c["code"]: c for c in client.get("/api/centers").get_json()}
    assert by["TRV_3"]["status"] == "no_link"
    assert by["TRV_1"]["lat"] is not None and by["TRV_1"]["source"] == "gmaps_link"   # مركز من غير موقع خد موقع اللينك
    if HAS_GAZ:
        assert by["TRV_2"]["status"] == "far" and by["TRV_2"]["addr_gap_km"] > 100
        assert by["TRV_1"]["status"] in ("ok", "vague")

    # معاينة لينك صح (من غير حفظ) ثم اعتماد
    p = client.post("/api/centers/TRV_2/preview", json={"input": "30.04452, 31.24021"}).get_json()
    assert abs(p["lat"] - 30.04452) < 1e-6 and p["from_link_km"] > 100
    assert client.get("/api/centers").get_json() and \
        next(c for c in client.get("/api/centers").get_json() if c["code"] == "TRV_2")["review"] is None
    assert client.post("/api/centers/TRV_2/preview", json={"input": "كلام"}).status_code == 400
    r = client.post("/api/centers/TRV_2/confirm", json={"lat": p["lat"], "lon": p["lon"], "url": "https://maps.app.goo.gl/good",
                                                         "how": "لينك جديد من Google Maps"})
    assert r.status_code == 200, r.get_json()
    c = next(c for c in client.get("/api/centers").get_json() if c["code"] == "TRV_2")
    assert c["status"] == "confirmed" and c["source"] == "manual" and c["verified"] == 1
    assert c["correct_url"] == "https://maps.app.goo.gl/good" and abs(c["lat"] - 30.04452) < 1e-6
    assert client.post("/api/centers/TRV_2/confirm", json={"lat": 48.8, "lon": 2.3}).status_code == 400   # برّه مصر

    # رفع الشيت تاني بنفس اللينك الغلط: الموقع المعتمد ما يتغيرش
    _upload(client, [("L1_9002", "cairo", "مركز تجربة", "TRV_2", "وسط البلد", "شارع الفلكي 28، عابدين، القاهرة",
                      "https://www.google.com/maps?q=31.2001,29.9187")])
    c = next(c for c in client.get("/api/centers").get_json() if c["code"] == "TRV_2")
    assert c["status"] == "confirmed" and abs(c["lat"] - 30.04452) < 1e-6

    # لينك جديد في الشيت: إحداثيات اللينك القديم بتتمسح
    _upload(client, [("L1_9001", "cairo", "مدرسة السينما", "TRV_1", "وسط البلد", "شارع الفلكي 28، عابدين، القاهرة",
                      "https://www.google.com/maps?q=30.0500,31.2500")])
    c = next(c for c in client.get("/api/centers").get_json() if c["code"] == "TRV_1")
    assert c["link_lat"] is None and c["status"] == "pending"

    # التصدير: نفس أسماء أعمدة شيت المراكز + اللينك الصح
    r = client.get("/api/centers/master.xlsx")
    assert r.status_code == 200 and r.data[:2] == b"PK"
    x = pd.read_excel(io.BytesIO(r.data), dtype=str)
    assert {"Center Code", "Center Name", "City", "Area", "Center Address", "Location", "Latitude", "Longitude"} <= set(x.columns)
    assert x.loc[x["Center Code"] == "TRV_2", "Location"].iloc[0] == "https://maps.app.goo.gl/good"

    # رجوع للمراجعة
    assert client.post("/api/centers/TRV_2/reopen").status_code == 200
    c = next(c for c in client.get("/api/centers").get_json() if c["code"] == "TRV_2")
    assert c["status"] != "confirmed" and c["source"] == "manual"
    assert client.post("/api/centers/NOPE/reopen").status_code == 404

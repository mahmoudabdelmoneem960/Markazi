"""مصادر الجروبات: شيت الطلاب بس (المراكز من القاعدة) / الطلاب + المراكز / + شيت جروبات."""
import io
import time

import pandas as pd
import pytest

from conftest import HAS_GAZ
from distributor.core import engine, locdb
from distributor.services import groupsheet, sheets


def _xlsx(df):
    b = io.BytesIO()
    df.to_excel(b, index=False)
    return b.getvalue()


def _seed_db(sample_files):
    C = sheets.parse_file("centers_sample.csv", sample_files[1])
    con = locdb.connect(str(__import__("distributor.config", fromlist=["x"]).DB_PATH))
    try:
        locdb.centers_upsert(C, con=con)
    finally:
        con.close()
    return C


def test_group_key_new_codes():
    assert engine.group_key("L3WB_0002") == "L3WB"
    assert engine.group_key("PH-B-CAI-V03-L2-GEN-C008") == "L2"
    assert engine.group_key("ON-A-S1-L4-DT-C010") == "L4DT"
    assert engine.group_key("PH-A-CAI-V01-L3-CY-C001") == "L3CY"
    assert engine.group_key("XX-1") is None


def test_normalize_aliases():
    G = pd.DataFrame({"Group Code": ["L1_0001", "PH-B-ALX-V01-L2-GEN-C001", ""], "Center Code": ["A", "B", "C"],
                      "Slot 1 Day": ["Friday", "", ""], "Slot 1 Start": ["14:00:00", "09:00", ""], "Max Capacity": ["23", "", ""]})
    g, info = groupsheet.normalize_groups(G, default_cap=30)
    assert len(g) == 2 and list(g["Day"]) == ["Friday", "Saturday"]           # B من الكود = السبت
    assert list(g["Time"]) == ["14:00:00", "09:00"] and list(g["سعة القاعة"]) == [23, 30]
    with pytest.raises(ValueError):
        groupsheet.normalize_groups(pd.DataFrame({"Group Code": ["L1_0001"]}))
    # شيت مراكز فيه Slot 1 Day: بيتنسخ لـ Day عشان يظهر في النتيجة
    C = groupsheet.with_day_time(pd.DataFrame({"Group Code": ["L1_1"], "Slot 1 Day": ["الجمعة"], "Slot 1 Start": ["10:00"]}))
    assert C["Day"][0] == "Friday" and C["Time"][0] == "10:00"


def test_upload_sources(client, sample_files, monkeypatch):
    S, Cr = sample_files
    C = _seed_db(sample_files)
    st = (io.BytesIO(S), "students_sample.xlsx")
    # 1) الطلاب بس + شيت جروبات → المراكز من القاعدة
    G = pd.DataFrame({"Group Code": ["PH-A-CAI-V01-L1-GEN-C001", "PH-B-CAI-V01-L1-GEN-C002", "L2_9", "L1_77"],
                      "Center Code": ["SMP_CAI_C01", "SMP_CAI_C01", "SMP_CAI_C01", "NOPE_C99"],
                      "Time": ["10:00", "12:00", "14:00", "09:00"]})
    r = client.post("/api/runs/upload", data={"students": st, "source": "db", "default_cap": "20",
                                              "groups": (io.BytesIO(_xlsx(G)), "groups.xlsx")}, content_type="multipart/form-data")
    pf = r.get_json()
    assert r.status_code == 200, pf
    assert pf["centers"]["rows"] == 3 and pf["centers"]["centers"] == 1 and pf["centers"]["seats"] == 60
    assert pf["source"]["centers"] == "db" and "NOPE_C99" in " ".join(i["text"] for i in pf["issues"])
    assert pf["files"]["centers"] == "قاعدة المراكز + groups.xlsx"
    # 2) الطلاب بس من غير جروبات → جروبات آخر تشغيل
    monkeypatch.setattr(groupsheet, "latest_groups", lambda: (groupsheet.normalize_groups(C)[0], "جروبات آخر تشغيل: x (2026-01-01)"))
    r = client.post("/api/runs/upload", data={"students": (io.BytesIO(S), "s.xlsx"), "source": "db"}, content_type="multipart/form-data")
    pf = r.get_json()
    assert r.status_code == 200 and pf["centers"]["rows"] == len(C) and pf["centers"]["centers"] == 12
    monkeypatch.setattr(groupsheet, "latest_groups", lambda: (None, None))
    r = client.post("/api/runs/upload", data={"students": (io.BytesIO(S), "s.xlsx"), "source": "db"}, content_type="multipart/form-data")
    assert r.status_code == 400 and "شيت الجروبات" in r.get_json()["error"]
    # 3) الطلاب + المراكز + جروبات: بيانات المراكز من الشيت
    r = client.post("/api/runs/upload", data={"students": (io.BytesIO(S), "s.xlsx"), "centers": (io.BytesIO(Cr), "centers_sample.csv"),
                                              "groups": (io.BytesIO(_xlsx(G)), "g.xlsx")}, content_type="multipart/form-data")
    assert r.status_code == 200 and r.get_json()["centers"]["rows"] == 3
    # من غير مراكز في وضع الشيت = خطأ
    r = client.post("/api/runs/upload", data={"students": (io.BytesIO(S), "s.xlsx"), "source": "sheet"}, content_type="multipart/form-data")
    assert r.status_code == 400
    assert client.get("/api/runs/sources").get_json()["db_centers"] >= 12
    t = client.get("/api/runs/groups_template.xlsx")
    assert t.status_code == 200 and t.data[:2] == b"PK"


@pytest.mark.skipif(not HAS_GAZ, reason="قاعدة الأماكن مش موجودة")
def test_run_with_groups_sheet(client, sample_files):
    _seed_db(sample_files)
    S = sample_files[0]
    G = pd.DataFrame({"Group Code": [f"PH-{'AB'[i % 2]}-CAI-V01-L1-GEN-C{i + 1:03d}" for i in range(4)],
                      "Center Code": ["SMP_CAI_C01"] * 4, "Time": ["10:00"] * 4, "سعة القاعة": [10] * 4})
    r = client.post("/api/runs/upload", data={"students": (io.BytesIO(S), "s.xlsx"), "source": "db",
                                              "groups": (io.BytesIO(_xlsx(G)), "g.xlsx")}, content_type="multipart/form-data")
    pf = r.get_json()
    r = client.post("/api/runs", json={"token": pf["token"], "name": "g", "params": {"geo_mode": "addr", "road_mode": "factor"}})
    jid, run_id = r.get_json()["job_id"], r.get_json()["run_id"]
    for _ in range(600):
        j = client.get(f"/api/jobs/{jid}").get_json()
        if j["state"] in ("done", "error", "cancelled"):
            break
        time.sleep(0.2)
    assert j["state"] == "done", j.get("error")
    s = client.get(f"/api/runs/{run_id}/result").get_json()["summary"]
    assert s["assigned"] == 40
    found = client.get(f"/api/runs/search_student?q=PH-A-CAI-V01-L1-GEN-C001&run={run_id}").get_json()
    assert found["total"] == 10 and all(x.get("day") == "Friday" for x in found["rows"])

"""إعداد الاختبارات: تطبيق بفولدر بيانات مؤقت + قاعدة الأماكن (لو موجودة) + أدمن مسجّل دخول."""
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
ROOT = BACKEND_DIR.parent if BACKEND_DIR.name == "backend" else BACKEND_DIR
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="sd_test_")
os.environ["SD_DATA_DIR"] = _tmp
os.environ["SD_OPEN_BROWSER"] = "0"
_gaz = ROOT / "data" / "gazetteer.sqlite"
if _gaz.exists():
    os.environ.setdefault("GAZETTEER_DB", str(_gaz))

from distributor import config  # noqa: E402

HAS_GAZ = bool(os.environ.get("GAZETTEER_DB")) and Path(os.environ.get("GAZETTEER_DB", "")).exists()
SAMPLE = ROOT / "sample"


@pytest.fixture(scope="session")
def app():
    from distributor import create_app
    a = create_app()
    a.config["TESTING"] = True
    yield a
    shutil.rmtree(_tmp, ignore_errors=True)


@pytest.fixture(scope="session")
def client(app):
    c = app.test_client()
    r = c.post("/setup", data={"username": "admin", "password": "Test12345", "password2": "Test12345"})
    assert r.status_code in (200, 302)
    return c


@pytest.fixture(scope="session")
def sample_files():
    return (SAMPLE / "students_sample.xlsx").read_bytes(), (SAMPLE / "centers_sample.csv").read_bytes()

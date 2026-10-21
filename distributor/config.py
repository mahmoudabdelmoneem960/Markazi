"""إعدادات السيستم: كل المسارات والقيم الافتراضية في مكان واحد (قابلة للتغيير بمتغيرات البيئة أو ملف .env)."""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# .env (اختياري) جنب المشروع: سطر لكل متغير KEY=VALUE
_env = BASE_DIR / ".env"
if _env.exists():
    for line in _env.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

VERSION = "4.0.0"
DATA_DIR = Path(os.environ.get("SD_DATA_DIR", BASE_DIR / "data"))
RUNS_DIR = DATA_DIR / "runs"
TMP_DIR = DATA_DIR / "tmp"
DB_PATH = Path(os.environ.get("LOCATIONS_DB", DATA_DIR / "app.db"))          # قاعدة واحدة: مواقع + تشغيلات + مستخدمين
GAZETTEER_CANDIDATES = [os.environ.get("GAZETTEER_DB", ""), str(DATA_DIR / "gazetteer.sqlite"),
                        str(Path.home() / "locations" / "data" / "gazetteer.sqlite")]
OSRM_URL = os.environ.get("OSRM_URL", "http://127.0.0.1:5001")
OSRM_CACHE = Path(os.environ.get("OSRM_CACHE", DATA_DIR / "osrm_cache.pkl"))
GOOGLE_KEY_FILE = DATA_DIR / "google_key.txt"
HOST = os.environ.get("SD_HOST", "127.0.0.1")
PORT = int(os.environ.get("SD_PORT", "5000"))
MAX_UPLOAD_MB = int(os.environ.get("SD_MAX_UPLOAD_MB", "300"))
KEEP_RUNS = int(os.environ.get("SD_KEEP_RUNS", "0"))          # 0 = ما نمسحش تشغيلات قديمة تلقائيًا
OPEN_BROWSER = os.environ.get("SD_OPEN_BROWSER", "1") != "0"

# القيم الافتراضية لشاشة التشغيل (المستخدم يقدر يغيّرها من الإعدادات وبتتحفظ في الـDB)
DEFAULTS = {"max_km": "50", "detour": "1.3", "road_mode": "osrm", "geo_mode": "addr", "osrm_url": OSRM_URL}


def ensure_dirs():
    for d in (DATA_DIR, RUNS_DIR, TMP_DIR):
        d.mkdir(parents=True, exist_ok=True)


def secret_key():
    """مفتاح جلسات Flask: بيتولّد مرة واحدة ويتحفظ في data/secret.key (مش في الكود ولا في Git)."""
    p = DATA_DIR / "secret.key"
    ensure_dirs()
    if not p.exists():
        p.write_text(secrets.token_hex(32), encoding="utf-8")
    return p.read_text(encoding="utf-8").strip()

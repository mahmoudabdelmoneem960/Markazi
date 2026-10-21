"""قاعدة بيانات التطبيق (نفس ملف SQLite بتاع المواقع): مستخدمين، تشغيلات، مهام، إعدادات.
جداول المواقع (centers, geo_cache, overrides...) بتتعمل في core/locdb.py، وهنا بنضيف جداول التطبيق عليها."""
import json
import sqlite3
import time

from distributor import config
from distributor.core import locdb

APP_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, pw_hash TEXT NOT NULL, created_at TEXT);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, created_at TEXT, name TEXT, params TEXT, students_file TEXT, centers_file TEXT,
  n_students INTEGER, n_centers INTEGER, status TEXT, summary TEXT, error TEXT, finished_at TEXT,
  elapsed REAL, result_elapsed REAL, job_id TEXT);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, kind TEXT, run_id TEXT, state TEXT, progress INTEGER DEFAULT 0, message TEXT, error TEXT,
  result TEXT, created_at TEXT, started_at TEXT, finished_at TEXT);
CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, user TEXT, action TEXT, target TEXT, detail TEXT);
"""


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def connect():
    """اتصال جاهز: جداول المواقع + جداول التطبيق. اقفله بعد الاستخدام (أو استخدم `with closing(...)`)."""
    config.ensure_dirs()
    con = locdb.connect(str(config.DB_PATH))
    with locdb._lock:
        con.executescript(APP_SCHEMA)
        con.commit()
    return con


# ---------------- إعدادات ----------------
def get_settings(con):
    s = dict(config.DEFAULTS)
    for r in con.execute("SELECT k, v FROM settings"):
        s[r["k"]] = r["v"]
    return s


def set_settings(con, values):
    with locdb._lock:
        for k, v in values.items():
            if k in config.DEFAULTS:
                con.execute("INSERT OR REPLACE INTO settings(k, v) VALUES (?, ?)", (k, str(v)))
        con.commit()


# ---------------- سجل التعديلات ----------------
def audit(con, user, action, target="", detail=None):
    with locdb._lock:
        con.execute("INSERT INTO audit(at, user, action, target, detail) VALUES (?,?,?,?,?)",
                    (now(), user or "", action, target, json.dumps(detail, ensure_ascii=False) if detail is not None else None))
        con.commit()


# ---------------- التشغيلات ----------------
def run_insert(con, run):
    with locdb._lock:
        con.execute("INSERT INTO runs(id, created_at, name, params, students_file, centers_file, status, job_id) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (run["id"], now(), run.get("name", ""), json.dumps(run["params"], ensure_ascii=False),
                     run.get("students_file", ""), run.get("centers_file", ""), "queued", run.get("job_id")))
        con.commit()


def run_update(con, run_id, **fields):
    if not fields:
        return
    cols, vals = [], []
    for k, v in fields.items():
        cols.append(f"{k}=?")
        vals.append(json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
    vals.append(run_id)
    with locdb._lock:
        con.execute(f"UPDATE runs SET {', '.join(cols)} WHERE id=?", vals)
        con.commit()


def _run_row(r):
    if r is None:
        return None
    d = dict(r)
    for k in ("params", "summary"):
        try:
            d[k] = json.loads(d[k]) if d.get(k) else ({} if k == "params" else None)
        except Exception:  # noqa
            d[k] = None
    return d


def run_get(con, run_id):
    return _run_row(con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())


def runs_list(con, limit=200):
    return [_run_row(r) for r in con.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,))]


def run_delete(con, run_id):
    with locdb._lock:
        con.execute("DELETE FROM runs WHERE id=?", (run_id,))
        con.commit()


# ---------------- المهام ----------------
def job_insert(con, job):
    with locdb._lock:
        con.execute("INSERT INTO jobs(id, kind, run_id, state, progress, message, created_at, started_at) VALUES (?,?,?,?,?,?,?,?)",
                    (job["id"], job["kind"], job.get("run_id"), job["state"], 0, job.get("message", ""), now(), now()))
        con.commit()


def job_finish(con, job):
    with locdb._lock:
        con.execute("UPDATE jobs SET state=?, progress=?, message=?, error=?, result=?, finished_at=? WHERE id=?",
                    (job["state"], job.get("progress", 0), job.get("message", ""), job.get("error"),
                     json.dumps(job.get("result"), ensure_ascii=False, default=str) if job.get("result") is not None else None,
                     now(), job["id"]))
        con.commit()


def job_get(con, job_id):
    r = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if r is None:
        return None
    d = dict(r)
    try:
        d["result"] = json.loads(d["result"]) if d.get("result") else None
    except Exception:  # noqa
        d["result"] = None
    return d


def jobs_mark_stale(con):
    """بعد إعادة تشغيل السيستم: أي مهمة كانت شغالة ما خلصتش."""
    with locdb._lock:
        con.execute("UPDATE jobs SET state='error', error='السيستم اتقفل أثناء التشغيل', finished_at=? WHERE state IN ('running','queued')", (now(),))
        con.execute("UPDATE runs SET status='error', error='السيستم اتقفل أثناء التوزيع' WHERE status IN ('running','queued')")
        con.commit()

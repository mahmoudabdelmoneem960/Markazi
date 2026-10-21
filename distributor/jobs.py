"""مهام الخلفية (التوزيع، فحص اللينكات، فحص جوجل...): بتشتغل في thread، والتقدم في الذاكرة، والنتيجة النهائية في الـDB."""
import threading
import time
import traceback
import uuid

from distributor import db
from distributor.core import engine

_live = {}
_lock = threading.Lock()


class JobCancelled(engine.JobCancelled):
    pass


def new_job(kind, run_id=None):
    job = {"id": uuid.uuid4().hex[:10], "kind": kind, "run_id": run_id, "state": "running", "progress": 0,
           "message": "بدء", "started": time.time(), "cancel": False}
    con = db.connect()
    try:
        db.job_insert(con, job)
    finally:
        con.close()
    with _lock:
        _live[job["id"]] = job
    return job


def submit(kind, fn, run_id=None):
    """fn(job) بتشتغل في الخلفية وبتكتب job['result'] / job['error']؛ الحالة النهائية بتتحفظ في الـDB."""
    job = new_job(kind, run_id)

    def _run():
        try:
            fn(job)
            if job["state"] == "running":
                job["state"], job["progress"], job["message"] = "done", 100, "تم"
        except engine.JobCancelled:
            job["state"], job["message"] = "cancelled", job.get("message_cancel", "اتلغى")
        except Exception as e:  # noqa
            traceback.print_exc()
            job["state"], job["error"] = "error", str(e)
        finally:
            job["finished"] = time.time()
            try:
                con = db.connect()
                try:
                    db.job_finish(con, job)
                finally:
                    con.close()
            except Exception:  # noqa
                traceback.print_exc()
    threading.Thread(target=_run, daemon=True).start()
    return job


def get(job_id):
    """حالة المهمة (من الذاكرة لو شغالة، وإلا من الـDB)."""
    with _lock:
        job = _live.get(job_id)
    if job is not None:
        end = job.get("finished") or time.time()
        return {"id": job["id"], "kind": job["kind"], "run_id": job.get("run_id"), "state": job["state"],
                "progress": job.get("progress", 0), "message": job.get("message", ""), "error": job.get("error"),
                "result": job.get("result"), "files": job.get("files"), "elapsed": round(end - job["started"], 1),
                "result_elapsed": round(job["result_at"] - job["started"], 1) if job.get("result_at") else None}
    con = db.connect()
    try:
        row = db.job_get(con, job_id)
    finally:
        con.close()
    if row is None:
        return None
    return {"id": row["id"], "kind": row["kind"], "run_id": row["run_id"], "state": row["state"], "progress": row["progress"],
            "message": row["message"], "error": row["error"], "result": row["result"], "files": "ready" if row["state"] == "done" else None,
            "elapsed": None, "result_elapsed": None}


def cancel(job_id):
    with _lock:
        job = _live.get(job_id)
    if job is None:
        return False
    job["cancel"] = True
    return True


def progress_fn(job, lo=0, hi=100):
    """دالة تقدم للمحرك: بتحدّث الحالة وبترمي JobCancelled لو المستخدم لغى."""
    def progress(pct, msg):
        if job.get("cancel"):
            raise engine.JobCancelled()
        job["progress"] = max(job.get("progress", 0), int(lo + (hi - lo) * pct / 100))
        job["message"] = msg
    return progress


def startup_cleanup():
    con = db.connect()
    try:
        db.jobs_mark_stale(con)
    finally:
        con.close()

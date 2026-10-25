"""واجهة JSON: التشغيلات والمهام والمراكز والمراجعة والمقارنة والإعدادات."""
import json
import os

from flask import Blueprint, Response, jsonify, request, send_file
from flask_login import current_user

from distributor import config, db, jobs
from distributor.core import engine, locdb
from distributor.services import centers, compare, exports, runs, sheets, whatif

bp = Blueprint("api", __name__, url_prefix="/api")


def _user():
    return getattr(current_user, "username", "")


def _json():
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        return None
    return d


# ---------------- حالة عامة ----------------
@bp.route("/status")
def status():
    con = db.connect()
    try:
        loc = locdb.status(con)
        s = db.get_settings(con)
    finally:
        con.close()
    return jsonify(version=config.VERSION, locations=loc, settings=s)


@bp.route("/osrm_check")
def osrm_check():
    ok, msg, ms = engine.osrm_check(request.args.get("url", ""))
    return jsonify(ok=ok, message=msg, ms=ms)


@bp.route("/settings", methods=["POST"])
def settings_save():
    d = _json() or {}
    con = db.connect()
    try:
        db.set_settings(con, d)
        db.audit(con, _user(), "settings", "", d)
        return jsonify(ok=True, settings=db.get_settings(con))
    finally:
        con.close()


# ---------------- المهام ----------------
@bp.route("/jobs/<jid>")
def job_status(jid):
    j = jobs.get(jid)
    if j is None:
        return jsonify(error="job not found"), 404
    return jsonify(j)


@bp.route("/jobs/<jid>/cancel", methods=["POST"])
def job_cancel(jid):
    if not jobs.cancel(jid):
        return jsonify(error="job not found"), 404
    return jsonify(ok=True)


# ---------------- التشغيلات ----------------
@bp.route("/runs")
def runs_list():
    return jsonify(runs.list_runs())


@bp.route("/runs/upload", methods=["POST"])
def runs_upload():
    """الخطوة 1: رفع الشيتين → مسودة + فحص مسبق."""
    sf, cf = request.files.get("students"), request.files.get("centers")
    if not sf or not cf:
        return jsonify(error="ارفع شيت الطلاب وشيت المراكز."), 400
    try:
        token = runs.save_draft(sf.filename, sf.read(), cf.filename, cf.read())
        rep = runs.draft_preflight(token)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except Exception as e:  # noqa
        return jsonify(error=f"تعذر قراءة الملفات: {e}"), 400
    return jsonify(rep)


@bp.route("/runs/sample", methods=["POST"])
def runs_sample():
    """تحميل عينة جاهزة بضغطة زر للتجربة السريعة."""
    sample_dir = config.BASE_DIR / "sample"
    s_path = sample_dir / "students_sample.xlsx"
    c_path = sample_dir / "centers_sample.csv"
    if not s_path.exists() or not c_path.exists():
        return jsonify(error="ملفات العينة غير موجودة في مجلد sample"), 404
    try:
        token = runs.save_draft(s_path.name, s_path.read_bytes(), c_path.name, c_path.read_bytes())
        rep = runs.draft_preflight(token)
        rep["is_sample"] = True
    except Exception as e:  # noqa
        return jsonify(error=f"تعذر تجهيز ملفات العينة: {e}"), 400
    return jsonify(rep)


@bp.route("/runs", methods=["POST"])
def runs_create():
    """الخطوة 2: بدء التوزيع على المسودة."""
    d = _json()
    if not d or not d.get("token"):
        return jsonify(error="الطلب ناقص"), 400
    params = d.get("params") or {}
    if params.get("road_mode") == "osrm":
        ok, msg, _ = engine.osrm_check(params.get("osrm_url") or config.OSRM_URL)
        if not ok:
            return jsonify(error=msg), 400
    try:
        run_id, jid = runs.create_run(d["token"], params, name=d.get("name", ""), user=_user())
    except (ValueError, FileNotFoundError, StopIteration):
        return jsonify(error="المسودة انتهت أو اتمسحت: ارفع الملفات تاني."), 400
    return jsonify(run_id=run_id, job_id=jid)


@bp.route("/runs/<run_id>")
def run_get(run_id):
    run = runs.get_run(run_id)
    if not run:
        return jsonify(error="not found"), 404
    j = jobs.get(run["job_id"]) if run.get("job_id") else None
    return jsonify(run=run, job=j)


@bp.route("/runs/<run_id>/result")
def run_result(run_id):
    res = runs.load_result(run_id)
    if res is None:
        return jsonify(error="مفيش نتيجة لسه"), 404
    return jsonify(res)


@bp.route("/runs/<run_id>", methods=["DELETE"])
def run_delete(run_id):
    runs.delete_run(run_id, user=_user())
    return jsonify(ok=True)


@bp.route("/runs/<run_id>/file/<key>")
def run_file(run_id, key):
    if key == "per_center":
        try:
            p = exports.per_center_xlsx(run_id)
        except ValueError as e:
            return jsonify(error=str(e)), 404
    else:
        p = runs.file_path(run_id, key)
    if p is None:
        return jsonify(error="الملف مش جاهز"), 404
    return send_file(str(p), as_attachment=True, download_name=f"{run_id}_{p.name}")


@bp.route("/runs/<run_id>/centers")
def run_centers(run_id):
    return jsonify(runs.centers_geo(run_id))


@bp.route("/runs/<run_id>/students")
def run_students(run_id):
    a = request.args
    return jsonify(runs.students_points(run_id, center=a.get("center"), gov=a.get("gov"), status=a.get("status"),
                                        level=a.get("level"), limit=int(a.get("limit") or 6000)))


@bp.route("/runs/<run_id>/whatif", methods=["POST"])
def run_whatif(run_id):
    d = _json() or {}
    try:
        return jsonify(whatif.simulate(run_id, d.get("center"), d.get("level"), int(d.get("capacity") or 0), int(d.get("groups") or 1)))
    except (ValueError, TypeError) as e:
        return jsonify(error=str(e)), 400


@bp.route("/compare")
def compare_runs():
    a, b = request.args.get("a", ""), request.args.get("b", "")
    try:
        return jsonify(compare.compare(a, b))
    except ValueError as e:
        return jsonify(error=str(e)), 400


@bp.route("/compare.xlsx")
def compare_xlsx():
    a, b = request.args.get("a", ""), request.args.get("b", "")
    try:
        data = compare.compare_xlsx(a, b)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    return Response(data, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename=compare_{a}_{b}.xlsx"})


# ---------------- المراكز ----------------
@bp.route("/centers")
def centers_list():
    return jsonify(centers.list_centers())


@bp.route("/centers/<code>", methods=["POST"])
def center_set(code):
    d = _json() or {}
    try:
        return jsonify(centers.set_center(code, d.get("input", ""), note=d.get("note"), user=_user()))
    except (ValueError, KeyError) as e:
        return jsonify(error=str(e) if not isinstance(e, KeyError) else "المركز مش موجود"), 400


@bp.route("/centers/import", methods=["POST"])
def centers_import():
    f = request.files.get("file")
    if not f:
        return jsonify(error="اختار ملف"), 400
    try:
        return jsonify(ok=True, **centers.import_sheet(sheets.parse_file(f.filename, f.read()), user=_user()))
    except ValueError as e:
        return jsonify(error=str(e)), 400


@bp.route("/centers/verify_links", methods=["POST"])
def centers_verify_links():
    try:
        job = centers.start_verify_links()
    except ValueError as e:
        return jsonify(error=str(e)), 400
    return jsonify(id=job["id"])


@bp.route("/centers.csv")
def centers_csv():
    return Response(centers.centers_csv(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=centers_locations.csv"})


@bp.route("/google/status")
def google_status():
    return jsonify(centers.google_status())


@bp.route("/google/verify", methods=["POST"])
def google_verify():
    d = request.get_json()                     # 415 لو مش JSON
    if not isinstance(d, dict):
        return jsonify(error="الطلب لازم يكون JSON"), 400
    scope = "all" if d.get("scope") == "all" else "unverified"
    if d.get("dry_run"):
        _, info = centers.google_plan(scope)
        return jsonify(info)
    if d.get("run") is not True:
        return jsonify(error="لازم run=true لبدء الفحص الفعلي"), 400
    try:
        job, info = centers.start_google(scope, user=_user())
    except ValueError as e:
        return jsonify(error=str(e)), 400
    return jsonify(id=job["id"], **info)


# ---------------- مراجعة مواقع الطلاب ----------------
def _latest_review_run():
    best, best_t = None, 0
    if not config.RUNS_DIR.exists():
        return None
    for p in config.RUNS_DIR.iterdir():
        f = p / runs.FILES["review"]
        if f.exists() and f.stat().st_mtime > best_t:
            best, best_t = p.name, f.stat().st_mtime
    return best


@bp.route("/review")
def review():
    run_id = request.args.get("run") or _latest_review_run()
    if not run_id or not run_id.isalnum():
        return jsonify(run=None, items=[], total_low=0, empty_low=0, groups_total=0)
    p = runs.run_dir(run_id) / runs.FILES["review"]
    if not p.exists():
        return jsonify(run=run_id, items=[], total_low=0, empty_low=0, groups_total=0)
    rev = json.loads(p.read_text(encoding="utf-8"))
    q = (request.args.get("q") or "").strip()
    items = rev["items"]
    if q:
        items = [x for x in items if q in x["area"] or q in x["text"] or q in x["sample"] or q in x["gov"]]
    min_n = int(request.args.get("min_n") or 1)
    items = [x for x in items if x["n"] >= min_n]
    out = items[:int(request.args.get("limit") or 200)]
    con = db.connect()
    try:
        done = locdb.group_overrides_get(con, [x["gkey"] for x in out])
    finally:
        con.close()
    for x in out:
        x["fixed"] = x["gkey"] in done
        if x["fixed"]:
            x["fixed_lat"], x["fixed_lon"] = done[x["gkey"]]
    return jsonify(run=run_id, total_low=rev["total_low"], empty_low=rev["empty_low"], groups_total=rev["groups_total"],
                   shown=len(out), matching=len(items), items=out)


@bp.route("/review/group", methods=["POST"])
def review_group_save():
    d = _json() or {}
    con = db.connect()
    try:
        lat, lon, note = centers.parse_input(d.get("input", ""))
        locdb.group_override_set(con, d["gkey"], d.get("gov", ""), d.get("area", ""), d.get("text", ""), lat, lon, d.get("note") or note)
        db.audit(con, _user(), "group_override", d["gkey"], {"lat": lat, "lon": lon})
        return jsonify(ok=True, lat=lat, lon=lon)
    except (ValueError, KeyError) as e:
        return jsonify(error=str(e)), 400
    finally:
        con.close()


@bp.route("/review/group/delete", methods=["POST"])
def review_group_delete():
    d = _json() or {}
    con = db.connect()
    try:
        locdb.group_override_delete(con, d.get("gkey", ""))
        db.audit(con, _user(), "group_override_delete", d.get("gkey", ""))
        return jsonify(ok=True)
    finally:
        con.close()


@bp.route("/review/student", methods=["POST"])
def review_student_save():
    d = _json() or {}
    nid = str(d.get("nid", "")).strip()
    if not (nid.isdigit() and len(nid) == 14):
        return jsonify(error="الرقم القومي لازم يكون 14 رقم."), 400
    con = db.connect()
    try:
        lat, lon, note = centers.parse_input(d.get("input", ""))
        locdb.override_set(con, lat, lon, nid=nid, note=d.get("note") or note)
        db.audit(con, _user(), "student_override", nid[:6] + "********", {"lat": lat, "lon": lon})
        return jsonify(ok=True, lat=lat, lon=lon)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    finally:
        con.close()

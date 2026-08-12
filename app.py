"""
MHRA Archive — local search UI over the catalog + change monitor.

Run under the text-verifier venv (for PDF serving you only need stdlib+flask,
but keeping one interpreter is simplest):
    /Users/gmac/dev/text-verifier/venv/bin/python app.py
then open http://127.0.0.1:5060
"""
import hashlib
import os

from flask import (Flask, request, jsonify, render_template, send_file, abort,
                   Response, redirect, make_response, stream_with_context)

import db
import download
import report
import r2_store

app = Flask(__name__)
PUBLIC_PREFIX = os.environ.get("PUBLIC_PREFIX", "").rstrip("/")

# --- optional password gate -------------------------------------------------
# Set MHRA_PW to require a passcode (used on the internet-facing VPS deploy).
# Unset (local use) -> wide open. No username, single shared passcode; the cookie
# stores only a salted hash of the passcode, never the passcode itself.
GATE_PW = os.environ.get("MHRA_PW", "")
GATE_TOKEN = hashlib.sha256(("mhra-gate|" + GATE_PW).encode()).hexdigest() if GATE_PW else ""
_LOGIN_HTML = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>MHRA Archive</title>
<style>body{background:#0f1115;color:#e6e9ef;font-family:system-ui,Arial;display:flex;
min-height:100vh;align-items:center;justify-content:center;margin:0}.card{background:#171a21;
border:1px solid #2a2f3a;border-radius:14px;padding:30px 28px;width:300px;text-align:center}
.t{font-weight:700;font-size:20px}.s{color:#8b93a3;font-size:12px;margin:6px 0 18px}
input{width:100%;box-sizing:border-box;padding:11px;border-radius:8px;border:1px solid #2a2f3a;
background:#0f1115;color:#e6e9ef;font-size:15px}button{margin-top:12px;width:100%;padding:11px;
border-radius:8px;border:1px solid #2f4d7a;background:#16243b;color:#4f8cff;font-weight:700;
font-size:14px;cursor:pointer}.e{color:#e85d5d;font-size:12px;margin-top:10px;min-height:14px}</style>
</head><body><form class="card" method="POST" action="/pwlogin">
<div class="t">MHRA Archive</div><div class="s">Enter the passcode to continue</div>
<input type="password" name="pw" autofocus placeholder="Passcode">
<button type="submit">View</button><div class="e">%ERR%</div></form></body></html>"""


def _login(err=""):
    return Response(_LOGIN_HTML.replace("%ERR%", err), mimetype="text/html")


@app.before_request
def _gate():
    if not GATE_PW or request.path == "/pwlogin":
        return None
    if request.cookies.get("mhra_gate") == GATE_TOKEN:
        return None
    return _login()


@app.route("/pwlogin", methods=["POST"])
def pwlogin():
    if request.form.get("pw", "") == GATE_PW and GATE_PW:
        resp = make_response(redirect("/"))
        resp.set_cookie("mhra_gate", GATE_TOKEN, max_age=60 * 60 * 24 * 30,
                        httponly=True, samesite="Lax")
        return resp
    return _login(err="Incorrect passcode")


@app.route("/")
def index():
    return render_template("index.html", public_prefix=PUBLIC_PREFIX)


@app.route("/api/counts")
def api_counts():
    con = db.connect()
    c = db.counts(con)
    mirrored = max(con.execute("SELECT COUNT(*) FROM files").fetchone()[0],
                   r2_store.archived_count())
    snap = con.execute(
        "SELECT run_at,total,n_new,n_removed,n_changed FROM snapshots ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return jsonify({
        "counts": c,
        "classes": db.class_counts(con),
        "total": sum(c.values()),
        "mirrored": mirrored,
        "last_run": dict(snap) if snap else None,
    })


def _search_args():
    """Parse the shared search filters from the query string."""
    q = request.args.get("q", "").strip() or None
    doc_type = request.args.get("type", "").strip() or None
    pl_class = request.args.get("cls", "").strip().upper() or None
    if pl_class not in (None, "UK", "PI"):
        pl_class = None
    company = request.args.get("company", "").strip() or None
    in_text = request.args.get("intext") == "1"
    return q, doc_type, pl_class, company, in_text


@app.route("/api/search")
def api_search():
    con = db.connect()
    q, doc_type, pl_class, company, in_text = _search_args()
    try:
        page = max(0, int(request.args.get("page", "0")))
    except ValueError:
        page = 0
    try:
        size = int(request.args.get("size", "50"))
    except ValueError:
        size = 50
    size = min(max(size, 10), 500)
    matched = db.search(con, q=q, doc_type=doc_type, pl_class=pl_class,
                        company=company, in_text=in_text, limit=1000000, offset=0)
    grouped = {}
    for row in matched:
        key = (row.get("pl_number") or "", row.get("name_key") or "",
               row.get("company_no") or "", row.get("pl_class") or "")
        group = grouped.get(key)
        if group is None:
            group = dict(row)
            group["documents"] = {}
            grouped[key] = group
        dtype = (row.get("doc_type") or "").lower()
        current = group["documents"].get(dtype)
        if dtype in ("spc", "pil", "par") and (
                not current or (row.get("created") or "") > (current.get("created") or "")):
            group["documents"][dtype] = {
                "storage_name": row.get("storage_name"),
                "created": row.get("created"),
                "local": False,
            }
        if (row.get("created") or "") > (group.get("created") or ""):
            group["created"] = row.get("created")
            group["storage_name"] = row.get("storage_name")
    rows_all = sorted(grouped.values(), key=lambda r: (r.get("created") or ""), reverse=True)
    total = len(rows_all)
    pages = max(1, (total + size - 1) // size)
    page = min(page, pages - 1)
    rows = rows_all[page * size:(page + 1) * size]
    have = {r["storage_name"] for r in
            con.execute("SELECT storage_name FROM files").fetchall()}
    for r in rows:
        for document in r["documents"].values():
            document["local"] = document["storage_name"] in have
    company_name = None
    if company:
        row = con.execute("SELECT name FROM mah WHERE company_no=?", (company,)).fetchone()
        company_name = row["name"] if row else None
    return jsonify({"rows": rows, "page": page, "size": size, "total": total,
                    "pages": pages, "company_name": company_name})


@app.route("/export.csv")
def export_csv():
    """Export every row matching the current filters (not just one page) as CSV."""
    import csv
    import io
    con = db.connect()
    q, doc_type, pl_class, company, in_text = _search_args()
    rows = db.search(con, q=q, doc_type=doc_type, pl_class=pl_class, company=company,
                     in_text=in_text, limit=1000000, offset=0)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Product", "Active substance", "PL number", "Class", "MAH / holder",
                "Company no", "Doc type", "Territory", "Updated", "First authorised"])
    for r in rows:
        w.writerow([
            r.get("product_name", ""),
            r.get("substance", ""),
            db.format_pl(r.get("pl_number", "")),
            r.get("pl_class", ""),
            r.get("mah", ""),
            r.get("company_no", ""),
            (r.get("doc_type") or "").upper(),
            r.get("territory", ""),
            (r.get("created") or "")[:10],
            r.get("grant_date", ""),
        ])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition":
                             'attachment; filename="mhra_export.csv"'})


@app.route("/api/related")
def api_related():
    """PI products linked to a UK reference doc (by name, then substance)."""
    con = db.connect()
    storage = request.args.get("storage", "").strip()
    if not storage.isalnum():
        abort(400)
    rel = db.related_pis(con, storage)
    have = {r["storage_name"] for r in
            con.execute("SELECT storage_name FROM files").fetchall()}
    for grp in rel.values():
        for r in grp:
            r["local"] = r["storage_name"] in have
    return jsonify(rel)


@app.route("/report/changes.html")
def report_changes():
    con = db.connect()
    since = request.args.get("since") or None
    until = request.args.get("until") or None
    last_run = request.args.get("last_run") == "1"
    html_doc, rows = report.generate(con, since=since, until=until, last_run=last_run)
    dl = request.args.get("download") == "1"
    headers = {}
    if dl:
        headers["Content-Disposition"] = 'attachment; filename="mhra_change_report.html"'
    return Response(html_doc, mimetype="text/html", headers=headers)


@app.route("/api/changes")
def api_changes():
    con = db.connect()
    kind = request.args.get("kind", "").strip() or None
    sql = ("SELECT run_at,kind,doc_type,product_name,pl_number,old_storage,"
           "new_storage,diff_pdf,detail FROM changes ")
    params = []
    if kind:
        sql += "WHERE kind=? "
        params.append(kind)
    sql += "ORDER BY id DESC LIMIT 300"
    rows = [dict(r) for r in con.execute(sql, params).fetchall()]
    for r in rows:
        r["has_diff"] = bool(r.get("diff_pdf") and os.path.exists(r["diff_pdf"]))
    return jsonify({"rows": rows})


@app.route("/pdf/<storage_name>")
def pdf(storage_name):
    """Serve local/R2 archived copies first; use the live MHRA blob as fallback."""
    if not storage_name.isalnum():
        abort(400)
    local = download._path_for(storage_name)
    if os.path.exists(local):
        return send_file(local, mimetype="application/pdf")
    con = db.connect()
    row = con.execute("SELECT url FROM docs WHERE storage_name=?",
                      (storage_name,)).fetchone()
    if not row:
        abort(404)
    if r2_store.is_archived(storage_name):
        try:
            requested_range = request.headers.get("Range")
            obj = r2_store.open_object(storage_name, requested_range)
            body = obj["Body"]

            def chunks():
                try:
                    while True:
                        chunk = body.read(64 * 1024)
                        if not chunk:
                            break
                        yield chunk
                finally:
                    body.close()

            headers = {
                "Content-Length": str(obj["ContentLength"]),
                "Accept-Ranges": "bytes",
                "Cache-Control": "private, max-age=3600",
            }
            if obj.get("ContentRange"):
                headers["Content-Range"] = obj["ContentRange"]
            return Response(stream_with_context(chunks()),
                            status=206 if requested_range else 200,
                            mimetype="application/pdf", headers=headers)
        except Exception:  # R2 outage should not hide a still-live MHRA document.
            app.logger.exception("R2 read failed for %s; using MHRA source", storage_name)
    import urllib.request
    req = urllib.request.Request(row["url"], headers={"User-Agent": "mhra-archive/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    return Response(data, mimetype="application/pdf")


@app.route("/diff/<path:name>")
def diff(name):
    p = os.path.join(os.path.dirname(__file__), "diffs", os.path.basename(name))
    if not os.path.exists(p):
        abort(404)
    return send_file(p, mimetype="application/pdf")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5060"))
    host = os.environ.get("HOST", "127.0.0.1")
    gate = "passcode ON" if GATE_PW else "open (no passcode)"
    print(f"\n  MHRA Archive on http://{host}:{port}  [{gate}]\n")
    app.run(host=host, port=port, debug=False)

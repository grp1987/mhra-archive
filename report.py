"""
Change report — a standalone HTML summary of new / updated / removed documents
recorded by the monitor, over a date window. Downloadable from the UI or runnable
on its own:

    python3 report.py --since 2026-06-01 --out report.html
    python3 report.py --last-run            # just the most recent monitor run
"""
import argparse
import datetime
import html
import os

import db


def gather(con, since=None, until=None, last_run=False, kinds=("new", "changed", "removed")):
    params = []
    sql = ("SELECT run_at,kind,doc_type,product_name,pl_number,old_storage,"
           "new_storage,diff_pdf,detail FROM changes WHERE 1=1 ")
    if last_run:
        row = con.execute("SELECT run_at FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
        if row:
            sql += "AND run_at=? "
            params.append(row["run_at"])
    else:
        if since:
            sql += "AND run_at>=? "
            params.append(since)
        if until:
            sql += "AND run_at<=? "
            params.append(until)
    ph = ",".join("?" * len(kinds))
    sql += f"AND kind IN ({ph}) ORDER BY kind, product_name"
    params += list(kinds)
    return [dict(r) for r in con.execute(sql, params).fetchall()]


def build_html(con, rows, title, window):
    by = {"new": [], "changed": [], "removed": []}
    for r in rows:
        by.setdefault(r["kind"], []).append(r)

    def esc(s):
        return html.escape(str(s or ""))

    def section(kind, label, color):
        items = by.get(kind, [])
        if not items:
            return f"<h2 style='color:{color}'>{label} <span class='n'>0</span></h2>"
        out = [f"<h2 style='color:{color}'>{label} <span class='n'>{len(items)}</span></h2>",
               "<table><thead><tr><th>Product</th><th>PL number</th><th>Type</th>"
               "<th>Detail</th><th>Links</th></tr></thead><tbody>"]
        for r in items:
            links = []
            if r.get("diff_pdf") and os.path.exists(r["diff_pdf"]):
                links.append(f"<a href='/diff/{esc(os.path.basename(r['diff_pdf']))}'>redline</a>")
            if r.get("new_storage"):
                links.append(f"<a href='/pdf/{esc(r['new_storage'])}'>new PDF</a>")
            if r.get("old_storage"):
                links.append(f"<a href='/pdf/{esc(r['old_storage'])}'>previous</a>")
            out.append(
                f"<tr><td>{esc(r['product_name'])}</td><td class='pl'>{esc(r['pl_number'])}</td>"
                f"<td>{esc((r['doc_type'] or '').upper())}</td><td>{esc(r['detail'])}</td>"
                f"<td>{' · '.join(links)}</td></tr>")
        out.append("</tbody></table>")
        return "\n".join(out)

    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>{esc(title)}</title>
<style>
 body{{font:14px system-ui,sans-serif;max-width:1100px;margin:24px auto;padding:0 18px;color:#1a1a1a}}
 h1{{font-size:22px;margin-bottom:2px}} .win{{color:#666;margin-bottom:20px}}
 h2{{font-size:17px;margin-top:28px;border-bottom:2px solid #eee;padding-bottom:5px}}
 .n{{background:#eee;color:#444;border-radius:12px;padding:1px 9px;font-size:13px;font-weight:600}}
 table{{width:100%;border-collapse:collapse;margin-top:8px}}
 th{{text-align:left;color:#888;font-weight:600;padding:6px 8px;border-bottom:1px solid #ddd;font-size:12px}}
 td{{padding:7px 8px;border-bottom:1px solid #f0f0f0;vertical-align:top}}
 .pl{{font-family:ui-monospace,monospace;font-size:12px;white-space:nowrap}}
 a{{color:#1763d6;text-decoration:none}} a:hover{{text-decoration:underline}}
 .summary{{background:#f7f8fa;border:1px solid #e6e8ec;border-radius:8px;padding:12px 16px}}
</style></head><body>
<h1>{esc(title)}</h1>
<div class="win">{esc(window)} · generated {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC</div>
<div class="summary"><b>{len(by.get('new',[]))}</b> new &nbsp;·&nbsp;
 <b>{len(by.get('changed',[]))}</b> updated &nbsp;·&nbsp;
 <b>{len(by.get('removed',[]))}</b> removed</div>
{section('changed','Updated documents','#b8860b')}
{section('new','New documents','#2e8b57')}
{section('removed','Removed documents','#c0392b')}
</body></html>"""


def generate(con, since=None, until=None, last_run=False):
    rows = gather(con, since=since, until=until, last_run=last_run)
    if last_run:
        snap = con.execute("SELECT run_at FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
        window = f"Most recent check: {snap['run_at'][:16].replace('T',' ')}" if snap else "No runs yet"
    else:
        window = f"{since or 'beginning'} → {until or 'now'}"
    return build_html(con, rows, "MHRA Change Report", window), rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--last-run", action="store_true")
    ap.add_argument("--out", default="report.html")
    a = ap.parse_args()
    con = db.connect()
    html_doc, rows = generate(con, since=a.since, until=a.until, last_run=a.last_run)
    with open(a.out, "w") as f:
        f.write(html_doc)
    print(f"wrote {a.out} ({len(rows)} changes)")

"""
Resumable PDF mirror. Downloads each doc's blob to store/<aa>/<hash>.pdf where
<aa> is the first two hex chars (keeps directories small). Idempotent: a hash
already in `files` with a present file on disk is skipped, so re-running resumes.

Usage:
    python3 download.py [--types Spc,Pil] [--limit N] [--workers 8]
"""
import argparse
import datetime
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

import db

STORE = os.path.join(os.path.dirname(__file__), "store")


def _path_for(storage_name):
    sub = storage_name[:2]
    return os.path.join(STORE, sub, storage_name + ".pdf")


def _fetch(url, dest, timeout=90, retries=3):
    tmp = dest + ".part"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "mhra-archive/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, "wb") as f:
                while True:
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
            os.replace(tmp, dest)
            return os.path.getsize(dest)
        except Exception:  # noqa: BLE001
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            if attempt == retries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))


def pending(con, types):
    """Docs of the wanted types that aren't recorded as downloaded yet."""
    ph = ",".join("?" * len(types))
    rows = con.execute(
        f"""SELECT d.storage_name, d.url FROM docs d
            LEFT JOIN files f ON f.storage_name=d.storage_name
            WHERE d.doc_type IN ({ph}) AND f.storage_name IS NULL""",
        types,
    ).fetchall()
    return [(r["storage_name"], r["url"]) for r in rows]


def run(types, limit=None, workers=8):
    con = db.connect()
    db.init(con)
    todo = pending(con, types)
    if limit:
        todo = todo[:limit]
    total = len(todo)
    print(f"{total} files to download (types={types}, workers={workers})")
    if not total:
        return

    done = 0
    bytes_total = 0
    t0 = time.time()

    def work(item):
        name, url = item
        dest = _path_for(name)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.exists(dest):
            return name, dest, os.path.getsize(dest), None
        try:
            n = _fetch(url, dest)
            return name, dest, n, None
        except Exception as e:  # noqa: BLE001
            return name, dest, 0, str(e)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(work, it) for it in todo]
        batch = []
        for fut in as_completed(futs):
            name, dest, n, err = fut.result()
            done += 1
            if err:
                print(f"  ! {name[:12]} {err}", file=sys.stderr)
                continue
            bytes_total += n
            batch.append((name, dest, n, datetime.datetime.utcnow().isoformat() + "Z"))
            if len(batch) >= 200:
                con.executemany(
                    "INSERT OR REPLACE INTO files(storage_name,path,bytes,downloaded) VALUES(?,?,?,?)",
                    batch,
                )
                con.commit()
                batch.clear()
            if done % 500 == 0:
                rate = done / (time.time() - t0)
                eta = (total - done) / rate / 60 if rate else 0
                print(f"  {done}/{total}  {bytes_total/1e9:.2f} GB  {rate:.0f}/s  ETA {eta:.0f} min")
        if batch:
            con.executemany(
                "INSERT OR REPLACE INTO files(storage_name,path,bytes,downloaded) VALUES(?,?,?,?)",
                batch,
            )
            con.commit()
    print(f"done: {done} files, {bytes_total/1e9:.2f} GB in {(time.time()-t0)/60:.1f} min")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--types", default="Spc,Pil")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    run([t.strip() for t in args.types.split(",") if t.strip()], args.limit, args.workers)

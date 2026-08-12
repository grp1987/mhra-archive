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
import tempfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

import db
import r2_store

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


def pending(con, types, storage="local", retry_unavailable=False):
    """Docs of the wanted types that aren't recorded as downloaded yet."""
    ph = ",".join("?" * len(types))
    if storage == "r2":
        archived = r2_store.archived_names()
        unavailable = set() if retry_unavailable else r2_store.unavailable_names()
        rows = con.execute(
            f"SELECT storage_name,url FROM docs WHERE doc_type IN ({ph})", types
        ).fetchall()
        return [(r["storage_name"], r["url"]) for r in rows
                if r["storage_name"] not in archived and
                r["storage_name"] not in unavailable]
    rows = con.execute(
        f"""SELECT d.storage_name, d.url FROM docs d
            LEFT JOIN files f ON f.storage_name=d.storage_name
            WHERE d.doc_type IN ({ph}) AND f.storage_name IS NULL""",
        types,
    ).fetchall()
    return [(r["storage_name"], r["url"]) for r in rows]


def run(types, limit=None, workers=8, storage="local", retry_unavailable=False):
    con = db.connect()
    db.init(con)
    todo = pending(con, types, storage, retry_unavailable)
    if limit:
        todo = todo[:limit]
    total = len(todo)
    print(f"{total} files to archive (types={types}, storage={storage}, workers={workers})")
    if not total:
        return

    done = 0
    succeeded = 0
    unavailable = 0
    failed = 0
    bytes_total = 0
    t0 = time.time()

    def work(item):
        name, url = item
        if storage == "r2":
            # Use one temporary file per worker, upload it privately, then remove it.
            # This keeps the VPS disk footprint bounded while the archive grows.
            tmp = None
            try:
                fd, tmp = tempfile.mkstemp(prefix="mhra-", suffix=".pdf")
                os.close(fd)
                n = _fetch(url, tmp)
                key, n = r2_store.upload_file(name, tmp)
                return name, f"r2://{key}", n, None, False
            except Exception as e:  # noqa: BLE001
                return name, "", 0, str(e), getattr(e, "code", None) == 404
            finally:
                if tmp and os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
        dest = _path_for(name)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.exists(dest):
            return name, dest, os.path.getsize(dest), None, False
        try:
            n = _fetch(url, dest)
            return name, dest, n, None, False
        except Exception as e:  # noqa: BLE001
            return name, dest, 0, str(e), getattr(e, "code", None) == 404

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(work, it) for it in todo]
        batch = []
        for fut in as_completed(futs):
            name, dest, n, err, is_unavailable = fut.result()
            done += 1
            if err:
                print(f"  ! {name[:12]} {err}", file=sys.stderr)
                if is_unavailable and storage == "r2":
                    unavailable += 1
                    r2_store.record_unavailable(
                        name, err, datetime.datetime.now(datetime.UTC).isoformat()
                    )
                else:
                    failed += 1
                continue
            succeeded += 1
            bytes_total += n
            archived_at = datetime.datetime.now(datetime.UTC).isoformat()
            batch.append((name, dest, n, archived_at))
            if len(batch) >= 200:
                if storage == "r2":
                    r2_store.record_archived(
                        [(name, path.removeprefix("r2://"), n, at)
                         for name, path, n, at in batch]
                    )
                else:
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
            if storage == "r2":
                r2_store.record_archived(
                    [(name, path.removeprefix("r2://"), n, at)
                     for name, path, n, at in batch]
                )
            else:
                con.executemany(
                    "INSERT OR REPLACE INTO files(storage_name,path,bytes,downloaded) VALUES(?,?,?,?)",
                    batch,
                )
                con.commit()
    print(f"done: {succeeded} archived, {unavailable} unavailable at MHRA, "
          f"{failed} failed, {bytes_total/1e9:.2f} GB in {(time.time()-t0)/60:.1f} min")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--types", default="Spc,Pil")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--storage", choices=("local", "r2"), default="local")
    ap.add_argument("--retry-unavailable", action="store_true")
    args = ap.parse_args()
    run([t.strip() for t in args.types.split(",") if t.strip()], args.limit,
        args.workers, args.storage, args.retry_unavailable)

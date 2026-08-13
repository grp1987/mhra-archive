"""Recover an existing local MHRA PDF mirror into private R2 storage.

Checks each immutable hash in R2 first, uploads only missing objects, and writes
an independent recovery register that can later be merged on the VPS.
"""
import argparse
import datetime
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import r2_store
from botocore.exceptions import ClientError


_clients = threading.local()


def client_and_bucket():
    if not hasattr(_clients, "pair"):
        _clients.pair = r2_store.client_and_bucket()
    return _clients.pair


def recover_one(storage_name, path):
    client, bucket = client_and_bucket()
    key = r2_store.object_key(storage_name)
    try:
        client.head_object(Bucket=bucket, Key=key)
        return storage_name, key, os.path.getsize(path), "already", None
    except ClientError as exc:
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status != 404:
            return storage_name, key, 0, "failed", str(exc)
    try:
        client.upload_file(path, bucket, key, ExtraArgs={"ContentType": "application/pdf"})
        return storage_name, key, os.path.getsize(path), "uploaded", None
    except Exception as exc:  # noqa: BLE001
        return storage_name, key, 0, "failed", str(exc)


def init_register(path):
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE IF NOT EXISTS objects (
        storage_name TEXT PRIMARY KEY, object_key TEXT NOT NULL,
        bytes INTEGER NOT NULL, archived_at TEXT NOT NULL)""")
    con.commit()
    return con


def source_rows(source_db):
    con = sqlite3.connect(source_db)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT storage_name,path,bytes FROM files ORDER BY storage_name").fetchall()
    con.close()
    return [(row["storage_name"], row["path"], row["bytes"] or 0) for row in rows]


def run(source_db, register, workers=6, limit=None):
    rows = source_rows(source_db)
    known = set()
    out = init_register(register)
    known.update(row[0] for row in out.execute("SELECT storage_name FROM objects"))
    rows = [(name, path, size) for name, path, size in rows if name not in known]
    if limit:
        rows = rows[:limit]
    missing_local = [(name, path) for name, path, _ in rows if not os.path.isfile(path)]
    rows = [(name, path, size) for name, path, size in rows if os.path.isfile(path)]
    print(f"Recovery candidates: {len(rows)}; missing locally: {len(missing_local)}; workers: {workers}")

    uploaded = already = failed = bytes_uploaded = done = 0
    started = time.time()
    batch = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(recover_one, name, path) for name, path, _ in rows]
        for future in as_completed(futures):
            name, key, size, status, error = future.result()
            done += 1
            if status == "failed":
                failed += 1
                print(f"  ! {name[:12]} {error}")
            else:
                uploaded += status == "uploaded"
                already += status == "already"
                bytes_uploaded += size if status == "uploaded" else 0
                batch.append((name, key, size, datetime.datetime.now(datetime.UTC).isoformat()))
                if len(batch) >= 200:
                    out.executemany("INSERT OR REPLACE INTO objects VALUES(?,?,?,?)", batch)
                    out.commit()
                    batch.clear()
            if done % 500 == 0:
                rate = done / max(time.time() - started, 0.001)
                eta = (len(rows) - done) / rate / 60
                print(f"  {done}/{len(rows)} uploaded={uploaded} already={already} "
                      f"failed={failed} new={bytes_uploaded/1e9:.2f} GB ETA={eta:.0f} min")
    if batch:
        out.executemany("INSERT OR REPLACE INTO objects VALUES(?,?,?,?)", batch)
        out.commit()
    out.close()
    print(f"done: {uploaded} uploaded, {already} already in R2, {failed} failed, "
          f"{len(missing_local)} missing locally, {bytes_uploaded/1e9:.2f} new GB")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-db", required=True)
    parser.add_argument("--register", default="r2_recovery.db")
    parser.add_argument("--env", help="R2 environment file (never commit it)")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.env:
        settings = r2_store.load_env(Path(args.env))
        os.environ.update(settings)
        r2_store.INDEX_PATH = Path(args.register)
    run(args.source_db, args.register, args.workers, args.limit)

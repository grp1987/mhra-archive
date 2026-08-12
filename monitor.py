"""Nightly MHRA catalogue comparison for the VPS/R2 archive."""
import datetime

import db
import mhra


def now_utc():
    return datetime.datetime.now(datetime.UTC).isoformat()


def identity(doc_type, pl_number, title):
    return (doc_type or "", pl_number or "", title or "")


def row_identity(row):
    return identity(row["doc_type"], row["pl_number"], row["title"])


def flat_record(record):
    return {
        "storage_name": record.get("metadata_storage_name"),
        "doc_type": record.get("doc_type"),
        "pl_number": db._flat(record.get("pl_number")),
        "title": record.get("title"),
        "product_name": record.get("product_name") or "",
        "created": record.get("created") or "",
    }


def newest_by_identity(records):
    """The feed contains history; retain the newest hash for each logical document."""
    result = {}
    for record in sorted(records, key=lambda item: item.get("created") or "", reverse=True):
        result.setdefault(row_identity(record), record)
    return result


def run(record_changes=True):
    con = db.connect()
    db.init(con)
    run_at = now_utc()

    old_records = [dict(row) for row in con.execute(
        """SELECT storage_name,doc_type,pl_number,title,product_name,created
           FROM docs"""
    )]
    old_current = newest_by_identity(old_records)

    fresh_raw = list(mhra.harvest())
    fresh_flat = [flat_record(record) for record in fresh_raw]
    new_current = newest_by_identity(fresh_flat)

    old_ids = set(old_current)
    new_ids = set(new_current)
    added = new_ids - old_ids
    removed = old_ids - new_ids
    changed = {item for item in old_ids & new_ids
               if old_current[item]["storage_name"] != new_current[item]["storage_name"]}

    if record_changes:
        for kind, identities in (("new", added), ("removed", removed), ("changed", changed)):
            for item in identities:
                old = old_current.get(item)
                new = new_current.get(item)
                source = new or old
                detail = {
                    "new": "New document published by MHRA",
                    "removed": "Document no longer listed by MHRA",
                    "changed": "Document content updated; current and previous versions retained",
                }[kind]
                con.execute(
                    """INSERT INTO changes(run_at,kind,doc_type,product_name,pl_number,
                       old_storage,new_storage,diff_pdf,detail) VALUES (?,?,?,?,?,?,?,?,?)""",
                    (run_at, kind, source["doc_type"], source["product_name"],
                     source["pl_number"], old["storage_name"] if old else None,
                     new["storage_name"] if new else None, None, detail),
                )

    # Keep the searchable catalogue aligned to the current MHRA feed. Removed
    # hashes remain retrievable from R2 through the append-only change history.
    db.load_catalog(con, fresh_raw, run_at, rebuild_fts=False)
    con.execute("DELETE FROM docs WHERE last_seen<>? OR last_seen IS NULL", (run_at,))
    db.rebuild_fts_index(con)
    con.execute(
        """INSERT INTO snapshots(run_at,total,n_new,n_removed,n_changed)
           VALUES(?,?,?,?,?)""",
        (run_at, len(new_current), len(added) if record_changes else 0,
         len(removed) if record_changes else 0, len(changed) if record_changes else 0),
    )
    con.commit()
    mode = "change check" if record_changes else "initial baseline"
    print(f"[{run_at}] {mode}: current={len(new_current)} "
          f"new={len(added) if record_changes else 0} "
          f"removed={len(removed) if record_changes else 0} "
          f"changed={len(changed) if record_changes else 0}")
    return len(added), len(removed), len(changed)


if __name__ == "__main__":
    run()

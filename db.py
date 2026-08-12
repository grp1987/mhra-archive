"""
SQLite store for the MHRA archive.

Tables
  docs        one row per catalog document (current state), PK = storage_name (SHA1)
  docs_fts    FTS5 mirror for fast search over product/substance/pl/title
  files       local mirror bookkeeping: which storage_names are downloaded, path, bytes
  changes     append-only change log produced by the monitor (new/removed/changed)
  snapshots   one row per monitor run (when, totals)
"""
import json
import os
import sqlite3

DB_PATH = os.environ.get("MHRA_DB_PATH",
                         os.path.join(os.path.dirname(__file__), "mhra.db"))


def connect(path=None):
    path = path or DB_PATH
    con = sqlite3.connect(path, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=10000")  # wait out the indexer's writes
    return con


SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    storage_name TEXT PRIMARY KEY,      -- SHA1 / blob name / content fingerprint
    url          TEXT,
    doc_type     TEXT,                  -- Spc | Pil | Par
    product_name TEXT,
    pl_number    TEXT,                  -- JSON list flattened to comma string
    substance    TEXT,                  -- JSON list flattened to comma string
    created      TEXT,                  -- ISO timestamp of current version
    rev_label    TEXT,
    title        TEXT,
    file_name    TEXT,
    size         INTEGER,
    territory    TEXT,
    release_state TEXT,
    pl_class     TEXT,                  -- 'PI' (PLPI) | 'UK' (PL/PLGB/PLNI/THR/NR)
    name_key     TEXT,                  -- normalized product name, for UK<->PI linking
    company_no   TEXT,                  -- 5-digit MAH/licence-holder number from the PL
    first_seen   TEXT,                  -- when our archive first recorded this hash
    last_seen    TEXT                   -- last monitor run that still saw it
);
CREATE INDEX IF NOT EXISTS ix_docs_type  ON docs(doc_type);
CREATE INDEX IF NOT EXISTS ix_docs_pl    ON docs(pl_number);
CREATE INDEX IF NOT EXISTS ix_docs_class ON docs(pl_class);
CREATE INDEX IF NOT EXISTS ix_docs_namek ON docs(name_key);
CREATE INDEX IF NOT EXISTS ix_docs_comp  ON docs(company_no);

-- MAH (Marketing Authorisation / licence holder) name per company number,
-- resolved from leaflet PDF text. Joined into results so every product by a
-- known company shows its holder, including ones we never individually parsed.
CREATE TABLE IF NOT EXISTS mah (
    company_no     TEXT PRIMARY KEY,
    name           TEXT,
    source_storage TEXT,
    built_at       TEXT
);

-- "Date of first authorisation" (Section 9 of the SPC) per PL number = the PL
-- grant date. Keyed by PL so both the SPC and the PIL of that licence show it.
CREATE TABLE IF NOT EXISTS pl_dates (
    pl_number      TEXT PRIMARY KEY,
    grant_date     TEXT,               -- ISO YYYY-MM-DD (for sorting)
    grant_date_raw TEXT,               -- as printed in the SPC
    source_storage TEXT,
    built_at       TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
    product_name, substance, pl_number, title, doc_type,
    content='docs', content_rowid='rowid'
);

CREATE TABLE IF NOT EXISTS files (
    storage_name TEXT PRIMARY KEY,
    path         TEXT,
    bytes        INTEGER,
    downloaded   TEXT
);

CREATE TABLE IF NOT EXISTS snapshots (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at    TEXT,
    total     INTEGER,
    n_new     INTEGER,
    n_removed INTEGER,
    n_changed INTEGER
);

CREATE TABLE IF NOT EXISTS changes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at       TEXT,
    kind         TEXT,                  -- new | removed | changed
    doc_type     TEXT,
    product_name TEXT,
    pl_number    TEXT,
    old_storage  TEXT,                  -- prior hash (for changed/removed)
    new_storage  TEXT,                  -- new hash (for changed/new)
    diff_pdf     TEXT,                  -- path to highlighted redline, if produced
    detail       TEXT
);
"""


import re

_NONALNUM = re.compile(r"[^A-Z0-9]")


def name_key(product_name):
    """Normalized product name for UK<->PI matching: uppercase, alphanumeric
    only (kills '20MG/ML' vs '20 MG/ML', trailing '.', spacing noise)."""
    return _NONALNUM.sub("", (product_name or "").upper())


def classify_pl(pl_flat):
    """'PI' if any licence is a parallel import (PLPI), else 'UK'. pl_flat is the
    already-flattened comma string from _flat()."""
    return "PI" if "PLPI" in (pl_flat or "").upper() else "UK"


_PLFMT = re.compile(r"^([A-Z]+)(\d{5})(\d{4})$")


def format_pl(pl_flat):
    """Display form with separators: 'PLPI219230113' -> 'PLPI/21923/0113'.
    Keeps the real prefix (PL/PLPI/PLGB/PLNI/THR/NR). Leaves odd ones unchanged."""
    out = []
    for p in (pl_flat or "").split(","):
        p = p.replace(" ", "").strip()
        if not p:
            continue
        m = _PLFMT.match(p)
        out.append(f"{m.group(1)}/{m.group(2)}/{m.group(3)}" if m else p)
    return ", ".join(out)


_COMPANY = re.compile(r"^[A-Z]+(\d{5})\d{4}$")


def company_no(pl_flat):
    """5-digit MAH/licence-holder number from a PL (e.g. PLPI 18799-4281 -> 18799).
    Uses the first licence if several are present."""
    first = (pl_flat or "").split(",")[0].replace(" ", "").strip().upper()
    m = _COMPANY.match(first)
    return m.group(1) if m else None


def _flat(v):
    """Flatten Azure collection fields to a clean comma string. Some fields
    (notably pl_number) arrive double-encoded: a list whose elements are
    JSON-array strings like '["PLGB194940270"]' — unwrap those too."""
    if v is None:
        return ""
    items = v if isinstance(v, list) else [v]
    out = []
    for x in items:
        if isinstance(x, str) and x.startswith("[") and x.endswith("]"):
            try:
                inner = json.loads(x)
                if isinstance(inner, list):
                    out.extend(str(i) for i in inner if i)
                    continue
            except (ValueError, TypeError):
                pass
        if x:
            out.append(str(x))
    # de-dupe preserving order
    seen = set()
    return ", ".join(o for o in out if not (o in seen or seen.add(o)))


def init(con):
    con.executescript(SCHEMA)
    con.commit()


def _row_from_rec(rec):
    pl = _flat(rec.get("pl_number"))
    product = rec.get("product_name")
    return (
        rec.get("metadata_storage_name"),
        rec.get("metadata_storage_path"),
        rec.get("doc_type"),
        product,
        pl,
        _flat(rec.get("substance_name")),
        rec.get("created"),
        rec.get("rev_label"),
        rec.get("title"),
        rec.get("file_name"),
        rec.get("metadata_storage_size"),
        rec.get("territory"),
        rec.get("release_state"),
        classify_pl(pl),
        name_key(product),
        company_no(pl),
    )


def load_catalog(con, records, run_at, rebuild_fts=True):
    """Upsert catalog records. Sets first_seen on insert, last_seen always."""
    cur = con.cursor()
    n = 0
    for rec in records:
        row = _row_from_rec(rec)
        cur.execute(
            """
            INSERT INTO docs (storage_name,url,doc_type,product_name,pl_number,
                substance,created,rev_label,title,file_name,size,territory,
                release_state,pl_class,name_key,company_no,first_seen,last_seen)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(storage_name) DO UPDATE SET last_seen=excluded.last_seen,
                url=excluded.url, product_name=excluded.product_name,
                created=excluded.created, title=excluded.title,
                pl_class=excluded.pl_class, name_key=excluded.name_key,
                company_no=excluded.company_no
            """,
            row + (run_at, run_at),
        )
        n += 1
    con.commit()
    if rebuild_fts:
        rebuild_fts_index(con)
    return n


def rebuild_fts_index(con):
    con.execute("INSERT INTO docs_fts(docs_fts) VALUES('delete-all')")
    con.execute(
        """INSERT INTO docs_fts(rowid, product_name, substance, pl_number, title, doc_type)
           SELECT rowid, product_name, substance, pl_number, title, doc_type FROM docs"""
    )
    con.commit()


def _has_body_fts(con):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='body_fts'"
    ).fetchone() is not None


_SEL = "SELECT d.*, m.name AS mah, g.grant_date AS grant_date, g.grant_date_raw AS grant_date_raw"
_JOINS = ("LEFT JOIN mah m ON m.company_no=d.company_no "
          "LEFT JOIN pl_dates g ON g.pl_number=d.pl_number ")


def _from_where(q, doc_type, pl_class, company, in_text, con):
    """Shared FROM/JOIN/WHERE for search() and search_count()."""
    params = []
    if q and in_text and _has_body_fts(con):
        base = (f"FROM body_fts b JOIN docs d ON d.storage_name=b.storage_name "
                f"{_JOINS} WHERE body_fts MATCH ? ")
        params.append(_fts_query(q))
    elif q:
        base = (f"FROM docs_fts f JOIN docs d ON d.rowid=f.rowid "
                f"{_JOINS} WHERE docs_fts MATCH ? ")
        params.append(_fts_query(q))
    else:
        base = f"FROM docs d {_JOINS} WHERE 1=1 "
    if doc_type:
        base += "AND d.doc_type=? "; params.append(doc_type)
    if pl_class:
        base += "AND d.pl_class=? "; params.append(pl_class)
    if company:
        base += "AND d.company_no=? "; params.append(company)
    return base, params


def search_count(con, q=None, doc_type=None, pl_class=None, company=None, in_text=False):
    base, params = _from_where(q, doc_type, pl_class, company, in_text, con)
    return con.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]


def search(con, q=None, doc_type=None, pl_class=None, company=None, in_text=False,
           limit=100, offset=0):
    params = []
    if q and in_text and _has_body_fts(con):
        # full-text search over extracted PDF body text
        base = (
            f"{_SEL} FROM body_fts b JOIN docs d ON d.storage_name=b.storage_name "
            f"{_JOINS} WHERE body_fts MATCH ? "
        )
        params.append(_fts_query(q))
    elif q:
        base = (
            f"{_SEL} FROM docs_fts f JOIN docs d ON d.rowid=f.rowid "
            f"{_JOINS} WHERE docs_fts MATCH ? "
        )
        params.append(_fts_query(q))
    else:
        base = f"{_SEL} FROM docs d {_JOINS} WHERE 1=1 "
    if doc_type:
        base += "AND d.doc_type=? "
        params.append(doc_type)
    if pl_class:
        base += "AND d.pl_class=? "
        params.append(pl_class)
    if company:
        base += "AND d.company_no=? "
        params.append(company)
    base += "ORDER BY d.created DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    return [dict(r) for r in con.execute(base, params).fetchall()]


def related_pis(con, storage_name):
    """Parallel-import (PLPI) documents that match a given UK reference doc by
    normalized product name, then (fallback) by shared active substance."""
    ref = con.execute(
        "SELECT name_key, substance FROM docs WHERE storage_name=?", (storage_name,)
    ).fetchone()
    if not ref:
        return {"by_name": [], "by_substance": []}
    by_name = [dict(r) for r in con.execute(
        f"{_SEL} FROM docs d {_JOINS} "
        "WHERE d.pl_class='PI' AND d.name_key=? ORDER BY d.created DESC",
        (ref["name_key"],),
    ).fetchall()]
    seen = {r["storage_name"] for r in by_name}
    by_sub = []
    if ref["substance"]:
        rows = con.execute(
            f"{_SEL} FROM docs d {_JOINS} "
            "WHERE d.pl_class='PI' AND d.substance=? ORDER BY d.created DESC",
            (ref["substance"],),
        ).fetchall()
        by_sub = [dict(r) for r in rows if r["storage_name"] not in seen]
    return {"by_name": by_name, "by_substance": by_sub}


def _fts_query(q):
    # Turn a free text query into a safe FTS5 prefix-AND query.
    terms = [t for t in "".join(c if c.isalnum() else " " for c in q).split() if t]
    return " AND ".join(f'"{t}"*' for t in terms) if terms else q


def counts(con):
    rows = con.execute("SELECT doc_type, COUNT(*) c FROM docs GROUP BY doc_type").fetchall()
    return {r["doc_type"]: r["c"] for r in rows}


def class_counts(con):
    rows = con.execute("SELECT pl_class, COUNT(*) c FROM docs GROUP BY pl_class").fetchall()
    return {r["pl_class"]: r["c"] for r in rows}


if __name__ == "__main__":
    # Build the DB from catalog.jsonl produced by harvest_meta.py
    import datetime
    run_at = datetime.datetime.utcnow().isoformat() + "Z"
    con = connect()
    init(con)
    path = os.path.join(os.path.dirname(__file__), "catalog.jsonl")
    recs = (json.loads(l) for l in open(path))
    n = load_catalog(con, recs, run_at)
    print(f"loaded {n} docs; counts={counts(con)}")

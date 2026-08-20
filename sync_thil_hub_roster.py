"""Synchronise the approved Commercial Hub roster in the THIL portal database.

Existing password hashes are preserved. Missing accounts receive the supplied
temporary password and must replace it at first login. The update is performed
in one SQLite transaction and aborts on ambiguous username collisions.
"""
import argparse
import sqlite3

from thil_portal import DB_PATH, HUB_ROLES, password_hash


ROSTER = {
    "GrantAdmin": "management",
    "Emma": "management",
    "Grant": "salespurchasing",
    "Harvey": "salespurchasing",
    "Iulia": "salespurchasing",
    "Salvador": "salespurchasing",
    "Rose": "salespurchasing",
    "Chris": "salespurchasing",
    "Louise": "purchasing",
    "Daniel": "purchasing",
    "Gemma": "purchasing",
    "Purchasing": "purchasing",
    "Sales": "sales",
    "SandP": "salespurchasing",
}
LEGACY_ALIASES = {"GrantAdmin": ("grant-admin",)}


def find_matches(con, canonical):
    names = (canonical,) + LEGACY_ALIASES.get(canonical, ())
    placeholders = ",".join("?" for _ in names)
    return con.execute(
        f"SELECT * FROM users WHERE lower(username) IN ({placeholders})",
        tuple(name.lower() for name in names),
    ).fetchall()


def sync(temporary_password, apply_changes=False):
    if len(temporary_password) < 8:
        raise ValueError("Temporary password must be at least 8 characters.")
    if not set(ROSTER.values()).issubset(HUB_ROLES):
        raise RuntimeError("Roster contains a role unsupported by the portal.")

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    created = []
    updated = []
    try:
        con.execute("BEGIN IMMEDIATE")
        approved = tuple(name.lower() for name in ROSTER)
        placeholders = ",".join("?" for _ in approved)
        con.execute(
            f"""UPDATE users SET can_hub=0, hub_role=NULL
                WHERE lower(username) NOT IN ({placeholders})""",
            approved,
        )

        for username, role in ROSTER.items():
            matches = find_matches(con, username)
            if len(matches) > 1:
                names = ", ".join(row["username"] for row in matches)
                raise RuntimeError(f"Ambiguous portal accounts for {username}: {names}")
            if matches:
                row = matches[0]
                con.execute(
                    """UPDATE users SET username=?, display_name=?, can_hub=1,
                       hub_role=?, active=1, locked=0, failed_login_attempts=0,
                       is_admin=CASE WHEN ?='GrantAdmin' THEN 1 ELSE is_admin END
                       WHERE id=?""",
                    (username, username, role, username, row["id"]),
                )
                updated.append(username)
            else:
                con.execute(
                    """INSERT INTO users(
                       username,password_hash,is_admin,can_mhra,can_pid,active,
                       must_change_password,failed_login_attempts,locked,
                       can_hub,hub_role,display_name
                       ) VALUES(?,?,?,?,?,1,1,0,0,1,?,?)""",
                    (
                        username,
                        password_hash(temporary_password),
                        username == "GrantAdmin",
                        0,
                        0,
                        role,
                        username,
                    ),
                )
                created.append(username)

        rows = con.execute(
            """SELECT username,display_name,hub_role,active,locked,is_admin
               FROM users WHERE can_hub=1 ORDER BY lower(username)"""
        ).fetchall()
        actual = {
            row["username"]: row["hub_role"]
            for row in rows
            if row["username"] == row["display_name"] and row["active"] and not row["locked"]
        }
        if actual != ROSTER:
            raise RuntimeError("Post-update roster verification failed; transaction cancelled.")

        if apply_changes:
            con.commit()
        else:
            con.rollback()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    mode = "APPLIED" if apply_changes else "DRY RUN"
    print(f"Portal Hub roster {mode}: {len(updated)} updated, {len(created)} created")
    print("Updated: " + (", ".join(updated) or "none"))
    print("Created: " + (", ".join(created) or "none"))
    if created:
        print("New accounts require a password change on first login.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--temporary-password", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    sync(args.temporary_password, args.apply)


if __name__ == "__main__":
    main()

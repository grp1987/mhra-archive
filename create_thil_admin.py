"""Create or reset the first THIL administrator without echoing the password."""
import getpass
import sys

from thil_portal import connect, init_db, password_hash


def main():
    username = (sys.argv[1] if len(sys.argv) > 1 else input("Admin username [admin]: ").strip() or "admin")
    password = getpass.getpass("New password (8+ characters): ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        raise SystemExit("Passwords do not match.")
    if len(password) < 8:
        raise SystemExit("Password must be at least 8 characters.")
    init_db()
    with connect() as con:
        existing = con.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if existing:
            con.execute("UPDATE users SET password_hash=?,is_admin=1,can_mhra=1,can_pid=1,active=1 WHERE id=?",
                        (password_hash(password), existing["id"]))
            print(f"Administrator '{username}' reset.")
        else:
            con.execute("INSERT INTO users(username,password_hash,is_admin,can_mhra,can_pid,active) VALUES(?,?,1,1,1,1)",
                        (username, password_hash(password)))
            print(f"Administrator '{username}' created.")


if __name__ == "__main__":
    main()

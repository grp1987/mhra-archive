"""Merge a Mac recovery register into the VPS live R2 archive register."""
import argparse
import sqlite3


def merge(live_path, recovery_path):
    live = sqlite3.connect(live_path)
    live.execute("ATTACH DATABASE ? AS recovery", (recovery_path,))
    before = live.execute("SELECT COUNT(*) FROM objects").fetchone()[0]
    live.execute(
        """INSERT OR REPLACE INTO objects(storage_name,object_key,bytes,archived_at)
           SELECT storage_name,object_key,bytes,archived_at FROM recovery.objects"""
    )
    live.execute(
        "DELETE FROM unavailable WHERE storage_name IN (SELECT storage_name FROM recovery.objects)"
    )
    live.commit()
    after = live.execute("SELECT COUNT(*) FROM objects").fetchone()[0]
    live.close()
    print(f"Recovery register merged: {after - before} additional objects; {after} total.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", required=True)
    parser.add_argument("--recovery", required=True)
    args = parser.parse_args()
    merge(args.live, args.recovery)

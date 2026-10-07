"""Read-only comparison of existing academic records and media against a backup."""

import argparse
import hashlib
import json
import sqlite3
import tarfile
from pathlib import Path


def compare(baseline, current, media_archive=None, media_root=None):
    evidence = {}
    with (
        sqlite3.connect(f"file:{baseline}?mode=ro", uri=True) as before,
        sqlite3.connect(f"file:{current}?mode=ro", uri=True) as after,
    ):
        after.execute("BEGIN")
        assert after.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "SQLite integrity failed"
        assert not after.execute("PRAGMA foreign_key_check").fetchall(), "Invalid foreign keys"
        for (table,) in before.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            if not (table.startswith("questoes_") or table in ("auth_user", "django_admin_log")):
                continue
            columns = [r[1] for r in before.execute(f'PRAGMA table_info("{table}")')]
            primary = next(r[1] for r in before.execute(f'PRAGMA table_info("{table}")') if r[5])
            projection = ",".join('"' + c + '"' for c in columns)
            existing = before.execute(f'SELECT {projection} FROM "{table}"').fetchall()
            current_rows = after.execute(f'SELECT {projection} FROM "{table}"').fetchall()
            index = columns.index(primary)
            current_index = {row[index]: row for row in current_rows}
            assert all(current_index.get(row[index]) == row for row in existing), (
                f"Changed/missing rows: {table}"
            )
            evidence[table] = {
                "before": len(existing),
                "after": len(current_rows),
                "existing_preserved": True,
            }
    if media_archive:
        checked = 0
        with tarfile.open(media_archive, "r:gz") as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                relative = Path(member.name).relative_to("media")
                path = Path(media_root) / relative
                assert path.is_file(), "Missing media"
                assert (
                    hashlib.sha256(archive.extractfile(member).read()).digest()
                    == hashlib.sha256(path.read_bytes()).digest()
                ), "Changed media"
                checked += 1
        evidence["media_files_preserved"] = checked
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--current", required=True)
    parser.add_argument("--media-archive")
    parser.add_argument("--media-root")
    args = parser.parse_args()
    print(json.dumps(compare(args.baseline, args.current, args.media_archive, args.media_root), indent=2))

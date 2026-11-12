"""Read-only comparison of existing academic records and media against a backup."""

import argparse
import hashlib
import json
import sqlite3
import tarfile
from contextlib import closing
from pathlib import Path


def componentes_da_grade_atualizada(before, after):
    migration = "0021_atualizar_grade_oitavo_2026_2"
    query = "SELECT 1 FROM django_migrations WHERE app = 'questoes' AND name = ?"
    if before.execute(query, (migration,)).fetchone() or not after.execute(query, (migration,)).fetchone():
        return set()
    return {
        pk
        for (pk,) in before.execute(
            """
            SELECT c.id
            FROM questoes_componentecurricular c
            JOIN questoes_periodo p ON p.id = c.periodo_id
            JOIN questoes_semestre s ON s.id = p.semestre_id
            WHERE s.nome = '2026.2' AND c.consolidado_em_id IS NULL
            AND (p.nome = '8º Período' OR (p.nome = '8º Período (turmas C e D)' AND p.ativo))
            """
        )
    }


def compare(baseline, current, media_archive=None, media_root=None):
    evidence = {}
    with (
        closing(sqlite3.connect(f"file:{baseline}?mode=ro", uri=True)) as before,
        closing(sqlite3.connect(f"file:{current}?mode=ro", uri=True)) as after,
    ):
        after.execute("BEGIN")
        assert after.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "SQLite integrity failed"
        assert not after.execute("PRAGMA foreign_key_check").fetchall(), "Invalid foreign keys"
        for (table,) in before.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            if not (
                table.startswith(("questoes_", "pre_auditoria_", "bulk_submit_"))
                or table in ("auth_user", "django_admin_log")
            ):
                continue
            columns = [r[1] for r in before.execute(f'PRAGMA table_info("{table}")')]
            primary = next(r[1] for r in before.execute(f'PRAGMA table_info("{table}")') if r[5])
            projection = ",".join('"' + c + '"' for c in columns)
            existing = before.execute(f'SELECT {projection} FROM "{table}"').fetchall()
            current_rows = after.execute(f'SELECT {projection} FROM "{table}"').fetchall()
            index = columns.index(primary)
            current_index = {row[index]: row for row in current_rows}
            updated_grade = (
                componentes_da_grade_atualizada(before, after)
                if table == "questoes_componentecurricular" and "consolidado_em_id" in columns
                else set()
            )
            grade_changes = 0
            for row in existing:
                current_row = current_index.get(row[index])
                if current_row == row:
                    continue
                assert current_row is not None and row[index] in updated_grade, (
                    f"Changed/missing rows: {table}"
                )
                changed_fields = {col for col, old, new in zip(columns, row, current_row) if old != new}
                assert changed_fields <= {"nome", "numero_questoes_prova"}, f"Unexpected changes: {table}"
                grade_changes += 1
            evidence[table] = {
                "before": len(existing),
                "after": len(current_rows),
                "existing_preserved": grade_changes == 0,
            }
            if grade_changes:
                evidence[table]["grade_components_updated"] = grade_changes
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

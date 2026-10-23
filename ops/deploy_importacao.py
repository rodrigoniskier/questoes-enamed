#!/usr/bin/env python3
"""Backup privado antes do pull; prepara o deploy sem presumir reload ou smoke test."""

import argparse
import fcntl
import getpass
import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

ORIGINS = {
    "https://github.com/rodrigoniskier/questoes-enamed.git",
    "https://github.com/rodrigoniskier/questoes-enamed",
    "git@github.com:rodrigoniskier/questoes-enamed.git",
}


def run(*args, capture=False):
    return subprocess.run(args, check=True, text=True, stdout=subprocess.PIPE if capture else None).stdout


def checksum(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def backup_sqlite(source, destination):
    source, destination = Path(source), Path(destination)
    if not source.is_file():
        raise RuntimeError("Banco persistente inexistente; deploy interrompido.")
    os.close(os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    with closing(sqlite3.connect(f"file:{quote(str(source.resolve()))}?mode=ro", uri=True)) as before:
        with closing(sqlite3.connect(destination)) as backup:
            before.backup(backup)
            if backup.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise RuntimeError("Backup SQLite inválido.")
            if backup.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError("Backup com referências inválidas.")
            if not backup.execute("SELECT 1 FROM sqlite_master WHERE name='django_migrations'").fetchone():
                raise RuntimeError("Backup não contém o banco Django esperado.")
    return checksum(destination)


def set_revision(env_path, revision):
    if not re.fullmatch("[0-9a-f]{40}", revision):
        raise ValueError("Revisão inválida.")
    original = env_path.read_text()
    updated, count = re.subn(
        r"(?m)^(?:export\s+)?RELEASE_REVISION\s*=.*$",
        f"RELEASE_REVISION={revision}",
        original,
    )
    if count > 1:
        raise RuntimeError("Configuração de revisão ambígua; nenhuma alteração aplicada.")
    if not count:
        updated = original.rstrip("\n") + f"\nRELEASE_REVISION={revision}\n"
    temporary = env_path.with_name(".env.codex-release.tmp")
    with temporary.open("x") as output:
        os.chmod(temporary, 0o600)
        output.write(updated)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, env_path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--approved-sha", required=True)
    parser.add_argument("--expected-before", required=True)
    parser.add_argument("--wsgi", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if getpass.getuser() != "rodrigoniskier" or sys.prefix == sys.base_prefix:
        raise RuntimeError("Use a conta e o virtualenv confirmados no painel.")
    if not all(re.fullmatch("[0-9a-f]{40}", value) for value in (args.approved_sha, args.expected_before)):
        raise ValueError("SHA inválido.")
    checkout = args.checkout.resolve(strict=True)
    backup_dir = args.backup_dir.resolve()
    if backup_dir == checkout or checkout in backup_dir.parents:
        raise RuntimeError("Backup deve ficar fora do checkout e das áreas públicas.")
    os.chdir(checkout)
    if run("git", "remote", "get-url", "origin", capture=True).strip() not in ORIGINS:
        raise RuntimeError("Origin não corresponde a questoes-enamed.")
    if run("git", "branch", "--show-current", capture=True).strip() != "main":
        raise RuntimeError("Branch de produção divergente.")
    if run("git", "status", "--porcelain", capture=True).strip():
        raise RuntimeError("Checkout modificado; preserve o estado antes de continuar.")
    previous = run("git", "rev-parse", "HEAD", capture=True).strip()
    if previous != args.expected_before:
        raise RuntimeError("A revisão anterior diverge da inspeção aprovada.")
    wsgi = args.wsgi.read_text()
    if str(checkout) not in wsgi or "gerador_provas.settings" not in wsgi:
        raise RuntimeError("WSGI não confirma este checkout.")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gerador_provas.settings")
    config = json.loads(
        run(
            sys.executable,
            "-c",
            "import django,json;django.setup();from django.conf import settings as s;"
            "print(json.dumps({'database':s.DATABASES['default'],'media':str(s.MEDIA_ROOT)}))",
            capture=True,
        )
    )
    if config["database"]["ENGINE"] != "django.db.backends.sqlite3":
        raise RuntimeError("Este procedimento exige backup nativo SQLite.")
    database = Path(config["database"]["NAME"]).resolve(strict=True)
    media = Path(config["media"]).resolve(strict=True)
    env_path = checkout / ".env"
    if not env_path.is_file() or env_path.is_symlink():
        raise RuntimeError("Arquivo de configuração privado não confirmado.")
    run(sys.executable, "manage.py", "check")
    run(sys.executable, "manage.py", "migrate", "--check")
    run("git", "fetch", "origin", "main")
    if run("git", "rev-parse", "origin/main", capture=True).strip() != args.approved_sha:
        raise RuntimeError("A main avançou; revalidar antes do deploy.")
    run("git", "merge-base", "--is-ancestor", previous, args.approved_sha)
    backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup_dir.chmod(0o700)
    snapshot = backup_dir / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    snapshot.mkdir(mode=0o700)
    receipt = {"status": "BACKUP_EM_ANDAMENTO", "previous_sha": previous, "approved_sha": args.approved_sha}
    record = snapshot / "receipt.json"

    def save(status):
        receipt["status"] = status
        record.write_text(json.dumps(receipt, indent=2))
        record.chmod(0o600)
        print(f"{status}: {record}", flush=True)

    save("BACKUP_EM_ANDAMENTO")
    try:
        receipt["database_sha256"] = backup_sqlite(database, snapshot / "database.sqlite3")
        with tarfile.open(snapshot / "media.tar.gz", "w:gz") as archive:
            archive.add(media, arcname="media")
        for source, name in ((env_path, "environment.private"), (args.wsgi, "wsgi.private")):
            shutil.copyfile(source, snapshot / name)
        # O ledger de uso de IA também é privado e continua independente do banco acadêmico.
        ai_ledger = checkout / "private" / "ai-control.sqlite3"
        if ai_ledger.is_file():
            with closing(sqlite3.connect(f"file:{quote(str(ai_ledger))}?mode=ro", uri=True)) as source:
                with closing(sqlite3.connect(snapshot / "ai-control.sqlite3")) as target:
                    source.backup(target)
        run("git", "bundle", "create", str(snapshot / "code.bundle"), "HEAD")
        (snapshot / "packages.json").write_text(
            json.dumps({d.metadata["Name"]: d.version for d in importlib.metadata.distributions()}, indent=2)
        )
        receipt["backup_files"] = {}
        for path in snapshot.iterdir():
            path.chmod(0o600)
            if path != record:
                receipt["backup_files"][path.name] = checksum(path)
        save("BACKUP_VALIDADO")
        # Imutável mesmo se main avançar depois da verificação; nunca reset/force/clean.
        run("git", "pull", "--ff-only", "origin", args.approved_sha)
        receipt["git_head"] = run("git", "rev-parse", "HEAD", capture=True).strip()
        if receipt["git_head"] != args.approved_sha:
            raise RuntimeError("HEAD divergente após pull.")
        changed = run("git", "diff", "--name-only", previous, args.approved_sha, capture=True).splitlines()
        if "requirements.txt" in changed:
            run(sys.executable, "-m", "pip", "install", "-r", "requirements.txt")
        run(sys.executable, "-m", "pip", "check")
        for command in (
            ("check",),
            ("check", "--deploy"),
            ("migrate", "--plan"),
            ("migrate", "--noinput"),
            ("migrate", "--check"),
            ("collectstatic", "--noinput"),
        ):
            run(sys.executable, "manage.py", *command)
        receipt["integrity"] = json.loads(
            run(
                sys.executable,
                "ops/verify_integrity.py",
                "--baseline",
                str(snapshot / "database.sqlite3"),
                "--current",
                str(database),
                "--media-archive",
                str(snapshot / "media.tar.gz"),
                "--media-root",
                str(media),
                capture=True,
            )
        )
        set_revision(env_path, args.approved_sha)
        receipt["packages_after"] = {
            name: importlib.metadata.version(name) for name in ("Django", "Pillow", "django-cleanup")
        }
        save("CODIGO_BANCO_E_ESTATICOS_VERIFICADOS_RELOAD_PENDENTE")
    except Exception as error:
        # Log privado conserva diagnóstico; recibo não inclui credenciais nem conteúdo acadêmico.
        receipt["error_type"] = type(error).__name__
        save("FALHA_DEPLOY_INTERROMPIDO_SEM_RESTAURACAO_AUTOMATICA")
        raise


if __name__ == "__main__":
    os.umask(0o077)
    lock_dir = Path.home() / ".cache"
    lock_dir.mkdir(exist_ok=True)
    with (lock_dir / "questoes-enamed-deploy.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        main()

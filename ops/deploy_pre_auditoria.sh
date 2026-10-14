#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

fail() {
    printf 'ERRO: %s\n' "$1" >&2
    exit 1
}

PYTHON="${PYTHON:-python}"
command -v "$PYTHON" >/dev/null 2>&1 || fail "Python não encontrado no ambiente ativo."
command -v git >/dev/null 2>&1 || fail "Git não encontrado."

if [[ "$(git branch --show-current)" != "main" ]]; then
    fail "Execute o deploy somente na branch main após o pull autorizado."
fi
if ! git show-ref --verify --quiet refs/remotes/origin/main; then
    fail "A referência origin/main não está disponível para confirmar a revisão."
fi
if [[ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]]; then
    fail "HEAD não corresponde a origin/main. Atualize e confirme a revisão antes do deploy."
fi
git diff --quiet HEAD -- || fail "Há alterações rastreadas locais; preserve-as e resolva-as antes do deploy."
git diff --cached --quiet || fail "Há alterações staged locais; resolva-as antes do deploy."
untracked_code="$(git ls-files --others --exclude-standard | grep -E '(^|/)(migrations/.*\.py|[^/]+\.(py|sh|html|css|js))$' || true)"
[[ -z "$untracked_code" ]] || fail "Há arquivos locais de código não rastreados; resolva-os antes do deploy."

printf 'Revisão: %s\n' "$(git rev-parse --short HEAD)"
"$PYTHON" -m pip install -r requirements.txt
"$PYTHON" manage.py check

DB_INFO="$(DJANGO_SETTINGS_MODULE=gerador_provas.settings "$PYTHON" -c 'import django, os; django.setup(); from django.conf import settings; db=settings.DATABASES["default"]; print(db["ENGINE"] + "\t" + str(db["NAME"]))')"
IFS=$'\t' read -r DB_ENGINE DB_PATH <<< "$DB_INFO"
[[ "$DB_ENGINE" == "django.db.backends.sqlite3" ]] || fail "O banco padrão não é SQLite; este script não pode inferir um backup seguro."
[[ -n "$DB_PATH" && "$DB_PATH" != ":memory:" && -f "$DB_PATH" ]] || fail "O SQLite configurado não existe ou não é um arquivo persistente."
DB_PATH="$(realpath "$DB_PATH")"
MEDIA_ROOT="$(DJANGO_SETTINGS_MODULE=gerador_provas.settings "$PYTHON" -c 'import django; django.setup(); from django.conf import settings; print(settings.MEDIA_ROOT)')"

BACKUP_DIR="${PRE_AUDIT_BACKUP_DIR:-${HOME:?HOME precisa estar definido}/.private_backups/pre_auditoria}"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
BACKUP_DIR="$(realpath "$BACKUP_DIR")"
case "$BACKUP_DIR/" in
    "$ROOT_DIR/"*) fail "O diretório de backup precisa ficar fora do projeto e de áreas públicas." ;;
esac

REVISION="$(git rev-parse --short HEAD)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_PATH="$BACKUP_DIR/pre_auditoria-${REVISION}-${STAMP}.sqlite3"
MANIFEST_PATH="${BACKUP_PATH%.sqlite3}.counts.json"
MEDIA_ARCHIVE="$BACKUP_DIR/pre_auditoria-${REVISION}-${STAMP}-media.tar.gz"
ENV_BACKUP="$BACKUP_DIR/pre_auditoria-${REVISION}-${STAMP}.env"
WSGI_BACKUP="$BACKUP_DIR/pre_auditoria-${REVISION}-${STAMP}.wsgi.py"
umask 077

"$PYTHON" - "$DB_PATH" "$BACKUP_PATH" "$MANIFEST_PATH" <<'PY'
import json
import sqlite3
import sys
from urllib.parse import quote

source_path, backup_path, manifest_path = sys.argv[1:]
source = sqlite3.connect(f"file:{quote(source_path)}?mode=ro", uri=True)
target = sqlite3.connect(backup_path)
try:
    source.backup(target)
    check = target.execute("PRAGMA integrity_check").fetchone()[0]
    if check != "ok":
        raise SystemExit(f"Backup SQLite inválido: {check}")
    foreign_key_errors = target.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_key_errors:
        raise SystemExit("Backup contém violações de chave estrangeira; deploy abortado.")
    tables = [
        row[0]
        for row in target.execute("SELECT name FROM sqlite_master WHERE type='table'")
        if not row[0].startswith("sqlite_")
    ]
    counts = {
        table: target.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        for table in tables
    }
finally:
    target.close()
    source.close()
with open(manifest_path, "x", encoding="utf-8") as manifest:
    json.dump(counts, manifest, sort_keys=True)
PY
chmod 600 "$BACKUP_PATH" "$MANIFEST_PATH"
printf 'Backup privado verificado: %s\n' "$BACKUP_PATH"
if [[ -d "$MEDIA_ROOT" ]]; then
    tar -czf "$MEDIA_ARCHIVE" -C "$MEDIA_ROOT" .
    tar -tzf "$MEDIA_ARCHIVE" >/dev/null
    chmod 600 "$MEDIA_ARCHIVE"
    printf 'Arquivo privado de mídia verificado: %s\n' "$MEDIA_ARCHIVE"
fi
if [[ -f "$ROOT_DIR/.env" ]]; then
    cp -p -- "$ROOT_DIR/.env" "$ENV_BACKUP"
    chmod 600 "$ENV_BACKUP"
    printf 'Cópia privada da configuração .env: %s\n' "$ENV_BACKUP"
fi
WSGI_SOURCE="${PRE_AUDIT_WSGI_FILE:-}"
if [[ -z "$WSGI_SOURCE" && -d /var/www ]]; then
    mapfile -t WSGI_CANDIDATES < <(find /var/www -maxdepth 1 -type f -name '*_pythonanywhere_com_wsgi.py' -print)
    if [[ ${#WSGI_CANDIDATES[@]} -eq 1 ]]; then
        WSGI_SOURCE="${WSGI_CANDIDATES[0]}"
    elif [[ ${#WSGI_CANDIDATES[@]} -gt 1 ]]; then
        fail "Mais de um WSGI foi encontrado; defina PRE_AUDIT_WSGI_FILE para o arquivo correto."
    fi
fi
[[ -n "$WSGI_SOURCE" && -f "$WSGI_SOURCE" && -r "$WSGI_SOURCE" ]] || {
    fail "WSGI privado não identificado; defina PRE_AUDIT_WSGI_FILE para preservar a configuração antes das migrations."
}
cp -p -- "$WSGI_SOURCE" "$WSGI_BACKUP"
chmod 600 "$WSGI_BACKUP"
printf 'Cópia privada do WSGI: %s\n' "$WSGI_BACKUP"

"$PYTHON" manage.py migrate --plan
"$PYTHON" manage.py migrate --noinput
"$PYTHON" manage.py collectstatic --noinput
"$PYTHON" manage.py check
"$PYTHON" manage.py showmigrations pre_auditoria
"$PYTHON" ops/verify_integrity.py --baseline "$BACKUP_PATH" --current "$DB_PATH"

"$PYTHON" - "$DB_PATH" "$BACKUP_PATH" "$MANIFEST_PATH" <<'PY'
import json
import sqlite3
import sys
from urllib.parse import quote

current_path, backup_path, manifest_path = sys.argv[1:]
with open(manifest_path, encoding="utf-8") as manifest:
    baseline = json.load(manifest)
with sqlite3.connect(f"file:{quote(backup_path)}?mode=ro", uri=True) as backup:
    if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise SystemExit("Backup deixou de passar no integrity_check.")
with sqlite3.connect(f"file:{quote(current_path)}?mode=ro", uri=True) as current:
    if current.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise SystemExit("Banco atual falhou no integrity_check.")
    if current.execute("PRAGMA foreign_key_check").fetchall():
        raise SystemExit("Banco atual contém violações de chave estrangeira.")
    for table, old_count in baseline.items():
        exists = current.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            raise SystemExit(f"Tabela anterior ausente após migration: {table}")
        new_count = current.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        if new_count < old_count:
            raise SystemExit(f"Contagem reduzida em {table}: {old_count} -> {new_count}")
print("Backup e banco atual íntegros; contagens existentes não diminuíram.")
PY

printf '\nDeploy concluído para %s.\n' "$REVISION"
printf 'Backup mantido para homologação: %s\n' "$BACKUP_PATH"
[[ ! -f "$MEDIA_ARCHIVE" ]] || printf 'Mídia mantida para homologação: %s\n' "$MEDIA_ARCHIVE"
[[ ! -f "$ENV_BACKUP" ]] || printf 'Configuração privada mantida: %s\n' "$ENV_BACKUP"
printf 'WSGI privado mantido: %s\n' "$WSGI_BACKUP"
printf 'Ação restante: recarregue a aplicação na aba Web do PythonAnywhere e faça smoke test autenticado.\n'
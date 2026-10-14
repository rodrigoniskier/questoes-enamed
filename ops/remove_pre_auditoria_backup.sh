#!/usr/bin/env bash
set -Eeuo pipefail

BACKUP_DIR="${PRE_AUDIT_BACKUP_DIR:-${HOME:?HOME precisa estar definido}/.private_backups/pre_auditoria}"
[[ -d "$BACKUP_DIR" ]] || { printf 'Diretório de backups não encontrado.\n' >&2; exit 1; }
[[ $# -eq 1 ]] || { printf 'Uso: bash ops/remove_pre_auditoria_backup.sh <nome-exato-do-backup.sqlite3>\n' >&2; exit 2; }
BACKUP_DIR="$(realpath "$BACKUP_DIR")"
BACKUP_NAME="$1"
[[ "$BACKUP_NAME" == pre_auditoria-*.sqlite3 && "$BACKUP_NAME" != */* ]] || {
    printf 'O nome precisa identificar exatamente um backup desta implantação.\n' >&2
    exit 2
}
BACKUP_PATH="$BACKUP_DIR/$BACKUP_NAME"
[[ -f "$BACKUP_PATH" && "$(realpath "$BACKUP_PATH")" == "$BACKUP_DIR/$BACKUP_NAME" ]] || {
    printf 'O backup indicado não existe diretamente no diretório privado.\n' >&2
    exit 1
}
printf 'Backup selecionado: %s\n' "$BACKUP_PATH"
read -r -p "Digite EXCLUIR $BACKUP_NAME para confirmar: " confirmation
[[ "$confirmation" == "EXCLUIR $BACKUP_NAME" ]] || { printf 'Cancelado; nada foi removido.\n'; exit 0; }
rm -- "$BACKUP_PATH"
backup_prefix="${BACKUP_PATH%.sqlite3}"
for associated in "$backup_prefix.counts.json" "$backup_prefix.env" "$backup_prefix.wsgi.py" "$backup_prefix-media.tar.gz"; do
    [[ ! -f "$associated" ]] || rm -- "$associated"
done
printf 'Backup selecionado removido após autorização explícita.\n'
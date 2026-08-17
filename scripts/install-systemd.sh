#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(pwd -P)"
SERVICE_USER="$(id -un)"
SERVICE_GROUP="$(id -gn)"
INTERVAL="5min"
BATCH_SIZE="4"
RUN_NOW="false"

usage() {
  printf '%s\n' \
    "Uso: $0 [opções]" \
    "" \
    "  --project-dir DIR   Raiz do mflab-knowledge-rag (padrão: diretório atual)" \
    "  --user USUARIO      Usuário Linux do serviço (padrão: usuário atual)" \
    "  --group GRUPO       Grupo Linux do serviço (padrão: grupo atual)" \
    "  --interval TEMPO    Intervalo systemd: 30s, 5min, 1h etc. (padrão: 5min)" \
    "  --batch-size N      Minibatch dos embeddings, 1 a 256 (padrão: 4)" \
    "  --run-now           Inicia uma indexação após instalar" \
    "  --help              Mostra esta ajuda"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --project-dir)
      PROJECT_DIR="${2:?--project-dir exige um diretório}"
      shift 2
      ;;
    --user)
      SERVICE_USER="${2:?--user exige um usuário}"
      shift 2
      ;;
    --group)
      SERVICE_GROUP="${2:?--group exige um grupo}"
      shift 2
      ;;
    --interval)
      INTERVAL="${2:?--interval exige um valor}"
      shift 2
      ;;
    --batch-size)
      BATCH_SIZE="${2:?--batch-size exige um valor}"
      shift 2
      ;;
    --run-now)
      RUN_NOW="true"
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      printf 'Opção desconhecida: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

PROJECT_DIR="$(realpath "$PROJECT_DIR")"

case "$PROJECT_DIR" in
  *$'\n'*|*$'\r'*|*\"*)
    printf 'O caminho do projeto contém caractere não suportado.\n' >&2
    exit 2
    ;;
esac

if ! [[ "$SERVICE_USER" =~ ^[a-zA-Z_][a-zA-Z0-9_-]*[$]?$ ]]; then
  printf 'Usuário inválido: %s\n' "$SERVICE_USER" >&2
  exit 2
fi
if ! [[ "$SERVICE_GROUP" =~ ^[a-zA-Z_][a-zA-Z0-9_-]*$ ]]; then
  printf 'Grupo inválido: %s\n' "$SERVICE_GROUP" >&2
  exit 2
fi
if ! [[ "$INTERVAL" =~ ^[1-9][0-9]*(s|min|h|d)$ ]]; then
  printf 'Intervalo inválido: %s (exemplos: 30s, 5min, 1h)\n' "$INTERVAL" >&2
  exit 2
fi
if ! [[ "$BATCH_SIZE" =~ ^[0-9]+$ ]] ||
   [ "$BATCH_SIZE" -lt 1 ] || [ "$BATCH_SIZE" -gt 256 ]; then
  printf 'Batch size deve estar entre 1 e 256.\n' >&2
  exit 2
fi

PYTHON="$PROJECT_DIR/.venv/bin/python"
CONFIG_FILE="$PROJECT_DIR/repositories.toml"
ENV_FILE="$PROJECT_DIR/.env"
STATE_DIR="$PROJECT_DIR/state"
SERVICE_TEMPLATE="$PROJECT_DIR/deploy/systemd/mflab-knowledge-index.service.in"
TIMER_TEMPLATE="$PROJECT_DIR/deploy/systemd/mflab-knowledge-index.timer.in"

for required in "$PYTHON" "$CONFIG_FILE" "$ENV_FILE" "$SERVICE_TEMPLATE" "$TIMER_TEMPLATE"; do
  if [ ! -e "$required" ]; then
    printf 'Arquivo obrigatório não encontrado: %s\n' "$required" >&2
    exit 1
  fi
done

if [ ! -x "$PYTHON" ]; then
  printf 'Python do ambiente não é executável: %s\n' "$PYTHON" >&2
  exit 1
fi
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  printf 'Usuário Linux não encontrado: %s\n' "$SERVICE_USER" >&2
  exit 1
fi
if ! getent group "$SERVICE_GROUP" >/dev/null 2>&1; then
  printf 'Grupo Linux não encontrado: %s\n' "$SERVICE_GROUP" >&2
  exit 1
fi

escape_sed_replacement() {
  printf '%s' "$1" | sed 's/[&|\\]/\\&/g'
}

TEMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf -- "$TEMP_DIR"
}
trap cleanup EXIT

SERVICE_OUTPUT="$TEMP_DIR/mflab-knowledge-index.service"
TIMER_OUTPUT="$TEMP_DIR/mflab-knowledge-index.timer"

sed \
  -e "s|@PROJECT_DIR@|$(escape_sed_replacement "$PROJECT_DIR")|g" \
  -e "s|@SERVICE_USER@|$(escape_sed_replacement "$SERVICE_USER")|g" \
  -e "s|@SERVICE_GROUP@|$(escape_sed_replacement "$SERVICE_GROUP")|g" \
  -e "s|@PYTHON@|$(escape_sed_replacement "$PYTHON")|g" \
  -e "s|@CONFIG_FILE@|$(escape_sed_replacement "$CONFIG_FILE")|g" \
  -e "s|@ENV_FILE@|$(escape_sed_replacement "$ENV_FILE")|g" \
  -e "s|@STATE_DIR@|$(escape_sed_replacement "$STATE_DIR")|g" \
  -e "s|@BATCH_SIZE@|$BATCH_SIZE|g" \
  "$SERVICE_TEMPLATE" >"$SERVICE_OUTPUT"

sed \
  -e "s|@INTERVAL@|$INTERVAL|g" \
  "$TIMER_TEMPLATE" >"$TIMER_OUTPUT"

"$PYTHON" -m mflab_knowledge run-scheduled --help >/dev/null
if command -v systemd-analyze >/dev/null 2>&1; then
  systemd-analyze verify "$SERVICE_OUTPUT" "$TIMER_OUTPUT"
fi

if [ "$(id -u)" -eq 0 ]; then
  SUDO=()
else
  SUDO=(sudo)
fi

"${SUDO[@]}" install -d -m 0700 -o "$SERVICE_USER" -g "$SERVICE_GROUP" "$STATE_DIR"
"${SUDO[@]}" chown "$SERVICE_USER:$SERVICE_GROUP" "$ENV_FILE"
"${SUDO[@]}" chmod 0600 "$ENV_FILE"
"${SUDO[@]}" install -m 0644 "$SERVICE_OUTPUT" /etc/systemd/system/mflab-knowledge-index.service
"${SUDO[@]}" install -m 0644 "$TIMER_OUTPUT" /etc/systemd/system/mflab-knowledge-index.timer
"${SUDO[@]}" systemctl daemon-reload
"${SUDO[@]}" systemctl enable --now mflab-knowledge-index.timer

if [ "$RUN_NOW" = "true" ]; then
  "${SUDO[@]}" systemctl start mflab-knowledge-index.service
fi

printf '\nServiço instalado com sucesso.\n'
printf 'Projeto:    %s\n' "$PROJECT_DIR"
printf 'Usuário:    %s:%s\n' "$SERVICE_USER" "$SERVICE_GROUP"
printf 'Intervalo:  %s após o término de cada execução\n' "$INTERVAL"
printf 'Estado:     %s/last-run.json\n' "$STATE_DIR"
printf '\nPróxima execução:\n'
"${SUDO[@]}" systemctl list-timers mflab-knowledge-index.timer --no-pager

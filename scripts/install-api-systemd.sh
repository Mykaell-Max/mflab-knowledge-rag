#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(pwd -P)"
SERVICE_USER="$(id -un)"
SERVICE_GROUP="$(id -gn)"
PORT="8765"

usage() {
  printf '%s\n' \
    "Uso: $0 [opções]" \
    "" \
    "  --project-dir DIR   Raiz do projeto (padrão: diretório atual)" \
    "  --user USUARIO      Usuário Linux do serviço (padrão: atual)" \
    "  --group GRUPO       Grupo Linux do serviço (padrão: atual)" \
    "  --port PORTA        Porta loopback, 1024 a 65535 (padrão: 8765)" \
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
    --port)
      PORT="${2:?--port exige um valor}"
      shift 2
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
if ! [[ "$PORT" =~ ^[0-9]+$ ]] || [ "$PORT" -lt 1024 ] || [ "$PORT" -gt 65535 ]; then
  printf 'Porta deve estar entre 1024 e 65535.\n' >&2
  exit 2
fi

PYTHON="$PROJECT_DIR/.venv/bin/python"
ENV_FILE="$PROJECT_DIR/.env"
STATE_DIR="$PROJECT_DIR/state"
SERVICE_TEMPLATE="$PROJECT_DIR/deploy/systemd/mflab-knowledge-api.service.in"

for required in "$PYTHON" "$ENV_FILE" "$SERVICE_TEMPLATE"; do
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

SERVICE_OUTPUT="$TEMP_DIR/mflab-knowledge-api.service"

sed \
  -e "s|@PROJECT_DIR@|$(escape_sed_replacement "$PROJECT_DIR")|g" \
  -e "s|@SERVICE_USER@|$(escape_sed_replacement "$SERVICE_USER")|g" \
  -e "s|@SERVICE_GROUP@|$(escape_sed_replacement "$SERVICE_GROUP")|g" \
  -e "s|@PYTHON@|$(escape_sed_replacement "$PYTHON")|g" \
  -e "s|@ENV_FILE@|$(escape_sed_replacement "$ENV_FILE")|g" \
  -e "s|@STATE_DIR@|$(escape_sed_replacement "$STATE_DIR")|g" \
  -e "s|@PORT@|$PORT|g" \
  "$SERVICE_TEMPLATE" >"$SERVICE_OUTPUT"

"$PYTHON" -c 'import fastapi, pydantic, uvicorn'
"$PYTHON" -m mflab_knowledge serve --help >/dev/null
if command -v systemd-analyze >/dev/null 2>&1; then
  systemd-analyze verify "$SERVICE_OUTPUT"
fi

if [ "$(id -u)" -eq 0 ]; then
  SUDO=()
else
  SUDO=(sudo)
fi

"${SUDO[@]}" install -d -m 0700 -o "$SERVICE_USER" -g "$SERVICE_GROUP" "$STATE_DIR"
"${SUDO[@]}" chown "$SERVICE_USER:$SERVICE_GROUP" "$ENV_FILE"
"${SUDO[@]}" chmod 0600 "$ENV_FILE"
"${SUDO[@]}" install -m 0644 "$SERVICE_OUTPUT" /etc/systemd/system/mflab-knowledge-api.service
"${SUDO[@]}" systemctl daemon-reload
"${SUDO[@]}" systemctl enable mflab-knowledge-api.service
if ! "${SUDO[@]}" systemctl restart mflab-knowledge-api.service; then
  printf '\nNão foi possível iniciar a API. Estado e logs recentes:\n' >&2
  "${SUDO[@]}" systemctl status mflab-knowledge-api.service --no-pager >&2 || true
  "${SUDO[@]}" journalctl -u mflab-knowledge-api.service -n 40 --no-pager >&2 || true
  exit 1
fi

HEALTH_URL="http://127.0.0.1:$PORT/health"
HEALTHY="false"
for _attempt in $(seq 1 30); do
  if "$PYTHON" -c \
    'import json, sys, urllib.request; value=json.load(urllib.request.urlopen(sys.argv[1], timeout=2)); raise SystemExit(0 if value.get("status") == "ok" else 1)' \
    "$HEALTH_URL" >/dev/null 2>&1; then
    HEALTHY="true"
    break
  fi
  if ! "${SUDO[@]}" systemctl is-active --quiet mflab-knowledge-api.service; then
    break
  fi
  sleep 1
done

if [ "$HEALTHY" != "true" ]; then
  printf '\nA API não ficou saudável. Estado e logs recentes:\n' >&2
  "${SUDO[@]}" systemctl status mflab-knowledge-api.service --no-pager >&2 || true
  "${SUDO[@]}" journalctl -u mflab-knowledge-api.service -n 40 --no-pager >&2 || true
  exit 1
fi

printf '\nAPI instalada com sucesso.\n'
printf 'Projeto:  %s\n' "$PROJECT_DIR"
printf 'Usuário:  %s:%s\n' "$SERVICE_USER" "$SERVICE_GROUP"
printf 'Endpoint: http://127.0.0.1:%s\n' "$PORT"
printf 'Docs:     http://127.0.0.1:%s/docs\n' "$PORT"
printf '\nEstado do serviço:\n'
"${SUDO[@]}" systemctl status mflab-knowledge-api.service --no-pager

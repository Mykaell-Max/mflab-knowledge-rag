#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(pwd -P)"
SERVICE_USER="$(id -un)"
SERVICE_GROUP="$(id -gn)"
VLLM_PYTHON=""
MODEL_PATH=""
SERVED_MODEL=""
PORT="8000"
MAX_MODEL_LEN="8192"
GPU_MEMORY_UTILIZATION="0.75"
MAX_NUM_SEQS="2"
CHAT_TEMPLATE_KWARGS="{}"

if [ -t 1 ] && [ "${NO_COLOR:-}" = "" ]; then
  COLOR_INFO=$'\033[1;36m'
  COLOR_OK=$'\033[1;32m'
  COLOR_ERROR=$'\033[1;31m'
  COLOR_RESET=$'\033[0m'
else
  COLOR_INFO=""
  COLOR_OK=""
  COLOR_ERROR=""
  COLOR_RESET=""
fi

info() {
  printf '%s[mflab:INFO]%s %s\n' "$COLOR_INFO" "$COLOR_RESET" "$*"
}

ok() {
  printf '%s[mflab:OK]%s %s\n' "$COLOR_OK" "$COLOR_RESET" "$*"
}

fail() {
  printf '%s[mflab:ERRO]%s %s\n' "$COLOR_ERROR" "$COLOR_RESET" "$*" >&2
  exit 1
}

usage() {
  printf '%s\n' \
    "Uso: $0 --vllm-python ARQUIVO --model-path DIR --served-model-name NOME [opções]" \
    "" \
    "  --project-dir DIR              Raiz do projeto (padrão: diretório atual)" \
    "  --user USUARIO                 Usuário Linux do serviço (padrão: atual)" \
    "  --group GRUPO                  Grupo Linux do serviço (padrão: atual)" \
    "  --vllm-python ARQUIVO          Python do ambiente que contém vLLM" \
    "  --model-path DIR               Snapshot local completo do modelo" \
    "  --served-model-name NOME       Identificador publicado pela API local" \
    "  --port PORTA                   Porta loopback (padrão: 8000)" \
    "  --max-model-len TOKENS         Contexto máximo (padrão: 8192)" \
    "  --gpu-memory-utilization VALOR Fração de VRAM, >0 e <=1 (padrão: 0.75)" \
    "  --max-num-seqs NUMERO          Requisições simultâneas (padrão: 2)" \
    "  --chat-template-kwargs JSON    Argumentos JSON do template (padrão: {})" \
    "  --help                         Mostra esta ajuda"
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
    --vllm-python)
      VLLM_PYTHON="${2:?--vllm-python exige um arquivo}"
      shift 2
      ;;
    --model-path)
      MODEL_PATH="${2:?--model-path exige um diretório}"
      shift 2
      ;;
    --served-model-name)
      SERVED_MODEL="${2:?--served-model-name exige um nome}"
      shift 2
      ;;
    --port)
      PORT="${2:?--port exige um valor}"
      shift 2
      ;;
    --max-model-len)
      MAX_MODEL_LEN="${2:?--max-model-len exige um valor}"
      shift 2
      ;;
    --gpu-memory-utilization)
      GPU_MEMORY_UTILIZATION="${2:?--gpu-memory-utilization exige um valor}"
      shift 2
      ;;
    --max-num-seqs)
      MAX_NUM_SEQS="${2:?--max-num-seqs exige um valor}"
      shift 2
      ;;
    --chat-template-kwargs)
      CHAT_TEMPLATE_KWARGS="${2:?--chat-template-kwargs exige JSON}"
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

[ -n "$VLLM_PYTHON" ] || fail "Informe --vllm-python."
[ -n "$MODEL_PATH" ] || fail "Informe --model-path."
[ -n "$SERVED_MODEL" ] || fail "Informe --served-model-name."

PROJECT_DIR="$(realpath "$PROJECT_DIR")"
# Preserve the virtualenv entry point. Resolving its final symlink can select
# the base interpreter and silently discard the vLLM environment.
VLLM_PYTHON="$(realpath -s "$VLLM_PYTHON")"
MODEL_PATH="$(realpath "$MODEL_PATH")"

reject_unsafe_path() {
  local label="$1"
  local value="$2"
  case "$value" in
    *$'\n'*|*$'\r'*|*'"'*|*'%'*|*'$'*|*'\'*)
      fail "$label contém caractere não suportado."
      ;;
  esac
}

reject_unsafe_json() {
  local value="$1"
  case "$value" in
    *$'\n'*|*$'\r'*|*"'"*|*'%'*|*'$'*)
      fail "JSON do template contém caractere não suportado."
      ;;
  esac
}

reject_unsafe_path "Caminho do projeto" "$PROJECT_DIR"
reject_unsafe_path "Python do vLLM" "$VLLM_PYTHON"
reject_unsafe_path "Caminho do modelo" "$MODEL_PATH"
reject_unsafe_json "$CHAT_TEMPLATE_KWARGS"

if ! [[ "$SERVICE_USER" =~ ^[a-zA-Z_][a-zA-Z0-9_-]*[$]?$ ]]; then
  fail "Usuário inválido: $SERVICE_USER"
fi
if ! [[ "$SERVICE_GROUP" =~ ^[a-zA-Z_][a-zA-Z0-9_-]*$ ]]; then
  fail "Grupo inválido: $SERVICE_GROUP"
fi
if ! [[ "$SERVED_MODEL" =~ ^[a-zA-Z0-9_.:/-]+$ ]]; then
  fail "Nome publicado contém caracteres inválidos: $SERVED_MODEL"
fi
if ! [[ "$PORT" =~ ^[0-9]+$ ]] || [ "$PORT" -lt 1024 ] || [ "$PORT" -gt 65535 ]; then
  fail "Porta deve estar entre 1024 e 65535."
fi
for value in "$MAX_MODEL_LEN" "$MAX_NUM_SEQS"; do
  if ! [[ "$value" =~ ^[1-9][0-9]*$ ]]; then
    fail "Contagens devem ser inteiros positivos."
  fi
done

SERVICE_TEMPLATE="$PROJECT_DIR/deploy/systemd/mflab-knowledge-llm.service.in"
for required in "$VLLM_PYTHON" "$MODEL_PATH/config.json" "$SERVICE_TEMPLATE"; do
  [ -e "$required" ] || fail "Arquivo obrigatório não encontrado: $required"
done
[ -x "$VLLM_PYTHON" ] || fail "Python do vLLM não é executável: $VLLM_PYTHON"
id "$SERVICE_USER" >/dev/null 2>&1 || fail "Usuário Linux não encontrado: $SERVICE_USER"
getent group "$SERVICE_GROUP" >/dev/null 2>&1 || fail "Grupo Linux não encontrado: $SERVICE_GROUP"

"$VLLM_PYTHON" -c \
  'import json, sys, vllm; value=json.loads(sys.argv[1]); assert isinstance(value, dict)' \
  "$CHAT_TEMPLATE_KWARGS" || fail "--chat-template-kwargs deve ser um objeto JSON."
"$VLLM_PYTHON" -c \
  'import sys; value=float(sys.argv[1]); raise SystemExit(0 if 0 < value <= 1 else 1)' \
  "$GPU_MEMORY_UTILIZATION" || fail "--gpu-memory-utilization deve ser >0 e <=1."
"$VLLM_PYTHON" -m vllm.entrypoints.openai.api_server --help >/dev/null

escape_sed_replacement() {
  printf '%s' "$1" | sed 's/[&|\\]/\\&/g'
}

TEMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf -- "$TEMP_DIR"
}
trap cleanup EXIT

SERVICE_OUTPUT="$TEMP_DIR/mflab-knowledge-llm.service"

info "Renderizando unidade systemd genérica"
sed \
  -e "s|@PROJECT_DIR@|$(escape_sed_replacement "$PROJECT_DIR")|g" \
  -e "s|@SERVICE_USER@|$(escape_sed_replacement "$SERVICE_USER")|g" \
  -e "s|@SERVICE_GROUP@|$(escape_sed_replacement "$SERVICE_GROUP")|g" \
  -e "s|@VLLM_PYTHON@|$(escape_sed_replacement "$VLLM_PYTHON")|g" \
  -e "s|@MODEL_PATH@|$(escape_sed_replacement "$MODEL_PATH")|g" \
  -e "s|@SERVED_MODEL@|$(escape_sed_replacement "$SERVED_MODEL")|g" \
  -e "s|@PORT@|$PORT|g" \
  -e "s|@MAX_MODEL_LEN@|$MAX_MODEL_LEN|g" \
  -e "s|@GPU_MEMORY_UTILIZATION@|$GPU_MEMORY_UTILIZATION|g" \
  -e "s|@MAX_NUM_SEQS@|$MAX_NUM_SEQS|g" \
  -e "s|@CHAT_TEMPLATE_KWARGS@|$(escape_sed_replacement "$CHAT_TEMPLATE_KWARGS")|g" \
  "$SERVICE_TEMPLATE" >"$SERVICE_OUTPUT"

if grep -q '@[A-Z_][A-Z_]*@' "$SERVICE_OUTPUT"; then
  fail "A unidade renderizada ainda contém placeholders."
fi
if command -v systemd-analyze >/dev/null 2>&1; then
  systemd-analyze verify "$SERVICE_OUTPUT"
fi

if [ "$(id -u)" -eq 0 ]; then
  SUDO=()
else
  SUDO=(sudo)
fi

info "Instalando mflab-knowledge-llm.service"
"${SUDO[@]}" install -m 0644 \
  "$SERVICE_OUTPUT" /etc/systemd/system/mflab-knowledge-llm.service
"${SUDO[@]}" systemctl daemon-reload
"${SUDO[@]}" systemctl enable mflab-knowledge-llm.service
if ! "${SUDO[@]}" systemctl restart mflab-knowledge-llm.service; then
  printf '\n' >&2
  "${SUDO[@]}" systemctl status mflab-knowledge-llm.service --no-pager >&2 || true
  "${SUDO[@]}" journalctl -u mflab-knowledge-llm.service -n 80 --no-pager >&2 || true
  fail "Não foi possível iniciar o servidor LLM."
fi

HEALTH_URL="http://127.0.0.1:$PORT/health"
HEALTHY="false"
for attempt in $(seq 1 180); do
  if "$VLLM_PYTHON" -c \
    'import sys, urllib.request; urllib.request.urlopen(sys.argv[1], timeout=2)' \
    "$HEALTH_URL" >/dev/null 2>&1; then
    HEALTHY="true"
    printf '\r%80s\r' ''
    break
  fi
  if ! "${SUDO[@]}" systemctl is-active --quiet mflab-knowledge-llm.service; then
    break
  fi
  printf '\r%s[mflab:INFO]%s Aguardando modelo local: %3ss/180s' \
    "$COLOR_INFO" "$COLOR_RESET" "$attempt"
  sleep 1
done

if [ "$HEALTHY" != "true" ]; then
  printf '\n' >&2
  "${SUDO[@]}" systemctl status mflab-knowledge-llm.service --no-pager >&2 || true
  "${SUDO[@]}" journalctl -u mflab-knowledge-llm.service -n 80 --no-pager >&2 || true
  fail "O servidor LLM não ficou saudável."
fi

ok "Servidor LLM local instalado e saudável"
printf 'Modelo:   %s\n' "$SERVED_MODEL"
printf 'Snapshot: %s\n' "$MODEL_PATH"
printf 'Endpoint: http://127.0.0.1:%s/v1\n' "$PORT"
printf 'Métricas: http://127.0.0.1:%s/metrics\n' "$PORT"
printf '\nEstado do serviço:\n'
"${SUDO[@]}" systemctl status mflab-knowledge-llm.service --no-pager

#!/usr/bin/env bash
set -uo pipefail

PROJECT_DIR="$(pwd)"
SUITE=""
SUMMARY=""
BRANCH=""
API_BASE_URL="http://127.0.0.1:8765"
API_SERVICE="mflab-knowledge-api.service"
INDEX_SERVICE="mflab-knowledge-index.service"
INDEX_TIMER="mflab-knowledge-index.timer"
ALLOWED_ACCESS=(public lab)

usage() {
  cat <<'EOF'
Uso: scripts/validate-lab-investigation.sh \
  --suite ARQUIVO.json \
  --summary semantic-map.generated.json \
  [--branch BRANCH] [--project-dir DIRETORIO]

Executa testes, valida o grafo persistido, reinicia a API e roda uma suíte
de investigação. A automação de indexação é restaurada mesmo após falhas.
EOF
}

while (($#)); do
  case "$1" in
    --project-dir)
      PROJECT_DIR="$2"
      shift 2
      ;;
    --suite)
      SUITE="$2"
      shift 2
      ;;
    --summary)
      SUMMARY="$2"
      shift 2
      ;;
    --branch)
      BRANCH="$2"
      shift 2
      ;;
    --api-base-url)
      API_BASE_URL="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      printf '[ERRO] Opção desconhecida: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$SUITE" || -z "$SUMMARY" ]]; then
  printf '[ERRO] --suite e --summary são obrigatórios.\n' >&2
  usage >&2
  exit 2
fi

PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)" || exit 2
cd "$PROJECT_DIR" || exit 2

if [[ "$SUITE" != /* ]]; then
  SUITE="$PROJECT_DIR/$SUITE"
fi
if [[ "$SUMMARY" != /* ]]; then
  SUMMARY="$PROJECT_DIR/$SUMMARY"
fi

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
SUITE_NAME="$(basename "${SUITE%.json}")"
LOG="$PROJECT_DIR/logs/${SUITE_NAME}-$TIMESTAMP.log"
REPORT="$PROJECT_DIR/data/${SUITE_NAME}-$TIMESTAMP.generated.json"

mkdir -p "$PROJECT_DIR/logs" "$PROJECT_DIR/data"
USE_COLOR=0
if [[ -t 1 ]]; then
  USE_COLOR=1
fi
exec > >(tee -a "$LOG") 2>&1

if [[ "$USE_COLOR" -eq 1 ]]; then
  GREEN=$'\033[1;32m'
  YELLOW=$'\033[1;33m'
  RED=$'\033[1;31m'
  BLUE=$'\033[1;34m'
  CYAN=$'\033[1;36m'
  RESET=$'\033[0m'
else
  GREEN=""
  YELLOW=""
  RED=""
  BLUE=""
  CYAN=""
  RESET=""
fi

section() {
  printf '\n%s========== %s ==========%s\n' "$BLUE" "$1" "$RESET"
}

ok() {
  printf '%s[OK] %s%s\n' "$GREEN" "$1" "$RESET"
}

warn() {
  printf '%s[AVISO] %s%s\n' "$YELLOW" "$1" "$RESET"
}

error() {
  printf '%s[ERRO] %s%s\n' "$RED" "$1" "$RESET"
}

RESTORED=0

restore_timer() {
  if [[ "$RESTORED" -eq 1 ]]; then
    return
  fi
  section "RESTAURANDO INDEXAÇÃO AUTOMÁTICA"
  if sudo systemctl enable --now "$INDEX_TIMER"; then
    systemctl list-timers "$INDEX_TIMER" --no-pager || true
    ok "Timer automático restaurado."
  else
    error "Não foi possível restaurar o timer automático."
  fi
  RESTORED=1
}

trap restore_timer EXIT

run_validation() {
  section "VERSÃO EM TESTE"
  git log -3 --oneline

  if [[ ! -f "$SUITE" ]]; then
    error "Suíte não encontrada: $SUITE"
    return 2
  fi
  if [[ ! -f "$SUMMARY" ]]; then
    error "Mapa semântico não encontrado: $SUMMARY"
    return 2
  fi

  section "ATUALIZANDO PACOTE"
  if ! .venv/bin/python -m pip install -e .; then
    error "Falha ao instalar o pacote."
    return 2
  fi

  local installed_version
  installed_version="$(
    .venv/bin/python -c \
      'from importlib.metadata import version; print(version("mflab-knowledge-rag"))'
  )"
  printf 'Versão instalada: %s\n' "$installed_version"

  section "TESTES"
  if ! PYTHONPATH=src .venv/bin/python -m unittest discover -s tests; then
    error "Algum teste automatizado falhou."
    return 2
  fi
  ok "Testes aprovados."

  section "PAUSANDO AUTOMAÇÃO DURANTE O TESTE"
  if ! sudo -v; then
    error "Não foi possível autenticar o sudo."
    return 2
  fi
  if ! sudo systemctl stop "$INDEX_TIMER"; then
    error "Não foi possível pausar o timer."
    return 2
  fi

  local waited=0
  printf '%s[mflab:INFO] Aguardando eventual indexação em andamento%s\n' \
    "$CYAN" "$RESET"
  while systemctl is-active --quiet "$INDEX_SERVICE"; do
    printf '\r%s[mflab:PROGRESSO] Indexador ainda ativo: %ds%s' \
      "$CYAN" "$waited" "$RESET"
    sleep 5
    waited=$((waited + 5))
    if [[ "$waited" -ge 600 ]]; then
      printf '\n'
      error "O indexador não liberou os recursos em dez minutos."
      return 2
    fi
  done
  printf '\r%-80s\r' ' '
  ok "GPU disponível para a avaliação."

  section "VALIDANDO GRAFO PERSISTIDO"
  local graph_args=(
    --summary "$SUMMARY"
  )
  if [[ -n "$BRANCH" ]]; then
    graph_args+=(--branch "$BRANCH")
  fi
  local access
  for access in "${ALLOWED_ACCESS[@]}"; do
    graph_args+=(--allow-access "$access")
  done
  if ! .venv/bin/python scripts/validate-call-graph.py "${graph_args[@]}"; then
    error "A travessia do grafo no PostgreSQL falhou."
    return 2
  fi

  section "REINICIANDO API"
  if ! sudo systemctl restart "$API_SERVICE"; then
    error "Não foi possível reiniciar a API."
    return 2
  fi

  local health=""
  local attempt
  for attempt in $(seq 1 60); do
    if health="$(curl -fsS "$API_BASE_URL/health" 2>/dev/null)"; then
      break
    fi
    printf '\r%s[mflab:INFO] Aguardando API: %d/60%s' \
      "$CYAN" "$attempt" "$RESET"
    sleep 2
  done
  printf '\r%-80s\r' ' '
  if [[ -z "$health" ]]; then
    error "A API não respondeu ao health check."
    sudo systemctl status "$API_SERVICE" --no-pager || true
    return 2
  fi
  printf '%s\n' "$health" | .venv/bin/python -m json.tool

  local api_version
  api_version="$(
    printf '%s' "$health" |
      .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["version"])'
  )"
  if [[ "$api_version" != "$installed_version" ]]; then
    error "A API respondeu com $api_version; o pacote instalado é $installed_version."
    return 2
  fi
  ok "API $api_version saudável."

  section "AVALIAÇÃO REAL DA INVESTIGAÇÃO"
  printf '%sEsta etapa pode levar vários minutos.%s\n' "$CYAN" "$RESET"
  .venv/bin/python -m mflab_knowledge api-evaluate \
    --suite "$SUITE" \
    --api-base-url "$API_BASE_URL" \
    --timeout-seconds 720 \
    --output "$REPORT" \
    --color always
  local evaluation_status=$?

  if [[ ! -f "$REPORT" ]]; then
    error "A avaliação não produziu o relatório esperado."
    return 2
  fi

  section "RESUMO DA AVALIAÇÃO"
  if ! .venv/bin/python scripts/summarize-investigation.py \
    --suite-report "$REPORT"; then
    error "Não foi possível resumir o relatório."
    return 2
  fi

  if [[ "$evaluation_status" -eq 0 ]]; then
    ok "A suíte de investigação passou integralmente."
    return 0
  fi
  if [[ "$evaluation_status" -eq 1 ]]; then
    warn "A infraestrutura passou, mas alguma expectativa científica falhou."
    return 1
  fi
  error "A avaliação encontrou uma falha operacional."
  return 2
}

run_validation
STATUS=$?

restore_timer
trap - EXIT

section "RESULTADO FINAL"
if [[ "$STATUS" -eq 0 ]]; then
  ok "Grafo e investigação validados."
elif [[ "$STATUS" -eq 1 ]]; then
  warn "O teste terminou com achados de qualidade para o próximo ajuste."
else
  error "A validação encontrou uma falha operacional."
fi
printf 'Relatório: %s\n' "$REPORT"
printf 'Log completo: %s\n' "$LOG"
printf 'Status do bloco: %s\n' "$STATUS"
printf '\nPressione Enter depois de copiar o resultado...'
read -r _ </dev/tty || true

# O chamador recebe sempre zero para que uma janela de terminal não seja fechada
# por wrappers que tratam qualquer status diferente como falha fatal. O status
# real permanece explícito no resumo e no log.
exit 0

#!/usr/bin/env bash
# Deploy or update LullabAI Twins (Edge Docker stack + Web PWA) on a Linux host.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
EDGE_DIR="$SCRIPT_DIR"
WEB_DIR="$REPO_ROOT/web"

MODE="auto"        # auto | gcp | local
SKIP_WEB=false
SKIP_PULL=true
WEB_TARGET_DIR="${LULLABAI_WEB_DIR:-}"
EDGE_HEALTH_URL="http://127.0.0.1:8095/health"

usage() {
  cat <<'EOF'
Usage: bash edge/deploy_linux.sh [options]

Options:
  --gcp              Require GCP mode (docker-compose.gcp.yml + ADC file)
  --local            Force local-only Docker compose (no GCP mount)
  --pull             Run `git pull --ff-only` before building
  --skip-web         Skip building the Web PWA (`web/dist`)
  --web-dir <path>   Copy built `web/dist/` into <path> (e.g. /srv/lullabai)
  -h, --help         Show this help message
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --gcp) MODE="gcp"; shift ;;
    --local) MODE="local"; shift ;;
    --pull) SKIP_PULL=false; shift ;;
    --skip-web) SKIP_WEB=true; shift ;;
    --web-dir)
      [[ $# -ge 2 ]] || { echo "Missing value for --web-dir" >&2; exit 1; }
      WEB_TARGET_DIR="$2"
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
  esac
done

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Error: required command '$1' not found." >&2
    exit 1
  fi
}

require_cmd docker
require_cmd openssl
if ! docker compose version >/dev/null 2>&1; then
  echo "Error: 'docker compose' plugin is required." >&2
  exit 1
fi

if [[ "$SKIP_PULL" == false ]]; then
  require_cmd git
  echo "[1/5] Updating repository..."
  git -C "$REPO_ROOT" pull --ff-only
else
  echo "[1/5] Using current working tree at $REPO_ROOT"
fi

ENV_FILE="$EDGE_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  cp "$EDGE_DIR/.env.example" "$ENV_FILE"
  echo "Created $ENV_FILE from .env.example." >&2
  echo "Edit $ENV_FILE to set BABY_1_MAIN_RTSP, BABY_1_SUB_RTSP, WEB_PUSH_SUBJECT, and WEB_PUSH_SETUP_CODE, then re-run." >&2
  exit 1
fi

read_env_var() {
  local key="$1"
  local line
  line="$(grep -E "^[[:space:]]*${key}=" "$ENV_FILE" | tail -n 1 || true)"
  line="${line#*=}"
  line="${line%\"}"
  line="${line#\"}"
  line="${line%\'}"
  line="${line#\'}"
  printf '%s' "$line"
}

echo "[2/5] Checking secrets and directories..."
mkdir -p "$EDGE_DIR/local_events" "$EDGE_DIR/push-secrets"
if [[ ! -e "$EDGE_DIR/push-secrets/vapid.pem" ]]; then
  (
    umask 077
    openssl ecparam -name prime256v1 -genkey -noout -out "$EDGE_DIR/push-secrets/vapid.pem"
  )
  echo "  Generated new VAPID key at edge/push-secrets/vapid.pem"
else
  echo "  Keeping existing VAPID key at edge/push-secrets/vapid.pem"
fi

SETUP_CODE="$(read_env_var WEB_PUSH_SETUP_CODE)"
if [[ -z "$SETUP_CODE" ]]; then
  echo "  Warning: WEB_PUSH_SETUP_CODE is not set in edge/.env."
  echo "  Generate one with: openssl rand -hex 24"
fi

ADC_SETTING="$(read_env_var LULLABAI_GCP_ADC_FILE)"
ADC_PATH="${ADC_SETTING:-$EDGE_DIR/cloud-secrets/adc.json}"
if [[ "$ADC_PATH" != /* ]]; then
  ADC_PATH="$EDGE_DIR/$ADC_PATH"
fi

USE_GCP=false
if [[ "$MODE" == "gcp" ]]; then
  if [[ ! -f "$ADC_PATH" ]]; then
    echo "Error: GCP mode requested, but ADC file not found at: $ADC_PATH" >&2
    echo "Create it on this Linux host with:" >&2
    cat >&2 <<'EOF'
  cd edge
  (
    umask 077
    mkdir -p cloud-secrets
    export CLOUDSDK_CONFIG="$(mktemp -d)"
    trap 'rm -rf "$CLOUDSDK_CONFIG"' EXIT
    gcloud auth application-default login --no-browser \
      --disable-quota-project \
      --impersonate-service-account=lullabai-edge@twins-monitor.iam.gserviceaccount.com
    cp "$CLOUDSDK_CONFIG/application_default_credentials.json" cloud-secrets/adc.json
  )
EOF
    exit 1
  fi
  USE_GCP=true
elif [[ "$MODE" == "auto" && -f "$ADC_PATH" ]]; then
  USE_GCP=true
fi

echo "[3/5] Building and starting Edge Docker stack (GCP=$USE_GCP)..."
COMPOSE_FILES=(-f "$EDGE_DIR/docker-compose.yml")
if [[ "$USE_GCP" == true ]]; then
  COMPOSE_FILES+=(-f "$EDGE_DIR/docker-compose.gcp.yml")
fi
(
  cd "$EDGE_DIR"
  docker compose "${COMPOSE_FILES[@]}" up -d --build
)

if [[ "$SKIP_WEB" == false ]]; then
  require_cmd npm
  echo "[4/5] Building Web PWA..."
  (
    cd "$WEB_DIR"
    npm ci
    npm run build
  )
  if [[ -n "$WEB_TARGET_DIR" ]]; then
    echo "  Publishing web/dist/ to $WEB_TARGET_DIR..."
    mkdir -p "$WEB_TARGET_DIR"
    if command -v rsync >/dev/null 2>&1; then
      rsync -a --delete "$WEB_DIR/dist/" "$WEB_TARGET_DIR/"
    else
      find "$WEB_TARGET_DIR" -mindepth 1 -delete
      cp -a "$WEB_DIR/dist/." "$WEB_TARGET_DIR/"
    fi
  elif [[ -d "/srv/lullabai" && -w "/srv/lullabai" ]]; then
    echo "  Publishing web/dist/ to /srv/lullabai..."
    if command -v rsync >/dev/null 2>&1; then
      rsync -a --delete "$WEB_DIR/dist/" "/srv/lullabai/"
    else
      find "/srv/lullabai" -mindepth 1 -delete
      cp -a "$WEB_DIR/dist/." "/srv/lullabai/"
    fi
  else
    echo "  Built web/dist/ (pass --web-dir /srv/lullabai to publish to your reverse proxy root)."
  fi
else
  echo "[4/5] Skipped Web PWA build (--skip-web)."
fi

echo "[5/5] Verifying Edge detector health..."
HEALTH_OK=false
if command -v curl >/dev/null 2>&1; then
  for _ in $(seq 1 20); do
    if response="$(curl -fsS "$EDGE_HEALTH_URL" 2>/dev/null)"; then
      if [[ "$response" == *'"status": "ok"'* || "$response" == *'"status":"ok"'* ]]; then
        HEALTH_OK=true
        break
      fi
    fi
    sleep 1.5
  done
fi

if [[ "$HEALTH_OK" == true ]]; then
  echo "Deploy complete! Edge API is healthy at $EDGE_HEALTH_URL (GCP=$USE_GCP)."
else
  echo "Containers started, but $EDGE_HEALTH_URL did not report ok yet. Check: docker compose -f edge/docker-compose.yml logs --tail 40 edge-detector" >&2
fi

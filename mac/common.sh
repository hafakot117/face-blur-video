# Shared settings for install.sh and manage.sh (sourced, not run directly).

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.faceblur.server"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DATA="${FACEBLUR_HOME:-$HOME/FaceBlur}"
LOG="$DATA/logs/server.log"
PORT="${FACEBLUR_PORT:-8765}"
PY="$REPO/venv/bin/python"
DOMAIN="gui/$(id -u)"

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
ok()    { printf '\033[32m✓\033[0m %s\n' "$*"; }
warn()  { printf '\033[33m!\033[0m %s\n' "$*"; }
fail()  { printf '\033[31m✗ %s\033[0m\n' "$*"; exit 1; }
ask()   { local a; read -r -p "$1 [y/N] " a; [[ "$a" =~ ^[Yy] ]]; }

# Tailscale CLI: Homebrew/standalone install, or the one inside the app.
find_tailscale() {
  if command -v tailscale >/dev/null 2>&1; then command -v tailscale; return 0; fi
  for p in /Applications/Tailscale.app/Contents/MacOS/Tailscale /usr/local/bin/tailscale /opt/homebrew/bin/tailscale; do
    [ -x "$p" ] && { echo "$p"; return 0; }
  done
  return 1
}

# https://<machine>.<tailnet>.ts.net for this Mac, or nothing.
tailscale_url() {
  local ts; ts="$(find_tailscale)" || return 1
  "$ts" status --json 2>/dev/null | "$PY" -c '
import json, sys
try:
    name = json.load(sys.stdin)["Self"]["DNSName"].rstrip(".")
    print("https://" + name) if name else sys.exit(1)
except Exception:
    sys.exit(1)'
}

health() { curl -fsS --max-time 3 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; }

wait_healthy() {
  for _ in $(seq 1 30); do health && return 0; sleep 1; done
  return 1
}

service_start() {
  launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
  launchctl bootstrap "$DOMAIN" "$PLIST"
  launchctl enable "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
  launchctl kickstart -k "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
}

service_stop() { launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true; }

setup_tailscale_serve() {
  local ts url
  if ! ts="$(find_tailscale)"; then
    warn "Tailscale לא מותקן."
    echo "   1. התקן מ-App Store (חפש Tailscale) או מ-https://tailscale.com/download/mac"
    echo "   2. פתח אותו והתחבר (אותו חשבון שתשתמש בו בטלפון)"
    echo "   3. הרץ: ./mac/manage.sh tailscale"
    return 1
  fi
  if ! "$ts" status >/dev/null 2>&1; then
    warn "Tailscale מותקן אבל לא מחובר. פתח את אפליקציית Tailscale והתחבר, ואז הרץ: ./mac/manage.sh tailscale"
    return 1
  fi
  echo "מגדיר Tailscale Serve (HTTPS פרטי, רק למכשירים שלך)…"
  echo "אם יופיע קישור — פתח אותו ואשר הפעלת HTTPS, וההגדרה תמשיך לבד."
  if "$ts" serve --bg "http://127.0.0.1:$PORT"; then
    url="$(tailscale_url)" && ok "כתובת מהטלפון: $url"
    return 0
  fi
  warn "הגדרת Tailscale Serve נכשלה. נסה שוב: ./mac/manage.sh tailscale"
  return 1
}

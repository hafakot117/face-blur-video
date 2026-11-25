#!/bin/bash
# Face Blur - service management on the iMac.
#   ./mac/manage.sh status|start|stop|restart|logs|password|url|tailscale|update|uninstall
set -uo pipefail
source "$(dirname "$0")/common.sh"

cmd="${1:-status}"
case "$cmd" in
  status)
    if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then ok "שירות launchd רשום"; else warn "השירות לא רשום (הרץ ./mac/install.sh)"; fi
    if health; then ok "השרת עונה על פורט $PORT"; else warn "השרת לא עונה"; fi
    if url="$(tailscale_url)"; then ok "Tailscale: $url"; else warn "Tailscale לא מחובר / לא מותקן"; fi
    df -h "$HOME" | awk 'NR==2 {print "  דיסק פנוי: " $4 " מתוך " $2}'
    ;;
  start|restart)
    [ -f "$PLIST" ] || fail "השירות לא מותקן. הרץ ./mac/install.sh"
    service_start
    if wait_healthy; then ok "פועל"; else fail "לא עלה - בדוק: ./mac/manage.sh logs"; fi
    ;;
  stop)
    service_stop; ok "נעצר (יעלה שוב בכניסה הבאה למשתמש, או: ./mac/manage.sh start)"
    ;;
  logs)
    tail -n 100 -f "$LOG"
    ;;
  password)
    (cd "$REPO" && "$PY" server.py --set-password) && service_start && wait_healthy && ok "הסיסמה הוחלפה. כל המכשירים יצטרכו להתחבר מחדש."
    ;;
  url)
    tailscale_url || fail "Tailscale לא מחובר"
    ;;
  tailscale)
    setup_tailscale_serve
    ;;
  update)
    (cd "$REPO" && git pull --ff-only) || fail "git pull נכשל"
    "$PY" -m pip install --quiet -r "$REPO/requirements.txt"
    service_start
    if wait_healthy; then ok "עודכן ופועל"; else fail "לא עלה אחרי העדכון - בדוק: ./mac/manage.sh logs"; fi
    ;;
  uninstall)
    service_stop
    rm -f "$PLIST"
    if ts="$(find_tailscale)"; then "$ts" serve reset >/dev/null 2>&1 || true; fi
    ok "השירות הוסר. הנתונים נשארו ב-$DATA (מחק ידנית אם צריך)."
    ;;
  *)
    echo "שימוש: $0 status|start|stop|restart|logs|password|url|tailscale|update|uninstall"
    exit 1
    ;;
esac

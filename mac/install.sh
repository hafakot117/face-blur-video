#!/bin/bash
# Face Blur - one-time setup on the iMac.
#   cd ~/face-blur-video && ./mac/install.sh
# Safe to run again: it updates dependencies and restarts the service.
set -euo pipefail
source "$(dirname "$0")/common.sh"

[ "$(uname)" = "Darwin" ] || fail "הסקריפט הזה מיועד ל-macOS"

bold "=== Face Blur Remote - התקנה על ה-iMac ==="
echo "תיקיית הקוד:  $REPO"
echo "תיקיית נתונים: $DATA"
echo

# 1. Location. A background service can't read Desktop/Documents/Downloads/iCloud
#    without extra macOS permissions, so the code must live elsewhere.
case "$REPO" in
  "$HOME/Desktop"*|"$HOME/Documents"*|"$HOME/Downloads"*|"$HOME/Library/Mobile Documents"*)
    warn "הקוד נמצא בתיקייה ש-macOS חוסם לשירותי רקע (Desktop/Documents/Downloads/iCloud)."
    echo "   השירות לא יוכל לרוץ משם. העבר את התיקייה לתיקיית הבית ונסה שוב:"
    echo "   mv \"$REPO\" ~/face-blur-video && cd ~/face-blur-video && ./mac/install.sh"
    exit 1;;
esac

# 2. Python 3.9+
PYTHON=""
for p in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  if [ -x "$p" ] && "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
    PYTHON="$p"; break
  fi
done
if [ -z "$PYTHON" ]; then
  warn "לא נמצא Python 3.9 ומעלה."
  echo "   הרץ: xcode-select --install   (מתקין את כלי הפיתוח של Apple כולל Python), ואז הרץ שוב את ההתקנה."
  exit 1
fi
ok "Python: $("$PYTHON" --version 2>&1) ($PYTHON)"

# 3. Virtual environment + dependencies (ffmpeg comes bundled via imageio-ffmpeg)
if [ ! -x "$PY" ]; then
  "$PYTHON" -m venv "$REPO/venv"
fi
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet -r "$REPO/requirements.txt"
ok "ספריות הותקנו"
"$PY" -c "import cv2; assert hasattr(cv2, 'CascadeClassifier')" || fail "OpenCV לא תקין"

# 4. Folders + password
mkdir -p "$DATA/Inbox" "$DATA/Outputs" "$DATA/logs"
if ! "$PY" -c "import json,sys; sys.exit(0 if json.load(open('$DATA/config.json')).get('password_hash') else 1)" 2>/dev/null; then
  bold "בחר סיסמה לכניסה מהטלפון (לפחות 6 תווים):"
  (cd "$REPO" && "$PY" server.py --set-password)
else
  ok "סיסמה כבר מוגדרת (להחלפה: ./mac/manage.sh password)"
fi

# 5. launchd service: starts at login, restarts if it crashes, keeps the Mac awake
mkdir -p "$HOME/Library/LaunchAgents"
"$PY" - "$PLIST" "$REPO" "$PY" "$LOG" "$DATA" <<'EOF'
import plistlib, sys
plist, repo, py, log, data = sys.argv[1:]
with open(plist, "wb") as f:
    plistlib.dump({
        "Label": "com.faceblur.server",
        # server.py also starts `caffeinate` so the Mac doesn't sleep while it runs
        "ProgramArguments": [py, f"{repo}/server.py"],
        "WorkingDirectory": repo,
        "EnvironmentVariables": {
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "PYTHONUNBUFFERED": "1",
            "FACEBLUR_HOME": data,
        },
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "ProcessType": "Interactive",
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }, f)
EOF
service_start
if wait_healthy; then
  ok "השירות פועל (http://127.0.0.1:$PORT)"
else
  tail -n 30 "$LOG" 2>/dev/null || true
  fail "השירות לא עלה. הלוג למעלה; אפשר גם: ./mac/manage.sh logs"
fi

# 6. Power: never sleep, come back after a power cut
echo
if ask "להגדיר את ה-iMac לא להירדם אף פעם ולהידלק לבד אחרי הפסקת חשמל? (מומלץ, דורש סיסמת Mac)"; then
  sudo pmset -a sleep 0 disksleep 0 womp 1 autorestart 1 && ok "הגדרות חשמל עודכנו"
fi

# 7. Remote access over Tailscale
echo
URL=""
if setup_tailscale_serve; then URL="$(tailscale_url || true)"; fi

echo
bold "=== סיום ==="
if [ -n "$URL" ]; then
  echo "בטלפון (עם Tailscale מחובר) פתח:  $URL"
else
  echo "השירות רץ על ה-iMac. כדי לגשת מהטלפון השלם את הגדרת Tailscale (ראה README)."
fi
echo "סרטונים לעיבוד מה-iMac:  $DATA/Inbox"
echo "סרטונים מוכנים:          $DATA/Outputs"
echo "ניהול:  ./mac/manage.sh status | logs | restart | password | update"
echo
echo "לפני הנסיעה - עבור על הרשימה ב-README (סעיף 'לפני שיוצאים לחו\"ל')."

#!/usr/bin/env bash
# Reisülküttab uninstaller: takes back what install.sh put down, and nothing else
# unless asked. Your settings and your dictations survive a plain run; --purge
# is the word that deletes them.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$(command -v python3 || true)"
USER_NAME="$(id -un)"
BIN_DIR="$HOME/.local/bin"
APP_DIR="$HOME/.local/share/applications"
AUTOSTART_DIR="$HOME/.config/autostart"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/reisulkuttab"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/reisulkuttab"

PURGE=0
ASSUME_YES=0

say()  { printf '  %s\n' "$1"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }
gone() { printf '  \033[90m·\033[0m %s\n' "$1"; }
# "1 dictation", "3 dictations": how many is the point of printing it at all.
count() {
  if (( $1 == 1 )); then printf '%s %s' "$1" "$2"; else printf '%s %s' "$1" "$3"; fi
}

usage() {
  cat <<EOF
Usage: ./uninstall.sh [--purge] [--yes]

  --purge   also delete the settings ($CONFIG_DIR)
            and the dictations, meetings and recordings ($DATA_DIR)
  --yes     do not ask before deleting those

Without --purge nothing you have written is touched, and the source directory
is left alone either way.
EOF
}

for arg in "$@"; do
  case "$arg" in
    --purge) PURGE=1 ;;
    --yes|-y) ASSUME_YES=1 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'uninstall.sh: unknown option: %s\n' "$arg" >&2; usage >&2; exit 2 ;;
  esac
done

# A symlink whose target is gone is still a file to remove, hence -L.
remove() {
  if [[ -e "$1" || -L "$1" ]]; then
    rm -f "$1"
    ok "Removed $1"
  else
    gone "Was not there: $1"
  fi
}

echo
echo "Uninstalling Reisülküttab"
echo "──────────────────"

# 1. Global shortcuts ------------------------------------------------------
# Handed to Reisülküttab while it can still run, because it is the half that knows
# whether they went into KDE's kglobalshortcutsrc or GNOME's gsettings.
if [[ -n "$PY" ]] && python3 -c 'import PyQt6.QtWidgets' 2>/dev/null; then
  for which in toggle cancel ask meeting; do
    "$PY" "$DIR/reisulkuttab.py" shortcut remove "$which" >/dev/null 2>&1 || true
  done
  ok "Global shortcuts unregistered"
  say "KWin reads that file at startup, so the keys are free after your next login."
else
  warn "PyQt6 is missing, so the shortcuts were left registered."
  say  "Remove them in your desktop's shortcut settings."
fi

# 2. The running instance --------------------------------------------------
# It holds a tray icon and a socket; asking it to quit is tidier than pulling
# its launchers out from under it.
if pgrep -u "$USER_NAME" -f 'reisulkuttab\.py' >/dev/null 2>&1; then
  [[ -n "$PY" ]] && "$PY" "$DIR/reisulkuttab.py" quit >/dev/null 2>&1 || true
  sleep 0.5
  if pgrep -u "$USER_NAME" -f 'reisulkuttab\.py' >/dev/null 2>&1; then
    warn "Reisülküttab is still running; close it from the tray icon"
  else
    ok "Stopped the running instance"
  fi
fi

# 3. Launchers -------------------------------------------------------------
# Only our own symlink goes: a file of the same name that somebody else put
# there is not ours to delete.
if [[ -L "$BIN_DIR/reisulkuttab" ]]; then
  remove "$BIN_DIR/reisulkuttab"
elif [[ -e "$BIN_DIR/reisulkuttab" ]]; then
  warn "$BIN_DIR/reisulkuttab is not our symlink, leaving it alone"
else
  gone "Was not there: $BIN_DIR/reisulkuttab"
fi
remove "$APP_DIR/reisulkuttab.desktop"
remove "$AUTOSTART_DIR/reisulkuttab.desktop"
# Removing the shortcut takes its desktop file with it, but an install from
# before this script existed may have left one behind on a desktop that never
# used them.
for id in reisulkuttab-toggle reisulkuttab-cancel reisulkuttab-ask reisulkuttab-meeting; do
  if [[ -e "$APP_DIR/$id.desktop" ]]; then
    remove "$APP_DIR/$id.desktop"
  fi
done

# 4. Settings and dictations -----------------------------------------------
echo
if ((PURGE)); then
  warn "--purge also deletes:"
  if [[ -f "$CONFIG_DIR/config.json" ]]; then
    say "$CONFIG_DIR/config.json  (your API keys and every setting)"
  fi
  if [[ -f "$DATA_DIR/history.jsonl" ]]; then
    # grep -c rather than wc -l: a last line with no newline is still a dictation.
    say "$DATA_DIR/history.jsonl  ($(count "$(grep -c '' "$DATA_DIR/history.jsonl" 2>/dev/null || echo 0)" dictation dictations))"
  fi
  if [[ -d "$DATA_DIR/meetings" ]]; then
    say "$DATA_DIR/meetings  ($(count "$(find "$DATA_DIR/meetings" -name '*.md' | wc -l)" meeting meetings))"
  fi
  if [[ -d "$DATA_DIR/recordings" ]]; then
    say "$DATA_DIR/recordings  ($(du -sh "$DATA_DIR/recordings" | cut -f1) of audio)"
  fi

  if ((!ASSUME_YES)); then
    if [[ -t 0 ]]; then
      printf '  Type yes to delete them: '
      read -r reply
      [[ "$reply" == "yes" ]] || { PURGE=0; say "Kept."; }
    else
      PURGE=0
      warn "Not a terminal, so nothing was deleted. Pass --yes if you meant it."
    fi
  fi
fi

if ((PURGE)); then
  rm -rf "$CONFIG_DIR" "$DATA_DIR"
  ok "Settings and dictations deleted"
else
  say "Settings kept:     $CONFIG_DIR"
  say "Dictations kept:   $DATA_DIR"
  say "Delete them too with:  ./uninstall.sh --purge"
fi

echo
ok "Done."
say "The source directory is untouched: $DIR"
echo

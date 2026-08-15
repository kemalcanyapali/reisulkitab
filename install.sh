#!/usr/bin/env bash
# Reisülküttab installer: dependency check, launchers, global shortcuts.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$(command -v python3)"
USER_NAME="$(id -un)"
BIN_DIR="$HOME/.local/bin"
APP_DIR="$HOME/.local/share/applications"
AUTOSTART_DIR="$HOME/.config/autostart"
SHORTCUT="${1:-Ctrl+Space}"
# Without the colon, so that a second argument given as "" stays empty. That is
# how update.sh says "this one was turned off", as against not saying anything.
CANCEL_SHORTCUT="${2-Ctrl+Alt+Space}"

say()  { printf '  %s\n' "$1"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }

echo
echo "Installing Reisülküttab"
echo "────────────────"

# 1. Dependencies ----------------------------------------------------------
missing=()
audio_cmds=(ffmpeg)
if command -v parec >/dev/null || command -v pw-record >/dev/null; then
  :
else
  missing+=("pulseaudio-utils-or-pipewire-audio")
fi
if [[ "${XDG_SESSION_TYPE:-}" == "x11" ]]; then
  desktop_cmds=(xclip xdotool)
else
  desktop_cmds=(wl-copy wl-paste ydotool)
fi
for cmd in "${audio_cmds[@]}" "${desktop_cmds[@]}"; do
  command -v "$cmd" >/dev/null || missing+=("$cmd")
done
python3 -c 'import PyQt6.QtWidgets' 2>/dev/null || missing+=("python-pyqt6")

if ((${#missing[@]})); then
  warn "Missing: ${missing[*]}"
  say  "Ubuntu X11:     sudo apt install pulseaudio-utils xclip xdotool ffmpeg"
  say  "Arch Wayland:   sudo pacman -S --needed pipewire-audio wl-clipboard ydotool ffmpeg python-pyqt6"
  say  "Fedora Wayland: sudo dnf install pipewire-utils wl-clipboard ydotool ffmpeg-free python3-pyqt6"
  echo
else
  ok "All dependencies present"
fi

# 2. ydotoold --------------------------------------------------------------
# What auto-paste needs is a socket it may write to, which is not the same
# question as whether the unit is up: Fedora ships ydotool as a system service
# only, and its socket stays root-owned at mode 600, so there the daemon can be
# running while every paste is refused. The socket file outlives the daemon,
# though, so the process has to be there as well for the answer to be yes.
if [[ "${XDG_SESSION_TYPE:-}" != "x11" ]] && command -v ydotool >/dev/null; then
  socket="${YDOTOOL_SOCKET:-${XDG_RUNTIME_DIR:-/tmp}/.ydotool_socket}"
  alive() { pgrep -x ydotoold >/dev/null 2>&1; }
  if [[ -w "$socket" ]] && alive; then
    ok "ydotoold is running (auto-paste ready)"
  elif systemctl is-active --quiet ydotool 2>/dev/null; then
    warn "ydotoold's socket is not yours to write to, so auto-paste will fail"
    say  "Hand it over with the drop-in in the README's Fedora section."
  elif alive; then
    warn "ydotoold is running, but it did not put its socket at $socket"
    say  "Point Reisülküttab at the one it did make: export YDOTOOL_SOCKET=..."
  else
    warn "ydotoold is not running, auto-paste will not work"
    say  "systemctl --user enable --now ydotool   (on Fedora: see the README)"
  fi
fi

# 3. Launchers -------------------------------------------------------------
mkdir -p "$BIN_DIR" "$APP_DIR" "$AUTOSTART_DIR"
REPLACE_RUNNING=0
if command -v pkill >/dev/null && command -v pgrep >/dev/null \
    && pgrep -u "$USER_NAME" -f "$DIR/dikte\.py( |$)" >/dev/null 2>&1; then
  pkill -TERM -u "$USER_NAME" -f "$DIR/dikte\.py( |$)" || true
  sleep 0.5
  REPLACE_RUNNING=1
fi
if [[ -L "$BIN_DIR/dikte" ]] \
    && [[ "$(readlink "$BIN_DIR/dikte")" == "$DIR/dikte.py" ]]; then
  rm -f "$BIN_DIR/dikte"
elif [[ -e "$BIN_DIR/dikte" || -L "$BIN_DIR/dikte" ]]; then
  warn "$BIN_DIR/dikte is not the predecessor symlink; leaving it alone"
fi
rm -f "$APP_DIR/dikte.desktop" "$AUTOSTART_DIR/dikte.desktop"
for id in dikte-toggle dikte-cancel dikte-ask dikte-meeting; do
  rm -f "$APP_DIR/$id.desktop"
done
ln -sf "$DIR/reisulkuttab.py" "$BIN_DIR/reisulkuttab"
chmod +x "$DIR/reisulkuttab.py"
ok "Command installed: $BIN_DIR/reisulkuttab"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) warn "$BIN_DIR is not on your PATH. For fish: fish_add_path $BIN_DIR" ;;
esac

cat > "$APP_DIR/reisulkuttab.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Reisülküttab
Comment=Voice dictation: record, transcribe, clean up, paste
Exec=$PY $DIR/reisulkuttab.py
Icon=audio-input-microphone
Categories=Utility;AudioVideo;
StartupNotify=false
EOF
ok "Application menu entry added"

cat > "$AUTOSTART_DIR/reisulkuttab.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Reisülküttab
Exec=$PY $DIR/reisulkuttab.py
Icon=audio-input-microphone
X-GNOME-Autostart-enabled=true
StartupNotify=false
EOF
ok "Will start automatically on login"

# 4. Global shortcuts ------------------------------------------------------
# Two of them: one to start and stop, one to throw the recording away. The
# second is worth a key of its own because stopping is the step there is no
# taking back, being what sends the audio off to be transcribed.
#
# Reisülküttab registers them rather than this script writing the files itself: it
# knows which desktop it is on, and it stores the combination in the settings
# as well, which is where the built-in listener reads it from. A key written
# to only one of the two places is a key that half works.
if [[ "$SHORTCUT" == "$CANCEL_SHORTCUT" ]]; then
  warn "Both arguments are $SHORTCUT, so the discard key was left out."
  say  "Pass two different combinations, or set it in Settings → Shortcuts."
  CANCEL_SHORTCUT=""
fi

register() {   # which  combination  label
  if out="$("$PY" "$DIR/reisulkuttab.py" shortcut install "$1" --combo "$2" 2>&1)"; then
    ok "$3: $2"
  else
    # One line: the rest of what it has to say about KWin is printed below.
    warn "${out%%$'\n'*}"
  fi
}

if python3 -c 'import PyQt6.QtWidgets' 2>/dev/null; then
  register toggle "$SHORTCUT" "Start and stop"
  if [[ -n "$CANCEL_SHORTCUT" ]]; then
    register cancel "$CANCEL_SHORTCUT" "Discard the recording"
  fi
  if [[ "${XDG_CURRENT_DESKTOP:-}" != *[Gg][Nn][Oo][Mm][Ee]* ]]; then
    warn "KWin only reads these at startup, so they go live after your next"
    say  "login. Until then open Settings → Shortcuts and turn on the"
    say  "built-in listener to use them right away."
  fi
else
  warn "PyQt6 is missing, so no shortcut was registered. Install it, then run:"
  say  "reisulkuttab shortcut install toggle --combo '$SHORTCUT'"
fi

echo
if ((REPLACE_RUNNING)); then
  nohup "$PY" "$DIR/reisulkuttab.py" --gui >/dev/null 2>&1 &
  ok "Started Reisülküttab in place of the predecessor"
else
  ok "Done. Start it with:  reisulkuttab"
fi
say "The settings window opens on first run: download a speech model, or add"
say "an OpenAI or OpenRouter key instead."
echo

#!/usr/bin/env bash
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="${XDG_BIN_HOME:-$HOME/.local/bin}"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/remoteplay-display"
UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

for tool in kscreen-doctor pactl python3; do
    command -v "$tool" >/dev/null || { echo "missing required tool: $tool" >&2; exit 1; }
done

mkdir -p "$BIN" "$CONFIG" "$UNITS"
ln -sfn "$REPO/remoteplay-display" "$BIN/remoteplay-display"
ln -sfn "$REPO/remoteplay-display.service" "$UNITS/remoteplay-display.service"

if [ -f "$CONFIG/config.ini" ]; then
    echo "keeping existing $CONFIG/config.ini"
else
    cp "$REPO/config.example.ini" "$CONFIG/config.ini"
    echo "seeded $CONFIG/config.ini"
fi

systemctl --user daemon-reload
systemctl --user enable --now remoteplay-display.service

case ":$PATH:" in
    *":$BIN:"*) ;;
    *) echo "note: $BIN is not on your PATH" >&2 ;;
esac

echo
exec "$BIN/remoteplay-display" doctor

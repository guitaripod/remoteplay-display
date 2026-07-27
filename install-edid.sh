#!/usr/bin/env bash
# Install the synthetic dummy EDID so it is applied at every boot, before the compositor starts.
set -euo pipefail

CONNECTOR="${1:-HDMI-A-2}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$(id -u)" -ne 0 ]; then
    echo "run as root: sudo $0 [connector]" >&2
    exit 1
fi

install -Dm755 "$REPO/dummy-edid" /usr/local/bin/dummy-edid
install -d /lib/firmware/edid
/usr/local/bin/dummy-edid write --out /lib/firmware/edid/remoteplay-dummy.edid
chmod 644 /lib/firmware/edid/remoteplay-dummy.edid
install -Dm644 "$REPO/remoteplay-dummy-edid@.service" \
    /etc/systemd/system/"remoteplay-dummy-edid@.service"

systemctl daemon-reload
systemctl enable "remoteplay-dummy-edid@${CONNECTOR}.service"

echo
echo "enabled remoteplay-dummy-edid@${CONNECTOR}.service"
echo "it applies the EDID before display-manager.service, so no hotplug is needed at runtime."

#!/bin/sh
# Removes what install.sh put in place, and nothing else. The headers go back
# to the firmware's curves as the service stops.
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
installed=/var/lib/aeolus/installed

if [ -e /etc/systemd/system/aeolus.service ]; then
    systemctl disable --now aeolus.service || true
fi
if [ -f "$installed" ]; then
    while IFS= read -r f; do
        case "$f" in
            /usr/local/bin/aeolus|/etc/systemd/system/aeolus.service|/etc/modules-load.d/aeolus.conf|/etc/aeolus/config.json)
                rm -f "$f" ;;
        esac
    done < "$installed"
fi
rmdir /etc/aeolus 2>/dev/null || true
rm -rf /var/lib/aeolus /run/aeolus
systemctl daemon-reload
echo "Aeolus removed; fans are back on the firmware's curves."

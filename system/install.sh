#!/bin/sh
# Aeolus system side: run once with sudo from the plugin folder.
#   sudo ~/.config/omarchy/plugins/diogocezar.aeolus/system/install.sh
# Copies the service into root-owned paths (the plugin folder is yours and
# stays unprivileged), loads the fan chip driver at boot, measures your
# headers (aeolus setup) and starts the service. Nothing that already exists
# and wasn't put there by Aeolus is overwritten.
set -eu

[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
here=$(cd "$(dirname "$0")" && pwd)
marker="Installed by Aeolus"
state=/var/lib/aeolus
mkdir -p "$state"
installed="$state/installed"
touch "$installed"

ours() {  # file absent, or listed as ours, or carrying our marker
    [ ! -e "$1" ] || grep -qxF "$1" "$installed" || grep -qF "$marker" "$1"
}
place() {  # place <mode> <src> <dest>
    ours "$3" || { echo "refusing to overwrite $3 (not created by Aeolus)" >&2; exit 1; }
    install -D -o root -g root -m "$1" "$2" "$3"
    grep -qxF "$3" "$installed" || echo "$3" >> "$installed"
}

/usr/bin/python3 -m py_compile "$here/aeolus.py"
place 755 "$here/aeolus.py" /usr/local/bin/aeolus
place 644 "$here/aeolus.service" /etc/systemd/system/aeolus.service

# Fan chip driver: load it now if no chip offers PWM control yet, then make
# sure it loads at boot (Super I/O drivers aren't loaded on their own).
has_pwm() { ls /sys/class/hwmon/hwmon*/pwm1_enable >/dev/null 2>&1; }
if ! has_pwm; then
    for drv in nct6775 it87; do
        modprobe "$drv" 2>/dev/null && sleep 1 && has_pwm && break
    done
fi
for en in /sys/class/hwmon/hwmon*/pwm1_enable; do
    [ -e "$en" ] || continue
    mod=$(basename "$(readlink -f "$(dirname "$en")/device/driver/module" 2>/dev/null)" 2>/dev/null || true)
    case "$mod" in nct6775|nct6775_core|it87|f71882fg|w83627ehf) ;; *) continue ;; esac
    [ "$mod" = nct6775_core ] && mod=nct6775
    if ! grep -qsxF "$mod" /etc/modules-load.d/*.conf /usr/lib/modules-load.d/*.conf; then
        tmp=$(mktemp)
        printf '# %s\n%s\n' "$marker" "$mod" > "$tmp"
        place 644 "$tmp" /etc/modules-load.d/aeolus.conf
        rm -f "$tmp"
        echo "loading $mod at boot (/etc/modules-load.d/aeolus.conf)"
    fi
done
has_pwm || { echo "no fan controller found: Aeolus can't drive this board" >&2; exit 1; }

if [ ! -e /etc/aeolus/config.json ]; then
    /usr/local/bin/aeolus setup
    grep -qxF /etc/aeolus/config.json "$installed" || echo /etc/aeolus/config.json >> "$installed"
else
    echo "keeping existing /etc/aeolus/config.json"
fi
/usr/local/bin/aeolus check

systemctl daemon-reload
systemctl enable aeolus.service
systemctl restart aeolus.service
sleep 3
/usr/local/bin/aeolus status

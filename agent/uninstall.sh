#!/bin/sh
set -eu

purge=false

usage() {
    cat <<'EOF'
Usage: uninstall.sh [--purge]

Removes the Wolnut agent service and binary. Pairing state is preserved unless
--purge is supplied.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --purge)
            purge=true
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    if command -v sudo >/dev/null 2>&1; then
        if [ "$purge" = true ]; then
            exec sudo "$0" --purge
        fi
        exec sudo "$0"
    fi
    printf '%s\n' \
        'Root privileges are required and sudo is not installed.' \
        'Log in as root (for example with "su -") and run this uninstall script again.' >&2
    exit 1
fi

if command -v systemctl >/dev/null 2>&1; then
    systemctl disable --now wolnut-agent.service >/dev/null 2>&1 || true
fi

rm -f /etc/systemd/system/wolnut-agent.service
rm -f /usr/local/bin/wolnut-agent

if command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload
fi

if [ "$purge" = true ]; then
    rm -rf /var/lib/wolnut-agent
    rm -rf /etc/wolnut-agent
    echo "Wolnut agent removed with pairing state and configuration"
else
    echo "Wolnut agent removed; pairing state remains in /var/lib/wolnut-agent"
    echo "Run uninstall.sh --purge to remove all agent data"
fi

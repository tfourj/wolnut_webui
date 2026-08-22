#!/bin/sh
set -eu

download_base="https://github.com/tfourj/wolnut_webui/releases/latest/download"
listen_address="0.0.0.0:8184"
enrollment_url=""
enrollment_token=""

usage() {
    cat <<'EOF'
Usage: install.sh [options]

Options:
  --download-base URL       HTTPS directory containing agent release files
  --listen ADDRESS          Agent listen address (default 0.0.0.0:8184)
  --enroll-url URL          Wolnut HTTPS enrollment endpoint
  --enrollment-token TOKEN  One-time enrollment token
  --help                    Show this help
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --download-base|--listen|--enroll-url|--enrollment-token)
            if [ "$#" -lt 2 ]; then
                echo "Missing value for $1" >&2
                exit 2
            fi
            option="$1"
            value="$2"
            shift 2
            case "$option" in
                --download-base) download_base="${value%/}" ;;
                --listen) listen_address="$value" ;;
                --enroll-url) enrollment_url="$value" ;;
                --enrollment-token) enrollment_token="$value" ;;
            esac
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

case "$download_base" in
    https://*) ;;
    *)
        echo "The agent download URL must use HTTPS" >&2
        exit 1
        ;;
esac

if [ -n "$enrollment_url" ] || [ -n "$enrollment_token" ]; then
    if [ -z "$enrollment_url" ] || [ -z "$enrollment_token" ]; then
        echo "--enroll-url and --enrollment-token must be provided together" >&2
        exit 2
    fi
    case "$enrollment_url" in
        https://*) ;;
        *)
            echo "The Wolnut enrollment URL must use HTTPS" >&2
            exit 1
            ;;
    esac
fi

if [ "$(id -u)" -eq 0 ]; then
    privilege_command=""
elif command -v sudo >/dev/null 2>&1; then
    privilege_command="sudo"
else
    printf '%s\n' \
        'Root privileges are required and sudo is not installed.' \
        'Log in as root (for example with "su -") and run the same install command again.' >&2
    exit 1
fi

for command in curl sha256sum mktemp uname; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Required command not found: $command" >&2
        exit 1
    fi
done

machine_arch="$(uname -m)"
case "$machine_arch" in
    x86_64) agent_arch="amd64" ;;
    aarch64|arm64) agent_arch="arm64" ;;
    *)
        echo "Unsupported architecture: $machine_arch" >&2
        exit 1
        ;;
esac

temporary_directory="$(mktemp -d)"
trap 'rm -rf "$temporary_directory"' EXIT HUP INT TERM
binary_name="wolnut-agent-linux-$agent_arch"
binary_path="$temporary_directory/$binary_name"

curl --proto '=https' --proto-redir '=https' --tlsv1.2 -fsSL \
    "$download_base/$binary_name" -o "$binary_path"
curl --proto '=https' --proto-redir '=https' --tlsv1.2 -fsSL \
    "$download_base/$binary_name.sha256" -o "$binary_path.sha256"
(cd "$temporary_directory" && sha256sum -c "$binary_name.sha256")
chmod 0755 "$binary_path"

install_help="$("$binary_path" install-service --help 2>&1 || true)"
case "$install_help" in
    *download-base*) supports_download_base=true ;;
    *) supports_download_base=false ;;
esac

run_install() {
    privilege_command="$1"

    set -- "$binary_path" install-service --listen "$listen_address"
    if [ "$supports_download_base" = true ]; then
        set -- "$@" --download-base "$download_base"
    fi
    if [ -n "$enrollment_url" ]; then
        set -- "$@" --enroll-url "$enrollment_url" --enrollment-token "$enrollment_token"
    fi

    if [ -n "$privilege_command" ]; then
        "$privilege_command" "$@"
    else
        "$@"
    fi
}

run_install "$privilege_command"

echo "Wolnut agent installed on $listen_address"
if [ -z "$enrollment_url" ]; then
    echo "Manual pairing selected. As root, run: wolnut-agent pairing-code"
fi

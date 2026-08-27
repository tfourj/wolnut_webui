#!/bin/sh
set -eu

download_base="https://github.com/tfourj/wolnut_webui/releases/latest/download"
listen_address="0.0.0.0:8184"
enrollment_url=""
enrollment_token=""
interactive_mode=false

usage() {
    cat <<'EOF'
Usage: install.sh [options]

Run directly from GitHub:
  curl -fsSL https://raw.githubusercontent.com/tfourj/wolnut_webui/main/agent/install.sh | sudo bash
  curl -fsSL https://raw.githubusercontent.com/tfourj/wolnut_webui/refs/heads/main/agent/install.sh | sudo bash

Options:
  --download-base URL       HTTPS directory containing agent release files
  --listen ADDRESS          Agent listen address (default 0.0.0.0:8184)
  --enroll-url URL          Wolnut HTTPS enrollment endpoint
  --enrollment-token TOKEN  One-time enrollment token
  --help                    Show this help

When --enroll-url and --enrollment-token are omitted the installer prompts
interactively for the Wolnut server URL and enrollment token shown in the
WebUI Quick install dialog.
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

# ---------------------------------------------------------------------------
# Interactive prompts (when not provided via flags)
# ---------------------------------------------------------------------------
is_tty() {
    [ -t 0 ]
}

trim() {
    # trim leading/trailing whitespace without external deps
    trimmed="$1"
    # remove leading
    trimmed="${trimmed#"${trimmed%%[! \t\r\n]*}"}"
    # remove trailing
    trimmed="${trimmed%"${trimmed##*[! \t\r\n]}"}"
    printf '%s' "$trimmed"
}

prompt_value() {
    prompt_text="$1"
    default_value="$2"
    result_var="$3"
    is_secret="$4"

    if [ -n "$default_value" ]; then
        printf '%s [%s]: ' "$prompt_text" "$default_value" >&2
    else
        printf '%s: ' "$prompt_text" >&2
    fi

    if [ "$is_secret" = "true" ]; then
        # disable echo for token input
        stty -echo 2>/dev/null || true
        IFS= read -r input_value || input_value=""
        stty echo 2>/dev/null || true
        printf '\n' >&2
    else
        IFS= read -r input_value || input_value=""
    fi

    input_value="$(trim "$input_value")"
    if [ -z "$input_value" ] && [ -n "$default_value" ]; then
        input_value="$default_value"
    fi
    # Use eval to assign to caller variable (POSIX sh)
    eval "$result_var=\"\$input_value\""
}

if [ -z "$enrollment_url" ] && [ -z "$enrollment_token" ]; then
    if is_tty; then
        interactive_mode=true
        echo "" >&2
        echo "Wolnut agent quick install" >&2
        echo "Enter the values shown in the Wolnut WebUI Quick install dialog." >&2
        echo "" >&2

        # Prompt for server / enrollment URL
        server_input=""
        prompt_value "Wolnut server URL (e.g. https://wolnut.example.com) or full enrollment URL" "" server_input false
        server_input="$(trim "$server_input")"
        # strip trailing slash
        server_input="${server_input%/}"

        if [ -n "$server_input" ]; then
            case "$server_input" in
                *"/api/agents/enroll"*)
                    enrollment_url="$server_input"
                    ;;
                https://*)
                    enrollment_url="$server_input/api/agents/enroll"
                    ;;
                "")
                    ;;
                *)
                    # Will be validated later; keep as-is for error message
                    enrollment_url="$server_input"
                    ;;
            esac
        fi

        if [ -z "$enrollment_url" ]; then
            echo "Enrollment URL is required. Copy it from the WebUI Quick install dialog." >&2
            exit 2
        fi

        prompt_value "Enrollment token (one-time key from WebUI)" "" enrollment_token true
        enrollment_token="$(trim "$enrollment_token")"

        if [ -z "$enrollment_token" ]; then
            echo "Enrollment token is required." >&2
            exit 2
        fi

        # Prompt for listen address with default
        listen_input=""
        prompt_value "Agent listen address" "$listen_address" listen_input false
        listen_input="$(trim "$listen_input")"
        if [ -n "$listen_input" ]; then
            listen_address="$listen_input"
        fi

        echo "" >&2
    else
        # Non-interactive without token -> guide user
        echo "No enrollment token provided and no terminal for prompts." >&2
        echo "Run interactively:" >&2
        echo "  curl -fsSL https://raw.githubusercontent.com/tfourj/wolnut_webui/main/agent/install.sh | sudo bash" >&2
        echo "Or provide flags:" >&2
        echo "  .../install.sh --enroll-url https://wolnut.example.com/api/agents/enroll --enrollment-token <token>" >&2
        exit 2
    fi
fi

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

# ---------------------------------------------------------------------------
# Pre-flight: ping Wolnut server before downloading binary
# ---------------------------------------------------------------------------
if [ -n "$enrollment_url" ]; then
    # Derive health URL from enrollment URL
    # enrollment_url is https://host[:port]/api/agents/enroll -> base + /api/health
    base_url="${enrollment_url%/api/agents/enroll}"
    # Fallback if pattern not matched
    case "$base_url" in
        "$enrollment_url") base_url="$enrollment_url" ;;
    esac
    health_url="$base_url/api/health"

    echo "Checking Wolnut server connectivity..." >&2
    if ! curl --proto '=https' --proto-redir '=https' --tlsv1.2 -fsSL --connect-timeout 10 --max-time 15 "$health_url" -o /dev/null 2>&1; then
        # Also try enrollment URL with GET (expect 405 but TLS success)
        # Use -I with relaxed fail to just test TLS
        if ! curl --proto '=https' --proto-redir '=https' --tlsv1.2 -k --connect-timeout 10 --max-time 15 -s -o /dev/null "$health_url" 2>&1; then
            echo "Warning: could not reach Wolnut server at $health_url" >&2
            echo "Verify WOLNUT_PUBLIC_URL, reverse proxy, and network connectivity before continuing." >&2
            if [ "$interactive_mode" = true ]; then
                printf 'Continue anyway? [y/N]: ' >&2
                IFS= read -r confirm || confirm=""
                case "$confirm" in
                    y|Y|yes|YES) echo "Continuing despite failed health check..." >&2 ;;
                    *) echo "Aborted. Fix server connectivity and retry." >&2; exit 1 ;;
                esac
            else
                echo "Aborting due to failed server ping. Use --enroll-url with a reachable HTTPS URL." >&2
                exit 1
            fi
        else
            echo "Warning: Wolnut server health check returned non-200 (TLS reachable, continuing)" >&2
        fi
    else
        echo "Wolnut server is reachable." >&2
    fi
fi

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

echo "Downloading $binary_name from $download_base ..." >&2
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

if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet wolnut-agent.service; then
    if [ -n "$privilege_command" ]; then
        "$privilege_command" systemctl stop wolnut-agent.service
    else
        systemctl stop wolnut-agent.service
    fi
fi

run_install "$privilege_command"

# ---------------------------------------------------------------------------
# Post-install verification: ensure service is active and enrollment succeeded
# ---------------------------------------------------------------------------
echo "Verifying installation..." >&2
if command -v systemctl >/dev/null 2>&1; then
    # Wait briefly for service to start
    for attempt in 1 2 3 4 5; do
        if systemctl is-active --quiet wolnut-agent.service 2>/dev/null; then
            break
        fi
        sleep 1
    done
    if ! systemctl is-active --quiet wolnut-agent.service 2>/dev/null; then
        echo "Warning: wolnut-agent.service is not active. Check logs with: journalctl -u wolnut-agent -n 50" >&2
    else
        echo "wolnut-agent.service is active." >&2
    fi
fi

if [ -n "$enrollment_url" ]; then
    # Enrollment should have written state.json with server cert
    state_file="/var/lib/wolnut-agent/state.json"
    # Use privilege to check existence
    check_cmd="test -s $state_file && grep -q '\"server_cert_pem\"' $state_file"
    if [ -n "$privilege_command" ]; then
        if ! $privilege_command sh -c "$check_cmd" 2>/dev/null; then
            echo "Warning: enrollment may not have completed. Check Wolnut WebUI enrollment status and agent logs." >&2
        else
            echo "Enrollment verified." >&2
        fi
    else
        if ! sh -c "$check_cmd" 2>/dev/null; then
            echo "Warning: enrollment may not have completed. Check Wolnut WebUI enrollment status and agent logs." >&2
        else
            echo "Enrollment verified." >&2
        fi
    fi
fi

echo "Wolnut agent installed on $listen_address"
if [ -z "$enrollment_url" ]; then
    echo "Manual pairing selected. As root, run: wolnut-agent pairing-code"
fi

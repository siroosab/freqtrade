#!/bin/bash
#
# create-freqtrade-service.sh
#
# Place this script inside a bot's user_data folder and run it from there:
#   /home/ubuntu/freqtrade-bots/botXXX/freqtrade/user_data/create-freqtrade-service.sh
# or
#   /home/ubuntu/freqtrade-bots/botXXX/user_data/create-freqtrade-service.sh
#
# The script automatically detects which folder layout is in use.
# It then asks which service(s) you want to create: trade, hyperopt-scheduler,
# or both. Since these are two separate processes, each gets its own
# independent systemd service and they can run at the same time in parallel.
#
# Usage:
#   bash create-freqtrade-service.sh

set -e

# Absolute path of the folder the script itself (i.e. user_data) lives in
USER_DATA_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ "$(basename "$USER_DATA_DIR")" != "user_data" ]; then
    echo "Error: this script must be placed inside a user_data folder and run from there."
    echo "Current path: $USER_DATA_DIR"
    exit 1
fi

# Folder that contains user_data; can be "freqtrade" or the botXXX folder itself
PARENT_DIR="$(dirname "$USER_DATA_DIR")"

if [ "$(basename "$PARENT_DIR")" == "freqtrade" ]; then
    # Layout 1: .../botXXX/freqtrade/user_data
    WORKING_DIR="$PARENT_DIR"
    BOT_DIR="$(dirname "$PARENT_DIR")"
else
    # Layout 2: .../botXXX/user_data
    WORKING_DIR="$PARENT_DIR"
    BOT_DIR="$PARENT_DIR"
fi

BOT_NAME="$(basename "$BOT_DIR")"
VENV_ACTIVATE="$WORKING_DIR/.venv/bin/activate"

# Used at the end to print a summary cheat-sheet
CREATED_SERVICES=()
CREATED_TYPES=()

# ---------------------------------------------------------------------------
# Builds and installs one service (called once for trade and/or once for
# hyperopt-scheduler)
#   $1 = freqtrade subcommand (trade / hyperopt-scheduler)
#   $2 = service name suffix (trade / hyperopt)
#   $3 = default config filename
# ---------------------------------------------------------------------------
build_and_install_service() {
    local SUBCOMMAND="$1"
    local SERVICE_SUFFIX="$2"
    local DEFAULT_CONFIG="$3"

    echo ""
    echo "=== ${SUBCOMMAND} service settings ==="

    read -p "Config file name [default: ${DEFAULT_CONFIG}]: " CONFIG_NAME
    CONFIG_NAME="${CONFIG_NAME:-$DEFAULT_CONFIG}"
    local CONFIG_PATH="$USER_DATA_DIR/$CONFIG_NAME"

    read -p "Strategy name [default: SampleStrategy]: " STRATEGY
    STRATEGY="${STRATEGY:-SampleStrategy}"

    local SERVICE_NAME="freqtrade-${BOT_NAME}-${SERVICE_SUFFIX}"
    local SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

    echo "-------------------------------------------"
    echo "Bot name:            $BOT_NAME"
    echo "Service type:        $SUBCOMMAND"
    echo "WorkingDirectory:    $WORKING_DIR"
    echo "venv path:           $VENV_ACTIVATE"
    echo "Config path:         $CONFIG_PATH"
    echo "Strategy:            $STRATEGY"
    echo "Service name:        $SERVICE_NAME"
    echo "-------------------------------------------"

    local HAS_BLOCKING_ERROR=0

    # --- Check venv exists and freqtrade is installed inside it ---
    if [ ! -f "$VENV_ACTIVATE" ]; then
        echo "Warning: venv file not found at the path above ($VENV_ACTIVATE)."
        HAS_BLOCKING_ERROR=1
    else
        # Check that freqtrade is actually installed and runnable in this venv
        if source "$VENV_ACTIVATE" 2>/dev/null && command -v freqtrade >/dev/null 2>&1; then
            local FT_VERSION
            FT_VERSION="$(freqtrade --version 2>/dev/null | head -n1)"
            echo "freqtrade check: OK  ($FT_VERSION)"
            deactivate 2>/dev/null || true
        else
            echo "Error: freqtrade is not installed or not runnable in this venv ($VENV_ACTIVATE)."
            deactivate 2>/dev/null || true
            HAS_BLOCKING_ERROR=1
        fi
    fi

    # --- Check config file exists and is valid JSON ---
    if [ ! -f "$CONFIG_PATH" ]; then
        echo "Warning: config file not found at the path above ($CONFIG_PATH)."
        HAS_BLOCKING_ERROR=1
    else
        if command -v jq >/dev/null 2>&1; then
            if jq empty "$CONFIG_PATH" >/dev/null 2>&1; then
                echo "Config check ($CONFIG_NAME): valid JSON."
            else
                echo "Error: config file ($CONFIG_NAME) is not valid JSON."
                HAS_BLOCKING_ERROR=1
            fi
        elif command -v python3 >/dev/null 2>&1; then
            if python3 -m json.tool "$CONFIG_PATH" >/dev/null 2>&1; then
                echo "Config check ($CONFIG_NAME): valid JSON."
            else
                echo "Error: config file ($CONFIG_NAME) is not valid JSON."
                HAS_BLOCKING_ERROR=1
            fi
        else
            echo "Note: neither jq nor python3 is available, skipped JSON validation."
        fi
    fi

    if [ "$HAS_BLOCKING_ERROR" -eq 1 ]; then
        echo ""
        echo "Because of the issues above, continuing is not recommended."
        read -p "Do you want to continue anyway? (y/n) " FORCE_CONTINUE
        if [ "$FORCE_CONTINUE" != "y" ] && [ "$FORCE_CONTINUE" != "Y" ]; then
            echo "Creation of the ${SUBCOMMAND} service was cancelled."
            return
        fi
    fi

    read -p "Is the information above correct, and do you want this service created? (y/n) " CONFIRM
    if [ "$CONFIRM" != "y" ] && [ "$CONFIRM" != "Y" ]; then
        echo "Creation of the ${SUBCOMMAND} service was cancelled."
        return
    fi

    local TMP_SERVICE_FILE="/tmp/${SERVICE_NAME}.service"

    cat > "$TMP_SERVICE_FILE" <<EOF
[Unit]
Description=Freqtrade ${BOT_NAME} (${SUBCOMMAND})
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=${WORKING_DIR}
ExecStart=/bin/bash -c 'source ${VENV_ACTIVATE} && freqtrade ${SUBCOMMAND} --config ${CONFIG_PATH} --strategy ${STRATEGY}'
Restart=always
RestartSec=10
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF

    echo ""
    echo "Service file created at: $TMP_SERVICE_FILE"
    cat "$TMP_SERVICE_FILE"
    echo ""

    sudo cp "$TMP_SERVICE_FILE" "$SERVICE_FILE"
    sudo systemctl daemon-reload
    sudo systemctl enable "$SERVICE_NAME"

    echo "Service created and enabled successfully: $SERVICE_NAME"

    read -p "Start the $SERVICE_NAME service now? (y/n) " START_NOW
    if [ "$START_NOW" == "y" ] || [ "$START_NOW" == "Y" ]; then
        sudo systemctl start "$SERVICE_NAME"
        sudo systemctl status "$SERVICE_NAME" --no-pager || true
    fi

    CREATED_SERVICES+=("$SERVICE_NAME")
    CREATED_TYPES+=("$SUBCOMMAND")
}

# --- Choose service type ---
echo "Which service do you want to create?"
echo "  1) trade only                (real/dry-run bot execution)"
echo "  2) hyperopt-scheduler only   (strategy optimization scheduler)"
echo "  3) both (trade and hyperopt-scheduler in parallel, two independent services)"
read -p "Enter a number (1, 2, or 3): " SERVICE_TYPE_CHOICE

case "$SERVICE_TYPE_CHOICE" in
    1)
        build_and_install_service "trade" "trade" "config.json"
        ;;
    2)
        build_and_install_service "hyperopt-scheduler" "hyperopt" "Hyperopt_config.json"
        ;;
    3)
        build_and_install_service "trade" "trade" "config.json"
        build_and_install_service "hyperopt-scheduler" "hyperopt" "Hyperopt_config.json"
        ;;
    *)
        echo "Invalid choice."
        exit 1
        ;;
esac

# --- Final summary ---
if [ "${#CREATED_SERVICES[@]}" -eq 0 ]; then
    echo ""
    echo "No service was created."
    exit 0
fi

echo ""
echo "==================== Created services summary ===================="
for i in "${!CREATED_SERVICES[@]}"; do
    SVC="${CREATED_SERVICES[$i]}"
    TYPE="${CREATED_TYPES[$i]}"
    echo ""
    echo ">>> Service: $SVC   (type: $TYPE)"
    echo "  Start:               sudo systemctl start $SVC"
    echo "  Stop:                sudo systemctl stop $SVC"
    echo "  Restart:             sudo systemctl restart $SVC"
    echo "  Current status:      sudo systemctl status $SVC"
    echo "  Live logs:           sudo journalctl -u $SVC -f"
    echo "  Enable on boot:      sudo systemctl enable $SVC   (already done)"
    echo "  Disable on boot:     sudo systemctl disable $SVC"
done
echo ""
echo "======================================================================"
if [ "${#CREATED_SERVICES[@]}" -eq 2 ]; then
    echo "Note: since trade and hyperopt-scheduler are separate services, you"
    echo "must start/stop/restart each one individually; stopping one does not affect the other."
fi

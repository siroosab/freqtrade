#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CONFIG_FILE="$SCRIPT_DIR/user_data/config.json"
if [ ! -f "$CONFIG_FILE" ]; then
    echo "Config file not found: $CONFIG_FILE"
    exit 1
fi

if [ -f "$SCRIPT_DIR/.venv/bin/activate" ]; then
    . "$SCRIPT_DIR/.venv/bin/activate"
else
    echo "Virtual environment not found at $SCRIPT_DIR/.venv"
    exit 1
fi

LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"

echo "Starting Freqtrade trade..."
nohup freqtrade trade --config "$CONFIG_FILE" --strategy SampleStrategy \
    >"$LOG_DIR/trade.log" 2>&1 &
TRADE_PID=$!
echo "$TRADE_PID" > "$LOG_DIR/trade.pid"

echo "Starting Freqtrade hyperopt scheduler..."
nohup freqtrade hyperopt-scheduler --config "$CONFIG_FILE" --strategy SampleStrategy \
    >"$LOG_DIR/hyperopt_scheduler.log" 2>&1 &
SCHEDULER_PID=$!
echo "$SCHEDULER_PID" > "$LOG_DIR/hyperopt_scheduler.pid"

printf '\nStarted successfully.\n'
printf 'Trade PID: %s\n' "$TRADE_PID"
printf 'Scheduler PID: %s\n' "$SCHEDULER_PID"
printf 'Logs:\n'
printf '  - %s\n' "$LOG_DIR/trade.log"
printf '  - %s\n' "$LOG_DIR/hyperopt_scheduler.log"

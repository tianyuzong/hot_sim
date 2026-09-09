#!/usr/bin/env bash
set -euo pipefail

app_dir="${1:-$(pwd)}"
app_dir="$(cd "$app_dir" && pwd)"
service_name="${THERMOFLOW_SERVICE_NAME:-thermoflow}"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
unit_path="$unit_dir/$service_name.service"

if [[ ! -f "$app_dir/.env" ]]; then
    echo "缺少 $app_dir/.env；请先从 deploy/ubuntu/.env.a800.example 创建并检查配置。" >&2
    exit 2
fi

python3 -c 'import sys; assert sys.version_info >= (3, 10), "ThermoFlow 需要 Python 3.10 或更高版本"'
if [[ ! -x "$app_dir/.venv/bin/python" ]]; then
    if ! python3 -m venv "$app_dir/.venv"; then
        bootstrap_dir="${THERMOFLOW_BOOTSTRAP_DIR:-$app_dir/.bootstrap}"
        python3 -m pip install --no-cache-dir --target "$bootstrap_dir" virtualenv
        PYTHONPATH="$bootstrap_dir" python3 -m virtualenv "$app_dir/.venv"
    fi
fi
"$app_dir/.venv/bin/python" -m pip install --no-cache-dir --upgrade pip
"$app_dir/.venv/bin/python" -m pip install --no-cache-dir "$app_dir[gpu]"

mkdir -p "$unit_dir"
sed "s|@APP_DIR@|$app_dir|g" \
    "$app_dir/deploy/ubuntu/thermoflow.service.in" > "$unit_path"
systemctl --user daemon-reload
systemctl --user enable --now "$service_name.service"

echo "已启动 $service_name.service"
echo "状态：systemctl --user status $service_name.service"
echo "日志：journalctl --user -u $service_name.service -f"

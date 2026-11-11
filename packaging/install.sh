#!/usr/bin/env bash
# Per-user install of PROMETHEUS. No root, nothing outside your home directory.
#   Installs to:  ~/.local/share/prometheus/{venv,bridge}
#   Adds:         ~/.local/bin/prometheus, an application-menu entry and an icon
# Requires the system packages: python, pyside6 (and nodejs, or let Setup install Node for you).
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
data="${XDG_DATA_HOME:-$HOME/.local/share}"
app="$data/prometheus"
bin="$HOME/.local/bin"

python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || { echo "Python 3.11 or newer is required." >&2; exit 1; }
python3 -c 'import PySide6' 2>/dev/null \
  || echo "Note: system PySide6 not found (Arch package: pyside6); it will be installed into the app's own environment."

mkdir -p "$app" "$bin" "$data/applications" "$data/icons/hicolor/scalable/apps"
python3 -m venv --system-site-packages "$app/venv"
"$app/venv/bin/pip" install --quiet --disable-pip-version-check --upgrade "$repo"

# Bridge sources. Its npm dependencies are installed by the Setup wizard inside the app.
rm -rf "$app/bridge/src" "$app/bridge/dist"
mkdir -p "$app/bridge"
cp -r "$repo/bridge/src" "$repo/bridge/package.json" "$repo/bridge/package-lock.json" \
      "$repo/bridge/tsconfig.json" "$app/bridge/"

cat > "$bin/prometheus" <<LAUNCHER
#!/bin/sh
export PROMETHEUS_BRIDGE_DIR="$app/bridge"
exec "$app/venv/bin/python" -m prometheus "\$@"
LAUNCHER
chmod +x "$bin/prometheus"

install -m 644 "$repo/prometheus/resources/prometheus.svg" "$data/icons/hicolor/scalable/apps/prometheus.svg"
sed "s|^Exec=.*|Exec=$bin/prometheus|" "$repo/packaging/prometheus.desktop" > "$data/applications/prometheus.desktop"
command -v update-desktop-database >/dev/null && update-desktop-database "$data/applications" 2>/dev/null || true

echo "Installed. Start PROMETHEUS from your application menu."

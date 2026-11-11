#!/usr/bin/env bash
set -euo pipefail

version=0.2.1
asset="umbratlas-${version}.tar.gz"
release="https://github.com/DarolCup/umbratlas/releases/download/v${version}"
data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
bin_dir="${UMBRATLAS_BIN_DIR:-$HOME/.local/bin}"
app_dir="$data_home/umbratlas"
desktop_id="io.github.darolcup.umbratlas"
source_dir=
if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
    source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
fi
scratch=
staged=
pending_link=

cleanup() {
    if [[ -n "$scratch" ]]; then rm -rf -- "$scratch"; fi
    if [[ -n "$staged" ]]; then rm -rf -- "$staged"; fi
    if [[ -n "$pending_link" ]]; then rm -f -- "$pending_link"; fi
}
trap cleanup EXIT

if [[ -z "$source_dir" || ! -f "$source_dir/umbratlas/__main__.py" || ! -f "$source_dir/assets/$desktop_id.desktop" ]]; then
    scratch="$(mktemp -d)"
    curl --fail --silent --show-error --location "$release/$asset" -o "$scratch/$asset"
    curl --fail --silent --show-error --location "$release/SHA256SUMS" -o "$scratch/SHA256SUMS"
    (
        cd -- "$scratch"
        sha256sum --strict --ignore-missing -c SHA256SUMS
        tar -xzf "$asset"
    )
    source_dir="$scratch/umbratlas-${version}"
fi

if ! /usr/bin/python3 -c 'from PySide6.QtWidgets import QApplication' 2>/dev/null; then
    if ! command -v dnf >/dev/null; then
        echo "Установи Python 3 и PySide6 из репозитория своего дистрибутива." >&2
        exit 1
    fi
    echo "Устанавливаем системные Python и Qt. Fedora может запросить пароль sudo."
    sudo dnf install -y python3 python3-pyside6
    /usr/bin/python3 -c 'from PySide6.QtWidgets import QApplication'
fi

mkdir -p -- "$app_dir/versions" "$bin_dir" "$data_home/applications"     "$data_home/icons/hicolor/256x256/apps"
staged="$(mktemp -d "$app_dir/versions/${version}.XXXXXXXX")"
chmod 755 "$staged"
cp -R -- "$source_dir/umbratlas" "$staged/"
mkdir -- "$staged/assets"
install -m 644 -- "$source_dir/assets/umbratlas.png" "$staged/assets/umbratlas.png"
find "$staged" -type d -name __pycache__ -prune -exec rm -rf -- {} +

cat > "$bin_dir/umbratlas" <<'LAUNCHER'
#!/usr/bin/env bash
set -euo pipefail
app_dir="${XDG_DATA_HOME:-$HOME/.local/share}/umbratlas/current"
cd -- "$app_dir"
exec /usr/bin/python3 -m umbratlas "$@"
LAUNCHER
chmod 755 "$bin_dir/umbratlas"
install -m 644 -- "$source_dir/assets/umbratlas.png"     "$data_home/icons/hicolor/256x256/apps/$desktop_id.png"
# Quote according to the Desktop Entry Exec grammar, including the separate
# string-value escaping pass for literal backslashes.
/usr/bin/python3 - "$source_dir/assets/$desktop_id.desktop" "$data_home/applications/$desktop_id.desktop"     "$bin_dir/umbratlas" <<'PY'
from pathlib import Path
import sys

source, destination, executable = sys.argv[1:]
quoted = executable.replace("%", "%%")
quoted = "".join("\\" + char if char in '\\"$`' else char for char in quoted)
quoted = quoted.replace("\\", "\\\\")
desktop = Path(source).read_text(encoding="utf-8")
Path(destination).write_text(desktop.replace("Exec=umbratlas", f'Exec="{quoted}"'), encoding="utf-8")
PY

pending_link="$app_dir/.current.${staged##*/}"
ln -s -- "versions/${staged##*/}" "$pending_link"
mv -Tf -- "$pending_link" "$app_dir/current"
staged=
pending_link=
printf 'Umbratlas %s установлен. Открой его из меню приложений KDE или запусти:\n%s\n' "$version" "$bin_dir/umbratlas"

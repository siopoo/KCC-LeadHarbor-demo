#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$project_dir"

target_arch="$(uname -m)"
if [[ "$target_arch" != "arm64" && "$target_arch" != "x86_64" ]]; then
  echo "Unsupported macOS architecture: $target_arch" >&2
  exit 1
fi

python3 -m venv .venv-macos
.venv-macos/bin/python -m pip install --upgrade pip
.venv-macos/bin/python -m pip install -r requirements-build.txt
.venv-macos/bin/python -m PyInstaller \
  --noconfirm --clean \
  --target-arch "$target_arch" \
  --distpath dist-macos \
  --workpath build-macos \
  leadharbor-macos.spec

codesign --force --deep --sign - "dist-macos/KCC-LeadHarbor.app"
codesign --verify --deep --strict "dist-macos/KCC-LeadHarbor.app"
ditto -c -k --sequesterRsrc --keepParent \
  "dist-macos/KCC-LeadHarbor.app" \
  "dist-macos/KCC-LeadHarbor-macOS-${target_arch}.zip"
echo "Built: dist-macos/KCC-LeadHarbor-macOS-${target_arch}.zip"

#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$project_dir"

python3 -m venv .venv-linux
.venv-linux/bin/python -m pip install --upgrade pip
.venv-linux/bin/python -m pip install -r requirements-build.txt
.venv-linux/bin/python -m PyInstaller \
  --noconfirm --clean \
  --distpath dist-linux \
  --workpath build-linux \
  leadharbor-linux.spec

tar -C dist-linux -czf dist-linux/KCC-LeadHarbor-Linux.tar.gz KCC-LeadHarbor-Linux
echo "Built: dist-linux/KCC-LeadHarbor-Linux.tar.gz"

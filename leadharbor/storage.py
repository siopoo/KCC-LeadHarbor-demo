from __future__ import annotations

import os
import sys
from pathlib import Path


def app_data_dir() -> Path:
    configured = os.getenv("KCC_LEADHARBOR_DATA_DIR", "").strip()
    if configured:
        path = Path(configured)
    elif getattr(sys, "frozen", False):
        if sys.platform == "win32":
            local_app_data = os.getenv("LOCALAPPDATA", "").strip()
            root = Path(local_app_data) if local_app_data else Path.home()
            path = root / "KCC LeadHarbor"
        elif sys.platform == "darwin":
            path = Path.home() / "Library" / "Application Support" / "KCC LeadHarbor"
        else:
            xdg_data_home = os.getenv("XDG_DATA_HOME", "").strip()
            root = Path(xdg_data_home) if xdg_data_home else Path.home() / ".local" / "share"
            path = root / "KCC LeadHarbor"
    else:
        path = Path(__file__).resolve().parent.parent / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def resource_path(name: str) -> Path:
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return bundle_root / name

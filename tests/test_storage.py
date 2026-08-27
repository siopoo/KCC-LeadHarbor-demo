from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from leadharbor.storage import app_data_dir


class StorageTests(unittest.TestCase):
    def test_frozen_linux_uses_xdg_data_home(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch(
            "leadharbor.storage.sys.frozen", True, create=True
        ), patch("leadharbor.storage.sys.platform", "linux"), patch.dict(
            "leadharbor.storage.os.environ", {"XDG_DATA_HOME": directory}, clear=True
        ):
            self.assertEqual(app_data_dir(), Path(directory) / "KCC LeadHarbor")

    def test_frozen_macos_uses_application_support(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch(
            "leadharbor.storage.sys.frozen", True, create=True
        ), patch("leadharbor.storage.sys.platform", "darwin"), patch(
            "leadharbor.storage.Path.home", return_value=Path(directory)
        ), patch.dict("leadharbor.storage.os.environ", {}, clear=True):
            self.assertEqual(
                app_data_dir(),
                Path(directory) / "Library" / "Application Support" / "KCC LeadHarbor",
            )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from leadharbor.pipeline import LeadPipeline


class FailingSource:
    def discover(self, keyword: str, location: str, limit: int) -> list:
        raise RuntimeError("official directory unavailable")


class PipelineTests(unittest.TestCase):
    def test_all_discovery_failures_are_not_reported_as_zero_result_success(self) -> None:
        pipeline = LeadPipeline(crawl_websites=False, sources=[FailingSource()])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "leads.csv"
            with self.assertRaisesRegex(RuntimeError, "official directory unavailable"):
                pipeline.run("retail contractor", "United States", 100, output)


if __name__ == "__main__":
    unittest.main()

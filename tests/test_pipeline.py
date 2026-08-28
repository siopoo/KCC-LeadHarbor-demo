from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from leadharbor.models import Lead
from leadharbor.pipeline import LeadPipeline, TaskCancelled


class FailingSource:
    def discover(self, keyword: str, location: str, limit: int) -> list:
        raise RuntimeError("official directory unavailable")


class WorkingSource:
    def discover(self, keyword: str, location: str, limit: int) -> list[Lead]:
        return [Lead(name="Texas Builder", market="Texas", company_type="Builder")]


class PipelineTests(unittest.TestCase):
    def test_all_discovery_failures_are_not_reported_as_zero_result_success(self) -> None:
        pipeline = LeadPipeline(crawl_websites=False, sources=[FailingSource()])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "leads.csv"
            with self.assertRaisesRegex(RuntimeError, "official directory unavailable"):
                pipeline.run("retail contractor", "United States", 100, output)

    def test_pipeline_reports_progress_and_honors_cancellation(self) -> None:
        pipeline = LeadPipeline(crawl_websites=False, sources=[WorkingSource()])
        updates: list[tuple[int, str]] = []
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "leads.csv"
            leads = pipeline.run(
                "builder", "Texas", 10, output,
                progress=lambda value, message: updates.append((value, message)),
            )
            self.assertEqual(len(leads), 1)
            self.assertTrue(any(message.startswith("processing:") for _, message in updates))
            with self.assertRaises(TaskCancelled):
                pipeline.run(
                    "builder", "Texas", 10, output, is_cancelled=lambda: True,
                )


if __name__ == "__main__":
    unittest.main()

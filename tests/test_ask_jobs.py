from __future__ import annotations

import time
import unittest

from mflab_knowledge.ask_jobs import AskJobs


class AskJobsTests(unittest.TestCase):
    def _wait(self, jobs: AskJobs, job_id: str) -> dict[str, object]:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            value = jobs.get(job_id)
            assert value is not None
            if value["status"] in {"completed", "failed"}:
                return value
            time.sleep(0.01)
        self.fail("job did not finish")

    def test_publishes_real_progress_and_result(self) -> None:
        jobs = AskJobs()
        try:
            def runner(progress: object) -> dict[str, object]:
                progress({"stage": "scope", "title": "Scope resolved"})
                progress({"stage": "evidence", "title": "Evidence selected"})
                return {"answer": "result"}

            job_id = jobs.submit(runner)
            value = self._wait(jobs, job_id)
        finally:
            jobs.close()

        self.assertEqual(value["status"], "completed")
        self.assertEqual(value["result"], {"answer": "result"})
        self.assertEqual([step["sequence"] for step in value["steps"]], [1, 2])
        self.assertEqual(value["steps"][0]["stage"], "scope")

    def test_does_not_expose_unexpected_exception_details(self) -> None:
        jobs = AskJobs()
        try:
            def runner(_progress: object) -> dict[str, object]:
                raise RuntimeError("secret implementation detail")

            value = self._wait(jobs, jobs.submit(runner))
        finally:
            jobs.close()

        self.assertEqual(value["status"], "failed")
        self.assertNotIn("secret", str(value["error"]))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
ASSETS_ROOT = APP_ROOT.parent / "textlayer-work" / "tutor-assets"

try:
    from playwright.sync_api import sync_playwright  # type: ignore
except Exception:  # pragma: no cover
    sync_playwright = None  # type: ignore


@unittest.skipUnless(os.environ.get("RUN_PLAYWRIGHT_SMOKE") == "1", "Set RUN_PLAYWRIGHT_SMOKE=1 to run browser smoke tests.")
@unittest.skipIf(sync_playwright is None, "Playwright is not installed.")
class PlaywrightSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temp_data_dir = tempfile.TemporaryDirectory(prefix="tax-tutor-e2e-")
        cls.port = int(os.environ.get("E2E_PORT", "8876"))
        env = os.environ.copy()
        env["TAX_TUTOR_ASSETS_ROOT"] = str(ASSETS_ROOT)
        env["TAX_TUTOR_DATA_ROOT"] = cls._temp_data_dir.name
        cls.proc = subprocess.Popen(
            ["python3", "app.py", "--host", "127.0.0.1", "--port", str(cls.port)],
            cwd=str(APP_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(1.5)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
        cls._temp_data_dir.cleanup()

    def test_dashboard_tabs_load(self) -> None:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(f"http://127.0.0.1:{self.port}/", wait_until="networkidle", timeout=20000)
            self.assertEqual(page.title(), "Tax Tutor")
            for view in ("practice", "plan", "course", "today"):
                page.locator(f'[data-dashboard-view="{view}"]').click()
                page.wait_for_timeout(150)
            self.assertGreaterEqual(page.locator("[data-dashboard-view]").count(), 4)
            browser.close()


if __name__ == "__main__":
    unittest.main()

"""用可用的 Chrome 检查真实浏览器中的空历史和趋势渲染。"""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest

import history
import test_history


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


@unittest.skipUnless(shutil.which("google-chrome"), "未安装 google-chrome；仍执行数据与页面模型测试")
class BrowserTests(unittest.TestCase):
    def render(self, populated):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            if populated:
                fixture = test_history.HistoryTests()
                fixture.setUp()
                record = fixture.record()
                history.write_json(root / "source/history" / (record["id"] + ".json"), record)
            history.site(root / "source", root / "site")
            handler = partial(QuietHandler, directory=str(root / "site"))
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                result = subprocess.run([
                    "google-chrome", "--headless", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
                    "--no-proxy-server", "--no-first-run", "--user-data-dir=" + str(root / "chrome"),
                    "--virtual-time-budget=3000", "--dump-dom", f"http://127.0.0.1:{server.server_port}/",
                ], capture_output=True, text=True, timeout=30, check=True)
                return result.stdout
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_empty_history_displays_first_run_message(self):
        dom = self.render(False)
        self.assertIn("尚无 CI 性能记录", dom)
        self.assertNotIn("加载失败：", dom)

    def test_populated_history_renders_chart_table_and_raw_download(self):
        dom = self.render(True)
        self.assertIn('<svg viewBox="0 0 960 380"', dom)
        self.assertEqual(dom.count("<circle "), 4)
        self.assertIn("15 ms", dom)
        self.assertIn('href="history/123-1-aaaaaaaaaaaa.json"', dom)
        self.assertIn("Test CPU", dom)
        self.assertNotIn("加载失败：", dom)


if __name__ == "__main__":
    unittest.main()

"""验证独立构建会继承本地 workspace，同时保留原始配置。"""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import build


class BuildTests(unittest.TestCase):
    def test_existing_workspace_keeps_canvas_and_deduplicates_ofd(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ofd = root / "ofd"
            canvas = root / "canvas"
            for path in (ofd, canvas):
                path.mkdir()
                (path / "go.mod").write_text("module example.test/module\n", encoding="utf-8")
            work = ofd / "go.work"
            original = "go 1.26.0\nuse (. ../canvas)\n"
            work.write_text(original, encoding="utf-8")
            config = {"Use": [{"DiskPath": "."}, {"DiskPath": "../canvas"}], "Replace": None}
            with patch.object(build, "run", side_effect=[str(work), json.dumps(config)]):
                selected = build.workspace_modules(ofd, canvas)
            self.assertEqual(selected, [build.ROOT, ofd, canvas])
            self.assertEqual(work.read_text(encoding="utf-8"), original)

    def test_checkout_without_workspace_uses_only_selected_source(self):
        with tempfile.TemporaryDirectory() as directory:
            ofd = Path(directory)
            (ofd / "go.mod").write_text("module github.com/zc310/ofd\n", encoding="utf-8")
            with patch.object(build, "run", return_value=""):
                self.assertEqual(build.workspace_modules(ofd), [build.ROOT, ofd])


if __name__ == "__main__":
    unittest.main()

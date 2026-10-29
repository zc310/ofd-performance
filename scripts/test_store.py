"""在临时的本地 Git 仓库验证持久保存，不连接 GitHub。"""

from contextlib import chdir
import copy
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import history
import test_history


class StoreTests(unittest.TestCase):
    def test_append_and_concurrent_push_preserve_every_record(self):
        fixture = test_history.HistoryTests()
        fixture.setUp()
        # 分别覆盖首次创建数据分支的竞争，以及已有数据分支的追加竞争。
        for seed in (False, True):
            with self.subTest(existing_branch=seed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                identity = {
                    "GIT_AUTHOR_NAME": "Performance Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
                    "GIT_COMMITTER_NAME": "Performance Test", "GIT_COMMITTER_EMAIL": "test@example.invalid",
                    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
                }
                with patch.dict(os.environ, identity):
                    remote = root / "remote.git"
                    source = root / "source"
                    other = root / "other"
                    history.command("git", "init", "--bare", "--initial-branch=main", str(remote))
                    history.command("git", "init", "--initial-branch=main", str(source))
                    history.command("git", "commit", "--allow-empty", "-m", "test source", cwd=source)
                    original_source_head = history.command("git", "rev-parse", "HEAD", cwd=source)
                    history.command("git", "remote", "add", "origin", str(remote), cwd=source)
                    history.command("git", "push", "origin", "HEAD:main", cwd=source)
                    history.command("git", "clone", str(remote), str(other))

                    def record(number):
                        value = copy.deepcopy(fixture.record())
                        value.update(id=f"{number}-1-aaaaaaaaaaaa", run_id=str(number),
                                     run_url=f"https://github.com/zc310/ofd-performance/actions/runs/{number}/attempts/1")
                        path = root / f"record-{number}.json"
                        history.write_json(path, value)
                        return path

                    with chdir(source):
                        if seed:
                            history.store(record(100))
                        pending = record(200)
                        competitor = record(300)
                        original_run = subprocess.run
                        injected = False

                        def race(args, **kwargs):
                            nonlocal injected
                            if args[:3] == ["git", "push", "origin"] and not injected:
                                injected = True
                                with chdir(other):
                                    history.store(competitor)
                            return original_run(args, **kwargs)

                        with patch.object(history.subprocess, "run", side_effect=race):
                            history.store(pending)
                        self.assertTrue(injected)
                        self.assertEqual(history.command("git", "branch", "--show-current"), "main")
                        self.assertEqual(history.command("git", "rev-parse", "HEAD"), original_source_head)
                        exported = root / "exported"
                        history.fetch_history(exported)
                        ids = {path.stem for path in (exported / "history").glob("*.json")}
                        expected = {"200-1-aaaaaaaaaaaa", "300-1-aaaaaaaaaaaa"}
                        if seed:
                            expected.add("100-1-aaaaaaaaaaaa")
                        self.assertEqual(ids, expected)
                        self.assertEqual(history.command("git", "status", "--porcelain"), "")


if __name__ == "__main__":
    unittest.main()

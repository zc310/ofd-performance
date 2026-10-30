"""检查历史数据完整性、分组条件及静态页面生成，不执行性能测量。"""

import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import history


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.config = history.read_json(history.HERE / "ci.json")
        self.raw = {
            "method": self.config["method"],
            "date": "2026-10-29T12:00:00Z", "go_version": "go1.27.3", "gomaxprocs": 1,
            "repeat": 2, "system_fonts_only": True,
            "samples": [{"path": s["path"], "sha256": s["sha256"]} for s in self.config["samples"]],
            "results": [],
        }
        for sample in self.config["samples"]:
            for operation in history.sample_operations(sample):
                for elapsed in (10, 20):
                    self.raw["results"].append({
                        "file": sample["path"], "operation": operation, "elapsed_ms": elapsed,
                        "process_ms": elapsed + 1, "peak_rss_kib": elapsed * 1024,
                        "output_bytes": 0 if operation == "open" else 100,
                        "pages": sample["pages"], "width": 10, "height": 10, "characters": 10,
                    })
        self.environment = {
            "cpu": "Test CPU", "arch": "x86_64", "kernel": "test", "os": "Test OS",
            "affinity_cpus": 4, "runner": "github-hosted", "gogc": "100", "gomemlimit": "off",
            "go_env": {"CGO_ENABLED": "1", "GOAMD64": "v1", "GOFLAGS": "-mod=readonly", "GOEXPERIMENT": ""},
            "font_manifest": [{"path": "selected-fallback", "sha256": "a" * 64}], "limits": {},
        }

    def record(self):
        comparison = history.comparison_key(self.raw, self.environment, self.config)
        return {
            "schema_version": 1, "id": "123-1-aaaaaaaaaaaa", "commit": "a" * 40,
            "repository": "zc310/ofd", "run_id": "123", "attempt": "1", "date": self.raw["date"],
            "workflow_repository": "zc310/ofd-performance", "benchmark_commit": "b" * 40, "ofd_ref": "main",
            "run_url": "https://github.com/zc310/ofd-performance/actions/runs/123/attempts/1",
            "comparison": comparison, "group": history.digest(json.dumps(comparison, sort_keys=True).encode()),
            "environment": self.environment, "raw": self.raw, "summary": history.validate(self.raw, self.config),
        }

    def test_summary_preserves_median_range_and_maximum_rss(self):
        summary = history.validate(self.raw, self.config)
        self.assertEqual(len(summary), 18)
        converted = next(row for row in summary if row["operation"] == "pdf-to-ofd")
        self.assertEqual(converted["median_ms"], 15)
        self.assertEqual(summary[0]["median_ms"], 15)
        self.assertEqual(summary[0]["min_ms"], 10)
        self.assertEqual(summary[0]["max_ms"], 20)
        self.assertEqual(summary[0]["max_rss_mib"], 20)

    def test_incomplete_invalid_or_degraded_results_are_rejected(self):
        variants = []
        missing = copy.deepcopy(self.raw)
        missing["results"].pop()
        variants.append(missing)
        for field, value in (("elapsed_ms", float("nan")), ("peak_rss_kib", -1), ("pages", 99)):
            invalid = copy.deepcopy(self.raw)
            invalid["results"][0][field] = value
            variants.append(invalid)
        for field, value in (("width", 0), ("height", 0), ("output_bytes", 0)):
            invalid = copy.deepcopy(self.raw)
            invalid["results"][2][field] = value
            variants.append(invalid)
        empty_ofd = copy.deepcopy(self.raw)
        converted = next(row for row in empty_ofd["results"] if row["operation"] == "pdf-to-ofd")
        converted["output_bytes"] = 0
        variants.append(empty_ofd)
        wrong_method = copy.deepcopy(self.raw)
        wrong_method["method"] = "unknown-method"
        variants.append(wrong_method)
        changed = copy.deepcopy(self.raw)
        changed["samples"][0]["sha256"] = "0" * 64
        variants.append(changed)
        for index, invalid in enumerate(variants):
            with self.subTest(index=index), self.assertRaises(ValueError):
                history.validate(invalid, self.config)

    def test_environment_changes_split_comparison_groups(self):
        baseline = history.comparison_key(self.raw, self.environment, self.config)
        for field in ("cpu", "os", "kernel", "affinity_cpus", "font_manifest", "limits"):
            environment = copy.deepcopy(self.environment)
            environment[field] = "changed"
            self.assertNotEqual(baseline, history.comparison_key(self.raw, environment, self.config))
        for field, value in (("repeat", 10), ("go_version", "go1.27.4")):
            raw = copy.deepcopy(self.raw)
            raw[field] = value
            self.assertNotEqual(baseline, history.comparison_key(raw, self.environment, self.config))
        changed_method = copy.deepcopy(self.config)
        changed_method["method"] = "ofd-process-v1"
        self.assertEqual(baseline, history.comparison_key(self.raw, self.environment, changed_method))
        fewer_samples = copy.deepcopy(self.raw)
        fewer_samples["samples"] = fewer_samples["samples"][:-2]
        self.assertEqual(baseline, history.comparison_key(fewer_samples, self.environment, self.config))

    def test_display_group_joins_legacy_records_with_different_method_and_samples(self):
        record = self.record()
        legacy = copy.deepcopy(record["comparison"])
        legacy["method"] = "ofd-process-v1"
        legacy["samples"] = sorted([sample["path"], sample["sha256"]]
                                    for sample in self.config["samples"][:4])
        self.assertEqual(history.display_group(record["comparison"]), history.display_group(legacy))

    def test_site_supports_first_run_and_keeps_downloadable_raw_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "site"
            history.site(root / "empty", output)
            self.assertEqual(history.read_json(output / "data.json")["records"], [])
            self.assertTrue((output / "index.html").is_file())
            self.assertFalse((output / "model.test.mjs").exists())
            record = self.record()
            history.write_json(root / "source/history" / (record["id"] + ".json"), record)
            history.site(root / "source", output)
            data = history.read_json(output / "data.json")["records"]
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["summary"], record["summary"])
            self.assertNotIn("raw", data[0])
            self.assertEqual(history.read_json(output / "history" / (record["id"] + ".json")), record)

    def test_history_links_and_group_hash_are_validated_before_publication(self):
        record = self.record()
        history.check_record(record)
        for field, value in (("id", "../../escape"), ("commit", "b" * 40),
                             ("run_url", "javascript:alert(1)"), ("group", "0" * 64)):
            invalid = copy.deepcopy(record)
            invalid[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                history.check_record(invalid)

    def test_missing_remote_branch_is_an_empty_history(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(history, "remote_exists", return_value=False):
                history.fetch_history(directory)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_demo_record_is_rejected_before_accessing_history_branch(self):
        with tempfile.TemporaryDirectory() as directory:
            record = self.record()
            record["demo"] = True
            path = Path(directory) / "demo.json"
            history.write_json(path, record)
            with patch.object(history, "remote_exists") as remote:
                with self.assertRaisesRegex(ValueError, "演示记录"):
                    history.store(path)
                remote.assert_not_called()

    def test_collection_keeps_ofd_commit_separate_from_workflow_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            font = root / "font.ttf"
            font.write_bytes(b"test font")
            raw = copy.deepcopy(self.raw)
            raw["fallback_font"] = {"sha256": history.digest(font.read_bytes())}
            history.write_json(root / "results.json", raw)
            metadata = {
                "ofd_ref": "release-test", "ofd": {"commit": "a" * 40, "status": ""},
                "benchmark": {"commit": "b" * 40, "status": ""},
                "local_modules": {"source": {"status": ""}},
                "go_env": self.environment["go_env"], "modules": "selected OFD modules",
            }
            binary = str(root / "benchmark")
            history.write_json(binary + ".build.json", metadata)
            variables = {
                "PERFORMANCE_BINARY": binary, "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1",
                "GITHUB_REPOSITORY": "zc310/ofd-performance",
            }
            with (patch.dict(os.environ, variables),
                  patch.object(history, "validate_artifacts", return_value=[]),
                  patch.object(history, "font_manifest", return_value=[]),
                  patch.object(history, "command", return_value="binary build info")):
                record = history.collect(root, font, self.config, root / "record.json")
                self.assertEqual(record["repository"], "zc310/ofd")
                self.assertEqual(record["commit"], "a" * 40)
                self.assertEqual(record["benchmark_commit"], "b" * 40)
                self.assertEqual(record["ofd_ref"], "release-test")
                self.assertEqual(record["run_url"], "https://github.com/zc310/ofd-performance/actions/runs/123/attempts/1")
                metadata["local_modules"]["source"]["status"] = " M source.go"
                history.write_json(binary + ".build.json", metadata)
                with self.assertRaises(ValueError):
                    history.collect(root, font, self.config, root / "dirty.json")


if __name__ == "__main__":
    unittest.main()

"""生成可重复的演示数据和静态站点，不运行 OFD 转换。"""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import random
import tempfile

import history


def generate(output):
    config = history.read_json(history.HERE / "ci.json")
    rng = random.Random(20261029)
    # 数值仅用于展示趋势、波动和输出大小变化。
    timings = {
        "open": (145, 55, 56, 82), "png": (690, 100, 115, 305),
        "pdf": (625, 1430, 2190, 585), "text": (160, 87, 88, 87),
    }
    memory = {
        "open": (77, 76, 77, 88), "png": (172, 94, 100, 162),
        "pdf": (167, 415, 380, 242), "text": (86, 92, 97, 90),
    }
    sizes = {
        "open": (0, 0, 0, 0), "png": (440000, 88000, 123000, 298000),
        "pdf": (1930000, 9730000, 8430000, 1580000), "text": (4747, 15702, 518999, 20797),
    }
    total = 0
    with tempfile.TemporaryDirectory(prefix="ofd-performance-demo-history-") as directory:
        for environment_index, (cpu, count, speed) in enumerate((
            ("AMD EPYC 7763 · 演示", 12, 1.0), ("Intel Xeon Platinum 8370C · 演示", 6, 1.18),
        )):
            environment = {
                "cpu": cpu, "arch": "x86_64", "kernel": "demo-kernel", "os": "Ubuntu 24.04 LTS",
                "affinity_cpus": 4, "runner": "demo", "gogc": "100", "gomemlimit": "off", "limits": {},
                "go_env": {"CGO_ENABLED": "1", "GOAMD64": "v1", "GOFLAGS": "-mod=readonly", "GOEXPERIMENT": ""},
                "font_manifest": [{"path": "demo-font", "sha256": "0" * 64}],
            }
            for index in range(count):
                progress = index / (count - 1)
                date = datetime(2026, 10, 18, 10, tzinfo=timezone.utc) + timedelta(days=index)
                raw = {
                    "method": config["method"], "date": date.isoformat().replace("+00:00", "Z"),
                    "go_version": "go1.27.3", "gomaxprocs": 1, "repeat": 10, "system_fonts_only": True,
                    "samples": config["samples"], "results": [],
                }
                for sample_index, sample in enumerate(config["samples"]):
                    for operation in history.OPERATIONS:
                        improvement = (0.32 if operation == "pdf" else 0.10) * progress
                        # 中段出现一次小幅回退，让曲线更接近日常版本变化。
                        regression = 0.13 if index == count // 2 else 0
                        for _ in range(10):
                            jitter = rng.uniform(0.97, 1.04)
                            elapsed = timings[operation][sample_index] * speed * (1 - improvement + regression) * jitter
                            rss = memory[operation][sample_index] * (1 - improvement * .65) * rng.uniform(.98, 1.03)
                            size = sizes[operation][sample_index]
                            if operation == "pdf":
                                size = round(size * (1 - .24 * progress))
                            row = {
                                "file": sample["path"], "operation": operation, "elapsed_ms": round(elapsed, 3),
                                "process_ms": round(elapsed + 7, 3), "peak_rss_kib": round(rss * 1024),
                                "output_bytes": size,
                            }
                            if operation == "open":
                                row["pages"] = sample["pages"]
                            elif operation == "png":
                                row.update(width=1241, height=1754)
                            elif operation == "text":
                                row["characters"] = (2949, 7104, 321999, 11454)[sample_index]
                            raw["results"].append(row)
                commit = hashlib.sha256(f"demo-ofd-{index}".encode()).hexdigest()[:40]
                run_id = str(100000 + environment_index * 1000 + index)
                comparison = history.comparison_key(raw, environment, config)
                record = {
                    "schema_version": 1, "demo": True, "id": f"{run_id}-1-{commit[:12]}",
                    "date": raw["date"], "commit": commit, "repository": "zc310/ofd",
                    "workflow_repository": "zc310/ofd-performance", "benchmark_commit": "d" * 40,
                    "ofd_ref": "演示 · main" if index > 2 else f"演示 · v0.1.{index + 4}",
                    "run_id": run_id, "attempt": "1",
                    "run_url": f"https://github.com/zc310/ofd-performance/actions/runs/{run_id}/attempts/1",
                    "source_status": "", "comparison": comparison,
                    "group": history.digest(json.dumps(comparison, sort_keys=True).encode()),
                    "environment": environment, "summary": history.validate(raw, config), "raw": raw,
                    "note": "合成演示数据，仅用于预览页面，所有数值和提交编号均非实际测量记录。",
                }
                history.write_json(Path(directory) / "history" / (record["id"] + ".json"), record)
                total += 1
        history.site(directory, output)
    print(f"已生成 {total} 次演示记录、2 个环境、每次 16 项摘要。页面目录：{Path(output).resolve()}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="生成站点的目录")
    args = parser.parse_args()
    generate(args.output)


if __name__ == "__main__":
    main()

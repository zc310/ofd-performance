"""性能任务的样本准备、结果验证、历史保存和静态页面生成。只使用 Python 标准库。"""

import argparse
from collections import defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import statistics
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OFD_OPERATIONS = ("open", "png", "pdf", "text")
OPERATIONS = OFD_OPERATIONS + ("pdf-to-ofd",)


def sample_operations(sample):
    return ("pdf-to-ofd",) if sample.get("format") == "pdf" else OFD_OPERATIONS


def command(*args, cwd=None):
    return subprocess.check_output(args, cwd=cwd, text=True, timeout=120).strip()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def download(url):
    if urllib.parse.urlparse(url).scheme != "https":
        raise ValueError("下载地址必须为 HTTPS")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                data = response.read(64 * 1024 * 1024 + 1)
            if len(data) > 64 * 1024 * 1024:
                raise ValueError("单个输入超过下载上限")
            return data
        except OSError:
            if attempt == 2:
                raise
            time.sleep(2)


def prepare(root, font_path, config, fixture_dir=None):
    for sample in config["samples"]:
        path = Path(root) / sample["path"]
        if path.exists() and digest(path.read_bytes()) == sample["sha256"]:
            continue
        if fixture_dir:
            data = (Path(fixture_dir) / sample["path"]).read_bytes()
        else:
            url = ("https://raw.githubusercontent.com/zc310/ofd-fixtures/"
                   + config["fixtures_revision"] + "/" + sample["path"])
            data = download(url)
        if digest(data) != sample["sha256"]:
            raise ValueError(f"样本 SHA-256 不匹配: {sample['path']}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    if fixture_dir:
        return
    font = config["font"]
    data = download(font["url"])
    blob = b"blob " + str(len(data)).encode() + b"\0" + data
    if len(data) != font["bytes"] or hashlib.sha1(blob).hexdigest() != font["git_blob"]:
        raise ValueError("字体与固定 Git 对象不匹配")
    Path(font_path).parent.mkdir(parents=True, exist_ok=True)
    Path(font_path).write_bytes(data)


def validate(raw, config):
    """先确认操作及原始数据齐全，再允许结果进入历史。"""
    if raw.get("method") != config["method"]:
        raise ValueError("原始结果的测量口径与配置不一致")
    expected = {s["path"]: s for s in config["samples"]}
    actual = {s["path"]: s for s in raw["samples"]}
    if len(actual) != len(raw["samples"]) or set(actual) != set(expected):
        raise ValueError("测试样本集合与配置不一致")
    for path, sample in expected.items():
        if actual[path]["sha256"] != sample["sha256"]:
            raise ValueError(f"测试样本内容发生变化: {path}")
    repeat = raw["repeat"]
    if type(repeat) is not int or repeat < 1:
        raise ValueError("无效的测量次数")
    groups = defaultdict(list)
    for row in raw["results"]:
        sample = expected.get(row["file"])
        if sample is None or row["operation"] not in sample_operations(sample):
            raise ValueError("发现未知测试项目")
        for field in ("elapsed_ms", "process_ms", "peak_rss_kib", "output_bytes"):
            value = row[field]
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError(f"无效指标: {field}")
        if row["operation"] == "open" and row["pages"] != expected[row["file"]]["pages"]:
            raise ValueError("页面数量不匹配")
        if row["operation"] == "png":
            if row["width"] <= 0 or row["height"] <= 0 or row["output_bytes"] == 0:
                raise ValueError("首页 PNG 为空")
        if row["operation"] in ("pdf", "pdf-to-ofd") and row["output_bytes"] == 0:
            raise ValueError("文档转换输出为空")
        if row["operation"] == "text" and row.get("characters", 0) <= 0:
            raise ValueError("本组样本文字输出应非空")
        groups[(row["file"], row["operation"])].append(row)
    if set(groups) != {(sample["path"], op) for sample in expected.values() for op in sample_operations(sample)}:
        raise ValueError("测试项目不齐全")
    summary = []
    for (path, operation), rows in groups.items():
        if len(rows) != repeat:
            raise ValueError("测试次数不足或重复")
        elapsed = [r["elapsed_ms"] for r in rows]
        sizes = [r["output_bytes"] for r in rows]
        summary.append({
            "sample": path, "operation": operation, "runs": len(rows),
            "median_ms": statistics.median(elapsed), "min_ms": min(elapsed), "max_ms": max(elapsed),
            "max_rss_mib": max(r["peak_rss_kib"] for r in rows) / 1024,
            "output_bytes": statistics.median(sizes), "min_output_bytes": min(sizes), "max_output_bytes": max(sizes),
        })
    return summary


def validate_artifacts(directory, config):
    checked = []
    for sample in config["samples"]:
        name = Path(sample["path"]).name
        if sample.get("format") == "pdf":
            ofd = Path(directory) / (Path(name).stem + ".ofd")
            try:
                with zipfile.ZipFile(ofd) as archive:
                    root = ET.fromstring(archive.read("Doc_0/Document.xml"))
            except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError) as error:
                raise ValueError(f"OFD 产物无效: {name}: {error}") from error
            pages = root.find("{http://www.ofdspec.org/2016}Pages")
            count = len(pages.findall("{http://www.ofdspec.org/2016}Page")) if pages is not None else 0
            if count != sample["pages"]:
                raise ValueError(f"OFD 页面数量错误: {name}: {count}")
            checked.append({"sample": sample["path"], "ofd_pages": count})
            continue
        pdf = Path(directory) / (name + ".pdf")
        info = command("pdfinfo", str(pdf))
        pages = re.search(r"^Pages:\s*(\d+)$", info, re.MULTILINE)
        if not pages or int(pages[1]) != sample["pages"]:
            raise ValueError(f"PDF 页面数量错误: {name}")
        text = (Path(directory) / (name + ".text")).read_text(encoding="utf-8")
        if not text.strip():
            raise ValueError(f"文字产物为空: {name}")
        if name == "1000-pages.ofd" and not all(x in text for x in ("0001", "1000")):
            raise ValueError("千页文档缺少首尾页码")
        checked.append({"sample": sample["path"], "pdf_pages": int(pages[1]), "text_nonempty": True})
    return checked


def font_manifest(font_path):
    entries = []
    for root in (Path("/usr/share/fonts"), Path("/usr/local/share/fonts")):
        if root.exists():
            for path in sorted(root.rglob("*")):
                if path.is_file() and path.suffix.lower() in (".ttf", ".otf", ".ttc", ".woff", ".woff2"):
                    entries.append({"path": str(path), "sha256": digest(path.read_bytes())})
    entries.append({"path": "selected-fallback", "sha256": digest(Path(font_path).read_bytes())})
    return entries


def comparison_key(raw, environment, config):
    """源码和依赖是比较对象；运行环境决定是否可连线，缺失的样本显示为空点。"""
    return {
        "go": raw["go_version"], "gomaxprocs": raw["gomaxprocs"],
        "repeat": raw["repeat"],
        "system_fonts_only": raw["system_fonts_only"],
        "font_manifest": environment["font_manifest"], "cpu": environment["cpu"],
        "affinity_cpus": environment["affinity_cpus"], "os": environment["os"],
        "arch": environment["arch"], "gogc": environment["gogc"], "gomemlimit": environment["gomemlimit"],
        "cgo": environment["go_env"]["CGO_ENABLED"], "goamd64": environment["go_env"]["GOAMD64"],
        "runner": environment["runner"], "kernel": environment["kernel"],
        "go_flags": environment["go_env"]["GOFLAGS"], "go_experiment": environment["go_env"]["GOEXPERIMENT"],
        "limits": environment["limits"],
    }


def display_group(comparison):
    """忽略旧历史中的测量口径和样本清单，按共同环境重组趋势。"""
    key = {name: value for name, value in comparison.items() if name not in ("method", "samples")}
    return digest(json.dumps(key, sort_keys=True).encode())


def collect(directory, font_path, config, output):
    raw = read_json(Path(directory) / "results.json")
    summary = validate(raw, config)
    artifacts = validate_artifacts(directory, config)
    if not raw.get("system_fonts_only") or raw["gomaxprocs"] != 1:
        raise ValueError("CI 基线要求受控字体目录和 GOMAXPROCS=1")
    if raw["fallback_font"]["sha256"] != digest(Path(font_path).read_bytes()):
        raise ValueError("环境记录中的回退字体与测量输入不一致")
    build = read_json(os.environ["PERFORMANCE_BINARY"] + ".build.json")
    if any(source["status"] for source in build["local_modules"].values()):
        raise ValueError("CI 构建包含未提交源码，拒绝标记为干净提交")
    cpu = next(line.split(":", 1)[1].strip() for line in Path("/proc/cpuinfo").read_text().splitlines()
               if line.startswith("model name"))
    environment = {
        "cpu": cpu, "arch": platform.machine(), "kernel": platform.release(),
        "os": platform.freedesktop_os_release().get("PRETTY_NAME", platform.system()),
        "affinity_cpus": len(os.sched_getaffinity(0)),
        "runner": os.environ.get("RUNNER_ENVIRONMENT", "local"),
        "runner_image": os.environ.get("ImageVersion", ""),
        "gogc": os.environ.get("GOGC", "100"), "gomemlimit": os.environ.get("GOMEMLIMIT", "off"),
        "go_env": build["go_env"],
        "limits": {name: (Path("/sys/fs/cgroup") / name).read_text().strip()
                   for name in ("cpu.max", "memory.max") if (Path("/sys/fs/cgroup") / name).exists()},
        "font_manifest": font_manifest(font_path),
        "dependencies": build["modules"],
        "local_modules": build["local_modules"],
        "build_info": command("go", "version", "-m", os.environ["PERFORMANCE_BINARY"]),
    }
    commit = build["ofd"]["commit"]
    run_id = os.environ["GITHUB_RUN_ID"]
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    repository = "zc310/ofd"
    workflow_repository = os.environ["GITHUB_REPOSITORY"]
    record_id = f"{run_id}-{attempt}-{commit[:12]}"
    key = comparison_key(raw, environment, config)
    record = {
        "schema_version": 1, "id": record_id, "date": raw["date"], "commit": commit,
        "repository": repository, "run_id": run_id, "attempt": attempt,
        "workflow_repository": workflow_repository, "benchmark_commit": build["benchmark"]["commit"],
        "ofd_ref": build["ofd_ref"],
        "run_url": f"https://github.com/{workflow_repository}/actions/runs/{run_id}/attempts/{attempt}",
        "source_status": build["ofd"]["status"],
        "group": digest(json.dumps(key, sort_keys=True).encode()),
        "comparison": key, "environment": environment,
        "fixtures_revision": config["fixtures_revision"], "font_source": config["font"],
        "validation": artifacts, "summary": summary, "raw": raw,
    }
    check_record(record)
    write_json(output, record)
    return record


def markdown(record):
    text = ["## OFD 性能测试", "", f"OFD 提交：`{record['commit'][:12]}` · 测量工具：`{record['benchmark_commit'][:12]}` · 环境组：`{record['group'][:12]}`",
            "", "耗时为独立进程调用的中位数，内存为进程峰值 RSS。托管机器间的结果仅供同环境组参考。", "",
            "| 样本 | 操作 | 中位数 ms | 范围 ms | 峰值 MiB | 输出字节 |", "|---|---|---:|---:|---:|---:|"]
    for row in record["summary"]:
        text.append(f"| {Path(row['sample']).name} | {row['operation']} | {row['median_ms']:.2f} | "
                    f"{row['min_ms']:.2f}–{row['max_ms']:.2f} | {row['max_rss_mib']:.2f} | {row['output_bytes']:.0f} |")
    text.extend(["", "趋势页面：https://zc310.github.io/ofd-performance/", ""])
    return "\n".join(text)


def remote_exists(branch):
    probe = subprocess.run(["git", "ls-remote", "--exit-code", "--heads", "origin", branch],
                           capture_output=True, text=True, check=False, timeout=60)
    if probe.returncode not in (0, 2):
        raise RuntimeError(probe.stderr)
    return probe.returncode == 0


def check_record(record):
    if record.get("schema_version") != 1 or not re.fullmatch(r"[0-9]+-[0-9]+-[0-9a-f]{12}", record["id"]):
        raise ValueError("无效历史记录格式或 ID")
    if not re.fullmatch(r"[0-9a-f]{40}", record["commit"]) or not record["id"].endswith(record["commit"][:12]):
        raise ValueError("历史记录提交不匹配")
    for key in ("repository", "workflow_repository"):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", record[key]):
            raise ValueError("无效仓库名称")
    if not re.fullmatch(r"[0-9a-f]{40}", record["benchmark_commit"]):
        raise ValueError("无效的测量工具提交")
    if not isinstance(record["ofd_ref"], str) or not record["ofd_ref"].strip():
        raise ValueError("缺少请求的 OFD 版本")
    expected_url = f"https://github.com/{record['workflow_repository']}/actions/runs/{record['run_id']}/attempts/{record['attempt']}"
    if (not str(record["run_id"]).isdigit() or not str(record["attempt"]).isdigit()
            or record["run_url"] != expected_url):
        raise ValueError("无效运行链接")
    if record["group"] != digest(json.dumps(record["comparison"], sort_keys=True).encode()):
        raise ValueError("环境分组哈希不匹配")


def store(record_path):
    """仅供受信任的 main 推送任务使用；结果写入独立分支，不更新源码分支。"""
    record = read_json(record_path)
    check_record(record)
    if record.get("demo"):
        raise ValueError("演示记录不能保存到正式历史分支")
    branch = "refs/heads/benchmark-data"
    exists = remote_exists(branch)
    if exists:
        command("git", "fetch", "--depth=1", "origin", branch)
    with tempfile.TemporaryDirectory(prefix="ofd-history-") as parent:
        tree = Path(parent) / "data"
        command("git", "worktree", "add", "--detach", str(tree), "FETCH_HEAD" if exists else "HEAD")
        try:
            if not exists:
                command("git", "switch", "--orphan", "benchmark-data", cwd=tree)
            target = tree / "history" / (record["id"] + ".json")
            if target.exists():
                raise ValueError("历史记录已存在，拒绝覆盖")
            write_json(target, record)
            command("git", "add", str(target.relative_to(tree)), cwd=tree)
            command("git", "commit", "-m", f"perf: record {record['commit'][:12]} run {record['run_id']}", cwd=tree)
            for attempt in range(5):
                push = subprocess.run(["git", "push", "origin", "HEAD:" + branch], cwd=tree,
                                      capture_output=True, text=True, check=False, timeout=120)
                if push.returncode == 0:
                    return
                if attempt == 4:
                    raise RuntimeError(push.stderr)
                command("git", "fetch", "origin", branch, cwd=tree)
                if exists:
                    command("git", "rebase", "FETCH_HEAD", cwd=tree)
                else:
                    command("git", "rebase", "--onto", "FETCH_HEAD", "--root", cwd=tree)
                    exists = True
        finally:
            # tree 为本函数创建的临时工作树；重试失败时也清理其中的重放状态。
            command("git", "worktree", "remove", "--force", str(tree))


def fetch_history(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    branch = "refs/heads/benchmark-data"
    if not remote_exists(branch):
        return
    command("git", "fetch", "--depth=1", "origin", branch)
    names = command("git", "ls-tree", "-r", "--name-only", "FETCH_HEAD", "history").splitlines()
    for name in names:
        if not re.fullmatch(r"history/[0-9]+-[0-9]+-[0-9a-f]{12}\.json", name):
            raise ValueError(f"历史分支包含意外文件: {name}")
        record = json.loads(command("git", "show", f"FETCH_HEAD:{name}"))
        check_record(record)
        write_json(output / name, record)


def site(history, output):
    records = []
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    for path in sorted(Path(history).glob("history/*.json")):
        record = read_json(path)
        check_record(record)
        if path.stem != record["id"]:
            raise ValueError(f"历史记录文件名不匹配: {path}")
        # 页面只加载摘要；每次原始记录仍可按链接下载。
        records.append({key: record[key] for key in
                         ("id", "date", "commit", "repository", "run_url", "summary", "ofd_ref", "benchmark_commit", "workflow_repository")}
                        | {"group": display_group(record["comparison"])}
                        | {"cpu": record["environment"]["cpu"], "go": record["raw"]["go_version"]})
        records[-1]["demo"] = bool(record.get("demo"))
        write_json(output / "history" / path.name, record)
    records.sort(key=lambda r: (r["date"], r["id"]))
    write_json(output / "data.json", {"schema_version": 1, "demo": any(r["demo"] for r in records), "records": records})
    shutil.copytree(ROOT / "web", output, dirs_exist_ok=True, ignore=shutil.ignore_patterns("*.test.mjs"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    prepare_args = commands.add_parser("prepare")
    prepare_args.add_argument("--root", default=".")
    prepare_args.add_argument("--font", required=True)
    prepare_args.add_argument("--fixtures-dir", help="从本地 fixture 目录复制并校验样本，不下载固定字体")
    collect_args = commands.add_parser("collect")
    collect_args.add_argument("--directory", required=True)
    collect_args.add_argument("--font", required=True)
    collect_args.add_argument("--output", required=True)
    store_args = commands.add_parser("store")
    store_args.add_argument("--record", required=True)
    fetch_args = commands.add_parser("fetch")
    fetch_args.add_argument("--output", required=True)
    site_args = commands.add_parser("site")
    site_args.add_argument("--history", required=True)
    site_args.add_argument("--output", required=True)
    args = parser.parse_args()
    config = read_json(HERE / "ci.json")
    if args.action == "prepare":
        prepare(args.root, args.font, config, args.fixtures_dir)
    elif args.action == "collect":
        record = collect(args.directory, args.font, config, args.output)
        if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(summary, "a", encoding="utf-8") as output:
                output.write(markdown(record))
    elif args.action == "store":
        store(args.record)
    elif args.action == "fetch":
        fetch_history(args.output)
    elif args.action == "site":
        site(args.history, args.output)


if __name__ == "__main__":
    main()

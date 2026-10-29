"""在临时 Go workspace 中构建测量程序，沿用被测 OFD 的依赖和本地 workspace。"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parent.parent


def run(*args, cwd, env=None):
    return subprocess.check_output(args, cwd=cwd, env=env, text=True).strip()


def workspace_modules(ofd_dir, canvas_dir=None):
    ofd_dir = Path(ofd_dir).resolve(strict=True)
    modules = [ROOT, ofd_dir]
    # 导入 OFD 已有的 workspace，使本地 canvas 等替换继续生效。
    existing = run("go", "env", "GOWORK", cwd=ofd_dir)
    if existing and existing != "off":
        work = Path(existing)
        config = json.loads(run("go", "work", "edit", "-json", str(work), cwd=ofd_dir))
        if config.get("Replace"):
            raise ValueError("已有 workspace 包含 replace；请先将替换目标加入 use 后再构建")
        for item in config.get("Use", []):
            module = (work.parent / item["DiskPath"]).resolve(strict=True)
            if module not in modules:
                modules.append(module)
    if canvas_dir:
        canvas = Path(canvas_dir).resolve(strict=True)
        if canvas not in modules:
            modules.append(canvas)
    for module in modules:
        if not (module / "go.mod").is_file():
            raise ValueError(f"不是 Go 模块目录: {module}")
    return modules


def source_info(directory):
    directory = Path(directory)
    probe = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=directory,
                           capture_output=True, text=True, check=False)
    if probe.returncode or Path(probe.stdout.strip()).resolve() != directory.resolve():
        return {"commit": None, "status": "unversioned"}
    difference = subprocess.check_output(["git", "diff", "HEAD", "--binary"], cwd=directory)
    untracked = run("git", "ls-files", "--others", "--exclude-standard", cwd=directory).splitlines()
    return {
        "commit": run("git", "rev-parse", "HEAD", cwd=directory),
        "status": run("git", "status", "--porcelain", cwd=directory),
        "diff_sha256": hashlib.sha256(difference).hexdigest(),
        "untracked": {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
                      for name in untracked if (directory / name).is_file()},
    }


def build(ofd_dir, output, canvas_dir=None, ofd_ref="local", run_tests=False):
    modules = workspace_modules(ofd_dir, canvas_dir)
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ofd-performance-build-") as directory:
        environment = dict(os.environ, GOWORK="off")
        run("go", "work", "init", *(str(path) for path in modules), cwd=directory, env=environment)
        environment["GOWORK"] = str(Path(directory) / "go.work")
        if run_tests:
            run("go", "test", ".", "-count=1", cwd=ROOT, env=environment)
        run("go", "build", "-o", str(output), ".", cwd=ROOT, env=environment)
        metadata = {
            "ofd_ref": ofd_ref, "ofd": source_info(Path(ofd_dir).resolve()), "benchmark": source_info(ROOT),
            "modules": run("go", "list", "-m", "-json", "all", cwd=ROOT, env=environment),
            "go_env": json.loads(run("go", "env", "-json", "CGO_ENABLED", "GOAMD64", "GOFLAGS", "GOEXPERIMENT", cwd=ROOT, env=environment)),
            "local_modules": {str(path): source_info(path) for path in modules},
        }
        Path(str(output) + ".build.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # 记录实际选中的模块；Go 的 build info 也保留在二进制中。
        print(run("go", "list", "-m", "-f", "{{.Path}} => {{.Dir}}", "github.com/zc310/ofd", "github.com/zc310/canvas", cwd=ROOT, env=environment))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ofd-dir", required=True)
    parser.add_argument("--canvas-dir")
    parser.add_argument("--output", required=True)
    parser.add_argument("--ofd-ref", default="local", help="记录请求的分支、标签或 SHA；源码由 --ofd-dir 指定")
    parser.add_argument("--test", action="store_true", help="构建前运行测量程序的功能测试")
    args = parser.parse_args()
    build(args.ofd_dir, args.output, args.canvas_dir, args.ofd_ref, args.test)


if __name__ == "__main__":
    main()

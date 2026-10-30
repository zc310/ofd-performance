# ofd-performance

测量 [zc310/ofd](https://github.com/zc310/ofd) 不同版本的处理耗时、峰值内存和输出大小，
使用固定样本和字体，长期保存原始 JSON，并发布交互式性能趋势页面。

本项目关注 OFD 自身的版本变化；多种 OFD 库的转换能力与还原度对比见
[ofd-benchmark](https://github.com/zc310/ofd-benchmark)。

## 手动指定版本

在仓库的 **Actions → Performance → Run workflow** 中填写 `ofd_ref`：

| 输入 | 被测源码 |
|---|---|
| `main`（默认） | 执行时解析出的 main 提交 |
| `v0.1.6` | 对应标签 |
| 完整 commit SHA | 精确指定的提交 |

也可以使用 GitHub CLI，例如：

```bash
gh workflow run performance.yml --repo zc310/ofd-performance -f ofd_ref=main
```

测量工作流仅通过 `workflow_dispatch` 手动触发。测试成功后自动保存历史、更新 Pages。
每次记录包含请求的 `ofd_ref`、最终 OFD 完整 SHA、测量工具 SHA、实际依赖、
Go 构建信息、样本和字体哈希、CPU/系统及各次原始数值。
分支和标签可能移动，复现时使用结果里的完整 SHA。

测量程序依赖 `pkg/converter` 和 `pkg/webreader` 当前提供的接口。
较早版本如果缺少这些 API，构建会失败；测量该版本需选择与它兼容的测量工具提交。
构建失败的版本不会产生成功历史。

## 测量方法

使用 Linux amd64、Ubuntu 24.04、Go stable、CGO_ENABLED=1、GOAMD64=v1，
GOMAXPROCS=1、GOGC=100、GOMEMLIMIT=off。测试顺序执行。
每次记录实际 Go 版本，Go 升级后进入独立环境组。

六份样本及其来源提交、SHA-256 和预期页数固定在 [scripts/ci.json](scripts/ci.json)：

| 样本 | 页数 |
|---|---:|
| `testdata/ofdrw/zsbk.ofd` | 2 |
| `testdata/ofdrw/intro.ofd` | 42 |
| `testdata/ofd-creator/1000-pages.ofd` | 1000 |
| `testdata/ofdrw/y.ofd` | 3 |
| `testdata/standards/GBT_33190-2016.pdf` | 132 |
| `testdata/standards/GBT_42133-2022.pdf` | 14 |

OFD 样本测试四项：打开文档、打开并生成首页 PNG、整份文档转 PDF、提取全文。
PDF 样本测试 PDF 转 OFD。打开文档使用 `webreader.Open`；PNG、PDF、文字及 PDF→OFD 均直接调用 `converter` 包。
首页 PNG 使用 `converter.Convert` 和 `Page(1)`，150 DPI、白色背景，当前编码器压缩级别为 7；
PDF 使用 150 DPI、白色背景、默认压缩和字体子集，
关闭页面并行渲染。文字提取使用默认接口，不启用 OCR。

每项先预跑一次并保存输出，再启动 10 个独立进程正式测量，共 180 条记录。
API 耗时包含输入读取、解析、相应处理和资源关闭，不含编译、进程启动、结果检查及产物落盘。
进程完整耗时另记为 `process_ms`。没有清空操作系统文件缓存，结果不代表磁盘冷读。
耗时展示中位数和范围；内存展示独立进程的最大峰值 RSS，包含 Go 运行时、初始化、结果检查及输出缓冲，
不是累计内存分配量。PDF/TXT 输出写入内存缓冲。

回退字体为 Google Fonts 固定提交中的 Noto Sans SC 可变字体，下载时核对 Git blob 和文件大小。
字体许可链接也在配置中。子进程排除用户字体，保留系统字体目录和指定字体；
全部系统字体清单及 SHA-256 会写入结果。与原有本机历史中的静态 Noto 字体分别比较。

保存前检查测量口径、样本哈希、18 个测试项目及每项次数、非空输出、PNG 尺寸、PDF/OFD 页数、
UTF-8 文字和千页样本的首尾页码。预跑的 PNG 在计时结束后完整解码检查。
不完整或校验失败的结果不会进入成功历史。
这些检查用于发现遗漏输出，外观质量另用渲染回归验证。

## 本地构建与测量

需要 Linux、Go、Python 3.11+；输出检查另需 Poppler 的 `pdfinfo`。
在本项目根目录运行：

```bash
python3 scripts/build.py --ofd-dir ../ofd --output /tmp/ofd-performance
```

构建脚本在临时目录生成 Go workspace，将本项目和指定 OFD 源码加入 `use`。
它同时导入 OFD 已有 workspace 中的模块，因此现有本地 canvas 仍会生效。
也可以显式提供 `--canvas-dir ../third_party/canvas`。
临时 workspace 随构建结束清理，源码目录的 workspace 不被改写。
`go.mod` 中的 OFD 默认版本仅用于模块声明，构建脚本实际使用 `--ofd-dir` 指定的源码。

二进制旁的 `*.build.json` 保存实际模块、源码 SHA、未提交状态及差异哈希。
本地可以构建工作区中的改动；CI 历史只接受干净源码。

准备固定样本与 CI 字体，然后运行测量（结果目录必须尚不存在）：

```bash
python3 scripts/history.py prepare --font /tmp/ofd-font.ttf

GOMAXPROCS=1 GOGC=100 GOMEMLIMIT=off /tmp/ofd-performance \
  -font /tmp/ofd-font.ttf -repeat 10 -out /tmp/ofd-performance-results
```

也可用自己的中文 TTF/OTF 替换字体路径。输入路径相对当前工作目录，因此测量在本项目根目录执行。
`results.json` 保存原始数值；`*.ofd.png`、`*.ofd.pdf`、`*.ofd.text` 是预跑产物。
`-system-fonts-only=false` 可用于观察完整用户字体环境。
本地仅构建或运行下面的测试，不会启动性能测量。

## 历史数据与趋势页面

Performance 工作流使用单独的只读任务构建和测量，再由具有 `contents: write` 的保存任务
将 JSON 提交到本仓库的 `benchmark-data` 分支。每次记录的文件名包含运行 ID、重跑次数和 OFD SHA，
拒绝覆盖已有记录；并发推送冲突会在拉取和重放后重试。
原始 JSON 长期保存，Actions artifact 另保留 30 天的结果和预跑产物。

Pages 工作流在 Performance 成功完成后发布，也可以单独手动运行。
站点地址为 **https://zc310.github.io/ofd-performance/**。
首次启用时，在仓库 **Settings → Pages → Source** 选择 **GitHub Actions**。
历史分支尚不存在时，页面显示等待首次成功测量。

页面支持选择操作和耗时、峰值内存、输出大小，并展示请求版本、OFD 提交、测量工具提交、
Actions 运行及原始 JSON。Go 版本可以单独筛选，不会拆分环境组。只连接相同环境组的结果；
分组包含 CPU、可用 CPU 数、GC/内存限制、系统及内核、字体、重复次数等运行条件。
测量口径或样本集合变化不会拆分环境组；
某次记录缺少的样本在趋势中显示为空点。
OFD 源码和依赖版本是比较对象，保存在元数据中。
GitHub 托管机器仍有频率和共享资源波动，连续多次结果更有参考价值。
修改计时范围、预跑策略或操作参数时，同时递增 `main.go` 和 `scripts/ci.json` 的 `method`。
当前测量口径为 `ofd-process-v2`，各项转换均直接使用 converter。

## 验证与本地预览

```bash
python3 scripts/build.py --ofd-dir ../ofd --test --output /tmp/ofd-performance
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts -p 'test_*.py'
node --test web/model.test.mjs
node --check web/app.mjs
actionlint .github/workflows/performance.yml .github/workflows/pages.yml

python3 scripts/history.py site --history /tmp/ofd-history --output /tmp/ofd-performance-site
python3 -m http.server 8000 --directory /tmp/ofd-performance-site
```

打开 `http://localhost:8000/`。`/tmp/ofd-history/history/` 可放历史分支的 JSON，目录不存在时生成空历史页面。
要先看有曲线的显示效果，可以生成合成演示数据：

```bash
python3 scripts/demo.py --output /tmp/ofd-performance-demo
python3 -m http.server 8000 --directory /tmp/ofd-performance-demo
```

演示包含两个环境、18 次版本记录和全部操作，页面会显示演示标识；演示记录不能写入正式历史分支。
Python 测试在安装了 `google-chrome` 时还会验证真实浏览器渲染；未安装时跳过浏览器部分。
Git 保存测试只使用临时本地仓库。

测量程序成功退出 0，参数或运行错误退出 1，标准 flag 解析错误退出 2。
Python 工具成功退出 0，下载、构建、校验或 Git 操作失败时退出非零。

## 目录

```text
main.go                        Linux 测量程序
scripts/build.py               在临时 workspace 构建指定 OFD 源码
scripts/ci.json                固定输入和测量口径
scripts/history.py             准备输入、收集结果、保存历史和生成站点
scripts/test_*.py              数据、构建、Git 并发保存及浏览器测试
web/                           静态趋势页面及页面模型测试
.github/workflows/performance.yml  手动选择 OFD 版本并测量
.github/workflows/pages.yml        发布趋势页面
```

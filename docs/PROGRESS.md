# Video2Notes 发布检查点

本文件记录已经存在于仓库、测试输出或本地验证产物中的检查点。它不使用“完成百分比”，也不把规划功能写成已交付功能。

## 2026-07-28：从研究基线到可运行的本地流水线

### 仓库与可追溯性

- 根仓库为 `main`，本仓库 Git 作者配置为 `LainElaina <1796137367@qq.com>`。
- BiliNote v2.4.4 的 `6d67e5a76a2c8da1dd73067943d39021ed137c26` 固定为只读 submodule；新代码没有复制上游源码。
- 每次任务创建独立 run 目录；`manifest.json` 记录来源描述、阶段版本/指纹、输入输出 SHA-256、耗时、警告、状态和模型调用信息。
- 阶段输出使用原子写入；已验证输出可按同一 run 的 fingerprint 复用；取消任务不会删除已经落盘的证据。

### 已完成的端到端模块

| 模块 | 当前已交付行为 |
| --- | --- |
| 来源获取 | 本地文件（硬链接/可恢复复制）与 Bilibili、YouTube、X URL；固定版 yt-dlp；可选 Chrome/Edge/Firefox profile 或 Netscape `cookies.txt` 引用。 |
| 安全边界 | 不存储 Cookie 值；CLI/API 脱敏 API key、Bearer、Token、Cookie、SESSDATA；API 只绑定 `127.0.0.1`，使用会话 token，拒绝直接读取任务原始 `manifest.json`。 |
| 媒体与视觉 | `ffprobe` 生成媒体清单；PyAV 保留原始 PTS/time-base；低成本粗扫、候选窗口精扫、稳定关键帧、长扫描协作取消。 |
| 音频与字幕 | FFmpeg 16kHz mono WAV；SRT/VTT 解析；faster-whisper 延迟加载、VAD、词级时间戳/置信度；多个语言提示启用逐段 code-switch 语言识别；仅对低置信、缺失、语言不确定或冲突窗口按模式选择性二次 ASR。 |
| OCR | PaddleOCR 本地目录适配、候选帧 OCR、置信度保守拒绝、文字轨迹与加权 set-cover；PaddleOCR 3.7 + PaddlePaddle 3.3.1 Windows CPU 已实际运行。模型/推理引擎未配置时明确跳过。 |
| 融合与笔记 | 语音停顿 + 视觉边界形成时间窗口；字幕/ASR 冲突保留；事实→草稿→校验的可选角色化 LLM；不存在 LLM 时生成确定性的抽取式证据笔记。 |
| 输出 | `notes/note.md`、`notes/document.json`、`notes/assets/*.jpg`、内嵌图像的离线 HTML、可选 Edge/Chrome PDF；Markdown 保留 evidence ID 注释与时间链接。OCR abstain 时仍按证据窗口保留内容自适应代表帧。 |
| 本地服务 | FastAPI 任务提交/进度/结果/取消、来源探测、浏览器 profile、硬件与耗时估算、provider/secret 管理、运行产物读取。 |
| 界面 | React 19 + Tauri 2 的 Windows 工作台支持真实探测/任务/历史/取消/证据同步播放/截图/导出/Provider/Secret/Role；Tauri 自动启动 256-bit token 保护的 PyInstaller + FFmpeg sidecar，并携带无账号内置样例。 |

### 关键提交

| 提交 | 里程碑 |
| --- | --- |
| `81ce0f6` | 研究基线与 BiliNote 审计 |
| `4a5a2eb` / `e91341c` | 证据、PTS、artifact 和可恢复任务契约 |
| `526d61d` | 本地/Bilibili/YouTube/X 获取 |
| `6511f1c` / `0f2e366` | 物理时间融合、证据约束笔记 |
| `d1f3bc8` / `eae7ade` | 时间校准 ASR 与选择性二次识别 |
| `829cbca` | PTS 绑定的保守 OCR |
| `646b7b6` | Markdown/HTML/PDF 渲染 |
| `0548c9b` / `0cfe38b` | 模型角色路由、硬件与质量模式解耦、耗时区间 |
| `417a6d3` / `b7c62a7` / `7cd163a` | loopback API、持久化结果与 CLI process/serve |
| `b28542d` / `ce02b00` | PaddleOCR 3.7 本地模型、VAD 与逐段多语言 ASR |
| `1224c07` | Provider 真实连接测试 |
| `f8f2e54` | 内容自适应代表帧进入 canonical note assets |
| `5c747f6` | 真实 API 驱动的 React 证据工作台 |
| `99d8ebe` | Tauri 隔离 sidecar 生命周期、原生文件与 artifact 路径 |
| `860a17d` / `aa8d02f` | 自包含 Windows 演示发行、可移植验证与 Vite 进程树回收 |
| `bcb9329` / `cb04b8c` | 严格验证真实 API UI 路径、可复现 sidecar 清单与发行 notices |
| `8a6ddd5` / `e502129` | 干净 Python venv 构建后端与 Tauri 空资源目录修复 |

### 验证记录

- Python 全量：`scripts/test.ps1` 已通过 Ruff、strict mypy、`compileall`、`unittest` + coverage；当次为 **133** 个测试、约 **82%** 覆盖率。
- 定向验证覆盖 API 鉴权/脱敏/取消/结果持久化、CLI 输入与服务、运行时模型解析、硬件计划、音频/流水线与 OCR/ASR 合同。
- PDF：实际生成 HTML→PDF，使用 Poppler 渲染为两页 PNG 并人工检查页面布局。
- 前端：ESLint、TypeScript、Vite production build 和 **12/12** Vitest 通过；真实 API client/store 测试覆盖 token、probe、job、artifact、Provider secret/test 和 role 保存。
- UI：Playwright 显式 demo 主路径与真实 loopback API 主路径通过；`verify.ps1 -Strict` 会自行启动临时 API 和 Vite 并跑两条路径，真实阅读器显示本地视频、自适应关键帧、证据轨与 canonical 导出。
- 干净克隆：在路径含空格、没有 `.venv`/`node_modules`/Tauri target/生成 sidecar 的新克隆中，`bootstrap.ps1 -WithPlaywright`、完整 `verify.ps1 -Strict` 和标准 `build.ps1` 均通过。
- 发行：PyInstaller onedir sidecar 在没有仓库 `.venv`、PATH 仅含 System32 时通过 `--help` 与 token `/api/health`；其 1,104 个 payload 的 size/SHA-256 清单逐项匹配。Tauri MSI/NSIS 均构建成功，最终大小分别为 154,059,279 B 与 112,515,353 B，sidecar 资源约 355.4 MiB。包内已核对后端、样例、项目许可证、第三方 notices、FFmpeg 许可证与构建信息。
- 真实来源：在当次验证时对 X、YouTube、Bilibili URL 分别完成下载、处理与 Markdown/HTML 输出；详细耗时和边界见下表。

| 验证输入 | 媒体时长 | Fast 端到端 | 证据 / 视觉状态 | 特别说明 |
| --- | ---: | ---: | --- | --- |
| X `captainamerica/status/719944021058060289` | 3.18 s | 9.30 s | 1 / 1 | 当次下载阶段占 8.84 s；2026-07-28 最终复探返回 422，平台可用性会变化。 |
| YouTube `jNQXAC9IVRw` | 18.95 s | 14.19 s | 7 / 1 | 发现并解析平台 VTT；源最高可用仅 240p，任务警告已保留。 |
| Bilibili `BV1ru4y1279Y` | 45.00 s | 9.48 s | 1 / 9 | 静音压缩样本，视觉扫描为 6.02 s。 |
| 本地 YouTube 副本 + `faster-whisper-tiny` CPU/int8 | 18.95 s | 约 3.5 s | 3（其中 2 条 ASR）/ 1 | 验证实际本地 ASR 时间戳；tiny 的听写错误也被保留为模型选择风险。 |
| `samples/evidence-demo.mp4` | 9.00 s | Fast 4.29 s | 13 / 3 | 12 条真实 PaddleOCR evidence、3 节、3 张 assets、Markdown/HTML。 |
| 同一内置样例 | 9.00 s | Accurate 9.32 s | 13 / 3 | 更密视觉扫描、3 张 assets、Markdown/HTML、249,037 B PDF。 |

真实验证文件存放在被 Git 忽略的 `artifacts/verification/`；它们不是发布包的一部分，不能替代每台机器的重新验证。

最终无密钥 UI run：Fast `20260728T111917Z-bbe2a4ad7d6d`，Accurate `20260728T111857Z-3fdc1b9b50e4`。Accurate 的阶段时间为视觉 3.77 s、OCR 3.27 s、render 1.74 s；Fast 为视觉 0.58 s、OCR 3.19 s、render 0.01 s（未请求 PDF）。两者均检测 0/3/6 秒的三个稳定文字状态，未记录 `peak_vram_bytes`，因此不虚构显存数据。

最终冻结 sidecar 独立处理 run `20260728T114922Z-13de3d081925`：外部 `PATH` 仅含 System32，数据目录故意含空格；产出 3 张自适应截图、Markdown、HTML 与 263,800 B PDF。最终 `video2notes-desktop.exe` 也实际打开，窗口读取到后端 `0.1.0`；正常关闭后桌面进程、托管 sidecar 和 loopback 端口均消失。

## 当前非阻断边界

1. 需要用户先下载并配置本地 faster-whisper/PaddleOCR 模型；不自动下载模型。PaddleOCR 的 Windows 推理引擎 wheel 与 GPU 支持需要按本机 Python、CUDA、驱动确认。
2. 还没有跨语言、讲座/教程/录屏/短视频等分桶的人工标注评测集，因此没有发布 WER、CER、OCR 准确率或“比通用助手更准”的量化宣称。
3. 受平台、地区、账号及源格式影响，网络来源未必提供高分辨率媒体或字幕；项目不绕过 DRM/访问控制。
4. Windows 安装包已携带自动启动的 Python/FFmpeg sidecar 和内置样例，但不携带 OCR/ASR/LLM 模型或权重；高精度本地引擎仍需源码环境显式安装/配置，避免把数 GB 模型和许可未知权重塞进默认包。
5. `peak_vram_bytes` 契约已存在但尚未接通统一 GPU 采样器；benchmark 将该值记录为空而非 0。
6. 笔记内截图和 `video2notes://seek` 是实验性增强项；Markdown、HTML 与 PDF 是稳定首选输出。
7. 当前本地 MSI/NSIS 未做 Authenticode 签名；个人机器可运行，但 SmartScreen 可能提示未知发布者。

## 复现命令

```powershell
.\scripts\bootstrap.ps1 -WithAsr -WithOcr -WithPlaywright
.\scripts\verify.ps1 -Strict
.\scripts\build.ps1 -Tauri

# 显式数据目录，避免混淆用户应用数据
.\.venv\Scripts\video2notes process ".\samples\evidence-demo.mp4" `
  --mode balanced --data-root "$PWD\data"

$env:VIDEO2NOTES_TOKEN = "a-local-token-with-at-least-16-characters"
.\.venv\Scripts\video2notes serve --data-root "$PWD\data"
```

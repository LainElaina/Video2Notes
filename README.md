# Video2Notes

**面向 Windows 的本地、证据优先的视频转笔记工作台。** 它先把媒体、平台字幕、ASR、OCR 和视觉状态锚定到同一条容器 PTS 时间轴，再从这些可回查证据生成笔记。首要产物是 Markdown；同一份 `NoteDocument` 会确定性渲染为 HTML 和可选 PDF。

当前版本已经是一条可运行的本地流水线，而不是“把字幕直接交给模型”的演示：支持本地视频、Bilibili、YouTube 与 X 链接；支持已登录的本机 Chrome/Edge/Firefox profile 或用户自有 `cookies.txt`；任务可取消、逐阶段落盘、重启后可读取结果。桌面壳使用 React 19 + Tauri 2；开发模式连接同一套 Python loopback API，Windows 安装包会自动启动随包携带的隔离后端和 FFmpeg，不需要另开终端。

> 仅处理用户有权访问、下载和分析的内容。项目不托管 Cookie、不接收账号信息，也不会尝试规避 DRM、访问控制或平台限制。受限内容应使用对应平台允许的方式和你自己的已登录会话。

## 它解决的问题

```text
媒体/链接
  → 下载或本地导入（保留实际格式与可用字幕）
  → ffprobe + PTS 统一时间原点
  → 自适应视觉粗扫/局部精扫，找稳定关键帧
  → 平台字幕 + 本地 ASR + 选择性二次 ASR
  → 本地 PaddleOCR（仅对候选关键帧）
  → 时间重叠融合、冲突保留、证据编号
  → 事实提取 / 草稿 / 校验（可选角色化 LLM）
  → notes/note.md + render/note.html + render/note.pdf（可选）
```

与把视频直接交给通用助手相比，优势在于：下载、解码和模型调用可以在本机复用；画面不是机械地“每 N 秒截一帧”；每个结论能回到其时间段、文字/语音证据或截图；低置信与跨来源冲突会留下警告，而不是被无依据地润色掉。它不是对所有视频都自动保证更准确的黑盒替代品——模型、语言、源清晰度和视频内容仍会决定结果。

## Windows 快速开始

### 1. 前置条件

- Windows 10/11，PowerShell 7 或 Windows PowerShell。
- Python **3.11+**（当前开发机以 Python 3.13 验证）。
- `ffmpeg` 和 `ffprobe` 已加入 `PATH`。它们用于媒体探测、音频提取、解码和视频处理；PDF 由 Edge/Chrome 从 HTML 打印生成。
- Node.js + `pnpm` 仅在构建/运行 React + Tauri 桌面壳时需要；Rust stable + Visual Studio C++ Build Tools 仅在打包 Tauri 时需要。

从仓库根目录执行。标准安装会装 API、开发检查、HTML 渲染、Windows Credential Manager 适配和锁定的桌面依赖；ASR、OCR 与 Playwright 浏览器是显式可选项：

```powershell
# 基础开发环境（含 api / dev / render / secrets）
.\scripts\bootstrap.ps1

# 本地 API + faster-whisper + PaddleOCR
.\scripts\bootstrap.ps1 -WithAsr -WithOcr

# 再安装 Chromium，用于 scripts/verify.ps1 -Strict 的 UI 主路径
.\scripts\bootstrap.ps1 -WithPlaywright
```

脚本会创建原生 Windows `.venv` 并检查 `python`、`ffmpeg`、`ffprobe`。如已有 MSYS 创建的 `.venv`，删除该虚拟环境后重新执行脚本；不要混用两种环境。

### 2. 最小可用：本地视频生成笔记

```powershell
.\.venv\Scripts\video2notes process "D:\Videos\lecture.mp4" `
  --mode balanced `
  --language zh-CN `
  --data-root "$PWD\data"
```

命令输出为 NDJSON：会先给出 `started` 和阶段进度，最后一行 `completed.outputs` 包含绝对路径。上述例子的文件位于：

```text
data/
└─ runs/<run-id>/
   ├─ notes/note.md           # 首要可编辑产物
   ├─ notes/document.json     # 规范化内容模型
   ├─ notes/assets/*.jpg      # 内容自适应选出的本地关键帧
   ├─ render/note.html        # 可离线打开
   ├─ render/note.pdf         # 若本机存在 Edge/Chrome 且未 --no-pdf
   ├─ evidence/timeline.json  # 所有融合后的证据
   ├─ vision/                 # 视觉状态、关键帧与变化事件
   ├─ asr/、ocr/、subtitles/、audio/、media/
   └─ manifest.json           # 任务、阶段哈希、警告与产物清单
```

不传 `--data-root` 时，配置和运行结果位于当前 Windows 用户的应用数据目录（由 `platformdirs` 决定）；不传 `--runs-root` 时使用 `DATA_ROOT\runs`。每个阶段记录输入/输出哈希和指纹，重复在**同一 run 目录**执行可复用已验证的阶段产物。

`--no-screenshots` 关闭实验性的笔记内截图；`--no-pdf` 只产出 Markdown、HTML 和 JSON。按 `Ctrl+C` 会请求协作取消并把任务标记为 cancelled，而不是删除已有证据。

### 3. 无账号、无密钥演示

仓库自带 MIT 许可的 9 秒样例 [`samples/evidence-demo.mp4`](samples/evidence-demo.mp4)。它在 0、3、6 秒出现三种稳定文字画面，专门用于核验内容自适应变化检测、OCR、关键帧、PTS 和导出链；音轨只是安静的合成音，不伪造语音内容。Tauri 安装包的新建任务页可直接点击“内置样例”，源码运行也可选择该文件：

```powershell
.\.venv\Scripts\video2notes process ".\samples\evidence-demo.mp4" `
  --mode accurate --data-root "$PWD\data"
```

样例可用 [`scripts/create_demo_media.ps1`](scripts/create_demo_media.ps1) 从 FFmpeg 确定性重建。未配置 ASR/OCR 时，流水线仍会用元数据和内容自适应视觉状态生成可追溯笔记，并明确警告缺失的本地引擎；按“本地 ASR 与 OCR”一节配置模型后，同一 UI 路径会实际运行识别。

## 输入、下载与本机登录会话

`process` 只接受本地文件，或 Bilibili、YouTube、X/Twitter 的 HTTP(S) 视频 URL。网络来源由固定的平台适配器交给固定版本的 `yt-dlp`；不接受任意 shell、后处理命令或输出模板。

```powershell
# Bilibili / YouTube / X：匿名可访问的链接
.\.venv\Scripts\video2notes process "https://www.bilibili.com/video/BV1ru4y1279Y/" --mode balanced
.\.venv\Scripts\video2notes process "https://www.youtube.com/watch?v=jNQXAC9IVRw" --mode fast
.\.venv\Scripts\video2notes process "https://x.com/captainamerica/status/719944021058060289" --mode accurate
```

有权访问但匿名只能拿到低清版本时，优先使用你**已在正常浏览器中登录**的 profile。程序只枚举 profile 描述；任务运行时由 yt-dlp 临时读取对应浏览器会话，任务清单只保存 profile 引用，不保存 Cookie 内容。

```powershell
# 先启动 API，随后 GET /api/browser-profiles 可列出可用 profile。
# CLI 直接指定浏览器和 profile ID/目录：
.\.venv\Scripts\video2notes process "https://www.youtube.com/watch?v=<id>" `
  --browser edge --profile "Default" --mode balanced

# 也可显式指定你自己导出的 Netscape cookies.txt；不能与 --browser / --profile 同用。
.\.venv\Scripts\video2notes process "https://www.bilibili.com/video/<id>/" `
  --cookie-file "D:\Private\bili.cookies.txt" --mode balanced
```

支持的浏览器标识为 `chrome`、`edge`、`firefox`。Cookie 文件不能置入仓库（`.gitignore` 已排除 `*.cookies.txt`）；日志、任务事件和 API 响应会对 Cookie、Bearer、Token、API key、SESSDATA 等敏感字段脱敏。平台最终只提供低分辨率源时，任务仍会完成，但 `manifest.json` 与笔记会保留“小文字 OCR 可能受限”的警告。

## Fast / Balanced / Accurate

质量意图和硬件能力是两条独立轴：显存不足时系统会降低并发或首轮宽度，并写出警告；不会默默宣称使用了更高精度模型。

| 模式 | 视觉计划 | ASR / 复核 | 关键帧与验证 | 适用场景 |
| --- | --- | --- | --- | --- |
| `fast` | 480px；2 / 6 fps 粗扫/精扫 | 单路主 ASR；不运行二次 ASR | 较少视觉候选；仍可保留每个证据窗口的代表帧 | 快速判断内容价值 |
| `balanced`（默认） | 768px；3 / 12 fps | 只对字幕冲突或语言冲突窗口运行第二路已配置 ASR；可绑定异构模型/供应商 | 中等视觉密度；配置 verifier 时执行证据校验 | 日常高质量笔记 |
| `accurate` | 1080px；6 / 24 fps | 对低置信、缺失、字幕/语言不确定或冲突窗口选择性复核 | 更细候选窗口和 OCR；保留更多独特状态 | 文字密集、需要复核的材料 |

模式不硬编码具体模型文件；`asr.primary`、`asr.secondary`、OCR 和笔记角色分别绑定模型。硬件档位按可检测的显存分为 CPU/iGPU、约 8 GB、约 12 GB、24 GB 以上。`GET /api/system` 返回硬件快照与各模式执行计划；`POST /api/estimate` 再结合输入时长、分辨率和帧率返回**宽耗时区间**，不是虚假的精确 ETA。网络下载、API 排队、画面变化密度与模型本身会显著改变实际耗时。

## 配置本地模型、OpenAI-compatible 与 Ollama

首次运行 API 或 CLI 时，`DATA_ROOT\config\providers.json` 会生成本地默认注册表。它是实际加载的配置；[`config/profiles.example.yaml`](config/profiles.example.yaml) 是带注释的架构参考，不会被运行时直接读取。

### 本地 ASR 与 OCR

安装可选依赖不会替你下载模型，也不会在运行时偷偷联网拉模型。把模型先下载到本地后，在 `providers.json` 的 `models` 中填写目录：

```jsonc
{
  "models": {
    "faster-whisper": {
      "id": "faster-whisper",
      "provider_id": "local",
      "model_id": "faster-whisper",
      "display_name": "Local Whisper",
      "capabilities": ["asr", "language_id", "segment_timestamps", "word_timestamps", "word_confidence"],
      "locality": "local",
      "settings": {
        "engine": "faster_whisper",
        "model_path": "D:/Models/faster-whisper-large-v3",
        "device": "cuda",
        "compute_type": "float16",
        "beam_size": 5,
        "vad_filter": true,
        "multilingual": true,
        "language_detection_threshold": 0.70,
        "language_detection_segments": 3
      },
      "enabled": true
    },
    "paddleocr": {
      "id": "paddleocr",
      "provider_id": "local",
      "model_id": "paddleocr",
      "display_name": "Local PaddleOCR",
      "capabilities": ["ocr", "ocr_boxes", "ocr_confidence"],
      "locality": "local",
      "settings": {
        "engine": "paddleocr",
        "detection_model_dir": "D:/Models/ppocr/det",
        "recognition_model_dir": "D:/Models/ppocr/rec",
        "language": "ch",
        "device": "cpu",
        "enable_mkldnn": false
      },
      "enabled": true
    }
  }
}
```

`faster-whisper` 使用 `local_files_only=True`，因此 `model_path` 必须是实际存在的本地目录；CPU 可设 `device: "cpu"` 和 `compute_type: "int8"`。一个语言提示会固定语言；多个提示会启用 faster-whisper 1.2.1 的 multilingual 解码，并把逐段语言、概率与检测方法写入 evidence。默认 VAD 以 500 ms 静音形成自然短句边界，低语言概率/语言冲突只在模式允许时触发第二 ASR。

PaddleOCR 的 detector 和 recognizer 目录也必须真实存在且含模型文件。当前 Windows CPU 验证组合是 PaddleOCR 3.7 + PaddlePaddle 3.3.1；为避开该组合已观察到的 oneDNN 算子错误，示例设置 `enable_mkldnn: false`。GPU Paddle wheel 需按本机 CUDA 单独安装后再改成 `gpu:0`。若依赖或模型未正确安装，运行时会报告 OCR 未配置并保守跳过，不会下载模型或伪造文本。

### OpenAI-compatible / Ollama 的笔记角色

Provider 与 Model 是分开的；Model 必须声明其能力，role 绑定会在保存时校验。笔记路径可分别绑定 `notes.fact_extractor`、`notes.drafter` 和 `notes.verifier`；前两者任一不可用时，系统使用确定性的抽取式证据笔记并发出警告。

下面是实际 JSON schema 的精简示例。不要把 API key 写进 JSON：通过 API 的 `PUT /api/providers/{provider_id}/secret` 写入 Windows Credential Manager，配置中只留下 `credential_ref`。

```jsonc
{
  "providers": {
    "local": { "id": "local", "display_name": "Local engines", "kind": "local", "locality": "local", "enabled": true },
    "llm": {
      "id": "llm", "display_name": "Compatible LLM", "kind": "openai_compatible",
      "base_url": "https://your-endpoint.example/v1", "endpoint_style": "chat_completions",
      "credential_ref": "keyring://Video2Notes/providers/llm", "request_timeout_seconds": 180,
      "locality": "cloud", "enabled": true
    },
    "ollama": {
      "id": "ollama", "display_name": "Ollama", "kind": "ollama",
      "base_url": "http://127.0.0.1:11434/v1", "endpoint_style": "chat_completions",
      "locality": "local", "enabled": true
    }
  },
  "models": {
    "note-llm": {
      "id": "note-llm", "provider_id": "llm", "model_id": "configure-model-id",
      "display_name": "Evidence note model", "capabilities": ["text", "structured_output", "long_context"],
      "locality": "cloud", "context_window": 65536, "settings": {}, "enabled": true
    }
  },
  "roles": {
    "notes.fact_extractor": { "role": "notes.fact_extractor", "primary_model_id": "note-llm", "fallback_model_ids": [] },
    "notes.drafter": { "role": "notes.drafter", "primary_model_id": "note-llm", "fallback_model_ids": [] },
    "notes.verifier": { "role": "notes.verifier", "primary_model_id": "note-llm", "fallback_model_ids": [] }
  }
}
```

Ollama 同样走 OpenAI-compatible Chat Completions 适配层；前提是所选 Ollama 服务/模型确实提供该接口并满足对应 role 的能力。LLM 返回的事实卡和草稿只可引用现有 evidence ID；校验层拒绝无效引用并回退到可追溯内容。

## 本地 API 与桌面壳

启动经过令牌保护的 loopback 服务（只绑定 `127.0.0.1:43119`）：

```powershell
$env:VIDEO2NOTES_TOKEN = "请使用至少16字符的随机本地令牌"
.\.venv\Scripts\video2notes serve --data-root "$PWD\data" --port 43119
```

若不设置令牌，服务会生成一个短生命周期的私有 token 文件，并在退出时清理；它不会把 token 输出到控制台。除 `/api/health` 外，所有 API 都需要 `X-Video2Notes-Token: <token>`。可用端点包括：

- `GET /api/system`、`GET /api/runtime`、`GET /api/browser-profiles`
- `POST /api/sources/probe`、`POST /api/estimate`
- `GET/PUT /api/providers`、`POST /api/providers/{id}/test` 及 provider secret 状态/写入/删除
- `POST /api/jobs`、`GET /api/jobs/{run_id}`、`GET /api/jobs/{run_id}/result`、`POST /api/jobs/{run_id}/cancel`
- `GET /api/runs` 与受路径校验保护的 `GET /api/runs/{run_id}/artifact?path=...`

推荐从仓库根目录使用组合脚本：

```powershell
.\scripts\dev.ps1              # loopback API + Vite
.\scripts\dev.ps1 -Tauri       # Tauri 开发窗口；脚本管理本次开发 API
.\scripts\build.ps1            # Python wheel + Vite 生产构建
.\scripts\build.ps1 -Tauri     # 自包含后端 + FFmpeg + Tauri 安装包
```

Tauri 启动时创建 256-bit 内存会话令牌，选择空闲 loopback 端口，隐藏启动 Python sidecar，并在桌面程序退出时终止它。令牌只通过子进程环境变量传递。原生文件选择器只返回绝对路径；本地视频与截图 URL 经过 run 目录 canonicalize/范围检查。

发布构建的 sidecar 是 PyInstaller onedir 包，携带固定版本的 Python 业务代码以及构建机提供的 `ffmpeg.exe`/`ffprobe.exe`。它不打包 Cookie、运行数据、下载视频、API Key、OCR/ASR 模型或大模型权重。安装包可以直接打开并处理内置样例；若要实际 ASR/OCR，请使用 `bootstrap.ps1 -WithAsr -WithOcr` 的源码环境和本地模型，或自行制作包含已获许可模型运行时的内部构建。

构建脚本会从 FFmpeg 二进制目录或其父目录一并查找许可证；若目录布局不同，使用 `-FfmpegLicensePath` 显式指定。发行包还包含项目 MIT 许可证、第三方 notices、完整 `ffmpeg -version` 输出和对应源码引用。

构建产物位于 `apps/desktop/src-tauri/target/release/bundle/{msi,nsis}/`。本机最终验证的含样例 MSI 为 **154,042,761 B**，NSIS 为 **112,509,531 B**；安装后不依赖系统 Python、仓库 `.venv` 或 PATH 中的 FFmpeg。sidecar 原始资源约 355.4 MiB，主要体积来自两个静态 FFmpeg 工具。

## 测试与构建

```powershell
# Python：Ruff、strict mypy、compileall、coverage + unittest
.\scripts\test.ps1

# Python + React + Rust + Playwright 主路径
.\scripts\verify.ps1 -Strict

# 构建并 smoke 测试独立后端，再产出 Windows 安装包
.\scripts\build.ps1 -Tauri
```

截至 2026-07-28，完整 Python 验证已通过 **133** 个测试（约 **82%** 覆盖率）；React 已通过 ESLint、TypeScript、**12** 个 Vitest、Vite build，Rust 已通过 `cargo fmt --check`/`cargo check`。`verify.ps1 -Strict` 会先跑显式 demo，再启动临时 token 保护的真实 loopback API，用可再分发样例从 UI 生成并打开真实笔记。请在自己的机器上重新执行这些命令；下载器和 GPU 推理依赖外部平台、驱动和模型，不能由仓库中的历史结果替代。

最终冻结 sidecar 还在 `PATH` 仅含 System32、数据目录含空格的条件下直接处理同一内置样例，run `20260728T114922Z-13de3d081925` 生成 3 张自适应截图、Markdown、HTML 和 263,800 B PDF。最终桌面 exe 实际打开后显示后端 `0.1.0`；通过窗口关闭后，托管 sidecar 与 loopback 监听均已退出。

## 已实测的运行证据（不是精度宣称）

使用 2026-07-28 的开发机（Ryzen 9 9950X3D、RTX 5090 D v2 24,455 MiB、约 100.5 GB RAM）执行了真实平台和本地流水线。下表是当时网络、源分辨率、模型和缓存状态下的观察值，**不能外推为通用性能或识别精度**。

可再分发样例在相同媒体、相同本地 PaddleOCR 配置下完成了 Fast/Accurate 对照；“总阶段时间”是 manifest 中各 stage wall time 之和：

| 输入 | 模式 | 媒体 | 总阶段时间 | 视觉 | OCR | 输出与可见差异 |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| `samples/evidence-demo.mp4` | Fast | 9.00 s | **4.29 s** | 0.58 s；3 状态 | 3.19 s；12 条 | 13 条融合证据、3 节、3 张 assets、Markdown + HTML |
| 同一文件 | Accurate | 9.00 s | **9.32 s** | 3.77 s；3 状态 | 3.27 s；12 条 | 更密集视觉扫描、13 条融合证据、3 节、3 张 assets、Markdown + HTML + 249,037 B PDF |

两次 run 分别为 `20260728T111917Z-bbe2a4ad7d6d` 与 `20260728T111857Z-3fdc1b9b50e4`；关键帧位于 0/3/6 秒附近，OCR 能区分 `EVIDENCE FIRST`、`SCREEN TEXT CHANGED`、`ASR + OCR ALIGNED`。该样例音轨是合成音，因此正确结果是 ASR abstain；它用于验证视觉文字变化而不是 WER。

真实来源验证：

| 来源 | 模式 | 媒体时长 | 当次端到端 | 证据 / 视觉状态 | 说明 |
| --- | --- | ---: | ---: | --- | --- |
| X `captainamerica/status/719944021058060289` | Fast | 3.18 s | 9.30 s | 1 / 1 | 当次实际下载成功；2026-07-28 最终复探返回 422，说明平台可用性会变化。 |
| YouTube `jNQXAC9IVRw` | Fast | 18.95 s | 14.19 s | 7 / 1 | 实际下载和平台 VTT；匿名最高仅 240p，低清警告被保留。 |
| Bilibili `BV1ru4y1279Y` | Fast | 45.00 s | 9.48 s | 1 / 9 | 实际下载；静音压缩样本，视觉扫描 6.02 s。 |

下载后的 18.95 秒 YouTube 媒体还分别跑过真实 Fast/Accurate：本地 `faster-whisper-tiny` CPU/int8 得到 2 个词级时间戳 ASR 片段；PaddleOCR 3.7 + PaddlePaddle 3.3.1 CPU 实际执行；Accurate 作业约 13.6 秒并生成 2 个内容自适应 assets 与 274,227 B PDF。tiny 模型有可见听写错误，因此它只证明引擎、PTS 与产物闭环，不是高精度模型推荐。另用人工绘制测试帧实测 OCR 文本置信度约 0.98；HTML→Edge PDF 也已用 Poppler 渲染后人工检查。

## 已知非阻断限制

- **模型准备是显式的。** 默认注册表只给出本地引擎占位；没有模型目录、Paddle 推理引擎或可用 provider credential 时，对应模块会保守跳过。Markdown/HTML 仍会从已有字幕、元数据和视觉状态生成。
- **OCR/ASR 质量尚未用带标注的大规模真实语料校准。** 自适应取帧、PTS 对齐、置信度和选择性复核的机制已经落地，但 WER/CER、专有名词和截图选择还需要按语言/内容类别建立评测集。
- **平台可用性和清晰度受账号、地区、站点更新、源文件及平台政策影响。** 下载器会记录可用格式与低清警告；不支持 DRM 绕过。
- **安装包不携带模型权重。** React/Tauri 安装包包含可自动启动的 Python/FFmpeg sidecar 和内置样例，但为控制体积、许可边界与显式模型选择，不包含 PaddleOCR/faster-whisper 包及权重；源码环境的 `-WithAsr -WithOcr` 是当前高精度本地推理入口。
- **显存峰值目前为空。** stage manifest 已有 `peak_vram_bytes` 字段，但通用 CPU/Paddle/faster-whisper 路径尚未接统一显存采样器；因此 benchmark 不虚报 0 B。
- **当前本地安装包未做 Authenticode 签名。** 个人本机安装不影响功能，但 Windows SmartScreen 可能显示未知发布者；公开分发前应配置受信任的代码签名证书。
- **HTML 的 `video2notes://seek/...` 链接是桌面集成协议。** 在普通浏览器中仍可阅读 HTML，但该自定义跳转需要宿主应用实现；截图是实验性、按证据预算选择的附加信息。

## 参考与版权

- [`reference/BiliNote`](reference/BiliNote) 是固定在 `6d67e5a` 的只读研究 submodule（上游 MIT）；当前代码为新的实现，未复制其源码。
- 详细技术背景：[`docs/01-bilinote-audit.md`](docs/01-bilinote-audit.md)、[`docs/02-system-architecture.md`](docs/02-system-architecture.md)、[`docs/03-evaluation-and-hardware-profiles.md`](docs/03-evaluation-and-hardware-profiles.md)。
- 第三方材料与依赖说明见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。

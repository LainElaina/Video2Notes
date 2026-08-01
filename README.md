# Video2Notes

**面向 Windows 的本地、证据优先的视频转笔记工作台。** 它先把媒体、平台字幕、ASR、OCR 和视觉状态锚定到同一条容器 PTS 时间轴，再从这些可回查证据生成笔记。首要产物是 Markdown；同一份 `NoteDocument` 会确定性渲染为 HTML 和可选 PDF。

当前版本已经是一条可运行的本地流水线，而不是“把字幕直接交给模型”的演示：支持本地视频、Bilibili、YouTube 与 X 链接；支持已登录的本机 Chrome/Edge/Firefox profile 或用户自有 `cookies.txt`；任务可取消、逐阶段落盘、重启后可读取结果。桌面壳使用 React 19 + Tauri 2；开发模式连接同一套 Python loopback API，Windows 安装包会自动启动随包携带的隔离后端和 FFmpeg，不需要另开终端。

桌面工作台现有两种亮色阅读视图：**简约版**突出视频、目录与正文，**详细版**增加语音、屏幕文字、视觉变化、置信度、阶段指标和统一时间线。两者读取同一份真实任务数据；内置演示数据会明确标为 Demo，不会伪装成实际模型输出。界面已预留并持久化亮/暗色切换，但当前设计验收基线是亮色，暗色色板与细节尚未精修。

> 仅处理用户有权访问、下载和分析的内容。项目不托管 Cookie、不接收账号信息，也不会尝试规避 DRM、访问控制或平台限制。受限内容应使用对应平台允许的方式和你自己的已登录会话。

## 它解决的问题

```text
媒体/链接
  → 下载或本地导入（保留实际格式与可用字幕）
  → ffprobe + PTS 统一时间原点
  → 自适应 / 固定间隔 / 跳过视觉采样，可按时间段覆盖
  → 平台字幕 + 本地 ASR + 选择性二次 ASR
  → 本地 PaddleOCR（仅对候选关键帧）
  → 时间重叠融合、冲突保留、证据编号
  → 可选局部视觉重扫 / ASR 重转写 / 人工证据修订
  → 绑定用户补充的文字与图片资料
  → 按报告 preset 做事实提取 / 草稿 / 校验（可选角色化 LLM）
  → Markdown + HTML + 可选 PDF；后续重生成保留不可变 revision
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
   ├─ supporting/             # 用户绑定的文字/图片补充资料与索引
   ├─ operations/             # 局部返工请求、结果与局部产物
   ├─ revisions/evidence/     # 返工产生的不可变证据 revision
   ├─ revisions/notes/        # 后续重生成的不可变报告 revision
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

## 采样计划、局部返工与报告 revision

质量模式决定整条流水线的默认预算；画面采样计划则允许进一步指定**某一段视频如何取帧**。新建任务时可选：

- `adaptive`：默认方式，基于画面状态和文字变化寻找稳定关键帧；
- `fixed_interval`：按固定间隔取帧，界面提供 0.1、0.5、1 秒快捷值，也可输入不小于 0.1 秒的值；
- `skip`：该段不做视觉取样，仍可继续处理音频、字幕等来源；
- 时间段覆盖：在全局默认方式之上，为互不重叠的时间段单独指定上述模式。

固定间隔是针对已知高信息密度片段的可控工具，不是默认的“无脑全片抽帧”。计划使用整数微秒落盘并按媒体真实时长校验；单个任务的固定取样预算上限为 5,000 帧，防止 0.1 秒间隔误用于长视频而失控。

已完成的 run 可以只返工选中区间，而不重跑整条任务：

- 视觉重扫：选择自适应或固定间隔，并可决定是否同时重跑 OCR；
- ASR 重转写：只提取并识别所选音频区间，可提供语言提示；
- 人工证据修订：对当前有效 evidence 写入新文本，不覆盖原记录。

成功操作会生成完整、不可变的 evidence revision，并把指定区间/模态的新结果设为当前有效视图；被替代证据仍保留用于审计。失败操作会留下失败记录，但不会激活半成品 revision。返工完成后报告**不会静默自动重写**，阅读器会提示用户显式重生成报告。

每个 run 还可绑定来自评论区、网页或本地研究的补充文字和 PNG/JPEG/WebP 图片，并可选绑定到精确时间段。资料保存在本机 run 目录，删除采用状态化记录；生成新报告时，活动资料会进入受 evidence 约束的内容组织和“补充资料”附录。

报告提供五种 preset：`concise`（简洁）、`detailed`（详细）、`professional`（专业）、`beginner`（入门）和 `executive`（领导/决策）。它们改变受众、章节/要点预算、术语解释和编辑目标，不降低“只能引用现有证据与补充资料”的约束。Markdown 是不可关闭的 canonical 输出；HTML 同源渲染，PDF 可选。对完成任务再次生成报告时，服务会读取当前 active evidence revision 并重新融合有效证据，将该 evidence revision ID 与活动资料 ID 一起写入报告记录；产物发布到 `revisions/notes/<revision-id>/` 并原子切换 `latest` 指针，不覆盖最初的 `notes/` 与 `render/` 产物。

## 模型、协议与性能设置

普通使用不需要打开终端、安装 Python 包或手改 JSON。进入桌面的“模型与算力”页即可完成整个配置闭环：

1. 先用“一键准备推荐模型”下载并自动绑定本机适合的 ASR/OCR 权重；程序运行库、FFmpeg 和下载工具已经在 full 便携包中。
2. 如需大模型整理与复核，新增供应商，选择线路协议、认证方式和 Base URL，再把密钥写入 Windows Credential Manager。密钥只写入系统凭据库，注册表只保存引用。
3. 连接测试通过后，可从协议支持的发现端点读取模型 ID，或手动输入服务实际提供的 ID；项目不硬编码远程模型名。
4. 明确确认每个模型真实具备的能力，再绑定事实提取、长文撰写、证据复核、翻译、画面解释等角色。角色下拉框只列出能力集合满足该角色要求且已启用的模型；未知能力不会靠品牌或协议猜测。

内置线路覆盖 OpenAI Responses、OpenAI Chat Completions、OpenAI Audio Transcriptions、Anthropic Messages、Gemini `generateContent`、Gemini Interactions、Ollama 原生 Chat、OpenAI-compatible 服务和实验性自定义 HTTP。OpenAI-compatible 可用于采用相同线协议的自建网关或第三方服务，并可配置 Bearer、`x-api-key`、`x-goog-api-key` 或受限制的自定义认证头。只有实现了结构化生成适配器的协议才会出现在笔记生成相关绑定中；音频转录与实验性通用 HTTP 不会伪装成可用的结构化笔记模型。

本地 ASR 使用 `local_files_only=True`，组件管理器只从固定版本清单下载到 `DATA_ROOT\components`，不会在任务处理中临时拉取未声明模型。一个语言提示会固定语言；多个提示会启用多语言解码，并把逐段语言、概率与检测方法写入 evidence。默认 VAD 按自然短句边界切分，低语言概率或语言冲突只在当前质量模式允许时触发第二 ASR。PaddleOCR detector/recognizer 同样使用组件管理器校验过的本地目录；模型缺失或不完整时会明确降级，不会伪造文本。

“入门模式”根据 CPU、可用内存、GPU/显存、FFmpeg 能力、当前占用和磁盘余量生成安全预算，并允许用户选择节能、均衡或性能倾向以及预留给其他程序的资源；每次任务开始前都会重新检测实时余量。“专业模式”在同一安全预算上开放 CPU/远程并发、GPU 引擎槽位、解码线程、固定采样上限、扫描宽度与频率、OCR/ASR 设备和线程、ASR beam、复核轮次及截图预算等已经接入执行器的参数。当前 PTS 保真视觉解码器使用软件解码；尚无真实批处理或学习型检测执行器的字段不会出现在可编辑界面，后端也拒绝非空覆盖。超过当前安全余量的设置会被钳制并显示原因，而不是让任务把整台电脑占满。

首次运行 API 或 CLI 时仍会在 `DATA_ROOT\config\providers.json` 生成 schema v2 注册表，供高级排障和备份；桌面界面是推荐且完整的配置入口。[`config/profiles.example.yaml`](config/profiles.example.yaml) 只是带注释的架构参考。LLM 返回的事实卡与草稿只能引用已有 evidence/material ID；校验层拒绝无效引用并回退到可追溯内容。

## 本地 API 与桌面壳

启动经过令牌保护的 loopback 服务（只绑定 `127.0.0.1:43119`）：

```powershell
$env:VIDEO2NOTES_TOKEN = "请使用至少16字符的随机本地令牌"
.\.venv\Scripts\video2notes serve --data-root "$PWD\data" --port 43119
```

若不设置令牌，服务会生成一个短生命周期的私有 token 文件，并在退出时清理；它不会把 token 输出到控制台。除 `/api/health` 外，所有 API 都需要 `X-Video2Notes-Token: <token>`。可用端点包括：

- `GET /api/system`、`GET /api/runtime`、`GET /api/browser-profiles`
- `GET /api/configuration-catalog`、`GET/PUT /api/performance`
- `GET /api/components`、`POST /api/components/prepare`
- `GET/PUT /api/providers`、`POST /api/providers/{id}/test`、`GET /api/providers/{id}/discover` 及 provider secret 状态/写入/删除
- `POST /api/sources/probe`、`POST /api/estimate`
- `POST /api/jobs`、`GET /api/jobs/{run_id}`、`GET /api/jobs/{run_id}/result`、`POST /api/jobs/{run_id}/cancel`
- `GET/POST /api/runs/{run_id}/operations`、`GET /api/runs/{run_id}/evidence`
- `GET /api/runs/{run_id}/materials`、`POST .../materials/text`、`POST .../materials/files`、`DELETE .../materials/{material_id}`
- `GET/POST /api/runs/{run_id}/report-revisions`，以及 `GET .../latest`、`GET .../{revision_id}`
- `GET /api/runs` 与受路径校验保护的 `GET /api/runs/{run_id}/artifact?path=...`

桌面 UI 的简约版与详细版只是两种信息密度，不是两套数据：切换视图不会复制 run 或重跑模型。真实任务的阶段指标、警告、证据、补充资料和 operation 记录都由 API 驱动；如果当前是内置演示任务，界面会持续显示 Demo 标记。项目定位为个人本地工具，资料、网站会话引用、媒体和结果不会由本项目上传到 Video2Notes 云服务（本项目也不提供该云服务）；若用户配置第三方模型 API，实际发送范围仍取决于对应 provider 与所绑定角色。

推荐从仓库根目录使用组合脚本：

```powershell
.\scripts\dev.ps1              # loopback API + Vite
.\scripts\dev.ps1 -Tauri       # Tauri 开发窗口；脚本管理本次开发 API
.\scripts\build.ps1            # Python wheel + Vite 生产构建
.\scripts\build_portable.ps1   # 免安装目录；双击顶层 Video2Notes.exe
.\scripts\build.ps1 -Tauri     # 自包含后端 + FFmpeg + Tauri 安装包
```

Tauri 启动时创建 256-bit 内存会话令牌，选择空闲 loopback 端口，隐藏启动 Python sidecar，并在桌面程序退出时终止它。令牌只通过子进程环境变量传递。原生文件选择器只返回绝对路径；本地视频与截图 URL 经过 run 目录 canonicalize/范围检查。

发布构建的 sidecar 是 PyInstaller onedir 包。默认 `full` 构建携带固定版本的 Python 业务代码、`yt-dlp`、`psutil`、`faster-whisper`、CTranslate2、Hugging Face Hub、PaddleOCR、PaddlePaddle，以及构建机提供的 `ffmpeg.exe`/`ffprobe.exe`；便携版用户无需另外安装 Python、FFmpeg 或这些推理包。构建过程只冻结已经安装在仓库 `.venv` 中的运行时，不读取用户模型目录和下载缓存，因此发布前应先执行 `bootstrap.ps1 -WithAsr -WithOcr`。`-CoreOnly` 是明确的开发快速迭代开关，绝不是默认发行配置。

运行时包与模型权重是两层内容：发行包包含执行 ASR/OCR 所需的程序库，但不包含用户选择的 Whisper、PaddleOCR 或远程大模型权重，也不包含 Cookie、任务数据、下载视频或 API Key。唯一精确允许的模型类随包资产是 faster-whisper 上游自带、默认 VAD 路径必需的小型 Silero VAD 文件；它不是用户选择的听写模型。首次使用可在“模型与算力”页点击“一键准备推荐模型”：组件管理器依据检测到的硬件档位下载固定版本的 ASR/OCR 权重到 Windows AppData，支持中断后续传、完整性校验、原子启用和失败恢复，不需要打开终端或手填模型路径。

构建脚本会从 FFmpeg 二进制目录或其父目录一并查找许可证；若目录布局不同，使用 `-FfmpegLicensePath` 显式指定。发行包还包含项目 MIT 许可证、第三方 notices、完整 `ffmpeg -version` 输出和对应源码引用。

免安装版输出到 `artifacts/portable/current/`；只需双击其中的 `Video2Notes.exe`，无需安装程序、注册卸载项、系统 Python 或 PATH 中的媒体工具。顶层 EXE 旁的 `backend/`、`demo/`、`licenses/` 是运行资源，不能单独移动 EXE。默认仍把任务与配置放在 Windows AppData，因此反复覆盖 `current` 不会删除已有结果。完整构建会重新冻结 full sidecar；只改 React/Rust 时可显式使用 `.\scripts\build_portable.ps1 -ReuseSidecar`，但复用的 manifest 必须同样是 full。开发者可用 `.\scripts\build_portable.ps1 -CoreOnly` 缩短冻结时间；另加 `-Zip` 可生成搬运用压缩包。每次构建的 commit、dirty 状态、runtime flavor、组件版本/状态和文件哈希记录在产物自己的 `BUILD_INFO.json`、`backend/manifest.json` 与 `SHA256SUMS.txt` 中。

安装包位于 `apps/desktop/src-tauri/target/release/bundle/{msi,nsis}/`。安装版和免安装版运行时都不依赖系统 Python、仓库 `.venv` 或 PATH 中的 FFmpeg。full sidecar 会明显大于 core-only，因为它真实携带四个推理运行时和原生库；最终大小以构建输出为准。未签名的本地调试构建首次运行时，Windows SmartScreen 可能提示未知发布者；调试构建的变化哈希不硬编码在 README 中，以产物旁的校验文件为准。

## 测试与构建

```powershell
# Python：Ruff、strict mypy、compileall、coverage + unittest
.\scripts\test.ps1

# Python + React + Rust + Playwright 主路径
.\scripts\verify.ps1 -Strict

# 构建并 smoke 测试独立后端，再产出 Windows 安装包
.\scripts\build.ps1 -Tauri

# 构建可反复覆盖、无需安装的 Windows 便携目录
.\scripts\build_portable.ps1

# 仅供开发快速迭代；输出不具备本地 ASR/OCR runtime
.\scripts\build_portable.ps1 -CoreOnly
```

截至 2026-08-01，完整 Python 验证已通过 **217** 个测试；React 已通过 ESLint、TypeScript、**41** 个 Vitest 和 Vite 生产构建，Rust 通过 `cargo fmt --check`/`cargo check`。`verify.ps1 -Strict` 会先跑显式 demo，再启动临时 token 保护的真实 loopback API，用可再分发样例从 UI 生成并打开真实笔记。请在自己的机器上重新执行这些命令；下载器和 GPU 推理依赖外部平台、驱动和模型，不能由仓库中的历史结果替代。

这些命令还在一个路径含空格、没有既有 `.venv`、`node_modules`、Tauri target 或生成 sidecar 的全新本地克隆中重新执行：`bootstrap.ps1 -WithPlaywright`、`verify.ps1 -Strict` 和标准 `build.ps1` 均通过。bootstrap 会先安装 `pyproject.toml` 声明的 setuptools 构建后端；跟踪的空 backend 占位目录允许 Tauri 在 sidecar 尚未冻结前完成干净克隆的 Rust 检查。

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

- **模型权重首次使用时按需下载。** 默认 full 发行包已经包含 faster-whisper/CTranslate2/Hugging Face Hub/PaddleOCR/PaddlePaddle 程序运行时，但刻意不把数 GB、随硬件档位变化的 ASR/OCR 权重塞进每次更新包。应用内“一键准备推荐模型”会下载、校验并自动绑定推荐权重；在准备完成前，对应模块会保守跳过，Markdown/HTML 仍可从已有字幕、元数据和视觉状态生成。
- **OCR/ASR 质量尚未用带标注的大规模真实语料校准。** 自适应取帧、PTS 对齐、置信度和选择性复核的机制已经落地，但 WER/CER、专有名词和截图选择还需要按语言/内容类别建立评测集。
- **平台可用性和清晰度受账号、地区、站点更新、源文件及平台政策影响。** 下载器会记录可用格式与低清警告；不支持 DRM 绕过。
- **便携包不预塞大体积模型权重。** React/Tauri 发行包包含可自动启动的 full Python/FFmpeg sidecar、本地推理与模型下载运行时以及内置样例；Whisper/PaddleOCR 权重由已经内置的组件管理器按机器档位准备到 AppData。开发构建只有显式传入 `-CoreOnly` 才会省略推理运行时。
- **显存峰值目前为空。** stage manifest 已有 `peak_vram_bytes` 字段，但通用 CPU/Paddle/faster-whisper 路径尚未接统一显存采样器；因此 benchmark 不虚报 0 B。
- **当前本地安装包未做 Authenticode 签名。** 个人本机安装不影响功能，但 Windows SmartScreen 可能显示未知发布者；公开分发前应配置受信任的代码签名证书。
- **HTML 的 `video2notes://seek/...` 链接是桌面集成协议。** 在普通浏览器中仍可阅读 HTML，但该自定义跳转需要宿主应用实现；截图是实验性、按证据预算选择的附加信息。
- **暗色主题尚未达到亮色主题的设计完成度。** 切换入口和偏好持久化已经存在，当前优先保证亮色简约版/详细版的一致性；暗色色板、图表对比度和长时间阅读细节仍需单独视觉验收。

## 参考与版权

- [`reference/BiliNote`](reference/BiliNote) 是固定在 `6d67e5a` 的只读研究 submodule（上游 MIT）；当前代码为新的实现，未复制其源码。
- 详细技术背景：[`docs/01-bilinote-audit.md`](docs/01-bilinote-audit.md)、[`docs/02-system-architecture.md`](docs/02-system-architecture.md)、[`docs/03-evaluation-and-hardware-profiles.md`](docs/03-evaluation-and-hardware-profiles.md)。
- 第三方材料与依赖说明见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。

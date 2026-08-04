# Video2Notes

Video2Notes 是一个面向 Windows 的本地视频转笔记工作台。它把视频下载或导入、字幕、语音识别、画面变化、OCR、证据对齐、笔记生成和 Markdown/HTML/PDF 导出串成一条可回查的完整流程。

项目当前版本是 `0.1.0`。首要产物始终是 Markdown；HTML 和 PDF 都从同一份规范化 `NoteDocument` 渲染，避免三种格式各写一遍后内容互相漂移。

> 只处理你有权访问和分析的内容。Video2Notes 不提供 DRM 绕过，也不把你的账号信息上传到项目方服务器。使用第三方大模型 API 时，绑定给该模型的证据会发送到对应供应商，请按供应商的隐私条款自行判断。

## 当前能完成什么

- 导入本地视频，或处理 Bilibili、YouTube、X/Twitter 视频链接。
- 使用游客访问、本机已登录的 Chrome/Edge/Firefox profile，或用户自己的 Netscape `cookies.txt` 获取可访问的视频与平台字幕。
- 在完整音画模式中执行内容自适应取帧、分段固定取帧、PaddleOCR、faster-whisper ASR 和统一时间线融合。
- 在仅音频模式中跳过视觉扫描、OCR 和截图，专注平台字幕、音频和语音识别。
- 提供 Fast、Balanced、Accurate 三档处理预算，并根据机器实时余量、用户预留资源和引擎可用性做安全钳制。
- 对指定时间段单独重做视觉扫描、OCR 或 ASR，也可以人工修订证据文本。
- 把评论区资料、网页摘录、文字或图片作为补充材料绑定到同一个 run。
- 生成简洁、详细、专业、入门和领导/决策五种报告风格。
- 输出 Markdown、HTML，以及在系统存在 Edge/Chrome/Chromium 时输出 PDF。
- 在没有配置 LLM、LLM 调用失败或本地模型缺失时，保守地输出可追溯的抽取式笔记，而不是伪造成功。

当前尚未完成的边界也需要先说明：云 ASR 协议可以登记但还没有接入任务执行器；视频解码仍是保留 PTS 的软件路径；应用内还没有完整的 run/模型清理界面；失败任务不能从失败阶段原地续跑；暗色主题和大规模带人工标注的准确率评测仍在继续完善。

## 三分钟开始：免安装版

在本仓库中，当前可直接运行的程序位于：

```text
artifacts\portable\current\Video2Notes.exe
```

使用步骤：

1. 保持 `Video2Notes.exe` 与旁边的 `backend`、`demo`、`licenses` 等目录在一起，不要只复制一个 EXE。
2. 双击 `Video2Notes.exe`。它不会安装 MSI，也不会写入卸载项。
3. 第一次进入“模型与算力”，检查“运行依赖”和“模型权重”。缺少本地 ASR/OCR 时可一键准备，程序会先显示来源、下载大小、安装后大小和落盘目录。
4. 先在“新建任务”里运行内置 9 秒样例，确认下载器、视觉、OCR、导出和本地后端工作正常。
5. 再粘贴视频链接或选择本地视频，选择处理范围与档位后开始任务。

仓库当前保留的 `artifacts\portable\current` 是兼容旧工作流的 `legacy_full` 单体版，已经携带 Python 后端、FFmpeg/FFprobe、yt-dlp、faster-whisper、PaddleOCR 和 NVIDIA 运行库，所以约为 5.17 GiB。它不是项目长期要求的最小体积。新版发行契约以小体积 `core` 为基础，把本地推理库做成可检测、绑定、安装、升级和移除的独立运行时包。普通用户无论选哪种发行形态，都不需要手动配置 Python、CUDA Toolkit、FFmpeg 或命令行环境。

PDF 是唯一仍依赖系统浏览器的输出：程序会调用已安装的 Edge、Chrome 或 Chromium 打印同一份 HTML。没有可用浏览器时，Markdown 和 HTML 仍会正常生成。

## 可选运行依赖与发行形态

### 主程序不应该固定占用 5 GiB

视频、模型权重和执行引擎是三类不同内容：

- `Video2Notes Core`：桌面 UI、任务/API、yt-dlp、FFmpeg/FFprobe、硬件探测和模型下载能力。
- 运行时包：faster-whisper、CTranslate2、PaddleOCR、PaddlePaddle、CUDA/cuDNN 等执行引擎及其固定 worker。
- 模型权重：用户选择的 Whisper/PaddleOCR 模型，仍按模型组件单独下载和复用。

CUDA 与 Paddle GPU 占当前 5.17 GiB 单体版的大部分空间。把它们永久塞进每个版本，会让只做云端总结、只用 CPU 或只识别音频的用户也承担相同体积。因此新版 profile 共用同一个 Core，差别只在是否附带可选运行时包：

| 发行 profile | 内容 | 预计解压体积，不含模型权重 | 适合 |
| --- | --- | ---: | --- |
| `core` | 小体积主程序，不预置本地推理 worker | 0.40-0.60 GiB | 联网按需安装、云模型、本机已有兼容运行时 |
| `cpu` | Core + 可移除 CPU ASR/OCR 离线包 | 1.80-2.30 GiB | 核显笔记本、无 NVIDIA 环境 |
| `nvidia_asr` | Core + CUDA ASR、CPU OCR 离线包 | 3.80-4.30 GiB | 语音占主导，希望 N 卡加速 ASR |
| `full_gpu` | Core + CUDA ASR/OCR 离线包 | 4.80-5.30 GiB | 高频本地精确识别、显存充足 |
| `legacy_full` | 旧式单体后端，所有库冻结在主程序内 | 当前 5.165 GiB | 兼容当前已验证便携版和迁移期回归 |

前三种离线版携带的 ZIP 与在线安装使用同一清单和校验规则，不是另一套永久写死的后端。`legacy_full` 仍可构建和运行，但它内嵌的推理库不能单独删除；这是兼容路径，不是未来默认形态。当前 Git 仓库只提交配方和空的可信目录，不提交数 GiB 的真实运行时 ZIP，也不会用虚假下载地址冒充已发布组件。

### 缺少依赖时会发生什么

任务提交前会按处理范围检查能力，而不是笼统检查“是否装过 AI 环境”：

| requirement ID | 用途 | 缺失影响 |
| --- | --- | --- |
| `tool.ffmpeg` / `tool.ffprobe` | 媒体转换与探测 | 需要处理媒体时阻止任务 |
| `download.ytdlp` | Bilibili、YouTube、X/Twitter 获取 | 网络来源任务阻止，本地文件不受影响 |
| `asr.faster_whisper` | 本地语音识别 | 无平台字幕或其他 ASR 时阻止；有字幕时可降级 |
| `ocr.paddleocr` | 画面文字识别 | 完整音画任务阻止或降级；仅音频任务不要求安装 |
| `render.chromium_pdf` | PDF 输出 | 只影响 PDF，Markdown/HTML 继续生成 |

安装确认界面必须先展示：包名和版本、提供的能力、官方归档地址、准确下载字节数或可信估算、安装后体积、目标目录、硬件兼容性以及是否需要重启 worker。用户确认后才下载。完成后会明确显示“校验成功并已绑定”“已安装但未绑定”或结构化失败原因，不把下载完成误报成可用。

### 安装位置和所有权

受管运行时默认安装到：

```text
DATA_ROOT\components\runtime-packs\<package-id>\<version>
```

绑定和自定义注册信息保存在：

```text
DATA_ROOT\config\runtime-packages.json
```

四种来源的删除规则不同：

| 来源 | 含义 | 软件可以做什么 |
| --- | --- | --- |
| `bundled` | `legacy_full` 内嵌兼容运行时 | 探测和使用；不能从正在运行的单体版中拆除 |
| `managed` | 软件安装到 `DATA_ROOT` 的版本化运行时 | 校验、绑定、升级、解绑和删除无租约的旧版本 |
| `system` | 本机自动发现的兼容、自描述运行时 | 绑定或解绑；绝不删除外部文件 |
| `custom` | 用户选择目录绑定的自描述运行时包 | 注册或忘记；绝不递归删除用户目录 |

软件不会把任意 `site-packages` 注入主进程，也不会在运行时执行 `pip install --target`。自定义目录必须带受支持的自描述清单和固定 worker 入口；“选择目录”不是允许执行任意脚本。

### 安装、升级与删除

受管安装依次经过 `downloading -> verifying_archive -> extracting -> probing -> publishing`。归档 SHA-256、展开文件哈希和允许路径来自随程序发布的可信目录；下载到的 ZIP 不能自行修改这些规则。解压在 `.staging` 中完成，拒绝绝对路径、`..`、符号链接、junction 和额外文件，worker 探测成功后才原子发布。

升级不会覆盖正在使用的 DLL。新版本安装到并列目录，探测通过后原子切换绑定；旧版本没有任务租约和绑定时才允许删除。升级失败会保留原绑定，因此不需要重新安装整个 Video2Notes。CPU、NVIDIA ASR 和 Full GPU 之间也通过添加/切换/删除运行时包升级，不需要更换另一套应用代码。

## 第一次使用的完整闭环

### 1. 准备本地识别模型

打开“模型与算力”：

- 小白模式会读取 CPU、内存、NVIDIA GPU、显存、磁盘和当前占用，推荐适合本机的组件与资源预算。
- 专业模式可以调整资源偏好、给其他程序预留的 CPU/内存/GPU 余量、扫描频率、OCR 宽度、ASR beam、线程和固定采样预算等已经真正接入执行器的参数。
- 本地 ASR 当前使用 faster-whisper，本地 OCR 当前使用 PaddleOCR。
- NVIDIA 版会分别探测 ASR 与 OCR 是否真的能使用 CUDA；某个引擎失败时只回退该引擎，不会把“检测到 N 卡”误报成全流水线 GPU 加速。

### 2. 创建任务

输入支持：

| 来源 | 当前状态 | 说明 |
| --- | --- | --- |
| 本地文件 | 已实现 | 支持常见视频容器；同卷时可使用 hardlink，无法 hardlink 时复制到 run |
| Bilibili | 已实现 | 通过固定版本 yt-dlp 获取用户有权访问的格式和字幕 |
| YouTube | 已实现 | 匿名清晰度可能较低，可使用已登录浏览器 profile |
| X/Twitter | 已实现 | 平台变更频繁，能否获取取决于当时页面和账号权限 |
| 其他任意网站 | 未实现 | 不接受任意 shell 命令或任意 yt-dlp 输出模板 |

登录/认证有三种方式：

- 匿名访问：适合公开内容。
- 浏览器 profile：选择本机正常 Chrome、Edge 或 Firefox 的已登录 profile。程序不打开非标准内嵌登录窗，也不把 Cookie 内容写入任务清单。
- `cookies.txt`：使用你自己导出的 Netscape 格式 Cookie 文件。它与浏览器 profile 方式互斥。

来源探测会记录平台实际提供的格式。若账号、地区或平台策略只允许低清晰度，任务仍可继续，但会留下“小文字 OCR 可能受限”的警告，不会静默把低清视频当成高清。

### 3. 选择处理范围

| 范围 | 会执行 | 会跳过 | 适合 |
| --- | --- | --- | --- |
| 完整音画 | 下载/导入、探测、字幕、ASR、视觉扫描、OCR、融合、笔记、导出 | 无 | 教程、演示、PPT、软件操作、画面文字重要的视频 |
| 仅音频 | 下载/导入、探测、字幕、音频提取、ASR、融合、笔记、导出 | 视觉扫描、OCR、截图 | 播客、访谈、确认画面没有有效信息的视频 |

仅音频模式减少的是视觉解码和 OCR 分析成本。网络视频仍需要先取得可处理的媒体，因此它不保证减少下载时间或下载体积。为了让结果可审计，`vision.scan` 和 `ocr.extract` 仍会写出明确的 `skipped=true` 产物，而不是让阶段凭空消失。

### 4. 选择质量档位

质量档位控制处理预算和复核策略，不是一个脱离视频类型的“准确率百分比”。

| 档位 | 默认视觉基线 | ASR/OCR策略 | 建议用途 |
| --- | --- | --- | --- |
| Fast | 2 / 6 FPS 粗扫/精扫；较低分辨率与 1,000 固定帧预算 | 单路主 ASR，较少视觉候选 | 全片第一遍、语音主导内容、快速判断价值 |
| Balanced | 3 / 12 FPS；中等 OCR 宽度与 3,000 固定帧预算 | 对低置信或冲突区间选择性第二 ASR | 日常教程、操作演示、常规高质量笔记 |
| Accurate | 6 / 24 FPS；更高 OCR 宽度与 5,000 固定帧预算 | 更密集视觉/OCR候选和复核 | 小字、瞬时弹窗、二维码、指定片段精查 |

实际执行值还会根据显存、可用内存、实时负载和用户预留余量降低。每个 run 的 `system/execution-plan.json` 会写明请求值、实际值、设备、模型和回退原因。

### 5. 选择取帧方式

- `adaptive`：默认。先低成本粗扫，再只对可能变化的区间精扫；综合亮度、直方图、边缘、dHash 和文字敏感变化，要求变化持续并重新稳定后才建立视觉状态。
- `fixed_interval`：按 0.1 秒以上的固定间隔取帧，适合已知高信息密度区间。
- `skip`：该区间不做视觉分析，音频和字幕仍可继续。
- 分段覆盖：同一个视频可以让大部分区间自适应，只给 10:00-20:00 等特定区间设置 0.5 秒或 0.1 秒固定采样。

固定间隔不是默认方案，因为长视频每 0.1 秒一帧会迅速放大 OCR 成本。前后端都会按当前档位和性能设置校验总帧预算。

### 6. 阅读、返工和导出

任务完成后可以在阅读器中：

- 在简约/小白视图阅读视频、目录和正文。
- 在专业视图查看语音、画面文字、视觉变化、置信度、阶段指标、时间线和证据来源。
- 点击时间证据回到对应视频位置。
- 对指定区间执行 `vision_rescan`、`asr_retranscribe` 或人工 `evidence_correct`。
- 添加文字、PNG、JPEG、WebP 补充资料并绑定时间段。
- 基于当前有效证据重新生成另一种报告 preset。

局部返工不会原地覆盖旧证据，而是创建不可变 evidence revision。重新生成报告也会创建独立 note revision；初始报告和历史版本仍可回查。

## 软件实际上如何工作

```text
本地文件 / Bilibili / YouTube / X
                |
                v
source.acquire  获取媒体、平台字幕和来源清单
                |
                v
media.probe     用 ffprobe 建立容器 PTS、time base 和统一时间原点
                |
                v
system.plan     检测硬件、实时余量、CUDA 与本次实际执行参数
                |
                v
vision.scan     内容自适应或分段固定取帧（仅音频时明确跳过）
                |
                v
audio.extract   提取 16 kHz 单声道 PCM 工作音轨
                |
                v
captions.parse  规范化平台字幕
                |
                v
audio.asr       faster-whisper 主识别 + 必要区间的第二识别
                |
                v
ocr.extract     只识别选中的稳定视觉状态，并跟踪/合并重复文字
                |
                v
evidence.fuse   按真实时间重叠融合字幕、ASR、OCR和视觉状态，保留冲突
                |
                v
notes.compose   证据窗口 -> FactCard -> 章节 -> 可选校验 -> NoteDocument
                |
                v
render.outputs  Markdown + HTML + 可选 PDF
```

内部持久化时间统一使用整数微秒 `time_us`。视频帧保留解码得到的真实 PTS；ASR 的音频相对时间会映射回媒体时间线；融合使用时间区间重叠，而不是简单找“最近的一个时间点”。这使同一时刻的语音、字幕、画面文字和截图可以建立明确关系。

每一条 `EvidenceSpan` 都会保存模态、起止时间、原始/规范文本、置信度、供应商/模型/版本、artifact、OCR box 和 provenance。遇到字幕与 ASR 不一致，或相同屏幕区域的 OCR 互相冲突时，系统会记录冲突，不会合成一个没有依据的“总置信度”。

更细的模块、数据结构、进程边界和新人阅读路线见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 报告与输出文件

五种报告 preset：

| Preset | 目标 |
| --- | --- |
| `concise` | 快速浏览，减少章节和细节 |
| `detailed` | 保留过程、证据和更多上下文 |
| `professional` | 突出方法、数据、假设和不确定性 |
| `beginner` | 解释术语，面向第一次接触主题的读者 |
| `executive` | 强调结论、风险、决策和下一步 |

一个典型 run 的关键文件：

```text
runs/<run-id>/
├─ manifest.json             # run 状态、阶段版本、哈希、耗时、警告
├─ source/                   # 来源清单、下载结果、媒体
├─ system/execution-plan.json
├─ media/                    # ffprobe 与时间轴信息
├─ subtitles/                # 平台字幕证据
├─ audio/                    # 16 kHz 工作音轨
├─ asr/                      # ASR 片段和决策记录
├─ vision/                   # 变化事件、视觉状态、关键帧
├─ ocr/                      # OCR 原结果、拒识、跟踪和合并证据
├─ evidence/timeline.json    # 统一时间线
├─ notes/document.json       # 唯一规范 NoteDocument
├─ notes/note.md             # 首要可编辑产物
├─ render/note.html          # 离线阅读版
├─ render/note.pdf           # 可选
├─ supporting/               # 用户补充资料
├─ operations/               # 局部返工操作记录
└─ revisions/                # 不可变 evidence/note revisions
```

## 模型、供应商和协议

配置分三层：

1. `Provider`：供应商、Base URL、线路协议、认证方式。
2. `Model`：服务实际提供的模型 ID 和用户确认的能力集合。
3. `RoleBinding`：把符合能力要求的模型绑定到某个处理角色。

角色下拉框由 capability 过滤。不满足角色要求、被禁用或供应商被禁用的模型不会出现在对应可选项中；系统不会仅根据模型名称或供应商品牌猜能力。

| 协议 | 可配置 | 结构化笔记生成已接入 | ASR任务已接入 |
| --- | --- | --- | --- |
| Local | 是 | 本地确定性 fallback | faster-whisper |
| OpenAI Responses | 是 | 是 | 否 |
| OpenAI Chat Completions / compatible | 是 | 是 | 否 |
| OpenAI Audio Transcriptions | 是 | 不适用 | 尚未接入执行器 |
| Anthropic Messages | 是 | 是 | 否 |
| Gemini `generateContent` | 是 | 是 | 否 |
| Gemini Interactions | 是 | 是 | 否 |
| Ollama Native Chat | 是 | 是 | 否 |
| Custom HTTP | 实验性 | 尚无适配器 | 尚无适配器 |

当前流水线真正读取的角色是：`asr.primary`、`asr.secondary`、`ocr.primary`、`notes.fact_extractor`、`notes.drafter`、`notes.verifier`。`vision.frame_explainer`、`ocr.escalation`、`asr.adjudicator`、`translation` 等属于配置契约预留，当前任务执行器还没有调用它们。

供应商注册表保存在 `DATA_ROOT\config\providers.json`。API key 不写入该 JSON，而是保存到 Windows Credential Manager/keyring。远程模型调用失败时，系统会记录安全的错误类型并回退，不把密钥、完整请求或 Cookie 写进日志。

## 性能、GPU 与三档参考

系统把质量意图、使用偏好和界面复杂度分开：

- 质量：Fast / Balanced / Accurate。
- 资源偏好：Responsive / Balanced / Throughput。
- 体验：小白 / 专业。
- 资源预留：用户可留出一部分 CPU、内存和 GPU 给其他程序。

正式 full GPU 便携包已经在 NVIDIA 环境中验证 faster-whisper CUDA ASR 与 PaddleOCR CUDA 的短片完整工作流。当前保真视觉解码仍是软件解码；不能因为 FFmpeg 列出 CUDA 就把它写成 NVDEC 已启用。

`BV12hsEz3ELL` 7 分 32 秒参考视频的完整三档基线：

| 指标 | Fast | Balanced | Accurate（CUDA校正） |
| --- | ---: | ---: | ---: |
| 完整处理时间 | 526.994 s | 1662.287 s | 3013.919 s |
| 相对视频时长 | 1.17x | 3.68x | 6.66x |
| 视觉扫描 | 50.513 s | 129.111 s | 568.793 s |
| CUDA ASR | 23.831 s | 34.969 s | 79.113 s |
| 当时的 CPU OCR | 449.710 s | 1493.339 s | 2358.735 s |
| 视觉状态 | 88 | 115 | 213 |
| OCR 接受行 | 1,362 | 3,198 | 5,847 |
| OCR 合并证据 | 1,014 | 2,355 | 4,383 |
| 不重复规范化 OCR 文本 | 769 | 1,595 | 2,607 |

这组数据来自 Ryzen 9 9950X3D、RTX 5090 D v2 23.88 GiB、约 100.5 GB 内存的开发机。ASR 使用 `faster-whisper-small / CUDA float16`；完整三档 OCR 计时来自当时的 CPU PaddleOCR，所以 OCR 占总耗时约 78%-90%。它说明高档位收集了更多视觉/OCR证据，但没有人工 gold reference，不能被解释成 WER/CER 或准确率提升。完整 provenance、人工观察、资源限制和不可宣称事项见 [docs/05-full-bv12hsez3ell-gpu-benchmark-2026-08-02.md](docs/05-full-bv12hsez3ell-gpu-benchmark-2026-08-02.md)。

产品上的合理用法是：Fast 做全片第一遍，Balanced 做常规视觉教程，Accurate 优先用于小字、瞬时画面和指定区间返工，而不是默认让所有长视频整段 24 FPS 精扫。

## 数据位置、磁盘占用与清理

桌面版默认使用 Tauri 应用数据目录，Windows 上通常是：

```text
%APPDATA%\com.lainelaina.video2notes
```

实际路径以软件后端状态返回的 `dataRoot` 为准。可以在启动前通过 `VIDEO2NOTES_DATA_ROOT` 覆盖。CLI 的默认目录不同，由 `platformdirs` 决定，在当前 Windows 环境中是：

```text
%LOCALAPPDATA%\Video2Notes
```

长期占用主要来自：

- `runs/`：下载媒体、PCM 音轨、关键帧、OCR/ASR证据、补充资料和 revisions。
- `components/<model-id>/`：按需下载的 ASR/OCR 模型权重。
- `components/runtime-packs/`：按版本安装的可选推理运行时；删除未绑定、无任务租约的 `managed` 版本可以回收空间。
- 源码工作区的 `.venv/`、PyInstaller 输出、Tauri `target/`、后端暂存、验证工作区、便携包和 benchmark。

2026-08-04 再次按“不跟随 reparse point/junction”的口径审计，仓库真实逻辑大小为 `13.163 GiB`，不是 50 GiB。此前清理前确实曾约为 `59.30 GiB`，原因是同一套约 5 GiB 的 Paddle/CUDA 后端同时存在于 `.venv`、PyInstaller、Tauri/Rust 构建、便携包和历史备份；这些可重建副本已经清理。

另一个容易误判的原因是 `apps/desktop/node_modules` 中有 684 个 pnpm junction，它们都指向仓库内部的 pnpm store。会跟随 junction 或按每个链接重复统计目标的磁盘工具，可能再次显示 `50 GB+` 的表观大小；这不代表磁盘上又存在 684 份依赖。判断仓库占用时应关闭“跟随符号链接/junction”，或使用项目清理脚本采用的口径。

当前顶层组成约为：

| 顶层目录 | 逻辑大小约 | 说明 |
| --- | ---: | --- |
| `artifacts` | 7.530 GiB | 当前便携成品、正式 benchmark、模型和少量生成物 |
| `.venv` | 5.410 GiB | 源码开发、测试、PyInstaller 打包环境 |
| `apps` | 0.150 GiB | 不跟随 pnpm junction 后的 React/Tauri 源码与少量本地文件 |

其中以下大目录是有明确用途的保留内容，不能仅因为体积大就当垃圾删除：

| 内容 | 大小约 | 为什么保留 |
| --- | ---: | --- |
| `artifacts/portable/current` | 5.17 GiB | 当前可直接双击的 full GPU 便携版 |
| `.venv` | 5.41 GiB | 源码开发、测试和重新打包环境 |
| `artifacts/benchmarks` | 1.78 GiB | 三档完整基准与回归证据 |
| `artifacts/models` | 0.57 GiB | 已下载的本地模型 |

仓库提供安全清理脚本，默认只预览：

```powershell
# 查看会删除什么；保护 .venv、模型、正式 benchmark 和 portable/current
.\scripts\cleanup_generated.ps1

# 删除已验证的构建/验证/临时目录
.\scripts\cleanup_generated.ps1 -Execute

# 另外删除 current 的重复 ZIP；不会删除可运行目录
.\scripts\cleanup_generated.ps1 -IncludePortableZip -Execute
```

便携构建现在只在替换过程中临时保留旧 `current` 用于失败回滚；新版本验证成功后会默认删除临时备份。如确实需要保留上一版，构建时显式使用 `-KeepPreviousPortable`。

当前应用还没有 run 保留期限和自动垃圾回收。重复处理网络视频会创建新的 run，并保留各自媒体；运行时包有明确的版本/所有权删除边界，但 run 媒体仍是下一阶段需要补齐的空间管理闭环。

## 隐私和本机边界

- FastAPI 只绑定 `127.0.0.1`。
- Tauri 每次启动生成 32 字节随机会话令牌，通过环境变量传给后端，不放入命令行。
- React 请求除健康检查外都携带 `X-Video2Notes-Token`。
- 桌面退出时会终止它启动的 Python 后端。
- 本地 artifact 路径同时受 run ID、相对路径和 canonical path 边界检查。
- 浏览器 profile 与 Cookie 文件只用于用户主动选择的平台获取流程；manifest 保存引用，不保存 Cookie 内容。
- 日志会脱敏 Cookie、Bearer、Token、API key、SESSDATA 等常见敏感值。
- 不配置远程 LLM 时，媒体、ASR、OCR、证据和笔记可以全部留在本机。
- 配置远程 LLM 后，对应角色需要的证据会发给该 provider；Video2Notes 没有自己的云服务，但第三方 API 不是本地处理。

## 源码开发

普通便携版用户不需要本节工具。只有修改源码、运行测试或重新打包时才需要 Python、Node、pnpm、Rust 和 Visual Studio C++ Build Tools。

```powershell
# 基础 Python/API/开发依赖
.\scripts\bootstrap.ps1

# 本地 ASR + CPU OCR
.\scripts\bootstrap.ps1 -WithAsr -WithOcr

# 本地 ASR + NVIDIA PaddleOCR CUDA 12.9
.\scripts\bootstrap.ps1 -WithAsr -WithOcrGpu

# 安装 Playwright 浏览器用于严格 UI 验证
.\scripts\bootstrap.ps1 -WithPlaywright

# loopback API + Vite
.\scripts\dev.ps1

# Tauri 开发窗口
.\scripts\dev.ps1 -Tauri
```

源码开发环境要求 Python 3.11+；当前开发机使用 Python 3.13。`bootstrap.ps1` 会创建仓库根 `.venv`。开发模式还要求 `ffmpeg`、`ffprobe` 在 `PATH` 中；这些限制不适用于已打包的 Core 或兼容单体便携版。

## CLI 常用命令

```powershell
# 完整音画
.\.venv\Scripts\video2notes.exe process "D:\Videos\lecture.mp4" `
  --mode balanced --language zh-CN

# 仅音频，不生成 PDF
.\.venv\Scripts\video2notes.exe process "D:\Videos\podcast.mp4" `
  --mode balanced --audio-only --no-pdf

# 使用已登录 Edge profile
.\.venv\Scripts\video2notes.exe process "https://www.youtube.com/watch?v=<id>" `
  --browser edge --profile "Default" --mode balanced

# 使用 cookies.txt；不能与 --browser/--profile 同时使用
.\.venv\Scripts\video2notes.exe process "https://www.bilibili.com/video/<id>/" `
  --cookie-file "D:\Private\bili.cookies.txt" --mode accurate
```

`--language` 可以重复或传入逗号分隔的 BCP-47 标签。一个提示会固定语言；多个提示允许多语言解码。Markdown 和 HTML 始终生成；PDF 与截图可以关闭。

启动本地 API：

```powershell
$env:VIDEO2NOTES_TOKEN = "至少16字符的随机本地令牌"
.\.venv\Scripts\video2notes.exe serve --data-root "$PWD\data" --port 43119
```

## 测试和打包

```powershell
# Python：Ruff、mypy、compileall、unittest、coverage
.\scripts\test.ps1

# 再加 ESLint、TypeScript、Vitest、Vite、Rust 和 Playwright 主路径
.\scripts\verify.ps1 -Strict

# 构建小体积 Core；真实运行时由可信目录按需安装
.\scripts\build_portable.ps1 -ReleaseProfile core

# 构建现有单体兼容版
.\scripts\build_portable.ps1 -ReleaseProfile legacy_full

# 从已经冻结好的 worker 目录构建可离线安装、带完整哈希的 CPU 运行时包
.\scripts\build_runtime_pack.ps1 `
  -RecipePath .\packaging\runtime-packs\recipes\local-inference-cpu-win-x64.json `
  -PayloadRoot D:\Build\video2notes-runtime-worker-cpu `
  -OutputDirectory .\artifacts\runtime-packs

# 把上一步的离线包放进 CPU 发行版
.\scripts\build_portable.ps1 -ReleaseProfile cpu `
  -OfflineRuntimePackDirectory .\artifacts\runtime-packs

# 同时生成搬运用 ZIP
.\scripts\build_portable.ps1 -Zip

# 保留上一版 portable 目录；默认不会保留
.\scripts\build_portable.ps1 -KeepPreviousPortable

# 验证冻结成品中的 NVIDIA ASR/OCR
.\scripts\test_portable_cuda.ps1
```

`-CoreOnly` 保留为 `-ReleaseProfile core` 的旧命令别名。迁移期构建脚本仍默认 `legacy_full`，避免在受管运行时安装/回滚验收完成前破坏现有便携版；发行配置文件的目标默认值已经是 `core`。`-ReuseSidecar` 只有在 Python、可信运行时目录和打包源码指纹仍与冻结后端一致时才允许复用。在线发布时还必须给 `build_runtime_pack.ps1` 传入项目控制的 HTTPS `-SourceUrl`；不传时生成的是 `offline_only=true` 的离线目录条目。

## 已知限制

- 当前任务执行器的 ASR 只有本地 faster-whisper，OCR 只有本地 PaddleOCR；远程音频转录和自定义 HTTP 还未接入实际处理。
- 视觉、音频、OCR 阶段当前按顺序执行；桌面 API 默认一次只运行一个任务，避免本地模型在同一 GPU 上重复加载。
- 视觉解码仍是软件路径，没有接入保留 PTS 的 NVDEC 实现。
- 失败任务保留已经落盘的产物，但 UI 还不能从失败阶段原地继续；通常需要新建 run 或做支持的局部返工。
- 当前没有自动 run 清理、保留期限和跨 run 媒体/推理缓存。
- PDF 依赖系统 Edge/Chrome/Chromium；尚未输出 DOCX、tagged PDF 或其他办公格式。
- 平台下载能力受站点变化、账号、地区、源清晰度和平台政策影响，不支持 DRM 绕过。
- OCR/ASR 尚未用大规模人工 gold 数据集给出可信的通用准确率；现有 benchmark 是工程性能和证据覆盖参考。
- 当前仓库保留的 `legacy_full` NVIDIA 便携版仍较大；新版运行时包配方、可信目录、离线 ZIP 校验和发行 profile 已拆分，但正式数 GiB 归档仍需在发布流水线构建并放到项目控制的下载地址。
- 安装包/便携版当前未做 Authenticode 签名，Windows SmartScreen 可能显示未知发布者。
- 暗色切换已经存在，但当前视觉验收基线仍是亮色；暗色色板尚未达到同等完成度。

## 文档导航

- [ARCHITECTURE.md](ARCHITECTURE.md)：当前真实实现的完整中文架构说明，新人应从这里开始。
- [docs/01-bilinote-audit.md](docs/01-bilinote-audit.md)：对 BiliNote 参考项目的研究。
- [docs/03-evaluation-and-hardware-profiles.md](docs/03-evaluation-and-hardware-profiles.md)：评测和硬件档位背景。
- [docs/04-reference-benchmark-2026-08-01.md](docs/04-reference-benchmark-2026-08-01.md)：受限 CPU 片段实验。
- [docs/05-full-bv12hsez3ell-gpu-benchmark-2026-08-02.md](docs/05-full-bv12hsez3ell-gpu-benchmark-2026-08-02.md)：完整三档 GPU/CPU OCR 基准。
- [docs/04-roadmap.md](docs/04-roadmap.md)：后续路线，不代表已经实现。
- [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)：第三方依赖与许可证。

`reference/BiliNote` 是固定 commit 的只读研究 submodule。Video2Notes 当前代码是新的实现，没有把参考项目源码直接复制进主程序。

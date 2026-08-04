# Video2Notes 架构说明

本文档描述 Video2Notes `0.2.0` 当前代码真实执行的架构。它不是愿景文档，也不会把路线图里的候选方案写成已经完成。

目标读者包括第一次接触本项目的程序员。阅读时不要求提前了解视频容器、PTS、ASR、OCR、Tauri 或 FastAPI；重要名词会先解释，再说明它们在代码里如何连接。

## 1. 如何判断文档中的状态

本文统一使用三种状态：

- **已实现**：当前主分支有可执行代码和对应产物。
- **当前限制**：已有部分能力，但存在明确边界，不能扩大解释。
- **计划/候选**：研究方向或配置契约预留，当前流水线没有执行。

如果本文与旧的 `docs/02-system-architecture.md` 或路线图冲突，以本文和当前代码为准。旧文档包含 SQLite、浏览器扩展、云 ASR、并行执行等早期设计，其中部分尚未落地。

## 2. 先理解六个核心名词

### 2.1 Run

一次视频处理任务叫一个 **run**。每个 run 都有独立目录、独立 `manifest.json`、独立输入媒体和独立结果。

run 不是数据库中的一行，而是一个完整文件夹：

```text
DATA_ROOT/runs/<run-id>/
```

### 2.2 Stage

流水线中的一个处理步骤叫 **stage**，例如 `audio.asr` 或 `ocr.extract`。

每个 stage 都记录：

- stage 版本；
- 配置哈希；
- 输入 artifact 的路径和 SHA-256；
- 输出 artifact 的路径和 SHA-256；
- 状态、尝试次数、耗时、指标、警告和错误。

### 2.3 Artifact

**Artifact** 是落盘的中间或最终文件，例如下载的视频、音频 WAV、OCR JSON、关键帧 JPG、Markdown 或 PDF。

项目强调“先有证据，再写笔记”，所以不会只保留最终摘要而丢掉中间依据。

### 2.4 Evidence

**EvidenceSpan** 是可放到统一时间线上的证据片段。它可能来自平台字幕、ASR、OCR 或用户补充资料。

一条 evidence 至少知道：

- 它是什么模态；
- 它从何时开始、何时结束；
- 原始文本和规范化文本；
- 置信度；
- 使用了哪个 provider、model、version；
- 对应哪个 artifact；
- OCR 时还会保存 box；
- 它如何生成，也就是 provenance。

### 2.5 PTS 与 `time_us`

视频容器里的帧不一定严格按“第 N 帧 = N / FPS 秒”排列。**PTS** 是容器给帧或音频包的展示时间戳，`time_base` 是把 PTS 换算成时间的比例。

Video2Notes 读取原始 PTS，再把所有时间转换成从统一原点开始的整数微秒 `time_us`。这样字幕、ASR、OCR 和视频画面才能对应到同一条时间线。

### 2.6 Revision

局部返工或重新生成报告产生 **revision**。revision 是追加式、不可变的历史版本，不会把原始 evidence 或初始报告覆盖掉。

## 3. 一张图看懂整个系统

```text
┌─────────────────────────────────────────────────────────────────────┐
│ React 19 前端                                                       │
│ 新建任务 / 任务进度 / 阅读器 / 模型与算力                          │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ Tauri command + HTTP
┌──────────────────────────────v──────────────────────────────────────┐
│ Tauri 2 Rust 桌面壳                                                 │
│ 文件选择器、后端生命周期、随机令牌、loopback 端口、artifact 路径边界 │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ 127.0.0.1 + X-Video2Notes-Token
┌──────────────────────────────v──────────────────────────────────────┐
│ FastAPI 本地服务                                                    │
│ 配置、硬件探测、组件管理、任务队列、run、材料、返工、报告 revision  │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ 单任务后台线程
┌──────────────────────────────v──────────────────────────────────────┐
│ Python evidence-first 流水线                                       │
│ 获取 -> 探测 -> 规划 -> 视觉 -> 音频 -> 字幕 -> ASR -> OCR          │
│      -> 证据融合 -> NoteDocument -> Markdown/HTML/PDF               │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ 原子写入
┌──────────────────────────────v──────────────────────────────────────┐
│ 文件型 DATA_ROOT                                                    │
│ config/  components/  runs/<run-id>/                                │
└─────────────────────────────────────────────────────────────────────┘
```

当前没有 SQLite、PostgreSQL 或远程 Video2Notes 云服务。持久化真值是 JSON、媒体文件、图片、Markdown、HTML 和 PDF。

## 4. 进程边界

### 4.1 React 前端

入口是 [`apps/desktop/src/App.tsx`](apps/desktop/src/App.tsx)。主页面有四个：

| 页面 | 文件 | 责任 |
| --- | --- | --- |
| 新建任务 | `pages/CreatePage.tsx` | 来源、认证、范围、档位、采样计划、报告配置 |
| 任务 | `pages/RunPage.tsx` | 阶段进度、警告、诊断、取消 |
| 阅读器 | `pages/ReaderPage.tsx` | 笔记、时间同步、证据、材料、返工、报告 revision |
| 模型与算力 | `pages/ModelsPage.tsx` | Provider、Model、Role、性能、组件准备 |

主要业务状态在 [`apps/desktop/src/store.ts`](apps/desktop/src/store.ts)，UI 偏好在 [`apps/desktop/src/stores/uiPreferences.ts`](apps/desktop/src/stores/uiPreferences.ts)。

前端每秒调用一次 `advanceTasks` 轮询真实后端任务。小白/专业视图和主题偏好保存在 `localStorage`；它们只改变展示和可编辑参数，不会复制 run，也不会创建第二套业务数据。

### 4.2 Tauri 桌面壳

入口是 [`apps/desktop/src-tauri/src/lib.rs`](apps/desktop/src-tauri/src/lib.rs)。它负责 Web UI 不适合直接承担的 Windows 原生边界：

1. 从端口 `43119` 开始寻找最多 32 个空闲 loopback 端口。
2. 生成 32 字节随机会话令牌。
3. 通过 `VIDEO2NOTES_TOKEN` 环境变量传给 Python 子进程，不把令牌写进命令行参数。
4. 启动 `video2notes.exe serve --port ... --data-root ...`。
5. 通过 Tauri command 只把 URL 和令牌交给当前 WebView。
6. 桌面程序退出时终止它启动的后端子进程。
7. 使用系统文件选择器选择本地视频。
8. 校验 run ID、相对路径和 canonical path 后，才允许前端打开本地 artifact。

发布版优先使用随包冻结的 `backend/video2notes.exe`；`tauri dev` 才会寻找仓库 `.venv` 中的可编辑后端。

### 4.3 FastAPI 服务

入口是 [`src/video2notes/api/app.py`](src/video2notes/api/app.py)。服务只绑定 `127.0.0.1`。

除 `/api/health` 外，API 都要求：

```http
X-Video2Notes-Token: <本次桌面启动生成的令牌>
```

API 大致分为六组：

| 分组 | 主要端点 | 用途 |
| --- | --- | --- |
| 系统 | `/api/system`、`/api/runtime`、`/api/estimate` | 硬件、实际能力、宽区间 ETA |
| 配置 | `/api/performance`、`/api/configuration-catalog` | 资源预留、专业覆盖、协议目录 |
| 组件 | `/api/components`、`/api/components/prepare` | 下载和启用本地模型权重 |
| 模型 | `/api/providers`、secret、test、discover | Provider/Model/Role 与凭据 |
| 任务 | `/api/jobs`、cancel、result | 提交、轮询、取消和结果 |
| Run | `/api/runs`、artifact、materials、operations、evidence、report-revisions | 已落盘任务与后处理 |

FastAPI 创建 `JobManager(max_workers=1)`。虽然通用 `JobManager` 类默认可以配置两个 worker，桌面服务明确限制为一个任务，避免本地 ASR/OCR 模型在同一 GPU 上重复加载和争抢显存。

任务事件和线程句柄保存在内存中；run 文件和最终 `render/outcome.json` 持久化。后端重启后，旧任务的实时事件不会恢复，但已经完成或失败的 run 仍可以从磁盘读取。

### 4.4 Python CLI

入口是 [`src/video2notes/cli.py`](src/video2notes/cli.py)。CLI 和桌面服务调用同一套 runtime 与 pipeline，不存在“桌面一套、命令行另一套识别逻辑”。

主要命令：

- `video2notes process`：处理本地文件或受支持链接；
- `video2notes serve`：启动本地 API；
- `video2notes scan-changes`：单独研究视觉变化检测。

## 5. 一次任务从点击开始到生成笔记

前端创建任务时会提交一个 `PipelineRequest`。重要字段包括：

- source；
- quality mode；
- processing scope；
- browser profile 或 cookie file 引用；
- language hints；
- sampling plan；
- screenshots/PDF 开关；
- report preset。

处理范围是 run 级不可变属性。如果一个 run 已经是 `audio_only`，不能在原目录里改成 `audio_visual`；必须创建新 run。这样历史 manifest 不会出现“前半段按一种范围、后半段按另一种范围却没有记录”的歧义。

当前主流水线按以下顺序执行：

```text
source.acquire
  -> media.probe
  -> system.plan
  -> vision.scan
  -> audio.extract
  -> captions.parse
  -> audio.asr
  -> ocr.extract
  -> evidence.fuse
  -> notes.compose
  -> render.outputs
```

注意：这是顺序执行，不是音频和视觉并行。远程事实提取可以在 `notes.compose` 内按证据窗口并发，但媒体处理主阶段仍是串行的。

## 6. Stage 逐步说明

### 6.1 `source.acquire`

代码主要位于：

- [`src/video2notes/sources/local.py`](src/video2notes/sources/local.py)
- [`src/video2notes/sources/ytdlp.py`](src/video2notes/sources/ytdlp.py)
- [`src/video2notes/sources/registry.py`](src/video2notes/sources/registry.py)

责任：

1. 判断输入是本地文件还是 URL。
2. URL 按解析后的主机名严格匹配 Bilibili、YouTube 或 X，不做危险的字符串包含判断。
3. 用 yt-dlp 探测格式、字幕和媒体信息。
4. 根据账号可访问范围选择并固定具体 format ID。
5. 下载完成后再次校验实际画质，防止静默降级。
6. 保存来源清单、获取结果、媒体和平台字幕。

本地文件如果与 run 位于同一卷，会优先 hardlink；hardlink 只是两个目录项指向同一物理数据，通常不会再次占用完整视频大小。跨卷或 hardlink 失败时会复制。

认证只保存引用：浏览器种类、profile 标识或 Cookie 文件路径。Cookie 内容不会写入 manifest。

### 6.2 `media.probe`

代码主要位于 [`src/video2notes/media`](src/video2notes/media)。

责任：

1. 调用 ffprobe 读取容器和每条流。
2. 保存 codec、分辨率、帧率、音频参数、duration。
3. 保存 rational `time_base`、起始 PTS 和时间偏移。
4. 建立共享 timeline origin。

为什么不能只用“秒数”：

- 视频可能有非零起始 PTS；
- 可变帧率视频的帧间隔不固定；
- 音频和视频流可能有不同 time base；
- 固定间隔请求的 10.0 秒，实际解码到的最近帧可能不是精确 10.0 秒。

Video2Notes 会同时保留“请求采样时间”和“实际帧 PTS”，不会伪造帧时间。

### 6.3 `system.plan`

代码主要位于：

- [`src/video2notes/system/hardware.py`](src/video2notes/system/hardware.py)
- [`src/video2notes/system/resources.py`](src/video2notes/system/resources.py)
- [`src/video2notes/system/profiles.py`](src/video2notes/system/profiles.py)
- [`src/video2notes/system/estimate.py`](src/video2notes/system/estimate.py)

系统读取：

- CPU 型号、核心和当前占用；
- 总内存、可用内存；
- 目标磁盘和余量；
- NVIDIA GPU、显存、利用率和驱动；
- FFmpeg 声明的硬件能力；
- faster-whisper 与 PaddleOCR 各自的真实 CUDA 探测。

然后组合四类输入：

1. 质量档位：Fast、Balanced、Accurate。
2. 使用偏好：Responsive、Balanced、Throughput。
3. 用户资源预留。
4. 专业模式覆盖值。

输出 `system/execution-plan.json`。如果请求超过安全余量，会钳制并写明原因。

硬件大致分为：

- CPU/iGPU；
- 6-10 GiB GPU；
- 10-20 GiB GPU；
- 20 GiB 以上 GPU。

这只是预算档位，不是“某张显卡保证某个速度”。视频内容密度、OCR 行数、模型、网络和其他进程都会改变实际耗时。

### 6.4 `vision.scan`

代码主要位于：

- [`src/video2notes/vision/adaptive_sampler.py`](src/video2notes/vision/adaptive_sampler.py)
- [`src/video2notes/vision/sampling.py`](src/video2notes/vision/sampling.py)

有三种段落策略：

- `adaptive`：内容自适应；
- `fixed_interval`：固定间隔；
- `skip`：跳过。

自适应扫描分两遍：

1. 粗扫用较低成本频率寻找疑似变化。
2. 精扫只进入候选区间，提高频率定位稳定画面。

比较指标包括：

- 亮度变化；
- 颜色直方图；
- 边缘变化；
- dHash；
- 对文字区域更敏感的变化指标。

瞬间噪声不会立即成为状态。检测器要求变化持续，并在变化后重新稳定，才创建视觉状态和关键帧。

默认粗扫/精扫基线：

| 档位 | FPS |
| --- | --- |
| Fast | 2 / 6 |
| Balanced | 3 / 12 |
| Accurate | 6 / 24 |

固定采样预算默认分别为 1,000、3,000、5,000 帧。专业模式可以在更大的 schema 范围内请求覆盖，但仍会经过安全计划和运行时检查。

**仅音频行为**：不解码视觉帧，不创建截图，但会写出可缓存、可审计的 skipped artifact，原因是 `audio_only_scope`。

### 6.5 `audio.extract`

代码主要位于 [`src/video2notes/audio`](src/video2notes/audio)。

FFmpeg 把主音轨转换成：

```text
16 kHz / mono / PCM WAV
```

这样 ASR 不需要为每种视频容器和音频 codec 单独适配。音频提取结果也成为稳定 artifact，后续 ASR 重跑可以只依赖 WAV 和时间映射。

### 6.6 `captions.parse`

代码主要位于 [`src/video2notes/audio/subtitles.py`](src/video2notes/audio/subtitles.py)。

责任：

- 解析平台字幕；
- 规范化 cue 时间；
- 保存原始文本和 provenance；
- 转成 EvidenceSpan。

平台字幕不是自动真值。后续会与 ASR 比较，冲突会被记录。

### 6.7 `audio.asr`

代码主要位于：

- [`src/video2notes/audio/asr.py`](src/video2notes/audio/asr.py)
- [`src/video2notes/audio/secondary.py`](src/video2notes/audio/secondary.py)

当前可执行适配器是本地 faster-whisper。

已实现：

- VAD；
- segment 时间戳；
- word 时间戳；
- 置信度信息；
- 单语言或多语言提示；
- CPU/CUDA 设备选择；
- 按质量档位选择模型配置；
- 对必要区间选择性运行第二 ASR。

第二 ASR 不会默认把整段视频重复识别一次。触发因素包括：

- 主 ASR 低置信；
- 与平台字幕冲突；
- 语言不确定或语言冲突；
- 当前档位允许复核。

当前没有 ROVER、多云 ASR 投票、speaker diarization、WhisperX 或 FunASR 执行器。相关内容属于计划/候选，不应写成现状。

ASR 在工作 WAV 上得到的相对时间会映射回 canonical media timeline，再保存为 `time_us`。

### 6.8 `ocr.extract`

代码主要位于：

- [`src/video2notes/ocr/paddle.py`](src/video2notes/ocr/paddle.py)
- [`src/video2notes/ocr/pipeline.py`](src/video2notes/ocr/pipeline.py)

当前可执行适配器是本地 PaddleOCR。

OCR 不会对视频每一帧运行。输入是 `vision.scan` 选出的视觉状态关键帧。

处理步骤：

1. 用当前执行计划决定设备和最大推理宽度。
2. 识别文本、box 和置信度。
3. 拒绝低置信、裁剪过小、模糊或无效 box。
4. 在相邻视觉状态间按空间区域和文本相似度跟踪。
5. 合并连续重复文字，但保留所有 observation provenance。
6. 对滚动内容使用带解释分数的 greedy weighted set cover 选择代表截图。

场景检测宽度与 OCR 推理宽度是两套预算。粗扫可以便宜，但真正 OCR 不必被连带压到相同小分辨率。

正式 NVIDIA 便携包已经验证 PaddleOCR CUDA 与 faster-whisper CUDA 在规定加载顺序下共存。仍需注意：历史完整三档 benchmark 的 OCR 是 CPU 基线，不能把短片 GPU POC 的速度直接外推到整段视频。

### 6.9 `evidence.fuse`

代码主要位于 [`src/video2notes/fusion/timeline.py`](src/video2notes/fusion/timeline.py)。

输入：

- 平台字幕 evidence；
- ASR evidence；
- OCR evidence；
- 视觉状态；
- processing scope。

融合不是“把所有文本拼起来”。它会：

1. 按半开区间 `[start_us, end_us)` 计算真实时间重叠。
2. 根据视觉变化、语音停顿和最长 90 秒上下文建立 evidence window。
3. 链接同一窗口内相关的音频、字幕、OCR 和视觉状态。
4. 记录字幕与 ASR 文本冲突。
5. 记录相同屏幕区域的 OCR 冲突。
6. 保留每条 evidence 的原始 provenance。

系统不会把不同模态置信度随意相乘或平均成一个看似精确的总分，因为这些分数往往不是同一标尺。

### 6.10 `notes.compose`

代码主要位于 [`src/video2notes/notes/composer.py`](src/video2notes/notes/composer.py)。

生成过程：

```text
EvidenceWindow
    -> FactCard
    -> 按 preset/受众组织章节
    -> 可选 verifier 检查事实引用
    -> NoteDocument
```

LLM 不是流水线唯一真值。模型只能引用输入中存在的 evidence/material ID；返回不存在的 ID 会被拒绝或回退。

配置了可用的 `notes.fact_extractor` 和 `notes.drafter` 时，可以执行结构化生成。二者必须同时可用，否则为了避免半套模型行为，结构化笔记生成会整体禁用并回退。`notes.verifier` 是可选的。

没有 LLM 或 LLM 失败时，`EvidenceNoteComposer` 仍会生成确定性的抽取式笔记。任务不会因为“没有 API key”就失去 Markdown 输出。

### 6.11 `render.outputs`

代码主要位于 [`src/video2notes/notes/render.py`](src/video2notes/notes/render.py)。

唯一内容模型是 `NoteDocument`：

- Markdown 从它确定性渲染；
- HTML 从它确定性渲染；
- PDF 调用 Edge/Chrome/Chromium 打印同一 HTML。

PDF 阶段不会再次调用 LLM，也不会重新总结，所以三种格式的事实内容应保持一致。

主要最终产物：

- `notes/document.json`；
- `notes/note.md`；
- `render/note.html`；
- `render/note.pdf`（可选）；
- `render/outcome.json`。

## 7. 仅音频模式的完整契约

`audio_only` 不是“只跑一个 ASR 函数”，而是有明确边界的完整 run：

```text
执行：source.acquire
执行：media.probe
执行：system.plan
跳过：vision.scan（写 skipped artifact）
执行：audio.extract
执行：captions.parse
执行：audio.asr
跳过：ocr.extract（写 skipped artifact）
执行：evidence.fuse
执行：notes.compose
执行：render.outputs
```

截图会被强制关闭。视觉返工和 OCR 操作会被 API 拒绝。这样 UI、manifest 和结果不会出现“仅音频任务却偷偷产生视觉证据”的不一致。

网络来源仍要获取可处理媒体；仅音频主要节省视觉扫描和 OCR，而不是保证节省下载。

## 8. 局部返工、补充资料与报告 revision

相关代码：

- [`src/video2notes/operations.py`](src/video2notes/operations.py)
- [`src/video2notes/materials.py`](src/video2notes/materials.py)
- [`src/video2notes/notes/revisions.py`](src/video2notes/notes/revisions.py)
- [`src/video2notes/notes/reporting.py`](src/video2notes/notes/reporting.py)

### 8.1 支持的返工

- `vision_rescan`：指定区间重新自适应或固定采样，可选重跑 OCR。
- `asr_retranscribe`：只识别指定音频区间，可提供语言提示。
- `evidence_correct`：人工写入修正文本。

返工结果不会原地覆盖基线 evidence。成功操作生成完整 evidence revision，再原子切换 active revision；失败操作保留失败记录，但不会激活半成品。

### 8.2 补充资料

当前支持：

- 纯文字；
- PNG；
- JPEG；
- WebP；
- 可选时间段绑定。

补充资料与视频原始 evidence 使用不同 provenance，避免把“用户后来查到的资料”伪装成“视频本身说过”。

### 8.3 报告 revision

当 evidence revision 或补充资料改变后，旧报告不会静默重写。用户显式请求重新生成，系统才会：

1. 读取当前 active evidence revision；
2. 读取活动补充资料；
3. 重新融合当前有效证据；
4. 生成 `revisions/notes/<revision-id>/`；
5. 原子更新 latest 指针。

初始 `notes/` 和 `render/` 产物不被覆盖。

## 9. Provider、Model 与 Role

相关代码：

- [`src/video2notes/providers/registry.py`](src/video2notes/providers/registry.py)
- [`src/video2notes/providers/auth.py`](src/video2notes/providers/auth.py)
- [`src/video2notes/runtime.py`](src/video2notes/runtime.py)
- [`src/video2notes/llm/client.py`](src/video2notes/llm/client.py)

### 9.1 为什么分三层

```text
Provider：怎么连接、怎么认证
    |
    v
Model：远端或本地实际模型 ID、它具备什么能力
    |
    v
RoleBinding：本流水线哪个角色使用哪个模型
```

协议不能自动证明模型能力。例如一个 OpenAI-compatible 接口可能只有文本，也可能支持图片；项目要求用户或发现流程明确填写 capability。

保存 RoleBinding 时，后端检查角色所需 capability。`compatible_models(role)` 只返回：

- 模型已启用；
- Provider 已启用；
- 能力集合覆盖角色要求。

因此不适配模型不会进入对应下拉框。

### 9.2 可配置协议与真正执行能力

| 协议 | 注册表可保存 | 结构化笔记 adapter | 当前 ASR adapter |
| --- | --- | --- | --- |
| Local | 是 | 确定性本地 fallback | faster-whisper |
| OpenAI Responses | 是 | 已实现 | 无 |
| OpenAI Chat Completions | 是 | 已实现 | 无 |
| OpenAI Audio Transcriptions | 是 | 不适用 | 尚未实现 |
| Anthropic Messages | 是 | 已实现 | 无 |
| Gemini generateContent | 是 | 已实现 | 无 |
| Gemini Interactions | 是 | 已实现 | 无 |
| Ollama Native Chat | 是 | 已实现 | 无 |
| Custom HTTP | 是，实验性 | 尚未实现 | 尚未实现 |

“协议可登记”只代表 schema、UI 和连接信息能表达它，不代表所有处理角色都能执行。

### 9.3 当前执行角色

流水线当前真正读取：

- `asr.primary`；
- `asr.secondary`；
- `ocr.primary`；
- `notes.fact_extractor`；
- `notes.drafter`；
- `notes.verifier`。

配置契约预留但当前不执行：

- `vision.frame_explainer`；
- `ocr.escalation`；
- `asr.adjudicator`；
- `translation`。

### 9.4 Secret 保存

Provider JSON 位于：

```text
DATA_ROOT/config/providers.json
```

API key 不写入这个文件。Windows 下由 Credential Manager/keyring 保存，JSON 只保留 secret 引用和是否已配置的状态。

## 10. 硬件规划与资源保护

质量档位、资源偏好和体验模式是独立维度：

```text
质量档位：Fast / Balanced / Accurate
资源偏好：Responsive / Balanced / Throughput
体验模式：Guided / Professional
```

用户还可以设置资源预留。例如保留一部分 CPU 和内存给浏览器、IDE 或游戏。

任务开始前会重新检测实时余量，而不是只在安装时探测一次。计划随后经过三层门控：

1. 硬件预算；
2. 加速能力探测；
3. 实际 ASR/OCR backend 能力。

如果机器有 NVIDIA GPU，但安装的是 CPU Paddle，则 ASR 可以 CUDA、OCR 回退 CPU。计划与诊断必须分别显示，不能只显示一个笼统的“GPU 已启用”。

当前真实并发边界：

- 整机桌面服务最多一个 run；
- 主 pipeline stages 顺序执行；
- 本地 ASR/OCR adapter 单项调用，batch size 实际为 1；
- 远程 fact extraction 可以按窗口并发；
- execution plan 中尚未被执行器消费的研究字段不应暴露为“已经生效”。

当前视觉解码为软件 PyAV 路径。未来要接 NVDEC，必须同时保留原始 PTS 和当前采样语义，不能为了硬解速度破坏时间对齐。

## 11. 文件型存储与缓存

核心实现是 [`src/video2notes/artifacts/store.py`](src/video2notes/artifacts/store.py)。

### 11.1 DATA_ROOT

桌面版优先使用 `VIDEO2NOTES_DATA_ROOT`，否则使用 Tauri `app_data_dir()`。Windows 通常为：

```text
%APPDATA%/com.lainelaina.video2notes
```

CLI 默认由 `platformdirs.user_data_path("Video2Notes")` 决定，当前 Windows 为：

```text
%LOCALAPPDATA%/Video2Notes
```

### 11.2 Run 目录

```text
runs/<run-id>/
├─ manifest.json
├─ source/
├─ system/
├─ media/
├─ subtitles/
├─ audio/
├─ asr/
├─ vision/
├─ ocr/
├─ evidence/
├─ notes/
├─ render/
├─ supporting/
├─ operations/
├─ revisions/
└─ logs/
```

### 11.3 Manifest schema v2

`manifest.json` 保存：

- run ID；
- source；
- profile；
- processing scope；
- run status；
- warnings；
- 每个 stage 的完整记录。

旧 schema v1 run 在读取时会迁移为显式 `audio_visual`，再写回 schema v2，避免后续代码继续猜测范围。

### 11.4 原子写入

JSON 先写到临时文件，再用 `os.replace` 原子替换目标。这样程序中途崩溃时，旧完整文件通常仍在，不容易留下半个 JSON。

### 11.5 Stage 缓存

fingerprint 包含：

```text
stage_name
+ stage_version
+ config_hash
+ 每个输入 artifact 的 relative_path 和 SHA-256
```

只有以下条件同时满足才命中缓存：

- 旧 stage 已完成；
- fingerprint 相同；
- 所有旧输出仍存在；
- 文件大小和 SHA-256 仍正确。

当前缓存只在同一个 run 内生效。没有跨 run 的全局视频下载缓存、ASR 缓存或 OCR 缓存。

### 11.6 为什么磁盘会增长

每个网络 run 默认保留：

- 完整下载媒体；
- 16 kHz PCM WAV；
- 视觉关键帧；
- OCR/ASR 原始和合并证据；
- 报告与 revisions。

开发仓库还可能同时存在：

- `.venv`；
- PyInstaller build/dist；
- Tauri `target`；
- 冻结 backend staging；
- portable current 与 ZIP；
- benchmark；
- verification 和临时 run。

仓库的 [`scripts/cleanup_generated.ps1`](scripts/cleanup_generated.ps1) 会校验路径边界和 reparse point，默认 dry-run，并保护 `.venv`、模型、正式 benchmark 和 `portable/current`。

2026-08-04 按不跟随 reparse point/junction 的口径，仓库逻辑大小是 `13.163 GiB`。`apps/desktop/node_modules` 内有 684 个指向仓库内部 pnpm store 的 junction；会递归跟随并对每个链接重复统计的工具可能显示 `50 GB+`，但那不是 684 份物理副本。当前主要占用是 `artifacts 7.530 GiB`、`.venv 5.410 GiB`、`apps 0.150 GiB`。`portable/current`、开发 `.venv`、正式 benchmark 和模型都有明确用途，清理器必须继续保护它们，除非用户显式选择舍弃对应能力或证据。

当前产品没有 run 删除 API、保留期限或自动垃圾回收。这是已知产品闭环缺口，不是存储层已经解决的功能。

## 12. 安全与隐私架构

### 12.1 Loopback 安全

- 只监听 `127.0.0.1`；
- 每次启动随机 token；
- token 不放 argv、不打印日志；
- CORS 只允许桌面开发来源；
- 除 health 外统一 token 保护。

这不是面向公网的认证系统，目标是防止同机其他网页随意调用本地 API。

### 12.2 路径安全

artifact 请求经过：

1. run ID 校验；
2. 禁止绝对路径和 `..`；
3. run root canonicalize；
4. candidate canonicalize；
5. 确认 candidate 仍在 run root 内。

这样可以阻止普通目录穿越和符号链接跳出。

### 12.3 下载认证

- 不内置非标准登录浏览器；
- 使用正常浏览器现有 profile 或用户提供的 Cookie 文件；
- manifest 只保存引用；
- 日志对 Cookie、Bearer、Token、API key、SESSDATA 等脱敏；
- 不绕过 DRM 或访问控制。

### 12.4 第三方模型边界

不配置远程 LLM 时，内容处理可以全部本地完成。配置远程模型后，对应角色的证据会发送给该 Provider。项目自身没有云服务，但不能把第三方 API 调用描述成“数据绝不离开本机”。

## 13. 打包架构

### 13.1 为什么把主程序与推理运行时分开

旧的 `legacy_full` 使用 [`scripts/build_sidecar.ps1`](scripts/build_sidecar.ps1) 把以下内容冻结进同一个 PyInstaller `--onedir` 后端：

- Python 业务代码、yt-dlp、FFmpeg/FFprobe；
- faster-whisper / CTranslate2；
- PaddleOCR 与一种 PaddlePaddle runtime；
- NVIDIA 构建需要的 CUDA、cuDNN、cuBLAS 等用户态库；
- 许可证和构建 manifest。

这种形态已经通过现有 GPU 工作流，迁移期必须保留。但它约 5.165 GiB，其中 NVIDIA 与 Paddle GPU 占大约 87%；每次发版、回滚和复制都会重复这部分数据。

新版边界把系统拆成：

```text
Core 主程序
  ├─ UI、API、流水线、yt-dlp、FFmpeg/FFprobe
  ├─ 硬件探测与 huggingface-hub 模型下载能力
  └─ 可信 runtime catalog
          |
          v
独立 runtime worker ZIP
  ├─ 固定 worker 可执行文件
  ├─ faster-whisper / PaddleOCR / CUDA 等依赖
  ├─ 自描述 manifest
  └─ 完整展开文件哈希和许可证
```

Core 保留 `huggingface-hub`，所以“没有预装推理库”不等于“不能下载模型”。用户选择的 ASR/OCR 权重仍不放进主程序或通用运行时包，而是由现有模型组件管理器下载到 `DATA_ROOT/components/<model-id>`。允许保留的少量引擎资产必须在配方中显式列出，例如 faster-whisper 默认 VAD 的 Silero ONNX。

### 13.2 为什么运行时必须使用独立 worker

Windows 进程按 DLL basename 解析已经加载的原生库。CTranslate2 与 Paddle 可能携带名称相同、构建不同的 `cudnn64_9.dll`；把任意外部 `site-packages` 塞进正在运行的 FastAPI/PyInstaller 进程，会造成版本污染、加载顺序依赖和无法卸载的 `.pyd`/DLL。

因此 managed/system/custom 运行时都通过固定 worker 进程隔离：

```text
任务 role / requirement
        |
        v
runtime binding
        |
        v
固定 executable + 参数数组（shell=False）
        |
        v
worker hello/probe -> ASR/OCR request -> 现有 JSON 领域契约
```

OCR worker应常驻并复用，不为每一帧启动新进程。协议要有 request ID、超时、取消、一次受控重启和版本握手。manifest 只能声明受支持的固定相对入口，不能提供任意 shell 命令。worker/manifest 指纹必须进入 stage fingerprint，避免运行时升级后错误复用旧缓存。

### 13.3 可信目录与运行时包格式

仓库中的 [`packaging/runtime-packs/catalog.json`](packaging/runtime-packs/catalog.json) 是随应用发布的可信目录。`v0.2.0` 已加入 CPU、NVIDIA ASR 和 Full GPU 三个真实 Windows x64 release；每个条目都来自已经构建、解压校验并通过 worker probe 的归档。Git 不提交数 GiB 的 ZIP 本体，catalog 只保存 GitHub Release URL、准确字节数、完整 SHA-256 和全部展开文件哈希，不允许用占位 URL 或未知 SHA-256 冒充可下载版本。

每个 catalog package/release 至少记录：

```text
package_id / version / display_name
target_triple / runtime_protocol_version
capabilities[]:
  capability_id / engine_id / protocol_version
  transport / entrypoint / supported_devices
archive:
  file_name / source_url / size_bytes / sha256 / offline_only
  parts[]: file_name / source_url / size_bytes / sha256（可选）
installed_size_bytes
files[]:
  relative_path / size_bytes / sha256
licenses[] / upstream_sources[]
```

ZIP 根目录必须含 `runtime-package.json`。它重复身份、协议、capability、许可证和 payload 文件哈希，便于 system/custom 目录自描述；真正决定“官方归档可安装”的 archive URL、ZIP SHA-256 和展开文件清单仍来自 Core 携带的可信 catalog，不能信任刚下载 ZIP 自己提供的值。

单个 GitHub Release asset 不能超过 2 GiB。Full GPU ZIP 的完整大小和 SHA-256 仍作为最终信任目标保留在 `archive`，同时使用有序 `parts[]` 描述两个小于限制的固定分片。下载器逐片断点续传和校验，严格按 catalog 顺序重组，再验证完整 ZIP 的总字节数和 SHA-256；只有整体校验通过后才进入解压和 worker probe。分片名、URL、大小或哈希重复/不匹配都会在 catalog 加载或下载阶段被拒绝。

初始 capability/requirement 边界是：

| requirement ID | capability / 实现 |
| --- | --- |
| `tool.ffmpeg` | Core/bundled 或兼容外部 FFmpeg |
| `tool.ffprobe` | Core/bundled 或兼容外部 ffprobe |
| `download.ytdlp` | Core/bundled yt-dlp |
| `asr.faster_whisper` | faster-whisper worker |
| `ocr.paddleocr` | PaddleOCR worker |
| `render.chromium_pdf` | 系统 Edge/Chrome/Chromium |

模型权重与 runtime package 使用不同 manager。模型是数据，可以由不同兼容 worker 读取；runtime 是可执行代码和原生库，必须采用更严格的来源、哈希、路径和进程边界。

### 13.4 安装状态机与安全边界

受管安装是持久化异步操作：

```text
queued
 -> downloading
 -> verifying_archive
 -> extracting
 -> probing
 -> publishing
 -> completed

或 failed / cancelled
```

每个 operation 记录已下载字节、总字节、速度、ETA、阶段、是否可续传和结构化错误。安装前 UI 直接使用可信 catalog 展示官方来源、归档大小、安装后大小、目标目录、能力和硬件要求，不等下载后再猜。

安装器必须执行：

1. 在 `DATA_ROOT/components/runtime-packs/.staging-*` 下载和展开；
2. 用可信目录校验 archive SHA-256；
3. 在解压前拒绝绝对路径、空段、`.`、`..`、重复路径、符号链接和 junction；
4. 解压后逐个校验大小/SHA-256，拒绝 catalog 外的额外文件；
5. 以固定参数运行 worker probe，验证 package ID、协议、能力、设备和引擎版本；
6. 探测成功后原子发布到版本目录并切换 binding。

除了进程内 `RLock`，安装/升级/删除还需要跨进程文件锁。这样重复点击、两个桌面实例或残留操作不会同时发布同一路径。

### 13.5 存储、绑定和所有权

配置与受管目录：

```text
DATA_ROOT/
├─ config/runtime-packages.json
└─ components/runtime-packs/
   ├─ .staging-*/
   └─ <package-id>/<version>/
```

Provider/Model/Role 之下再增加 runtime requirement/binding：

```text
Provider / Model / Role
        |
        v
Runtime requirement
        |
        v
Runtime package binding
        |
        v
bundled / managed / system / custom worker
```

| source | 文件所有者 | 允许操作 |
| --- | --- | --- |
| `bundled` | 当前应用发行物 | 探测/绑定；程序不删除自己的单体后端文件 |
| `managed` | Video2Notes 的 `DATA_ROOT` | 安装、校验、升级、解绑、删除 |
| `system` | 系统或其他软件 | 发现、绑定、解绑；绝不删除 |
| `custom` | 用户 | 注册、绑定、忘记；绝不递归删除用户目录 |

system/custom 不是任意 Python 目录；它们必须有受支持的自描述 manifest，并通过 worker probe。目录选择器只返回固定目录路径，不接收命令行。

绑定精确到 requirement。音频-only 任务只要求 ASR，不因为 OCR 未安装而阻止；存在平台字幕但本地 ASR 缺失时可以返回 `degraded`；需要转码而 FFmpeg/ffprobe 缺失时才是 `blocked`。前端预检用于解释，`submit_job` 必须在后端再次预检，不能信任前端状态。

### 13.6 升级、回滚和删除

升级采用并列版本，而不是原地覆盖：

```text
<package-id>/1.0.0   <- 当前 binding、可能有 worker lease
<package-id>/1.1.0   <- 下载、校验、probe
                         |
                         v
                    原子切换 binding
```

新版本失败时旧 binding 不变。旧版本只有在没有 binding、没有活动 worker/job lease 时才可删除。`managed` 删除可以回收文件；`system/custom` 的“删除”实际是解绑或忘记注册，不能触碰外部目录。这一边界也使 CPU、NVIDIA ASR、Full GPU profile 之间能通过添加和切换 runtime package 升级，而不替换整个主程序。

### 13.7 发行 profile 与离线包

[`packaging/runtime-packs/release-profiles.json`](packaging/runtime-packs/release-profiles.json) 定义：

| profile | sidecar | 预期 runtime package | 说明 |
| --- | --- | --- | --- |
| `core` | `core-only` | 无 | 最小主程序，运行时按需联网安装或绑定 |
| `cpu` | `core-only` | CPU inference | 附带可移除 CPU ASR/OCR 离线 ZIP |
| `nvidia_asr` | `core-only` | NVIDIA ASR + CPU OCR | 语音 CUDA、OCR CPU |
| `full_gpu` | `core-only` | NVIDIA full | ASR/OCR 都支持 CUDA |
| `legacy_full` | `full` | 无独立包 | 兼容当前单体 5.165 GiB 后端 |

四个新 profile 使用同一 Core 和同一 runtime schema。离线发行版只是把发布流水线已经校验并写入可信目录的 managed ZIP 放进 `runtime-packs/offline/`，首次安装无需联网；之后仍可安装新版、切换绑定和删除 managed 版本。在线与离线不能维护两套包格式。当前契约有 SHA-256 信任链，但尚未把代码签名或 catalog 数字签名描述成已完成能力。

`build_sidecar.ps1` / `build_portable.ps1` 的参数默认值仍保留 `legacy_full` 以兼容旧命令；正式 Windows 发行使用 [`scripts/build_windows_release.ps1`](scripts/build_windows_release.ps1)，该脚本只接受 `core`，并拒绝包含本地推理依赖的 sidecar。配置目录的目标默认 profile 也是 `core`。`-CoreOnly` 只是旧命令兼容别名。

### 13.8 构建与验证脚本

[`scripts/build_runtime_worker.ps1`](scripts/build_runtime_worker.ps1) 在隔离的 CPU Paddle 或 Full GPU Python 环境中冻结固定 `runtime-worker.exe`，并检查所需 Paddle/CUDA 发行版、DLL 哨兵、许可证和私有数据边界。随后 [`scripts/build_runtime_pack.ps1`](scripts/build_runtime_pack.ps1) 只接受“已经构建好的 worker payload + 仓库配方”：

```powershell
.\scripts\build_runtime_pack.ps1 `
  -RecipePath .\packaging\runtime-packs\recipes\local-inference-cpu-win-x64.json `
  -PayloadRoot .\artifacts\build\runtime-workers\cpu `
  -OutputDirectory .\artifacts\runtime-packs\cpu
```

它不会执行 `pip`，而是校验入口、许可证、reparse point 和私有数据边界，写入 `runtime-package.json`，生成 ZIP、计算 archive/展开文件 SHA-256 和精确体积，再调用 [`scripts/test_runtime_pack.ps1`](scripts/test_runtime_pack.ps1) 独立解压并实际运行 worker probe。未传 `-SourceUrl` 时生成 `offline_only=true` 条目；在线发布必须使用项目控制的 HTTPS 地址。

超过单资产限制的归档由 [`scripts/split_release_archive.ps1`](scripts/split_release_archive.ps1) 流式拆分并复验重组哈希。最后 [`scripts/build_runtime_catalog.ps1`](scripts/build_runtime_catalog.ps1) 读取真实 entry 和分片 manifest，复核归档与分片，生成 GitHub Release URL，通过 Pydantic 契约验证后原子更新可信 catalog。Core 便携版由 `build_portable.ps1 -ReleaseProfile core -Zip` 生成；NSIS `.exe` 与 MSI 由 `build_windows_release.ps1` 在隔离 Cargo target 中构建，并输出统一 `SHA256SUMS.txt` 和机器可读发行 manifest。

[`scripts/build_portable.ps1`](scripts/build_portable.ps1) 通过 `-ReleaseProfile` 构建发行物。`cpu`、`nvidia_asr`、`full_gpu` 还必须传 `-OfflineRuntimePackDirectory`，且目录里的 package ID 必须与 profile 完全一致，多包、少包或哈希不符都会中止。

### 13.9 Tauri 与 Portable

Vite 构建 React 静态文件，Tauri 组合桌面壳、Core/兼容后端、内置样例、许可证和 runtime metadata。新版 portable 结构是：

```text
artifacts/portable/current/
├─ Video2Notes.exe
├─ backend/
├─ demo/
├─ licenses/
├─ runtime-packs/
│  ├─ catalog.json
│  ├─ release-profiles.json
│  ├─ offline-catalog.json
│  └─ offline/                 # Core/legacy 可为空
├─ BUILD_INFO.json
├─ SHA256SUMS.txt
└─ PORTABLE_README.txt
```

替换 `current` 时先把旧目录移动到临时 `.backup-*`，新目录验证失败则回滚。默认在新目录验证成功后删除临时备份；只有显式 `-KeepPreviousPortable` 才保留上一版。`-Zip` 会另外生成搬运副本，开发阶段不应长期同时保留 ZIP 和 `current`。

### 13.10 PDF 边界

便携包默认不携带完整 Chromium。`render.chromium_pdf` 可以绑定系统已安装的 Edge、Chrome 或 Chromium。缺失时 PDF 降级，Markdown 和 HTML 不受影响。未来若提供 Chromium managed pack，也必须走同一来源/大小/哈希/所有权规则。

## 14. 前端数据流

```text
React component
    |
    v
Zustand action
    |
    v
api.ts 统一请求与 snake_case/camelCase 映射
    |
    v
FastAPI
    |
    v
Pydantic request/response model
```

真实后端和 Demo fixture 被明确区分。Demo 数据只用于 UI 演示和测试，界面持续显示 Demo 标记，不能伪装成真实模型结果。

任务进度每秒轮询。为了避免前一次请求尚未结束又发出下一次请求，store 使用 `pollInFlight` 防止重叠轮询。

阅读器中的简约/专业模式读取同一个 task、NoteDocument 和 evidence，只改变信息密度。

## 15. 错误、取消与恢复

### 15.1 Stage 失败

`StageTransaction` 捕获异常并记录：

- `FAILED` 状态；
- 错误类型和消息；
- 结束时间；
- 已消耗 wall time。

run 状态同步变为 failed。已经写出的 artifact 不会自动删除，便于排障。

### 15.2 取消

取消是协作式的：

1. API 设置 `CancellationToken`。
2. 当前操作在检查点调用 `raise_if_cancelled()`。
3. stage/run 变为 cancelled。

它不会强行杀死正在执行的每个原生库调用，所以 UI 文案是“完成当前操作后取消”。

### 15.3 当前恢复能力

同一个 run 内，重新调用 stage 时可以根据 fingerprint 复用完整且校验通过的产物。

但是当前 UI 没有“从失败阶段原地续跑”按钮，后端重启也不会恢复 JobManager 的实时任务句柄。通常做法是：

- 创建新 run；或
- 对已完成 run 使用支持的局部返工；或
- 开发者手动调用同一 run 的底层能力进行排障。

## 16. 测试分层

### 16.1 Python

`tests/` 覆盖：

- 来源与 Cookie 规则；
- 时间轴与 PTS；
- 自适应采样；
- ASR/OCR适配；
- evidence 融合；
- NoteDocument 与渲染；
- Provider/Role capability；
- API、任务、返工、revision；
- 打包契约和安全边界。

入口：

```powershell
.\scripts\test.ps1
```

该脚本执行 Ruff、mypy、compileall、unittest 和 coverage。

### 16.2 React/Rust/端到端

入口：

```powershell
.\scripts\verify.ps1 -Strict
```

在 Python 检查之外执行：

- ESLint；
- TypeScript；
- Vitest；
- Vite build；
- Rust fmt/check；
- Playwright 主路径。

### 16.3 冻结成品

相关脚本：

- `scripts/test_sidecar.ps1`；
- `scripts/test_runtime_pack.ps1`；
- `scripts/build_runtime_pack.ps1`；
- `scripts/test_portable_cuda.ps1`；
- `scripts/build_portable.ps1`。

冻结成品必须单独验证，因为“源码环境能 import”不代表 PyInstaller 后端能找到 CUDA DLL、FFmpeg、模型组件和许可证。

## 17. 目录和模块地图

```text
Video2Notes/
├─ apps/desktop/                    React + Tauri 桌面程序
│  ├─ src/                          React 页面、组件、store、API 映射
│  └─ src-tauri/                    Rust 进程边界和打包配置
├─ src/video2notes/
│  ├─ api/                          FastAPI
│  ├─ artifacts/                    run workspace、manifest、stage cache
│  ├─ audio/                        字幕、音频提取、ASR、二次 ASR
│  ├─ components/                   模型组件与 runtime catalog/安装/绑定
│  ├─ domain/                       Pydantic 领域模型
│  ├─ evaluation/                   benchmark 工具
│  ├─ fusion/                       统一时间线和证据窗口
│  ├─ jobs/                         后台任务、事件和取消
│  ├─ llm/                          结构化生成协议适配器
│  ├─ media/                        ffprobe、时间基、媒体模型
│  ├─ notes/                        FactCard、NoteDocument、render、revision
│  ├─ ocr/                          PaddleOCR 与跟踪/合并
│  ├─ pipeline/                     主 runner
│  ├─ providers/                    Provider/Model/Role/Secret
│  ├─ sources/                      本地与 yt-dlp 来源
│  ├─ system/                       硬件、资源、性能档位和估算
│  ├─ vision/                       自适应扫描和采样计划
│  ├─ workers/                      隔离 worker 客户端、host 与协议
│  └─ runtime_worker.py             固定运行时 worker 入口
├─ tests/                           Python 测试
├─ scripts/                         bootstrap、测试、sidecar/runtime pack 打包、清理
├─ packaging/runtime-packs/         可信目录、发行 profile 与 worker 配方
├─ docs/                            研究、路线图和 benchmark
├─ samples/                         可再分发样例
├─ artifacts/                       本机生成物，不进入 Git
├─ reference/BiliNote/              只读研究 submodule
├─ README.md                        用户与开发者入口
└─ ARCHITECTURE.md                  当前实现架构真值
```

## 18. 新人修改功能时从哪里开始

### 18.1 增加一个视频来源

1. 阅读 `sources/registry.py` 的路由规则。
2. 新建符合 SourceAdapter 契约的实现。
3. 严格按解析域名匹配，不用字符串包含。
4. 输出统一的 acquisition/source manifest。
5. 增加探测、认证、低清降级和日志脱敏测试。

不要把平台专用字段直接泄漏到后续 ASR/OCR；来源层应先转换成项目领域模型。

### 18.2 增加一个云 ASR

这不是只在协议目录里加名字。完整工作至少包括：

1. 上传/分段请求适配器；
2. Secret 与认证；
3. 取消和超时；
4. segment/word 时间戳映射；
5. 语言与置信度规范化；
6. provenance；
7. 与 `asr.primary/secondary` runtime resolver 集成；
8. capability 过滤；
9. 费用和隐私提示；
10. 测试与失败 fallback。

目前 OpenAI Audio Transcriptions 只有配置表达，没有以上执行器，所以文档明确标为未接入。

### 18.3 增加一个结构化 LLM 协议

1. 在 `ProviderProtocol` 和 `PROTOCOL_CATALOG` 定义传输事实。
2. 在 `llm/client.py` 实现请求、认证、响应提取和 usage 映射。
3. 在 `runtime.py` 为结构化角色创建 backend。
4. 保证错误不包含 credential 或完整 prompt。
5. 用 JSON Schema 验证返回。
6. 增加 provider test/discover 和单元测试。

协议目录里的 `structured_generation_adapter` 必须与真实 runtime 保持一致。

### 18.4 增加一个 pipeline stage

1. 定义清晰输入和输出 artifact。
2. 给 stage 一个显式版本号。
3. 把所有影响结果的配置放进 config hash。
4. 使用 `workspace.stage(...)`。
5. 输出必须在 run 目录内并登记 SHA-256。
6. 设计取消检查点。
7. 说明音频-only时的行为。
8. 更新前端阶段映射、进度、测试和本文档。

### 18.5 修改时间逻辑

先阅读 `domain/models.py` 和 `fusion/timeline.py`。禁止：

- 用浮点秒替换持久化整数微秒；
- 丢弃原始 PTS；
- 把请求采样时间写成实际帧时间；
- 用“最近点”代替区间 overlap，却不更新所有融合测试。

时间轴是整个证据关系的基础，修改风险高于普通 UI 字段。

## 19. 当前限制与明确的未来方向

### 19.1 已知限制

- ASR runtime 只有 faster-whisper；OCR runtime 只有 PaddleOCR。
- 主阶段串行，一次只运行一个桌面任务。
- 视频解码没有 NVDEC。
- 没有自动 run/模型清理和保留期限。
- 没有跨 run 下载/推理缓存。
- 没有失败点 UI 续跑。
- PDF 依赖系统浏览器。
- 没有 DOCX、tagged PDF、Pandoc 路径。
- 没有大规模人工 gold 的 WER/CER/OCR 精度结论。
- 暗色主题完成度低于亮色主题。
- 安装包未做 Authenticode 签名。

### 19.2 计划/候选，不是当前能力

- 云 ASR 和多引擎 adjudication/ROVER；
- WhisperX、FunASR、speaker diarization；
- VLM frame explainer；
- OCR escalation；
- translation 执行器；
- 保留 PTS 的硬件视频解码；
- 音频与视觉主阶段并行；
- SQLite/PostgreSQL 索引；
- 浏览器扩展、官方 OAuth/API 连接器；
- DOCX、tagged PDF、复杂数学排版；
- 在正式发布流水线生成、托管并签署 CPU/NVIDIA runtime 大归档；
- 应用内磁盘统计、保留策略和一键清理。

这些方向落地前，需要同时更新代码、测试、README 和本文档状态，不能只更新 UI 文案。

## 20. 架构决策总结

Video2Notes 当前选择的是：

1. **本地优先**：下载、解码、ASR、OCR和产物默认在本机。
2. **证据优先**：最终笔记可以回到原始时间段、文字和截图。
3. **文件型持久化**：无需数据库即可检查和迁移 run。
4. **统一时间轴**：PTS 和整数微秒优先于近似秒数。
5. **能力驱动路由**：模型是否可选由 capability 决定，不猜模型名。
6. **可降级**：缺模型、缺 LLM、缺 PDF 浏览器时，仍尽可能生成可信的 Markdown/HTML。
7. **追加式返工**：revision 保留历史，不覆盖基线证据。
8. **资源保护**：质量档位不凌驾于实时硬件余量。
9. **明确边界**：配置 schema、执行 adapter 和未来候选必须分开描述。

对新人来说，最重要的主线是：先在 [`src/video2notes/pipeline/runner.py`](src/video2notes/pipeline/runner.py) 看 stage 顺序，再到每个模块看输入输出，最后回到 [`src/video2notes/artifacts/store.py`](src/video2notes/artifacts/store.py) 理解这些结果如何落盘和复用。只要抓住“run -> stage -> artifact -> evidence -> NoteDocument”这条链，整个项目就不会显得杂乱。

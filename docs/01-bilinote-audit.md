# BiliNote v2.4.4 代码审计

审计日期：2026-07-28  
参考仓库：[JefferyHcool/BiliNote](https://github.com/JefferyHcool/BiliNote)  
本地快照：`reference/BiliNote`  
Commit：`6d67e5a76a2c8da1dd73067943d39021ed137c26`

## 总结

BiliNote 已经是一个完整的“视频链接/本地文件 → 字幕或 ASR → LLM → Markdown”产品骨架，值得借鉴它的浏览器扩展、Provider 设置、字幕优先、Markdown 展示、长文本分块和断点恢复。

但它目前并不是高精度多模态理解系统。最核心的视觉路径仍然是固定间隔抽帧、MD5 精确去重、九宫格交给同一个大模型；图像和语音并没有按时间交集对齐。若直接在约 700 行的 `NoteGenerator` 中继续添加 OCR、多 ASR 和更多模型，系统会越来越难验证。因此本项目把上游作为只读参考，选择性迁移外围能力，围绕“统一时间轴 + 可追溯 artifact + 可插拔 stage”重建核心。

## 上游实际技术栈

| 层 | BiliNote 实现 |
|---|---|
| 后端 | Python 3.11、FastAPI、SQLAlchemy/SQLite |
| Web | React 19、Vite、TypeScript、Zustand、Radix/shadcn |
| 桌面 | Tauri 2 |
| 浏览器扩展 | Vue 3、MV3，支持 Chrome/Edge/Firefox |
| 媒体获取 | `yt-dlp`、FFmpeg，另有抖音/快手自定义逻辑 |
| ASR | faster-whisper、MLX Whisper、Groq、必剪、快手 |
| LLM | OpenAI-compatible Chat Completions |
| 检索问答 | ChromaDB |
| 部署 | Docker Compose CPU/GPU |

关键依赖可见：

- `reference/BiliNote/backend/requirements.txt`
- `reference/BiliNote/BillNote_frontend/package.json`
- `reference/BiliNote/BillNote_extension/package.json`

上游使用 MIT License；选择性移植代码时必须保留原版权与许可声明：

- `reference/BiliNote/LICENSE`

## 当前数据流

```text
URL / 本地上传
  → 平台字幕（若有）
  → 否则下载音频并单路 ASR
  → 可选：按固定秒数抽帧并拼九宫格
  → 同一个 LLM 生成 Markdown 和 Screenshot marker
  → FFmpeg 按 marker 截高清帧
  → JSON/SQLite/ChromaDB
  → React Markdown / Markmap / RAG 问答
```

### 字幕优先

这是正确且值得保留的方向。`NoteGenerator.generate()` 优先读取缓存，再尝试平台字幕，失败才下载音频转写：

- `reference/BiliNote/backend/app/services/note.py`

扩展还会在用户真实浏览器中调用 B 站 player API，把带时间戳字幕直接送到本地后端：

- `reference/BiliNote/BillNote_extension/src/logic/bilibili-subtitle.ts`
- `reference/BiliNote/backend/app/routers/note.py`

### 固定抽帧

高精度目标与上游差距最大的部分在：

- `reference/BiliNote/backend/app/utils/video_reader.py`

其逻辑是：

1. `range(0, duration, frame_interval)` 生成固定时间点；
2. 最多抽取 1000 帧；
3. FFmpeg 并行截 JPEG；
4. 只比较相邻 JPEG 的 MD5；
5. 拼成固定网格并编码为 base64。

这不是场景检测、文字变化检测或 UI 状态检测。压缩噪声、光标移动会让 MD5 不同；短暂但重要的文字状态则可能完全落在采样点之间。

### 音画没有真正对齐

`RequestChunker` 按图片序号与文本 chunk 数量的比例分配图片：

- `reference/BiliNote/backend/app/gpt/request_chunker.py`

它没有用图片时间戳与 `TranscriptSegment.start/end` 做区间交集。因此“同一请求中的文字和图片”只是在总体顺序上接近，不保证来自同一时间段。

### 截图 marker

上游让 LLM 输出：

```markdown
*Screenshot-[mm:ss]
```

后端再从原视频截高清帧：

- `reference/BiliNote/backend/app/gpt/prompt_builder.py`
- `reference/BiliNote/backend/app/services/note.py`
- `reference/BiliNote/backend/app/utils/video_helper.py`

“先做语义决策，再从原视频精确截帧”的概念可以保留；需要替换的是候选帧生成、时间对齐与证据校验。

## 可复用能力

| 能力 | 建议 | 参考位置 |
|---|---|---|
| 浏览器扩展读取当前登录态字幕 | 借鉴，但收紧权限和安全边界 | `BillNote_extension/src/logic/bilibili-subtitle.ts` |
| 平台字幕优先 | 保留，字幕作为一个候选证据源 | `backend/app/services/note.py` |
| Downloader/Transcriber adapter 雏形 | 保留接口思想，重做能力描述和生命周期 | `backend/app/downloaders/base.py`、`backend/app/transcriber/base.py` |
| Provider CRUD | 迁移为 capability-based Model Registry | `backend/app/services/provider.py` |
| OpenAI-compatible 模型接入 | 保留兼容层，但不能假定所有模型支持视觉/JSON/temperature | `backend/app/gpt/` |
| 长内容切块、checkpoint、重试 | 选择性移植并补 stage 级 provenance | `backend/app/gpt/request_chunker.py`、`universal_gpt.py` |
| Markdown 查看、时间跳转、Markmap | 可复用为前端起点 | `BillNote_frontend/src/pages/HomePage/components/` |
| Tauri + Web + 浏览器扩展产品形态 | 合理 | `BillNote_frontend/src-tauri`、`BillNote_extension` |

## 必须重建的能力

### 视觉

- 内容自适应事件扫描；
- 镜头切换与镜头内文字/UI 变化分离；
- 文字检测 mask；
- OCR box/line/token 与置信度；
- 滚屏、逐字/逐项出现的状态合并；
- 字幕、弹幕、水印、鼠标光标等短暂 overlay 隔离；
- 感知去重，不再使用 JPEG MD5；
- 按 PTS 对齐语音与画面。

### ASR

上游 `TranscriptSegment` 只有 `start/end/text`：

- `reference/BiliNote/backend/app/models/transcriber_model.py`

新模型至少还需要：

- word-level start/end；
- token/word confidence 或 log probability；
- 分段语言与 code-switch 边界；
- speaker/channel；
- ASR provider/model/version；
- platform subtitle 的人工/自动类型；
- 对齐质量、候选文本与裁决记录；
- raw 与 normalized 文本分离。

上游 faster-whisper 调用也没有暴露 VAD、word timestamps、hotwords、beam、hallucination 控制等高精度选项：

- `reference/BiliNote/backend/app/transcriber/whisper.py`

### 模型路由

上游每个任务只选一个 `provider_id + model_name`，同一个模型同时总结文本和看图。新系统必须按角色选择模型：

- OCR；
- ASR primary/secondary；
- ASR adjudicator；
- frame semantic explainer；
- segment fact extractor；
- note drafter；
- faithfulness verifier；
- translation；
- renderer（确定性程序，不是 LLM）。

### 任务与持久化

上游把下载、字幕、ASR、视觉、总结、截图、状态和 DB 写入集中在：

- `reference/BiliNote/backend/app/services/note.py`

新系统需要 stage DAG。每个 stage 记录输入 hash、配置、代码/模型版本、输出 artifact、耗时、成本、置信度和错误；失败后从最近成功 stage 恢复。

## 已确认的实现缺陷

这些不仅是架构偏好，而是代码中可复现的确定性问题：

1. **视觉任务目录竞争。** 默认最多三个任务并发，但所有 `VideoReader` 共用并会清空 `output_frames` 与 `grid_output`。
2. **B 站字幕命中后的免下载路径失效。** `NoteGenerator` 会向 `BilibiliDownloader.download()` 传 `skip_download=True`，而该方法签名没有这个参数，随后退化为完整下载。
3. **YouTube Cookie UI 与下载器脱节。** 扩展允许同步 YouTube Cookie，后端 `YoutubeDownloader` 却没有读取它。
4. **Cookie 更新不会稳定刷新 downloader。** 平台 map 在 import 时实例化 downloader，Cookie 随之缓存；UI 更新 JSON 后实例未重建。
5. **Cookie 明文持久化和回读。** `config/downloader.json` 保存完整 Cookie，API 可原样返回；B 站还写入 `delete=False` 临时文件但未清理。
6. **Provider 外键类型不一致。** Provider ID 是字符串，Model 表里的 `provider_id` 声明为 Integer 且没有 ForeignKey。
7. **本地上传缺少安全边界。** 原始文件名直接用于保存，没有 UUID、安全文件名、体积/MIME/魔数验证或服务端路径限制。
8. **PDF/HTML/Word 宣称与实现不一致。** 前端实际只下载 Markdown；`ExportUtils` 只有不完整 PDF 路径，README 也把 PDF/Word/Notion 标为未完成。

## 结论

不建议直接 fork 后继续扩展 `NoteGenerator`。建议：

- 保留上游只读快照；
- 按 MIT 要求选择性迁移小而清晰的外围模块；
- 新建高精度核心；
- 先定义证据/时间轴/阶段 artifact 契约，再接 UI 和平台连接器。


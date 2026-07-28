# 评测方法与硬件档位

状态：工程预算与验收设计，尚不是产品实测成绩  
日期：2026-07-28

## 一、先区分两个概念

### Hardware Profile

描述本机能并行放下哪些模型、可用 batch、解码/OCR/ASR 吞吐和显存。

### Quality Profile

描述愿意检查多少视觉事件、运行什么 ASR/OCR、多少疑难段升级、做几轮事实验证。

二者不能绑定：

- 核显机器也能跑 Max Quality，只是更慢，或把少量疑难段交给云 API；
- 5090 也可以跑 Draft，只会更快；
- 显卡本身不直接让同一个模型更准，精度提升来自更强模型、更密集扫描、更多异构复核和更高证据预算。

## 二、不能诚实地给一个“全局准确率”

“视频识别准确率 98%”没有定义，因为任务至少包含：

- 是否发现了重要画面变化；
- OCR 是否识别对；
- ASR 是否识别对；
- 语言与说话人是否识别对；
- 音画是否对齐；
- 总结是否覆盖关键事实；
- 每条 claim 是否被证据支持；
- 截图是否真正有帮助。

一个清晰的 1080p PPT 和一个 360p 快速滚屏短视频不应共享同一个精度数字。产品应按模块、素材类别、分辨率、语言和噪声分桶报告。

## 三、金标集

### 初始集

先建立 20–40 个 2–10 分钟 clip，而不是马上收集几百小时：

| 类别 | 必须覆盖 |
|---|---|
| PPT/讲座 | 整页切换、逐条出现、动画、激光笔/人像遮挡 |
| 代码/终端 | 输入、运行结果、错误提示、滚动 |
| UI 教程 | 菜单、弹窗、hover、短暂 toast、鼠标 |
| 网页/文档 | 连续滚屏、分页、固定导航栏 |
| Talking head | 内嵌字幕、片头片尾、B-roll |
| 弹幕/直播 | 弹幕、ticker、水印、画中画 |
| 快速剪辑 | hard cut、fade、flash、运动镜头 |
| 低清 | 360p/480p、小字、压缩块、录屏二次转码 |
| 音频 | 干净单人、多人、重叠、音乐、强噪、远场 |
| 语言 | 中文、英文、日文、方言、多语言、code-switch |

### 标注内容

- 每个有意义 visual state 的出现/稳定/结束时间；
- 变化类型和是否应当保留截图；
- OCR box、文字与可读/不可读标签；
- transcript word/segment 与 speaker；
- 专有名词；
- 章节与关键 fact；
- 每个 fact 的证据 ID；
- 明确的“待核对”片段。

至少 10% 样本双人标注，以测量人之间的一致性；如果人都无法稳定判断，不能把模型差异简单视为错误。

训练/调参集与最终 test set 分开；阈值只在开发集调，test set 不能反复看。

## 四、模块指标

### 视觉变化

- event precision / recall / F1；
- boundary tolerance：分别统计 ±0.3s、±0.5s、±1.0s；
- meaningful-change miss rate：最重要；
- stable keyframe delay；
- redundant keyframe rate；
- transient overlay false-positive rate；
- blur/occlusion rate；
- scrolling unique-text coverage；
- 每小时候选帧与最终帧数量。

不能只用 shot-boundary F1，因为 PPT 新增一条 bullet 并不是新镜头。

### OCR

- 中文/日文 CER；
- 空格语言 word accuracy/WER；
- text-line detection precision/recall；
- layout/reading-order accuracy；
- normalized edit distance；
- track consistency；
- unique text coverage；
- hallucinated character rate；
- abstention calibration。

按以下维度分桶：

- 360p/480p/720p/1080p/4K；
- 字号占画面高度；
- script；
- 静态/滚动；
- 原图/非生成增强/多帧聚合；
- 遮挡、压缩和透视。

### ASR

- 中文/日文 CER；
- 英文等空格语言 WER；
- named-entity error rate；
- hallucination rate；
- VAD false reject/accept；
- LID macro-F1 与 code-switch boundary F1；
- segment/word timestamp MAE 和 IoU；
- punctuation（作为独立指标）；
- platform caption、primary、selective ensemble 的配对差异。

### 说话人

- DER；
- JER；
- speaker count error；
- speaker attribution accuracy；
- overlap speech 单独统计。

### 音画融合

- frame/audio physical timestamp drift；
- evidence interval association precision/recall；
- 错误跨页归因率；
- semantic link precision（“先讲后展示”）；
- 时间戳点击后到正确证据的成功率。

### 笔记

- fact coverage；
- claim-evidence precision；
- unsupported claim rate；
- wrong-attribution rate；
- number/name/value fidelity；
- timestamp correctness；
- screenshot usefulness 与 redundancy；
- 人工评分：可复核、完整、清楚、是否减少回看。

LLM-as-judge 只能作为一个信号。最终 test set 必须有人工抽查，且 judge 不能看到系统名称。

### 性能与成本

- Real-time factor：`处理秒数 / 视频秒数`；
- decode FPS；
- OCR FPS；
- ASR RTF；
- peak RAM/VRAM；
- API cost / media hour；
- secondary ASR 升级比例；
- VLM 升级事件比例；
- cache hit rate；
- stage retry/failure rate。

## 五、多 ASR 的比较与校准

不同引擎返回的 `confidence=0.9` 不可直接横向比较。必须按：

```text
engine × language × content_domain × noise_bucket
```

在金标集上做温度缩放、Platt scaling 或 isotonic regression，并保存 reliability curve。

实验至少包含：

1. platform caption only；
2. primary only；
3. primary + 全程 secondary；
4. primary + selective secondary；
5. selective secondary + ROVER/confusion network；
6. 冲突段 adjudicator；
7. 同样流程但去掉 OCR/metadata glossary。

只有 selective ensemble 在 test set 上稳定改善 CER/WER、专名和 hallucination，同时成本/延迟可接受，才进入默认 profile。

[NIST ROVER](https://www.nist.gov/publications/post-processing-system-yield-reduced-word-error-rates-recognizer-output-voting-error) 是组合 ASR 的成熟起点；它不是“多数票必胜”的保证。

## 六、四档硬件的保守预算

### 已知基准锚点

[faster-whisper 官方 benchmark](https://github.com/SYSTRAN/faster-whisper#benchmark) 给出的一个参考点：

- RTX 3070 Ti 8 GB，13 分钟 large-v2：
  - batch 8、int8：约 16 秒、约 4.5 GB VRAM；
  - 非 batch int8：约 59 秒、约 2.9 GB VRAM。
- i7-12700K，13 分钟 small int8：约 1 分 42 秒、约 1.48 GB RAM。

它只代表 ASR，不能直接当作整个 Video2Notes 的速度。

官方硬件规格参考：

- [RTX 50 系 Laptop](https://www.nvidia.com/en-sg/geforce/laptops/50-series/)：5060 Laptop 通常是 8 GB，功耗差异会显著影响速度；
- [RTX 5070](https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5070-family/)：桌面版 12 GB；
- [RTX 5090](https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5090/)：标准桌面版 32 GB。

当前开发机实际探测为：

```text
AMD Ryzen 9 9950X3D
NVIDIA GeForce RTX 5090 D v2
24,455 MiB VRAM
```

因此它属于 5090 级吞吐，但本地大模型并发预算应按 24 GB 而不是标准 32 GB 计算。

### 1 小时 1080p 普通讲座/评测视频

以下是保守工程预算，不是实测承诺。4K、60 FPS、持续滚屏、弹幕、多人重叠讲话、强噪或云 API 排队可再乘 2–3。

| 硬件 | 推荐本地配置 | ASR-only | Balanced 全流程 | Max Quality 全流程 |
|---|---|---:|---:|---:|
| 核显/低压 CPU，16 GB RAM | 低分辨率视觉指标；PaddleOCR tiny/small；Whisper small int8 或 FunASR 中文 CPU ASR；VLM/疑难 ASR可走云 | 8–40 分钟 | 30–120 分钟 | 1–3 小时 |
| RTX 5060 Laptop 8 GB | large-v3 int8；OCR GPU；模型串行卸载；小 VLM 或云 VLM | 2–6 分钟 | 10–35 分钟 | 20–60 分钟 |
| RTX 5070 Desktop 12 GB | large-v3 fp16/int8 batch；OCR/diarization；7B Q4 VLM串行 | 1–4 分钟 | 6–20 分钟 | 12–40 分钟 |
| RTX 5090 24–32 GB | dense scan；ASR/OCR/diarization并行；7–14B 本地 VLM；较大 batch | 0.5–2 分钟 | 3–12 分钟 | 6–25 分钟 |

### 精度定位

| 档位 | 定位 |
|---|---|
| 核显/CPU | 全本地适合草稿；允许云端疑难升级时仍可追求高精度，只是更慢/有成本 |
| 5060 Laptop | 推荐的“高质量本地”最低档；8 GB 主要限制并发和本地 VLM 规模 |
| 5070 Desktop | 高精度默认档；可以更频繁做二次 OCR/ASR 和视觉复核 |
| 5090 | 最大本地/隐私档；可同时运行更密集扫描、更强模型和更多验证 |

## 七、Profile 初始建议

详细字段见 `config/profiles.example.yaml`。

| Profile | 粗扫/精扫 | OCR | ASR | Secondary | VLM/验证 |
|---|---|---|---|---|---|
| Eco CPU | 2/8 FPS，480w | mobile/tiny | small int8 | 关闭或极少 | 云端按需，1 轮 |
| Balanced 5060 | 3/12 FPS，640w | mobile GPU | large-v3 int8_float16 | 选择性 | 少量关键帧，1 轮 |
| Accurate 5070 | 4/16 FPS，768w | medium GPU | large-v3 fp16 | 选择性 | 更频繁，2 轮 |
| Max 5090 | 6/24 FPS，960w | medium + escalation | large-v3 + 异构复核 | 选择性高预算 | 本地 7–14B/云 premium，2 轮 |

这些 FPS 是当前实现的起始配置，不是永久常量。生产版的高精度扫描可以顺序检查每个源帧的低成本特征；表中 FPS 是文字 detector/复杂特征的更新预算。

## 八、首次启动校准与 ETA

应用第一次在一台机器上运行时，用 3–5 分钟、多场景校准片测量：

- software/hardware decode FPS；
- 低成本视觉指标 FPS；
- text detector/OCR FPS；
- ASR RTF；
- diarization RTF；
- RAM/VRAM 峰值；
- 本地 VLM tokens/s；
- 云 API 延迟。

对每个真实任务先 probe 预估：

```text
T_total ≈
  T_decode_and_visual_scan
  + candidate_frame_count / OCR_fps
  + audio_duration × ASR_RTF
  + uncertain_audio_duration × secondary_RTF
  + escalated_visual_event_count × VLM_latency
  + note_and_render_time
```

处理过程中使用已完成 stage 的实际速度滚动修正 ETA。UI 应显示区间，例如“预计 8–14 分钟”，而不是伪精确的“9 分 17 秒”。

## 九、首批验收目标

以下是研发目标，不是当前实测成绩：

### Phase 1

- 本地 1080p 视频 A/V drift 小于 100 ms；
- visual event manifest 可重复；
- 单帧 transient overlay 不产生持久状态；
- Markdown 中 100% 章节时间戳能跳到对应 evidence window；
- unsupported factual claim rate < 1%（人工抽查的清晰语料）。

### Phase 2

- 对稳定 ≥ 1 秒的有意义屏幕文字状态，事件 recall ≥ 97%；
- transient overlay false-positive rate ≤ 3%；
- redundant keyframe rate ≤ 15%；
- 清晰 720p/1080p PPT 文字 CER 目标 ≤ 5%；
- 对不可读低清文字有校准的 abstention，不以幻觉填补。

### Phase 3

- selective multi-ASR 相比 primary-only 在 test set 上改善总体 CER/WER；
- 专有名词 error rate 至少相对下降 20%；
- hallucination rate 不升高；
- secondary 只处理少数疑难时段，而不是无条件翻倍成本；
- 每个 fusion 修改保留候选和裁决 provenance。

目标必须按类别分桶审查。某一类素材未达标时，该 profile 应显示 beta/unsupported，而不是用总体平均掩盖。

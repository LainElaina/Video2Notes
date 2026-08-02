# BV12hsEz3ELL 完整三档 GPU 基准（2026-08-02）

这份记录汇总同一个 7 分 32 秒 Bilibili 视频在 Fast、Balanced、Accurate 三档下的完整本地处理结果，重点回答三个问题：

1. NVIDIA CUDA 加速是否真正生效；
2. 三档分别增加了哪些可观察的能力与成本；
3. 当前产物是否已经能被解释为“更高档就一定更准确、更好读”。

结论是：CUDA ASR 已经生效，视觉与 OCR 覆盖也形成了明确的三档梯度；但当前没有人工 gold reference，也没有配置笔记 LLM，因此不能声称 WER、CER、precision、recall 或“准确率提升”。现阶段最可靠的产品结论是：Fast 优先速度，Balanced 增加技术界面的可回查证据，Accurate 显著提高瞬时画面和小字召回；后两档也同步放大了 OCR 成本、截图冗余、错误 ASR 未纠正和隐私泄漏风险。

## 输入、源文件与可复现条件

- 参考视频：Bilibili `BV12hsEz3ELL`《bro问我怎么多平台直播和发布视频》。
- 源文件：`artifacts/benchmarks/BV12hsEz3ELL/source.mp4`。
- SHA-256：`c8b067cd3a7ea8568e27b5d39688379dc648214e9c942a43913eafd93a45033c`。
- 文件大小：34,970,589 bytes（约 33.35 MiB）。
- 时长：452.278912 秒，即约 07:32.279。
- 视频：1080×1920 竖屏、30 fps、AV1。
- 音频：AAC、44.1 kHz、双声道。
- 时间轴原点：0；三档处理的是同一完整媒体，不是不同片段。
- 基准记录时的 Git commit：`0421277230853eb4fd68c7ba6e22babc16639719`，benchmark manifest 记录为无 tracked changes。
- 运行时：Windows 11 `10.0.26200`、Python 3.13.5、Video2Notes 0.1.0、faster-whisper 1.2.1、CTranslate2 4.8.1、PaddleOCR 3.7.0、PaddlePaddle 3.3.1。

## 硬件、模型与实际加速边界

测试机器：

| 项目 | 实际硬件 |
| --- | --- |
| CPU | AMD Ryzen 9 9950X3D，16 核 / 32 线程 |
| 内存 | 93.61 GiB 可见物理内存 |
| GPU | NVIDIA GeForce RTX 5090 D v2 |
| 显存 | 23.88 GiB |
| FFmpeg 可见硬件加速 | CUDA、DXVA2、D3D11VA、D3D12VA、QSV、VAAPI、AMF |

三档校正比较使用同一份本地权重，没有给高档偷换输入：

| 模块 | 实际模型 | payload SHA-256 |
| --- | --- | --- |
| ASR | `faster-whisper-small` | `3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671` |
| OCR detector | `PP-OCRv5_mobile_det_infer` | `afa1820cb16c1fd0dad589d0f8b389139061c1ef6d68019685fd07be997dda5b` |
| OCR recognizer | `PP-OCRv5_mobile_rec_infer` | `2460da90875937c94db97eba74ae3d9e5d4c4c57c42f1f41531c09a26bcc771a` |

CUDA probe 确认 faster-whisper/CTranslate2 能找到 NVIDIA 设备及本地 cuBLAS/cuDNN 运行库，三档校正表中的 ASR 都实际使用 `cuda + float16`。PaddleOCR 仍是 CPU build，所以三档 OCR 均使用 CPU；本次不能被描述为“全流水线都在 GPU 上”。高档的 OCR 提升来自更密的采样、更高的分析分辨率和更大的 OCR 输入宽度，而不是 server OCR 或 GPU OCR。

整卡 GPU 利用率与显存是设备级遥测，包含桌面和其他进程。它们可以证明运行期间 GPU 有活动，但不能精确归因到 Video2Notes 单进程，也不能用来计算本程序独占显存。

## 资源偏好、第一次 Accurate 回退与 CUDA 校正 provenance

这次比较包含一次必须显式记录的安全回退和一次后续校正重跑。

### 原始串行会话

原始会话位于：

`artifacts/benchmarks/BV12hsEz3ELL/session-20260802-full-gpu-small/`

- Fast：`responsive` 资源偏好，ASR 实际为 small / CUDA / float16 / beam 1。
- Balanced：`responsive` 资源偏好，ASR 实际为 small / CUDA / float16 / beam 5。
- 第一次 Accurate：同样从 `responsive` 开始；规划时检测到显卡正承受较高外部负载、可安全使用的 GPU 余量不足，没有可分配 GPU stage slot。资源保护器因此把请求的 CUDA ASR 安全钳制为 CPU，并将 float16 改为 int8。

第一次 Accurate 的实际后端为 `faster-whisper-small / CPU int8 / beam 5 / 11 CPU workers`。它还如实记录了：理想的 large-v3 因当前实际后端和已注册权重而降级为 small，理想的 server OCR 因 PaddleOCR CPU runtime 而降级为 mobile。这个回退是资源保护正确工作的证据，不是 CUDA 不可用，也不应被混入最终三档 CUDA 对比。

### Accurate CUDA throughput 重跑

后续 Accurate 重跑位于：

`artifacts/benchmarks/BV12hsEz3ELL/session-20260802-accurate-gpu-throughput/`

该次运行显式使用 `throughput` 资源偏好，使 Accurate ASR 实际运行在 `small / CUDA float16 / beam 5`。实时 CPU 余量只分配了 3 个 workers，因此它也不是与第一次 CPU 运行完全隔离、只改变 device 的实验。

同一 Accurate 的设备对照：

| 指标 | 第一次 CPU int8 | CUDA float16 重跑 |
| --- | ---: | ---: |
| ASR 阶段 | 161.160 s | 79.113 s |
| ASR 片段 | 313 | 140 |
| ASR 非空白字符 | 2429 | 2446 |
| 平均片段置信度 | 0.4725 | 0.4346 |
| 端到端总耗时 | 3159.082 s | 3013.919 s |
| 有效 CPU workers | 11 | 3 |

CUDA 将 Accurate 的 ASR 阶段加速约 2.04×，但端到端只缩短约 4.60%，因为视觉扫描和 CPU OCR 才是主要成本。两份 ASR 规范化全文相似度为 95.08%；这只能说明输出接近，不能解释为 95.08% 准确率。平均片段置信度更低同样不能直接证明 CUDA 更差，因为切分方式、片段数量和置信度口径都发生了变化。

### 校正后的三档比较

最终三档报告位于：

`artifacts/benchmarks/BV12hsEz3ELL/session-20260802-corrected-all-cuda/`

它组合了原始会话中的 Fast、Balanced，以及后续 throughput 会话中的 Accurate CUDA 重跑。三档源媒体和模型哈希一致，ASR 均为 CUDA float16；但 Accurate 来自后续会话且资源偏好不同，所以这是带完整 provenance 的跨会话校正比较，不是严格隔离环境下的一次性 A/B/C。

## 三档实际执行计划

| 指标 | Fast | Balanced | Accurate（CUDA 校正） |
| --- | ---: | ---: | ---: |
| 资源偏好 | responsive | responsive | throughput |
| ASR | small / CUDA float16 | small / CUDA float16 | small / CUDA float16 |
| beam | 1 | 5 | 5 |
| OCR | mobile / CPU | mobile / CPU | mobile / CPU |
| 粗扫 FPS | 2 | 3 | 6 |
| 细扫 FPS | 6 | 12 | 24 |
| 画面分析宽度 | 480 px | 768 px | 1080 px |
| OCR 输入宽度上限 | 720 px | 1280 px | 2560 px |
| 核验轮次 | 0 | 1 | 2 |
| 配置截图预算/节 | 0 | 2 | 4 |

三档差异主要来自 beam、视觉扫描密度、分析宽度、OCR 输入宽度、复核轮次和截图预算。由于本次三个档位最终都使用 small ASR 与 mobile OCR，不能把结果解释成 large-v3 或 server OCR 的收益。

## 三档完整结果总表

| 指标 | Fast | Balanced | Accurate（CUDA 校正） |
| --- | ---: | ---: | ---: |
| 受保护进程总耗时 | 526.994 s | 1662.287 s | 3013.919 s |
| 相对 Fast | 1.00× | 3.15× | 5.72× |
| ASR 阶段 | 23.831 s | 34.969 s | 79.113 s |
| ASR 片段 | 144 | 169 | 140 |
| ASR 非空白字符 | 2432 | 2449 | 2446 |
| 视觉扫描 | 50.513 s | 129.111 s | 568.793 s |
| 视觉状态 | 88 | 115 | 213 |
| OCR 阶段 | 449.710 s | 1493.339 s | 2358.735 s |
| OCR 已处理帧 | 88 | 115 | 213 |
| OCR 原始接受行 | 1362 | 3198 | 5847 |
| OCR 合并证据 | 1014 | 2355 | 4383 |
| OCR 唯一规范文本 | 769 | 1595 | 2607 |
| OCR 合并证据字符 | 7478 | 16215 | 29093 |
| facts | 307 | 401 | 568 |
| 章节 | 20 | 19 | 22 |
| 截图 | 0 | 115 | 213 |
| Markdown 大小 | 82,641 B | 171,813 B | 297,106 B |
| HTML 大小 | 123,465 B | 67,180,572 B | 119,405,579 B |

相邻档无人工 gold 的方向性一致性：

| 方向 | ASR 全文相似度 | 视觉事件 ±0.5 s | 视觉事件 ±1 s | OCR 时间+文本一致性 |
| --- | ---: | ---: | ---: | ---: |
| Fast → Balanced | 95.71% | 54.02% | 73.56% | 77.77% |
| Balanced → Accurate | 98.26% | 67.54% | 84.21% | 89.28% |

这里的 OCR 一致性使用前后 1 秒时间容差和至少 80% 规范文本相似度，只表示低档结果有多少能在高档附近找到近似结果；它不是 OCR precision、recall 或字符准确率。

## 每档真正增加了什么

### Fast：速度档，不是故意做差的档位

- 8 分钟以内视频约 8 分 47 秒完成，是三档中唯一接近视频时长的完整流程。
- ASR 字符量与另外两档非常接近，说明主要语音内容并没有因为 beam 1 而被大面积丢弃。
- 88 个视觉状态和 1014 条 OCR 合并证据足以回查主流程，但对短暂二维码、弹窗、小字和快速 UI 状态的召回有限。
- 不插入截图，Markdown 与 HTML 体积小，阅读负担和视觉隐私暴露最低。
- 人工复核仍发现大量专有名词错误、原始 ASR 标题和只覆盖开头的摘要；Fast 的优势是成本，不是已经达到可交付的语义质量。

适合：以语音为主、画面只是陪衬、需要快速索引或之后只对少数片段返工的视频。

### Balanced：增加视觉证据的日常档

- 相对 Fast 耗时增加 1135.293 秒，整体为 3.15×。
- 视觉状态增加 30.7%，OCR 合并证据增加 132.2%，准确的软件名和界面词更容易在 evidence 中找到。
- beam 5 对少数普通中文词和句子有改善，但没有系统解决直播姬、爱思投屏、WO Mic、彗星号、TTSCat、火绒、咩播、蚁小二、本机发布等领域术语。
- 原始 run 的 115 张截图让技术教程可以回看界面，但当时的 composer 没有执行配置中的每节 2 张预算，造成明显重复和噪声；本轮基于缓存 evidence 生成的 refined detailed 产物已将截图压到 38 张，见后文“缓存证据后处理成品”。
- OCR 已经能提供正确词，却没有用于覆盖 ASR 标题、摘要和主叙述；因此 Balanced 更像“证据增强档”，还不是稳定的“语义准确档”。

适合：屏幕操作较多、需要回查软件界面和步骤，并能接受更长处理时间的日常技术视频。本轮 refined export 已增加截图筛选，但 OCR-ASR 术语融合仍未完成；在现有 fallback 语义质量下，不能无条件设为所有视频的默认值。

### Accurate：瞬时帧和小字召回档

- 相对 Balanced 再增加 1351.633 秒，整体为 Balanced 的 1.81×、Fast 的 5.72×。
- 视觉状态从 115 增至 213，OCR 合并证据从 2355 增至 4383；人工复核确认它补回了 Balanced 漏掉的爱思投屏二维码/Windows 下载瞬时画面，并把防挂机/验证码片段从单一状态扩展为完整的事件序列。
- 更高分辨率和更密采样让小字、菜单、账号状态和中间操作更可回查。
- ASR 与 Balanced 的全文相似度为 98.26%，字符量也几乎相同；没有 gold 时不能据此声称 Accurate ASR 更准。small 模型的领域词错误仍然存在。
- 原始 run 的 213 张截图、约 297 KB Markdown 和约 119 MB HTML 说明原始证据显著膨胀；本轮 refined detailed 产物已将截图压到 88 张。更高档并非低档关键帧的严格超集，视频结尾某些时间段 Accurate 的状态数反而少于 Balanced。
- 当前更适合作为局部取证、技术审计和返工模式，不适合作为所有完整视频的默认成品模式。

适合：短片段精查、屏幕小字密集、短暂弹窗或二维码不能漏、用户明确接受等待和人工复核的任务。推荐通过“只对不确定或用户指定时间段升级到 Accurate”来控制成本。

## 11 个时间点的人工结论摘要

下表是对同一完整视频的人工产品审阅，不是逐字稿 gold，也不能代替 WER/CER。它关注术语、步骤、瞬时帧、噪声、隐私和最终笔记是否真正利用了证据。

| 时间 | 内容 | Fast → Balanced | Balanced → Accurate（视觉/OCR以 CUDA 校正运行结果为准） | 净结论 |
| --- | --- | --- | --- | --- |
| 00:00–00:25 | B站直播姬、第三方推流、OBS 自定义服务器 | OCR 证据更丰富，但摘要仍使用错误 ASR，只总结开头 | 状态从该段约 5 增至 11，更多登录/模式切换画面；正确“直播姬/往下滑”仍未回写摘要 | 证据提升，语义基本不变 |
| 00:29–01:30 | OBS 显示器采集、爱思投屏、Alt 裁剪 | 普通词局部改善，爱思投屏和 Alt 仍错误；Balanced 漏掉约 00:53–00:55 的短暂安装画面 | 状态约 8→32，补回安卓二维码、Windows 32/64 位下载和更多投屏界面 | Accurate 最明确的瞬时帧收益之一 |
| 01:35–02:18 | WO Mic 安装、手机/电脑连接 | OCR 能读到 `WO Mic`，ASR/标题仍是 `wallmac` | 页面与英文安装说明更完整，正确词数量增加，但没有纠正主叙述 | 取证改善，成品仍需人工纠错 |
| 02:18–03:24 | 彗星号、平台绑定、布局、虚拟摄像头 | OCR 能找到“彗星号”，ASR 仍是近音错词 | 状态约 13→36，更多设置面板和身份码流程；同时暴露更多账号和联系信息 | 视觉增益大，隐私和冗余同步放大 |
| 03:32–04:18 | B站弹幕姬、TTSCat | Balanced 补到正确软件名、压缩包和插件目录，ASR 仍为 TTSK 等 | 状态约 13→25，插件解压、启用、朗读模板和小字更完整 | 技术 evidence 明显增强，章节仍是错误原话 |
| 04:18–04:53 | 抖音直播伴侣、火绒、推流小助手 | OCR 已有“打开火绒搜索推流小助手”，ASR 仍有火融/推留等 | 更多下载、安装、获取推流码状态；标题仍未使用正确 OCR 术语 | 没有实现真正 OCR-ASR 融合 |
| 04:55–05:20 | obs-multi-rtmp、多路推流、地址/流串密钥 | 插件和界面证据增加，“流串密钥”仍被听错 | 多路推流 OCR 覆盖明显增加，但 Ctrl 复制和密钥术语仍错误；敏感输入框被原样保留 | 操作证据提升，凭据风险加重 |
| 05:20–05:33 | 防挂机、验证码、封禁警告 | Balanced 只保留约 1 个视觉状态，关键警告没有进入摘要 | 状态约 1→5，覆盖测试、验证码、未响应、封禁和恢复；CUDA 校正笔记至少生成了警告导向的原始标题 | Accurate 第二个明确的瞬时事件收益 |
| 05:33–06:17 | 彗星号房间、咩播、本地发音引擎 | OCR 能找到正确工具名和 `Microsoft Yaoyao`，正文仍有翻音/翻译等错误 | 状态约 19→21，边际收益很小；正确“咩播/本地发音引擎”仍未稳定进入摘要和标题 | 此段接近采样饱和，继续加帧收益低 |
| 06:17–06:45 | 蚁小二、多账号、视频号每日重登 | OCR 有“蚁小二/视频号”，ASR 仍为近音错词，最终笔记未采用正确名 | 更多搜索、安装、登录和账号状态；CUDA 重跑消除部分错误词不等于识别出了正确词 | 正确 OCR 存在但融合失败，隐私风险较高 |
| 06:46–07:18 | 同步平台、一键发布、本机/浏览器发布 | 可还原发布方式，但标题仍错误、截图过多 | 关键“一键发布”画面清楚；该段状态数约 15→11，证明高档不是低档严格超集，本机仍可能被听成近音错词 | 局部画面更好，最终取舍说明仍未提炼 |

人工审阅的核心发现不是“高档没有用”，而是高档新增的价值停留在 evidence 层：OCR 能看见正确术语、视觉扫描能抓到关键瞬间，但 deterministic composer 没有完成术语仲裁、全片结构化摘要和关键截图策展。

## 为什么不能声称准确率

本次没有以下人工真值：

- 逐字校正的完整语音稿；
- 每个领域术语在时间轴上的正确写法；
- 逐帧屏幕文字标注；
- “哪些画面应该进入笔记”的关键帧 gold；
- 每个生成事实是否被原视频支持的人工标签。

因此以下指标都不是准确率：

- ASR 片段数、字符数和模型置信度；
- OCR 行数、唯一文本数和 evidence 数；
- Fast/Balanced/Accurate 之间的全文相似度；
- 相邻档的视觉/OCR 一致性；
- 人工审阅的 11 个检查点。

要正式声称三档准确率或相对提升，需要制作人工逐字稿和逐帧 truth set，并报告至少：ASR WER/CER、关键术语 recall、OCR CER/precision/recall、关键帧 precision/recall、事实支持率、隐私遮盖召回、端到端时间和成本。

## 无 LLM fallback 的限制

本次三档都没有配置 note fact/draft/verifier LLM，运行记录明确显示使用 extractive/deterministic fallback。该条件验证了本地 evidence 管线的下界，但不能评价目标产品在配置高质量 LLM 后的总结上限。

人工复核到的 fallback 限制：

- 摘要和核心要点主要来自视频开头十几秒，未覆盖完整 7 分 32 秒内容。
- 章节标题直接使用原始 ASR 句子，存在错词、口语、无意义短句和机械时间切片。
- 正确 OCR 与错误 ASR 并列保存，却没有按同一时间窗口、文本类型和置信度进行术语仲裁。
- 软件名、按钮、菜单、网址和快捷键本应优先采用屏幕文字，当前没有回写主叙述。
- 原始 run 的截图预算未执行：Balanced 配置 2 张/节却输出 115 张，Accurate 配置 4 张/节却输出 213 张；本轮缓存后处理已修复预算执行，但不追溯修改原始 benchmark 数字。
- 图片说明经常选择 FPS、GPU、CPU、窗口标题、衣服品牌或单字符，而非步骤语义。
- Accurate 的更多 evidence 直接导致 Markdown/HTML 膨胀，而不是更清晰的报告。

在配置 LLM 之前也应先修复确定性层：时间对齐术语仲裁、截图评分/去重、预算硬限制和隐私遮盖不应完全依赖远程模型。

## 缓存证据后处理成品

在不重新执行下载、视觉扫描、ASR 或 OCR 的前提下，本轮使用各档已经落盘的本地 evidence 重新组合了两套成品：

- `polished` / detailed：保留较多可回查事实，但对最终章节执行事实预算、截图预算、时间分散摘要和敏感 OCR 截图排除；
- `reader` / concise：压缩为 5 个章节、30 条事实，并进一步限制为每个最终章节至多 1 张截图，适合快速阅读和交付检查。

这些是**缓存证据的确定性后处理**。它们没有改变三档的识别模型、识别证据、视觉状态或任何阶段耗时，因此前文 526.994 / 1662.287 / 3013.919 秒和 ASR、视觉、OCR 分项计时仍是唯一的完整运行计时。PDF 是运行结束后另行渲染的，也没有计入 benchmark。

后处理结果：

| 成品指标 | Fast | Balanced | Accurate（CUDA 校正） |
| --- | ---: | ---: | ---: |
| detailed 章节 | 20 | 19 | 22 |
| detailed facts | 252 | 275 | 352 |
| detailed 截图 | 0 | 38 | 88 |
| 原始截图候选 | 0 | 115 | 213 |
| 因敏感 OCR 文本排除的候选 | 0 | 17 | 25 |
| reader 章节 | 5 | 5 | 5 |
| reader facts | 30 | 30 | 30 |
| reader 截图 | 0 | 5 | 5 |
| reader PDF 页数 | 5 | 12 | 12 |
| reader PDF 大小 | 799,885 B | 2,731,694 B | 3,132,595 B |

这里的 facts 数和截图数是最终文稿策展密度，不是识别精度指标。Balanced 和 Accurate 排除的 17 / 25 个候选只表示其 OCR 文本命中了当前敏感模式；它不是对全部图像隐私的人工 gold 检查。原始 OCR、时间轴和关键帧仍保存在本地 run 目录，后处理只对默认导出副本做筛选和脱敏。

三份 reader PDF 共 29 页，已逐页栅格化并做版面目检：没有发现空白页、内容遮挡、裁切或黑块；对可提取 PDF 文本的自动敏感模式扫描为 0 命中。这个“0”只覆盖当前规则能够识别的文本模式，不代表对图片像素中的未知账号、未被 OCR 识别的凭据或新型敏感格式作出零泄漏保证。

时间分散摘要解决了原 fallback 摘要只取视频开头的问题：候选先沿完整时间轴分区，再在各区选择信息量较高的语音事实。它改善的是全片覆盖，不会自动修复 small ASR 的领域词错写，也不能替代 OCR-ASR 术语仲裁或高质量 note LLM。

### Refined 输出路径

以下每行同时给出仓库相对路径和当前测试机绝对路径。每套还在同一 `notes` 目录包含相应的 `polished-document.json` 或 `reader-document.json` 结构化文档。

| 档位 / 版本 | 仓库相对路径 | 当前测试机绝对路径 |
| --- | --- | --- |
| Fast detailed | `artifacts/benchmarks/BV12hsEz3ELL/session-20260802-full-gpu-small/runs/fast/reference-fast/notes/polished-note.md`<br>`artifacts/benchmarks/BV12hsEz3ELL/session-20260802-full-gpu-small/runs/fast/reference-fast/render/polished-note.html`<br>`artifacts/benchmarks/BV12hsEz3ELL/session-20260802-full-gpu-small/runs/fast/reference-fast/render/polished-note.pdf` | `D:/_CodeNotSync/_Video2Notes/artifacts/benchmarks/BV12hsEz3ELL/session-20260802-full-gpu-small/runs/fast/reference-fast/notes/polished-note.md`<br>`D:/_CodeNotSync/_Video2Notes/artifacts/benchmarks/BV12hsEz3ELL/session-20260802-full-gpu-small/runs/fast/reference-fast/render/polished-note.html`<br>`D:/_CodeNotSync/_Video2Notes/artifacts/benchmarks/BV12hsEz3ELL/session-20260802-full-gpu-small/runs/fast/reference-fast/render/polished-note.pdf` |
| Fast reader | `artifacts/benchmarks/BV12hsEz3ELL/session-20260802-full-gpu-small/runs/fast/reference-fast/notes/reader-note.md`<br>`artifacts/benchmarks/BV12hsEz3ELL/session-20260802-full-gpu-small/runs/fast/reference-fast/render/reader-note.html`<br>`artifacts/benchmarks/BV12hsEz3ELL/session-20260802-full-gpu-small/runs/fast/reference-fast/render/reader-note.pdf` | `D:/_CodeNotSync/_Video2Notes/artifacts/benchmarks/BV12hsEz3ELL/session-20260802-full-gpu-small/runs/fast/reference-fast/notes/reader-note.md`<br>`D:/_CodeNotSync/_Video2Notes/artifacts/benchmarks/BV12hsEz3ELL/session-20260802-full-gpu-small/runs/fast/reference-fast/render/reader-note.html`<br>`D:/_CodeNotSync/_Video2Notes/artifacts/benchmarks/BV12hsEz3ELL/session-20260802-full-gpu-small/runs/fast/reference-fast/render/reader-note.pdf` |
| Balanced detailed | `artifacts/benchmarks/BV12hsEz3ELL/session-20260802-full-gpu-small/runs/balanced/reference-balanced/notes/polished-note.md`<br>`artifacts/benchmarks/BV12hsEz3ELL/session-20260802-full-gpu-small/runs/balanced/reference-balanced/render/polished-note.html`<br>`artifacts/benchmarks/BV12hsEz3ELL/session-20260802-full-gpu-small/runs/balanced/reference-balanced/render/polished-note.pdf` | `D:/_CodeNotSync/_Video2Notes/artifacts/benchmarks/BV12hsEz3ELL/session-20260802-full-gpu-small/runs/balanced/reference-balanced/notes/polished-note.md`<br>`D:/_CodeNotSync/_Video2Notes/artifacts/benchmarks/BV12hsEz3ELL/session-20260802-full-gpu-small/runs/balanced/reference-balanced/render/polished-note.html`<br>`D:/_CodeNotSync/_Video2Notes/artifacts/benchmarks/BV12hsEz3ELL/session-20260802-full-gpu-small/runs/balanced/reference-balanced/render/polished-note.pdf` |
| Balanced reader | `artifacts/benchmarks/BV12hsEz3ELL/session-20260802-full-gpu-small/runs/balanced/reference-balanced/notes/reader-note.md`<br>`artifacts/benchmarks/BV12hsEz3ELL/session-20260802-full-gpu-small/runs/balanced/reference-balanced/render/reader-note.html`<br>`artifacts/benchmarks/BV12hsEz3ELL/session-20260802-full-gpu-small/runs/balanced/reference-balanced/render/reader-note.pdf` | `D:/_CodeNotSync/_Video2Notes/artifacts/benchmarks/BV12hsEz3ELL/session-20260802-full-gpu-small/runs/balanced/reference-balanced/notes/reader-note.md`<br>`D:/_CodeNotSync/_Video2Notes/artifacts/benchmarks/BV12hsEz3ELL/session-20260802-full-gpu-small/runs/balanced/reference-balanced/render/reader-note.html`<br>`D:/_CodeNotSync/_Video2Notes/artifacts/benchmarks/BV12hsEz3ELL/session-20260802-full-gpu-small/runs/balanced/reference-balanced/render/reader-note.pdf` |
| Accurate detailed | `artifacts/benchmarks/BV12hsEz3ELL/session-20260802-accurate-gpu-throughput/runs/accurate/reference-accurate/notes/polished-note.md`<br>`artifacts/benchmarks/BV12hsEz3ELL/session-20260802-accurate-gpu-throughput/runs/accurate/reference-accurate/render/polished-note.html`<br>`artifacts/benchmarks/BV12hsEz3ELL/session-20260802-accurate-gpu-throughput/runs/accurate/reference-accurate/render/polished-note.pdf` | `D:/_CodeNotSync/_Video2Notes/artifacts/benchmarks/BV12hsEz3ELL/session-20260802-accurate-gpu-throughput/runs/accurate/reference-accurate/notes/polished-note.md`<br>`D:/_CodeNotSync/_Video2Notes/artifacts/benchmarks/BV12hsEz3ELL/session-20260802-accurate-gpu-throughput/runs/accurate/reference-accurate/render/polished-note.html`<br>`D:/_CodeNotSync/_Video2Notes/artifacts/benchmarks/BV12hsEz3ELL/session-20260802-accurate-gpu-throughput/runs/accurate/reference-accurate/render/polished-note.pdf` |
| Accurate reader | `artifacts/benchmarks/BV12hsEz3ELL/session-20260802-accurate-gpu-throughput/runs/accurate/reference-accurate/notes/reader-note.md`<br>`artifacts/benchmarks/BV12hsEz3ELL/session-20260802-accurate-gpu-throughput/runs/accurate/reference-accurate/render/reader-note.html`<br>`artifacts/benchmarks/BV12hsEz3ELL/session-20260802-accurate-gpu-throughput/runs/accurate/reference-accurate/render/reader-note.pdf` | `D:/_CodeNotSync/_Video2Notes/artifacts/benchmarks/BV12hsEz3ELL/session-20260802-accurate-gpu-throughput/runs/accurate/reference-accurate/notes/reader-note.md`<br>`D:/_CodeNotSync/_Video2Notes/artifacts/benchmarks/BV12hsEz3ELL/session-20260802-accurate-gpu-throughput/runs/accurate/reference-accurate/render/reader-note.html`<br>`D:/_CodeNotSync/_Video2Notes/artifacts/benchmarks/BV12hsEz3ELL/session-20260802-accurate-gpu-throughput/runs/accurate/reference-accurate/render/reader-note.pdf` |

## 性能瓶颈与产品推荐

### 当前瓶颈

OCR 是首要瓶颈：

| 档位 | 视觉扫描 | CUDA ASR | CPU OCR | OCR 占阶段总耗时的近似比例 |
| --- | ---: | ---: | ---: | ---: |
| Fast | 50.513 s | 23.831 s | 449.710 s | 85.7% |
| Balanced | 129.111 s | 34.969 s | 1493.339 s | 90.0% |
| Accurate | 568.793 s | 79.113 s | 2358.735 s | 78.3% |

Accurate 的视觉扫描也已经达到 568.793 秒，成为第二个高档瓶颈。融合、笔记组合和渲染只需秒级或更低，不值得通过牺牲证据来优化。

### OCR GPU 隔离 POC

为验证 RTX 5090 / Blackwell 上的 OCR GPU 可行性，本轮另在 `artifacts/poc/paddle-gpu-cu129-py313-20260802/` 建立了隔离虚拟环境。它没有改动完整三档 run、仓库主 `.venv` 或当时的 benchmark 计时。

POC 使用官方 `paddlepaddle-gpu 3.3.1` CUDA 12.9 构建，Paddle 报告编译 cuDNN 9.9.0，并在 NVIDIA GeForce RTX 5090 D v2、compute capability 12.0（sm120）上通过 `paddle.utils.run_check()`。在视频 30、60、120、180、300、420 秒处各取一个真实 720 px 宽画面，每帧连续处理两轮，共 12 次 PP-OCRv5 mobile GPU 调用：

| POC 指标 | 结果 |
| --- | ---: |
| OCR 引擎初始化 | 0.9316 s |
| 12 次真实帧平均 | 0.1736 s/帧 |
| 12 次真实帧中位数 | 0.1343 s/帧 |
| 最小 / 最大 | 0.0742 / 0.5554 s/帧 |
| 第二轮平均 | 0.13 s/帧 |
| 不同非空文本 | 153 |
| 失败 / CUDA 错误 / OOM | 0 / 0 / 0 |

同一进程随后加载本地 `faster-whisper-small`，用 CTranslate2 CUDA float16 处理 30 秒真实音频：模型加载 0.5504 秒，推理 1.6943 秒，得到 13 个片段且无错误。这证明了所测环境和规定加载顺序下 OCR GPU 与 ASR GPU 可以短时共存。

Windows 下有一个强制约束：**必须先预加载/导入 CTranslate2，再导入 Paddle**。Paddle-first 会先装载其 cuDNN 9.9 的同名 `cudnn64_9.dll`，随后 CTranslate2 会因另一份同名 cuDNN 构建发生 Windows error 127。正式 runtime 还需隔离并注册完整 CUDA 12.9、cuBLAS、cuDNN、cuFFT、cuRAND、cuSOLVER、cuSPARSE 和 nvJitLink DLL 目录，并用该顺序做冷启动测试。

这个 POC 只有 12 次 OCR 调用，单独看它不能声称长时间无泄漏、整段稳定性、完整视频吞吐或冻结成品可用。尤其需要强调：前文 Fast / Balanced / Accurate 的 449.710 / 1493.339 / 2358.735 秒 OCR 计时全部来自 CPU OCR，绝不能把此处 0.1736 秒/帧的短时、720 px GPU 样本混入完整三档耗时或用它倒推整段加速比。

### 正式成品包 GPU 验收

POC 后已把 Paddle GPU 路线正式集成到产品构建：`bootstrap.ps1 -WithAsr -WithOcrGpu` 使用官方 Paddle CUDA 12.9 wheel，并把 9 组 NVIDIA runtime、CTranslate2-first 加载顺序、动态能力探测和真实 Paddle distribution 写入冻结后端契约。2026-08-02 的 full portable 构建及验收结果如下：

| 成品验收项 | 结果 |
| --- | --- |
| 便携目录 | `artifacts/portable/current/`，约 5,288.9 MiB |
| 一键入口 | `artifacts/portable/current/Video2Notes.exe` |
| ZIP | `artifacts/portable/Video2Notes-portable-current.zip`，3,508,931,427 B |
| ZIP SHA-256 | `60b0a632e089e1286915c4003ddeac68fa0c32a39cf5b40f3377e5d8ff89f942`，与 `.sha256` 文件复算一致 |
| 后端契约 | schema 2 / full / `paddlepaddle-gpu 3.3.1` / 9 个 NVIDIA components |
| 隔离冷启动 | 清除外部 Python/CUDA 环境并把 PATH 限制到 System32 后，`/api/health`、未授权 401、授权 `/api/system` 全部通过 |
| ASR CUDA | 1 个设备；支持 float16、int8_float16 等；全部 runtime 路径位于成品包 `_internal/nvidia/` |
| OCR CUDA | 1 个设备；PaddleOCR/PaddlePaddle 报告 CUDA 可用；全部 runtime 路径位于成品包 `_internal/nvidia/` |

能力探测之外，还用最终冻结的 `backend/video2notes.exe` 对 `BV12hsEz3ELL` 的 15 秒真实片段执行了完整 Fast 工作流。该次 run 为 `artifacts/portable/acceptance-data/runs/20260802T095358Z-471fd12142cd/`，13.7 秒内完成来源导入、视觉扫描、音频提取、ASR、OCR、证据融合和 Markdown/HTML 渲染，得到 2 个视觉状态、2 条 ASR evidence、44 条 OCR evidence、共 47 条融合证据。它的 `system/execution-plan.json` 明确记录：

- ASR 实际后端为 CTranslate2 CUDA、float16、beam 1；
- OCR 实际后端为 `PaddleOcrBackend`，配置 `gpu:0`；
- 两个真实 OCR result 的 invocation 均为 `gpu:0 / paddleocr-v3`；
- ASR 与 OCR 的 9 组 runtime 路径全部来自 `artifacts/portable/current/backend/_internal/nvidia/`，没有引用开发 `.venv`。

因此，“正式产品可在本机冻结成品中实际使用 NVIDIA ASR + OCR”已经通过短片验证，不再只是 POC。仍未验证的是整段 7.5 分钟视频改用 GPU OCR 后的三档新耗时、长时间多分辨率显存 soak 和其他 N 卡型号的兼容性；这些限制不能由一次 15 秒成品验收外推消除。

### 推荐优先级

1. 保留 CUDA ASR，并在 UI/manifest 中显示“请求设备、实际设备、回退原因和模型降级”，这次 CPU 回退证明保护策略有价值。
2. 优先优化 OCR：官方 Paddle GPU 路线及冻结成品短片工作流均已通过；下一步应补整段 GPU OCR 三档基准、长时 soak，并增加 ROI/crop 选择、文本区域跟踪、滚动页面去重、批处理和仅对不确定 crop 二次识别。
3. 让 Fast 先跑全片，只把低置信度、快速变化、二维码/弹窗和用户指定区间升级到 Balanced/Accurate，避免整段 24 FPS 细扫。
4. 增加基于时间窗口的 OCR-ASR 术语仲裁：软件名、按钮、菜单、URL、命令和快捷键优先采用高置信度 OCR，并保留原 ASR provenance。
5. 强制执行每节截图预算，按“步骤必要性、清晰度、与相邻帧差异、隐私风险”评分；默认每节 1–3 张，而不是把所有 visual states 全部导出。
6. 配置可选的 note writer/verifier LLM 后，再比较简洁版、详细版和专业版总结；LLM 不应承担基础脱敏和截图预算的唯一责任。
7. 默认资源偏好使用 responsive；只有用户主动选择“吞吐/离开电脑运行”时才允许 throughput，并继续根据实时 GPU/显存/CPU 余量安全降级。

当前使用建议：

- Fast：语音主导视频、快速预览、低价值内容、全片第一遍。
- Balanced：技术教程和 UI 操作较多的视频；优先查看已执行截图预算的 refined/reader 成品，并继续人工回查领域术语。
- Accurate：局部返工、瞬时弹窗/二维码、小字和证据审计；不建议在当前 composer 条件下默认跑完整长视频。

## 隐私发现

本次视频虽然是用户主动提供的公开/自有测试内容，但输出仍暴露了产品必须处理的隐私类别。为避免二次泄漏，本报告不记录任何具体号码、群号、房间号、账号或 RTMP 值。

实际发现包括：

- 联系手机号与合作联系方式被 OCR 多次提取，并进入 Markdown 和截图说明；
- 群号、直播房间 URL/ID 和平台账号昵称可被全文搜索；
- 账号绑定、登录失效和发布状态出现在截图中；
- 身份码/token 形态的字符串被当作普通画面文字写入；
- RTMP endpoint、推流地址和密钥输入区域没有自动遮盖；
- 高档因截图和 OCR 更密，通常会增加同一敏感信息的重复次数。

上线“可分享的 HTML/PDF”前至少需要两层保护：

1. 文本层：手机号、群号、房间号、token、cookie、URL query、RTMP 地址和 stream key 的规则检测、分类与默认遮盖；
2. 图像层：对匹配文本框、账号卡片、聊天窗口和凭据区域进行局部模糊/色块遮盖，并允许用户在导出前预览与撤销。

原始 evidence 可在本地受控目录保留，但默认导出的 Markdown、HTML 和 PDF 应使用脱敏副本。

本轮 refined export 已落实文本字段脱敏、发送给远程 note LLM 前的文本脱敏，以及对含敏感 OCR 文本的截图候选排除。其 reader PDF 的可提取文本敏感模式扫描为 0 命中；这只能证明当前规则覆盖的文本没有进入这些 PDF，图像像素仍应在正式分享前人工预览。

## 产物与审计路径

校正比较与 provenance：

- `artifacts/benchmarks/BV12hsEz3ELL/session-20260802-corrected-all-cuda/benchmark-manifest.json`
- `artifacts/benchmarks/BV12hsEz3ELL/session-20260802-corrected-all-cuda/comparison-provenance.json`
- `artifacts/benchmarks/BV12hsEz3ELL/session-20260802-corrected-all-cuda/detailed-comparison.md`
- `artifacts/benchmarks/BV12hsEz3ELL/session-20260802-corrected-all-cuda/detailed-comparison.json`
- `artifacts/benchmarks/BV12hsEz3ELL/session-20260802-corrected-all-cuda/accurate-device-comparison.md`
- `artifacts/benchmarks/BV12hsEz3ELL/session-20260802-corrected-all-cuda/accurate-device-comparison.json`
- `artifacts/benchmarks/BV12hsEz3ELL/session-20260802-corrected-all-cuda/reports/fast.resource.json`
- `artifacts/benchmarks/BV12hsEz3ELL/session-20260802-corrected-all-cuda/reports/balanced.resource.json`
- `artifacts/benchmarks/BV12hsEz3ELL/session-20260802-corrected-all-cuda/reports/accurate.resource.json`
- `artifacts/benchmarks/BV12hsEz3ELL/session-20260802-corrected-all-cuda/polished-exports.json`：refined detailed/reader 路径、预算、排除数、文件大小与后处理策略。

OCR GPU 隔离 POC：

- `artifacts/poc/paddle-gpu-cu129-py313-20260802/POC-CONCLUSION.md`
- `artifacts/poc/paddle-gpu-cu129-py313-20260802/poc-report.json`
- `artifacts/poc/paddle-gpu-cu129-py313-20260802/POC-REPORT.md`

正式 GPU 便携包与短片验收：

- `artifacts/portable/current/Video2Notes.exe`
- `artifacts/portable/Video2Notes-portable-current.zip`
- `artifacts/portable/Video2Notes-portable-current.zip.sha256`
- `artifacts/portable/current/backend/manifest.json`
- `artifacts/portable/acceptance-data/runs/20260802T095358Z-471fd12142cd/system/execution-plan.json`
- `artifacts/portable/acceptance-data/runs/20260802T095358Z-471fd12142cd/ocr/ocr-evidence.json`
- `artifacts/portable/acceptance-data/runs/20260802T095358Z-471fd12142cd/notes/note.md`
- `artifacts/portable/acceptance-data/runs/20260802T095358Z-471fd12142cd/render/note.html`

三档校正 run：

- Fast：`artifacts/benchmarks/BV12hsEz3ELL/session-20260802-full-gpu-small/runs/fast/reference-fast/`
- Balanced：`artifacts/benchmarks/BV12hsEz3ELL/session-20260802-full-gpu-small/runs/balanced/reference-balanced/`
- Accurate CUDA：`artifacts/benchmarks/BV12hsEz3ELL/session-20260802-accurate-gpu-throughput/runs/accurate/reference-accurate/`
- 第一次 Accurate CPU 回退：`artifacts/benchmarks/BV12hsEz3ELL/session-20260802-full-gpu-small/runs/accurate/reference-accurate/`

每个 run 中最重要的文件：

- `system/execution-plan.json`：请求配置、硬件快照、实际后端、资源钳制与降级原因；
- `manifest.json`：阶段耗时、警告、输入输出和运行状态；
- `asr/asr-evidence.json`：带时间戳 ASR evidence；
- `vision/visual-states.json`：变化驱动的视觉状态与关键帧；
- `ocr/ocr-evidence.json`：OCR 原始行、合并 evidence、tracking 和滚动页选择；
- `evidence/timeline.json`：音频与画面的统一时间轴；
- `notes/document.json`：结构化笔记、facts、evidence 引用和截图；
- `notes/note.md`：Markdown 成品；
- `render/note.html`：HTML 成品。

这份基准验证了 Video2Notes 已经具备可重复的本地下载、CUDA ASR、变化驱动取帧、OCR、统一时间轴和三档资源梯度。本轮缓存后处理进一步落实了全片时间分散摘要、事实/截图预算和默认文本脱敏；官方 Paddle OCR GPU 也已从隔离 POC 推进到 full portable，并在最终冻结 sidecar 的真实 15 秒片段中完成 ASR/OCR 双 CUDA 工作流。完整三档的 OCR 计时仍是 CPU 基线，整段 GPU OCR 新基准、领域术语融合、图像级隐私遮盖、长时稳定性和有人工 gold 的准确率评估仍是下一阶段关键工作。

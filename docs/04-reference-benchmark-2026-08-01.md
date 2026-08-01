# BV12hsEz3ELL 三档受限资源基准（2026-08-01）

这份记录回答一个具体问题：在相同视频、相同本地模型、相同 CPU 资源硬限制下，
Video2Notes 的 Fast、Balanced、Accurate 是否真的形成了可解释的速度/信息量梯度。
它是一次可复现的产品诊断，不是通用 WER/CER、OCR 字符准确率或硬件跑分声明。

## 输入与固定条件

- 参考视频：Bilibili `BV12hsEz3ELL`《bro问我怎么多平台直播和发布视频》。
- 片段：原视频约 03:30–04:00 的 30.021 秒片段，1080×1920、30 fps、AV1 + AAC；
  该媒体没有平台字幕。
- 源文件 SHA-256：
  `6d7c2d7e1ac124c58e521782903e9ef2b54334251d4f25e738afcf8f54c04248`。
- 三档使用同一份 `faster-whisper-small`、PP-OCRv5 mobile detector 和 recognizer；
  没有给 Balanced/Accurate 偷换更强输入，也没有故意给 Fast 使用损坏或更差的模型。
- 三档全部 CPU-only。受控子进程树置于 Windows Job Object，CPU hard cap 为整机算力的
  25%，只开放 8 个逻辑处理器，并使用 below-normal priority。
- `CUDA_VISIBLE_DEVICES=-1` 在推理库导入前设置；整卡 GPU 使用率超过 50% 时看门狗会终止
  任务。GPU 指标是设备总量观测，不是本进程的精确归因。
- 未配置远程 LLM，因此三档都使用可追溯的 deterministic note fallback。此次结果只评估
  本地采集、ASR、OCR、融合与无 LLM 笔记下界。

模型和资源策略的完整散列保存在
`artifacts/benchmarks/BV12hsEz3ELL/session-20260801-postfix-30s-small/benchmark-manifest.json`。

复现命令：

```powershell
.\.venv\Scripts\python.exe -m video2notes.evaluation.reference_benchmark `
  --source artifacts\benchmarks\BV12hsEz3ELL\clip-210-240.mp4 `
  --session-root artifacts\benchmarks\BV12hsEz3ELL\session-20260801-postfix-30s-small `
  --asr-model-dir artifacts\models\faster-whisper-small `
  --ocr-detection-model-dir artifacts\models\paddleocr\PP-OCRv5_mobile_det_infer `
  --ocr-recognition-model-dir artifacts\models\paddleocr\PP-OCRv5_mobile_rec_infer `
  --max-cpu-ratio 0.25 `
  --timeout-seconds 1800 `
  --ffmpeg C:\ffmpeg\bin\ffmpeg.exe `
  --ffprobe C:\ffmpeg\bin\ffprobe.exe `
  --reference-url https://www.bilibili.com/video/BV12hsEz3ELL `
  --title "bro问我怎么多平台直播和发布视频" `
  --language zh
```

## 档位实际执行计划

| 模式 | 场景检测宽度 | OCR 输入上限 | 实际 ASR | beam | 实际 OCR | 截图预算/节 | 复核轮次 |
| --- | ---: | ---: | --- | ---: | --- | ---: | ---: |
| Fast | 480 px | 720 px | small | 1 | mobile | 0 | 0 |
| Balanced | 768 px | 1280 px | small | 5 | mobile | 2 | 1 |
| Accurate | 1080 px | 2560 px | small | 5 | mobile | 4 | 2 |

Balanced 的理想 ASR 是 `large-v3-turbo`；Accurate 的理想组合是 `large-v3 + server OCR`。
本次只注册了 small/mobile，所以运行产物如实写出 downgrade note；界面和 manifest 没有继续
假装更强模型已经运行。这也意味着本实验主要隔离了取帧密度、OCR 分辨率、beam、截图和复核
预算的效果，而不是强模型本身的增益。

## 时间与资源结果

| 模式 | 阶段总耗时 | RTF | 受控进程 CPU 平均/峰值 | 整机 CPU 峰值 | 整卡 GPU 峰值 | 峰值 RSS |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Fast | 28.889 s | **0.9623** | 12.21% / 24.82% | 49.9% | 16% | 839.2 MiB |
| Balanced | 139.063 s | **4.6322** | 12.25% / 25.08% | 83.4% | 17% | 1377.3 MiB |
| Accurate | 241.865 s | **8.0565** | 11.21% / 25.15% | 78.5% | 17% | 1419.6 MiB |

三个 resource report 都确认 `windows_job_hard_cap_applied=true`、CPU rate 为 2500，GPU
watchdog 没有越线。25.08%/25.15% 是采样与 Windows 记账的轻微边界抖动，不代表 hard cap
被移除。

整机 CPU 峰值包含用户正在运行的其他程序，不能归因给 Video2Notes。Balanced/Accurate 捕获
到 83.4%/78.5% 的设备瞬时值时，本任务自己的进程树仍被锁在约 25%。因此这些结果可以证明
本任务没有占满机器，但不能证明实验期间整台电脑的所有进程总和始终低于 50%。GPU 同理是
整卡遥测；本实验通过 CPU-only 环境避免了主动使用 GPU。

阶段分解表明本地 OCR 是主要成本，而不是笔记整理或融合：

| 模式 | 视觉扫描 | ASR | OCR | 融合 | deterministic note |
| --- | ---: | ---: | ---: | ---: | ---: |
| Fast | 1.88 s | 3.18 s | 23.50 s | 0.010 s | 0.010 s |
| Balanced | 7.92 s | 5.30 s | 125.38 s | 0.020 s | 0.050 s |
| Accurate | 40.24 s | 5.56 s | 195.55 s | 0.040 s | 0.080 s |

所以后续优化应优先投入 OCR 区域选择、批处理和不确定 crop 二次识别，而不是牺牲证据完整性
去微调已经只有几十毫秒的融合/笔记阶段。

## 信息量与可读性结果

| 模式 | 视觉状态 | OCR evidence | 总 evidence | 平均已记录置信度 | 截图 | facts | 原始章节 | 最终后处理章节 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Fast | 4 | 37 | 51 | 0.7552 | 0 | 27 | 5 | **4** |
| Balanced | 9 | 228 | 249 | 0.8351 | 9 | 38 | 10 | **5** |
| Accurate | 17 | 558 | 579 | 0.8956 | 17 | 54 | 18 | **8** |

三档证据时间覆盖都是 100%，引用完整性均通过，真实冲突数都是 0。这里的 0 不是强制清零：
融合器只把时间重叠、不同观察、同一语义槽和空间区域内真正互斥的文字判为 OCR conflict；
同一画面中正常共存的多行 UI 文字不再组成冲突笛卡尔积。

“平均已记录置信度”混合了不同来源和不同数量的 evidence，只能用作当次诊断信号，不能当作
人工标注准确率。Accurate 的数值更高与它捕获了更多清晰画面文字一致，但不等于 89.56% 的
字符准确率。

基准完成后又只重放了 deterministic composer，没有重新运行 ASR/OCR：

- 摘要和核心要点先按物理时间使用 ASR/平台字幕，只在不足时补充高质量 OCR；
- `STORMCREW+`、`APK`、`ZIP` 不再挤占摘要；单字“好”等应答仍留在事实时间线，但不再占用
  overview 名额；
- 相邻短章节按 Fast/Balanced/Accurate 的阅读密度合并，全部 fact/evidence/material/screenshot
  引用保持不变；
- 长视频先处理所有窗口再合并到 `max_sections`，不再因为前 24 个视觉窗口用完章节预算而截断
  后半视频。

真实结果重放后，Fast/Balanced/Accurate 分别从 5/10/18 节收敛到 4/5/8 节；27/38/54 个
facts、51/249/579 条 document evidence 和 0/9/17 张截图全部保留。

## 人工核对的八项术语/步骤清单

这不是 CER，而是针对该 30 秒片段内容人工确认的产品检查表。匹配在 ASR + OCR 的联合 evidence
中进行；错误同音字不算命中，避免把“火柔”当作“火绒”成功。

| 片段内信息 | Fast | Balanced | Accurate |
| --- | :---: | :---: | :---: |
| B站弹幕姬/弹幕机 | ✓ | ✓ | ✓ |
| 下载安装包 | ✓ | ✓ | ✓ |
| 插件列表 | ✓ | ✓ | ✓ |
| Re:TTSCat / TTSCat | — | ✓ | ✓ |
| 火绒应用商店 | — | ✓ | ✓ |
| Bandizip | — | ✓ | ✓ |
| Plugins | ✓ | ✓ | ✓ |
| `我的文件\弹幕姬\Plugins` 完整目录语义 | — | — | ✓ |
| **命中** | **4/8** | **7/8** | **8/8** |

具体观察：

- Fast 已经能形成“搜索/进入弹幕机 → 下载 → 安装插件 → 复制到 Plugins”的主流程，且在 25%
  CPU hard cap 下接近实时；但 beam 1 把“搜索弹幕机”听成“走走弹幕机”，专有名词也不稳定。
- Balanced 的 beam 5 修正了“搜索弹幕机”，并从画面补到 `Re:TTSCat`、`火绒应用商店`、
  `Bandizip` 和 `Plugins`，是本次条件下最合理的默认档。
- Accurate 进一步读到弹幕姬页面的长说明、`外掛程式目錄：我的文件\弹幕姬\Plugins` 等小字，
  为精细复核提供了真实增益；代价是约 8.06× 媒体时长和更多需要整理的 OCR evidence。
- small ASR 仍会把“火绒”听成“火融/火柔”、把 Bandizip 听成“班机铺”。Accurate 画面 OCR
  能补足其中一部分，但要验证高精度听写上限仍需运行实际 `large-v3` 和第二 ASR。

## 产品结论与未完成声明

三档现在具有真实意义：Fast 优先时间、Balanced 优先日常信息完整度、Accurate 优先小字和状态
召回；差异同时出现在执行参数、运行时间、状态数量、截图、术语覆盖和笔记密度中。Fast 没有被
刻意做差，它在严格资源限制下仍得到可用主流程。

本次结果还不能证明 Video2Notes 在所有视频上比“直接把视频交给 Codex/Claude”更准确，因为：

- 没有带人工逐字标注的 WER/CER 数据集；
- 没有在同一输入/预算下运行外部系统作盲测；
- 没有在本次基准中配置 LLM writer/verifier、large-v3、server OCR 或第二 ASR。

已经得到验证的优势是可重复的本地下载与处理、物理时间轴对齐、内容变化驱动取帧、档位与资源
硬边界、完整 evidence/citation、局部返工和可配置模型分工。下一阶段若要做正式“优于通用代码
代理”的精度声明，应固定一组多语言/屏幕密集视频，制作人工 truth set，并同时报告 WER、关键
术语 recall、OCR CER、截图命中率、事实支持率、端到端时间和成本。

原始比较、每档 worker/resource report 和完整 run 位于：

`artifacts/benchmarks/BV12hsEz3ELL/session-20260801-postfix-30s-small/`


# Video2Notes

高精度、证据可追溯的视频转笔记系统。项目目标不是“尽快把字幕扔给一个大模型”，而是把视频里的语音、屏幕文字、画面状态和时间关系整理成一条可核验的证据时间轴，再从它生成 Markdown 笔记；HTML、PDF、DOCX 等格式由同一份内容模型确定性渲染。

> 当前状态：研究与视觉变化检测原型阶段。这里还不是一个可替代 BiliNote 的完整应用。

## 已完成的第一步

- 将 [JefferyHcool/BiliNote](https://github.com/JefferyHcool/BiliNote) `v2.4.4` 浅克隆到 `reference/BiliNote`，作为只读参考。
- 完成 BiliNote 的代码级审计，确认可复用能力和高精度目标之间的差距。
- 调研 Bilibili、YouTube、X、本地文件的获取、真实浏览器登录、Cookie 安全和平台边界。
- 形成“统一 PTS 时间轴 + 分阶段 artifact + 模型按角色路由”的系统架构。
- 实现首个实验内核：低成本粗扫整段视频，只对疑似变化窗口做高帧率精扫，并等待画面稳定后选择关键帧。
- 用合成的“PPT 逐条出现”视频完成端到端验证：在约 `2.0s` 和 `4.0s` 都发现了文字/UI 状态变化；单帧短暂覆盖物的单元测试会被过滤。

## 快速运行视觉变化实验

前提：Python 3.11+、FFmpeg/FFprobe 已在 `PATH`。

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .
.\.venv\Scripts\video2notes scan-changes .\your-video.mp4 `
  --output .\artifacts\visual-events.json `
  --preview-dir .\artifacts\previews
```

当前原型只负责发现“什么时候出现了持久画面变化”并选择稳定帧，还不做 OCR。它已经采用两阶段自适应扫描，不是把固定秒数抽到的帧直接交给大模型：

```text
全视频低分辨率粗扫
  → 持久变化候选
  → 候选窗口高帧率重解码
  → 精确变化边界
  → 稳定、清晰关键帧
  → 后续 OCR / VLM（尚未实现）
```

运行测试：

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m unittest discover -s tests -v
```

## 关键设计结论

1. **BiliNote 适合做产品外围参考，不适合直接堆成高精度核心。** 可借鉴浏览器扩展、字幕优先、Provider 设置、Markdown 展示、长内容分块和断点恢复；视觉采样、OCR、时间轴、多 ASR 仲裁和 artifact 持久化需要重建。
2. **变化侦测不默认调用多模态大模型。** 连续帧差分、边缘/文字掩膜、运动补偿、稳定平台和局部精扫更便宜、更可控；VLM 只解释已经筛出的少量高价值帧。
3. **容器 PTS/time base 是唯一物理时间真值。** ASR、OCR、关键帧、字幕和章节都映射到同一毫秒/微秒时间轴，不能再按“文本块序号”和“图片序号”大致配对。
4. **多 ASR 不是全程盲目跑多个 API 后让 LLM 猜。** 先用置信度、语言、声学质量和平台字幕判断；只在低置信或冲突片段启用第二识别器，再做时间约束的 ROVER/混淆网络或质量仲裁。
5. **Markdown 是首要、也是 canonical 输出。** HTML/PDF/DOCX 由同一份 Note Document/Markdown 渲染，不能让大模型分别自由生成互相矛盾的四份正文。
6. **本地文件是第一等输入。** 网络平台的生产级路径优先官方 API/官方导出；`yt-dlp + 本机真实浏览器会话`只作为用户显式授权的本地兼容模式，不做云端 Cookie 托管或绕过 DRM/平台限制。

## 文档

- [BiliNote 代码审计](docs/01-bilinote-audit.md)
- [系统架构与高精度处理方案](docs/02-system-architecture.md)
- [评测方法与硬件档位](docs/03-evaluation-and-hardware-profiles.md)
- [分阶段实施路线](docs/04-roadmap.md)
- [模块级模型路由示例](config/profiles.example.yaml)

## 仓库结构

```text
Video2Notes/
├─ src/video2notes/             新的高精度核心
│  └─ vision/adaptive_sampler.py
├─ tests/                       确定性单元测试
├─ docs/                        调研、架构、评测、路线
├─ config/                      配置结构示例
├─ reference/BiliNote/          上游只读参考（独立浅克隆）
└─ Video2Notes原始提示词.txt     原始需求
```

## 当前限制

- 阈值只经过合成样例验证，尚未经过真实 B 站/YouTube/X 视频语料校准。
- 当前 Pillow 指标后端是可验证的研究基线，不是最终 GPU 加速实现。
- 尚未实现文字检测掩膜、字幕/弹幕/水印隔离、滚屏 set-cover、OCR、ASR、音画融合、笔记生成或 UI。
- 速度和精度数字目前是工程预算区间与验收目标，不是已经测得的产品指标；必须先建立标注语料库。


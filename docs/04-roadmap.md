# Video2Notes 实施路线

路线以“先能测，再变准；先 Markdown，再丰富输出”为原则。阶段结束条件是可验证的质量门槛，不是“页面上出现了一个开关”。

> 本文保留质量路线的阶段定义，但功能已经跨阶段并行落地；它不再等同于版本发布顺序。当前真实交付快照以 [`PROGRESS.md`](PROGRESS.md) 为准。特别是平台连接器、Markdown/HTML/PDF、桌面工作台、分段采样、局部 evidence revision、补充资料和报告 preset 已存在，不能再列为“尚未开始”。

## Phase 0：研究基线与可重复实验

状态：研究基线已完成；真实金标集与持续评测仍进行中

已完成：

- BiliNote v2.4.4 只读快照与代码审计；
- 输入平台、账号登录、Cookie 和条款调研；
- 高精度视觉/ASR/fusion 架构；
- 两阶段自适应视觉变化原型；
- 5 个确定性单元测试；
- 合成逐条 PPT 视频端到端验证。

结束条件：

- 文档评审通过；
- 建立最小真实视频评测集；
- 视觉 event schema 不再频繁破坏性变更。

## Phase 1：本地文件 → 高质量 Markdown MVP

状态：可运行基线已完成；大规模质量校准仍进行中

范围：

- 拖拽/选择本地视频；
- FFprobe 与媒体质量报告；
- 规范 PTS 时间轴；
- 音频提取；
- 单路 high-quality ASR；
- 自适应视觉事件；
- 全分辨率关键帧；
- PaddleOCR primary；
- 统一 evidence timeline；
- 分层事实卡片与 Markdown；
- 时间戳回跳；
- 每任务独立 artifact 目录；
- stage 缓存、重试和恢复。

该阶段最初不要求：

- Bilibili/YouTube/X URL；
- 多 ASR ensemble；
- 自动截图插入；
- PDF；
- RAG 问答；
- 云端多用户部署。

其中 Bilibili/YouTube/X 本地连接器、选择性 secondary ASR、证据预算截图与 PDF 已在后续实现中交付；RAG 与云端多用户仍未交付。

结束条件：

- 用本地文件完整跑通；
- Markdown 不依赖 UI，目录可直接打包带走；
- 所有章节能追溯到 evidence；
- 真实评测集上没有音画时间漂移；
- 失败任务能从最近 stage 恢复。

## Phase 2：精确屏幕文字变化

状态：真实 PTS、自适应/固定/跳过分段采样、稳定关键帧与候选 OCR 已完成；高级 mask、运动补偿与阈值标注工具未完成

范围：

- text detection mask；
- 运动补偿；
- subtitle/danmaku/watermark/mouse overlay mask；
- 稳定文字 box tracking；
- 滚屏 set-cover；
- 逐项/逐字出现状态策略；
- OCR 语言/脚本路由；
- 低清晰度质量警告；
- OCR escalation；
- 视觉事件标注与阈值校准工具。

结束条件：

- 文字状态变化 recall/precision 达到评测目标；
- 单帧 overlay 不产生笔记状态；
- 对 360p 无法读取的小字明确输出低置信，不发生猜测；
- 冗余关键帧受控。

## Phase 3：多语言与选择性多 ASR

状态：VAD、逐段语言识别、词级时间戳和选择性 secondary ASR 已完成；强制对齐、说话人分离与统计校准的 ROVER 仍待评测

范围：

- VAD；
- 分段 language posterior；
- code-switch；
- word timestamps/forced alignment；
- diarization；
- platform caption 候选；
- secondary ASR trigger；
- 时间约束 ROVER/confusion network；
- proper noun glossary；
- adjudication trace。

结束条件：

- primary-only 与 selective ensemble 有同一评测报告；
- selective ensemble 在关键指标上有统计显著改善；
- 成本/延迟增长可解释；
- 每个被改写词能看到候选与选择原因。

## Phase 4：模型注册、角色路由与桌面 UI

状态：Provider/Model/Role、能力校验、质量模式、耗时区间与 React/Tauri 核心工作台已完成；费用建模和浏览器 companion 不在当前完成范围

范围：

- Provider/Model/RoleBinding；
- capability validation；
- 本地/云、费用、隐私、并发信息；
- Eco/Balanced/Accurate/Max profile；
- ETA 和阶段进度；
- Tauri + React；
- artifact/evidence inspector；
- 浏览器 companion 的最小权限与本地配对。

结束条件：

- 不同 stage 可选不同供应商/模型；
- 不兼容绑定在保存时被拒绝；
- 切 profile 会展示预计速度、质量和云成本变化；
- secret 不出现在 SQLite 明文字段、API 响应或日志。

## Phase 5：受控平台连接器

状态：本地文件及 Bilibili/YouTube/X 的本地 yt-dlp 适配、浏览器 profile/cookies.txt 引用与质量警告已完成；官方合作/OAuth 连接器仍属后续

优先顺序：

1. 本地目录/拖拽增强；
2. X 官方 API v2；
3. `yt-dlp` 隔离 sidecar 的游客格式探测；
4. 用户显式授权的本机浏览器 Cookie 兼容模式；
5. Bilibili/YouTube 正式合作或平台允许的连接器。

范围：

- format probe；
- 质量选择与绝不静默降级；
- source metadata；
- 字幕优先；
- 系统浏览器 OAuth；
- OS credential store；
- 单条、低并发、可停止的本地兼容任务；
- rights attestation 与删除策略。

结束条件：

- Cookie 不上传；
- 不使用嵌入式登录；
- sidecar 参数 allowlist；
- 无 DRM/限制绕过；
- 平台失败会安全停止并提示本地上传。

## Phase 6：截图、HTML、PDF、DOCX

状态：canonical `NoteDocument`、证据预算截图、离线 HTML 与 Edge/Chrome PDF 已完成；DOCX、公式/复杂表格和完整可访问性仍属后续

范围：

- screenshot value score；
- 每章截图预算；
- caption 与 evidence link；
- canonical NoteDocument；
- themed HTML；
- Playwright PDF；
- Pandoc DOCX；
- 中文字体、公式、代码、表格、打印样式。

结束条件：

- Markdown、HTML、PDF 的正文来自同一内容树；
- PDF 通过视觉回归测试；
- 图片、公式、代码与分页无明显缺陷；
- 无网络也能打开打包后的 HTML/Markdown。

## Phase 7：问答、知识库与扩展

状态：未开始；当前人工 evidence correction 只是可审计返工，不等同于 active learning 或 RAG

最后再做：

- evidence-aware RAG；
- 跨视频检索；
- 用户人工修订与 active learning；
- 笔记模板；
- 课程/播放列表批处理；
- 协作与云端多租户。

这些功能不能早于核心质量评测，否则只会把不可靠 transcript/OCR 更快传播到更多界面。

## 当前最近的工程任务

1. 建立 10–20 个短真实视频 clip 的标注工具与初始 gold set，按讲座、录屏、PPT、短视频和语言分桶报告 OCR/ASR/视觉事件质量；
2. 用真实短片 fixture 扩展“返工 evidence → active revision → 重新融合 → 补充资料 → report revision”的端到端 provenance 与失败恢复回归；
3. 接入 text detector/overlay mask 与运动补偿，校准渐进文字、滚屏和逐项出现的 recall/precision；
4. 为局部视觉/ASR/人工修订完善桌面返工交互、失败原因和 revision 对比，不隐藏模型未配置等真实状态；
5. 完成亮色简约/详细视图的键盘、窄窗口和长文回归；暗色主题另开色系与对比度验收，不直接反转亮色；
6. 接统一 GPU/CPU 采样器，填充而不是伪造 `peak_vram_bytes`，并按 CPU/iGPU、8 GB、12 GB、24 GB+ 更新吞吐区间；
7. 完成 report revision 的 Markdown/HTML/PDF 视觉回归、复杂图片分页与历史 revision 导出，再评估 DOCX。

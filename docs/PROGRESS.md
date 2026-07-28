# Video2Notes Goal 进度

本文件记录可复核的检查点，不用完成百分比掩盖尚未打通的主路径。

## 2026-07-28：研究基线

- 根仓库已初始化为 `main`。
- 本仓库 Git 作者：`LainElaina <1796137367@qq.com>`。
- BiliNote v2.4.4 / `6d67e5a` 已登记为只读 submodule。
- 现有两阶段视觉状态扫描器的 5 个单元测试通过。
- 合成 7 秒渐进式 PPT 视频发现初始状态和约 2s、4s 两次持久变化。
- 基线提交：`81ce0f6 chore: establish research baseline`。

基线验证：

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m compileall -q src tests
python -m unittest discover -s tests -v
python -m video2notes --help
python -m video2notes scan-changes `
  .\artifacts\synthetic\incremental-slides.mp4 `
  --output .\artifacts\synthetic\events.baseline.json `
  --preview-dir .\artifacts\synthetic\previews-baseline
```

## 当前检查点：本地媒体与可恢复任务内核

进行中：

- 真实 PTS/time base 媒体探测与视觉扫描；
- `EvidenceSpan`、`VisualState`、`ArtifactManifest`；
- 每任务隔离目录、stage fingerprint、缓存与失败恢复；
- Windows bootstrap/test/verify 入口。

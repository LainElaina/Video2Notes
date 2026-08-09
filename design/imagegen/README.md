# Video2Notes UI 素材

这组素材服务于 Windows 桌面端的“证据时间轴工作台”视觉方向：亮色矿物玻璃、纸白环境、石墨文字、低饱和翡翠绿状态色，以及少量珊瑚和琥珀信号色。图片只承担产品氛围、模式语义和空状态，不进入流程日志、证据列表、指标矩阵或模型下拉框。

## 生成记录

- 模型：`gpt-image-2`
- 协议：OpenAI Images API 兼容的 `POST /v1/images/generations`
- 服务端点：通过本地环境变量 `OPENAI_BASE_URL=https://taffy.work/v1` 指定
- 原始生成尺寸：普通素材 `1024x1024`，演示帧 `1536x1024`
- 入库尺寸：最长边 `640px` 的 WebP，实际 30 张合计约 `0.89 MB`
- 密钥：只在生成命令的本地环境变量中使用，仓库不保存密钥

提示词清单在 [video2notes-ui-assets.jsonl](./video2notes-ui-assets.jsonl)。每一行对应一个最终文件名和一条独立提示词，便于以后替换单张素材而不改变组件接口。

## 重新生成

在仓库根目录、已安装 OpenAI Python SDK 的本地虚拟环境中运行：

```powershell
$env:OPENAI_API_KEY = '<只在本机设置>'
$env:OPENAI_BASE_URL = 'https://taffy.work/v1'
.\.venv\Scripts\python.exe `
  "$env:USERPROFILE\.codex\skills\.system\imagegen\scripts\image_gen.py" generate-batch `
  --input design/imagegen/video2notes-ui-assets.jsonl `
  --out-dir apps/desktop/src/assets/visuals `
  --model gpt-image-2 --quality low --size 1024x1024 `
  --output-format webp --output-compression 72 `
  --concurrency 6 --downscale-max-dim 640 --downscale-suffix=-ui
```

生成脚本会先写入模型源图和 `-ui` 优化图。验收通过后，只保留优化图并恢复清单里的规范文件名。前端通过 `VisualAsset` 组件静态导入本地 WebP，构建时由 Vite 生成哈希文件。

## 动效边界

素材默认静止；首屏、采样策略和空状态只做一次 `opacity + translateY` 进入。鼠标悬停只在精确指针设备上放大缩略图，系统启用 `prefers-reduced-motion` 或 `prefers-reduced-transparency` 时自动收敛。实时日志、时间轴游标和 OCR/ASR 指标不使用逐条动画。

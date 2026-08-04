# Video2Notes v0.2.0 发行说明

Video2Notes `0.2.0` 面向 Windows x64，把桌面主程序与本地推理依赖拆分为可独立安装、校验、绑定、升级和移除的组件。用户可以先使用体积较小的 Core，再按机器性能和任务需要添加 CPU、NVIDIA ASR 或 Full GPU 运行时，不必为了单一功能下载完整 CUDA 环境。

## 发行 profile

| Profile | 能力 | 当前额外运行时下载 | 当前额外安装占用 | 适用场景 |
| --- | --- | ---: | ---: | --- |
| `core` | 桌面 UI、本地 API、下载与媒体工具、硬件探测、运行时与模型管理 | 以最终 Core 资产为准 | 目标约 0.40-0.60 GiB | 云模型、本机已有兼容运行时、按需安装 |
| `cpu` | Core + CPU faster-whisper ASR + CPU PaddleOCR | 288.6 MiB | 763.3 MiB | 核显笔记本、无 NVIDIA GPU、低功耗离线处理 |
| `nvidia_asr` | Core + CUDA faster-whisper ASR + CPU PaddleOCR | 1662.8 MiB | 2816.9 MiB | 语音内容为主，希望用 NVIDIA GPU 加速 ASR |
| `full_gpu` | Core + CUDA faster-whisper ASR + CUDA PaddleOCR | 3254.1 MiB | 5059.5 MiB | 高频本地处理、需要同时加速 ASR 与 OCR |

表中的三组运行时数字来自本次已经构建并通过归档校验、展开文件校验和 worker probe 的 Windows x64 产物；它们是不含 Core 的额外占用，组合档位的总下载和总安装占用还需要加上 Core。最终 GitHub Release 页面还会列出每个资产的准确字节数与 SHA-256。

Full GPU 完整归档约为 3.18 GiB，超过 GitHub Release 的单资产大小限制时会发布为多个固定大小、固定 SHA-256 的分片。软件逐片下载和校验后在本地顺序重组，再校验完整归档的大小与 SHA-256；只有整体校验通过才会解压和发布运行时，用户不需要手工合并文件。分片不会降低总下载量。

## 模型权重

Core 和三个运行时包都不包含 Whisper、PaddleOCR 等用户模型权重。模型按实际选择的语言、尺寸和任务能力单独下载、校验和复用，因此安装包不会因为预置未使用模型而膨胀。首次启用相关能力时，软件会先显示下载来源、大小、目标目录和预计安装占用。

## 界面截图

README 已加入六张来自当前 React 桌面端演示状态的 `1600 x 1000` WebP 截图，覆盖新建任务、处理工作台、笔记阅读、运行时管理、性能设置和模型协议页面。截图合计约 409 KiB；演示中的任务进度和硬件数字是固定 fixture，不代表真实跑分。参见 [README 界面预览](../README.md#界面预览)。

## 使用建议

1. 大多数用户先下载 Core，再由“模型与算力”页面选择推荐运行时。
2. 只需要平台字幕、云模型或已有兼容环境时，可以保持 Core 配置。
3. 无 NVIDIA GPU 时选择 CPU；语音识别占主导时选择 NVIDIA ASR；同时需要高吞吐 ASR 与 OCR 时选择 Full GPU。
4. 本地模型权重按需安装，不使用时可以独立清理，不需要重新安装主程序。

# Video2Notes v0.2.1 发行说明

Video2Notes `0.2.1` 是 Windows x64 Core 桌面程序的修复版本。它解决免安装版启动后无法连接本机后端的问题，并统一了全局字体层级与设置体验。视频处理协议和受管推理运行时格式没有变化。

## 本次修复

- 修复便携目录同时发现两份相同运行时 catalog 时产生重复 `package_id/version`，导致后端在监听端口前退出的问题。
- 相同在线运行时目录现在按 SHA-256 去重；离线目录可以覆盖同版本的本地下载地址，但冲突元数据仍会被拒绝。
- 桌面壳会把 sidecar 启动日志保存到本机数据目录的 `logs/backend-session.log`，连接失败时显示结构化错误摘要和日志路径。
- 设置中心新增 `紧凑 / 舒适 / 大字` 三档全局字号，默认使用舒适档，并迁移旧版本地偏好。
- 统一页面文字层级；舒适档可见文字不低于 `12px`，大字档不低于 `14px`。
- Windows 便携构建会在元数据生成后启动真实 `Video2Notes.exe`，等待 WebView2 确认后端连接，并检查关闭后没有残留 sidecar。

## 下载选择

- `Video2Notes-0.2.1-core-windows-x64-portable.zip`：推荐调试和免安装使用。解压完整目录后双击 `Video2Notes.exe`。
- `Video2Notes-0.2.1-core-windows-x64-setup.exe`：NSIS 安装程序。
- `Video2Notes-0.2.1-core-windows-x64.msi`：面向需要 MSI 部署的环境。
- `SHA256SUMS.txt`：本次发布资产的 SHA-256 清单。
- `windows-release-manifest.json`：安装包构建提交、sidecar 指纹、体积和哈希元数据。
- `runtime-catalog.json`：随 `0.2.1` Core 使用的可信运行时目录副本。

免安装 ZIP、安装 EXE 和 MSI 都是 Core 发行形态，不包含 Whisper、PaddleOCR 等模型权重，也不内嵌数 GiB 的 CPU/CUDA 推理运行时。第一次启用本地 ASR 或 OCR 时，设置中心会显示来源、下载大小、安装占用和目标目录，再由用户确认安装或绑定本机已有组件。

## 运行时兼容

CPU、NVIDIA ASR 和 Full GPU 受管运行时的二进制内容没有随本次桌面热修复发生变化，因此继续复用 [`v0.2.0` Release](https://github.com/LainElaina/Video2Notes/releases/tag/v0.2.0) 中已经发布并校验的资产。`0.2.1` 内置 catalog 仍指向这些固定 URL、字节数和 SHA-256，不会下载未经声明的新文件。

## 升级说明

免安装用户应解压到一个完整目录，或整体替换旧便携程序目录；不要只复制主 EXE，因为 `backend`、`runtime-packs`、`demo` 和 `licenses` 都是程序的一部分。任务、配置和 WebView 数据保存在 Windows 用户数据目录，替换便携程序目录不会删除现有任务。API 密钥继续保存在 Windows Credential Manager。

Core 首次启动可能提示 faster-whisper 模型尚未配置。这表示本地 ASR 依赖或模型仍未安装，不是后端连接失败；顶部显示“后端 0.2.1”即代表本机服务已经正常连接。

## 验证范围

- Python Ruff、mypy 和完整测试套件。
- React ESLint、TypeScript、Vitest 与 Vite 生产构建。
- Rust `cargo test`、`cargo check` 与 Tauri release 构建。
- fixture Playwright、真实 loopback API Playwright 和真实便携桌面连接 smoke。
- 便携目录逐文件 SHA-256、构建提交、运行时 flavor 和残留进程检查。

本项目当前没有 Authenticode 代码签名，Windows SmartScreen 仍可能显示未知发布者。下载后可以使用 `SHA256SUMS.txt` 核对文件完整性。

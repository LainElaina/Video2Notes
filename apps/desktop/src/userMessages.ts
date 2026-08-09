import type { Locale } from './stores/uiPreferences'

const containsHan = (value: string): boolean => /[\u3400-\u9fff]/u.test(value)

const englishProductNames = new Map<string, string>([
  ['视频处理（FFmpeg）', 'FFmpeg'],
  ['媒体探测（FFprobe）', 'FFprobe'],
  ['多平台下载（yt-dlp）', 'yt-dlp'],
  ['网页/PDF 浏览器（Chromium）', 'Chromium'],
  ['Python 运行时', 'Python'],
  ['语音识别（faster-whisper）', 'faster-whisper'],
  ['语音推理引擎（CTranslate2）', 'CTranslate2'],
  ['画面文字识别（PaddleOCR）', 'PaddleOCR'],
  ['OCR 推理引擎（PaddlePaddle）', 'PaddlePaddle'],
  ['CUDA / NVIDIA 加速运行时', 'CUDA / NVIDIA runtime'],
])

const englishProductName = (value: string): string =>
  englishProductNames.get(value) ?? (containsHan(value) ? 'The selected item' : value)

// Store actions keep their original technical message for diagnostics and tests.
// The shell translates known product messages and never exposes an untranslated
// sentence as the only explanation in the other interface language.
const englishMessages = new Map<string, string>([
  ['后端版本不支持仅音频；请升级本地后端后再试。', 'This backend version does not support audio-only processing. Update the local backend and try again.'],
  ['正在检查依赖或提交任务，请等待本次操作确认。', 'Checking dependencies or submitting the task. Wait for the current operation to finish.'],
  ['任务未提交：后端版本不支持仅音频。', 'The task was not submitted because this backend version does not support audio-only processing.'],
  ['本地后端尚未连接。', 'The local backend is not connected.'],
  ['正在检查当前任务所需的本地工具与识别运行时。', 'Checking the local tools and recognition runtimes required by this task.'],
  ['任务尚未提交：请先安装或绑定缺失的本地依赖。', 'The task was not submitted. Install or bind the missing local dependencies first.'],
  ['真实任务当前支持协作取消与阶段恢复，不提供伪暂停。', 'Real tasks support cooperative cancellation and stage recovery; simulated pause is unavailable.'],
  ['演示任务已暂停。', 'The demo task is paused.'],
  ['演示任务已继续。', 'The demo task resumed.'],
  ['任务已取消；已产生的 artifact 仍保留在本地。', 'The task was cancelled. Existing artifacts remain on this computer.'],
  ['正在请求协作取消；当前媒体操作安全退出后会确认状态。', 'Requesting cooperative cancellation. The status will update after the current media operation exits safely.'],
  ['没有找到要刷新的任务。', 'No task was found to refresh.'],
  ['仅音频任务没有视觉基线，不能执行画面或 OCR 返工。', 'Audio-only tasks have no visual baseline, so frame or OCR rework is unavailable.'],
  ['只有已完成的任务可以执行局部视觉返工。', 'Only completed tasks can run local visual rework.'],
  ['本地后端尚未连接，无法执行视觉返工。', 'The local backend is not connected, so visual rework cannot run.'],
  ['只有已完成的任务可以执行局部语音返工。', 'Only completed tasks can run local speech rework.'],
  ['本地后端尚未连接，无法执行语音返工。', 'The local backend is not connected, so speech rework cannot run.'],
  ['人工修正文本不能为空。', 'The corrected text cannot be empty.'],
  ['本地后端尚未连接，无法修正证据。', 'The local backend is not connected, so evidence cannot be corrected.'],
  ['补充文字的标题和内容都不能为空。', 'A text material needs both a title and content.'],
  ['本地后端尚未连接，无法保存补充文字。', 'The local backend is not connected, so the text material cannot be saved.'],
  ['本地后端尚未连接，无法上传补充图片。', 'The local backend is not connected, so the supporting image cannot be uploaded.'],
  ['补充材料已从当前任务中删除。', 'The supporting material was deleted from the current task.'],
  ['演示模式只展示组件状态样例；没有扫描或修改本机文件。', 'Demo mode shows sample component states only; it does not scan or modify local files.'],
  ['演示模式只展示运行时包样例，不会扫描或修改本机目录。', 'Demo mode shows sample runtime packages only; it does not scan or modify local folders.'],
  ['连接真实本机后端后才能重新发现系统运行时。', 'Connect the real local backend before scanning system runtimes.'],
  ['正在检查随包、受管和本机系统运行时。', 'Scanning bundled, managed, and local system runtimes.'],
  ['连接真实本机后端后才能登记自定义运行时目录。', 'Connect the real local backend before registering a custom runtime folder.'],
  ['正在验证目录中的 runtime-package.json 与固定入口。', 'Validating runtime-package.json and the fixed entry point in the selected folder.'],
  ['自定义运行时已登记；应用不会接管或删除原目录。', 'The custom runtime is registered. The app will not own or delete its source folder.'],
  ['连接真实本机后端后才能绑定本机依赖路径。', 'Connect the real local backend before binding a local dependency path.'],
  ['正在验证所选路径、版本与兼容性。', 'Validating the selected path, version, and compatibility.'],
  ['连接真实本机后端后才能解除本机依赖绑定。', 'Connect the real local backend before removing a local dependency binding.'],
  ['已解除路径绑定；原程序和目录没有被删除。', 'The path binding was removed. The original program or folder was not deleted.'],
  ['安装任务已开始；可安全取消，已下载的分片会保留用于续传。', 'Installation started. It can be cancelled safely, and downloaded chunks are retained for resuming.'],
  ['只有应用受管的运行时包可以升级。', 'Only app-managed runtime packages can be upgraded.'],
  ['升级任务已开始；新版本验证通过后才会切换绑定。', 'The upgrade started. Bindings switch only after the new version passes validation.'],
  ['演示模式不会修改运行时绑定。', 'Demo mode does not change runtime bindings.'],
  ['只有应用受管的运行时包可以卸载。', 'Only app-managed runtime packages can be uninstalled.'],
  ['卸载任务已开始；仍被绑定或使用中的版本不会被删除。', 'Uninstallation started. Versions that remain bound or in use will not be deleted.'],
  ['只有自定义登记可以从清单中忘记。', 'Only custom registrations can be forgotten from the inventory.'],
  ['已忘记自定义运行时登记；原目录与文件没有被删除。', 'The custom runtime registration was forgotten. Its original folder and files were not deleted.'],
  ['当前没有可自动安装的依赖建议。', 'There are no dependency recommendations that can be installed automatically.'],
  ['正在创建缺失依赖的安装与绑定任务。', 'Creating installation and binding tasks for missing dependencies.'],
  ['正在保存性能与资源配置…', 'Saving performance and resource settings…'],
  ['性能配置已保存；算法计划已按当前机器重新计算。', 'Performance settings were saved, and the processing plan was recalculated for this computer.'],
  ['Provider ID 和显示名称不能为空。', 'Provider ID and display name are required.'],
  ['HTTP Provider 必须填写 Base URL。', 'An HTTP provider requires a Base URL.'],
  ['正在读取 Provider 的模型目录…', 'Reading the provider model catalog…'],
  ['请填写模型 ID，并至少明确声明一项能力。', 'Enter a model ID and explicitly declare at least one capability.'],
  ['请先保存 Provider，再添加模型。', 'Save the provider before adding a model.'],
  ['正在由本地后端测试 Provider 连接…', 'Testing the provider connection through the local backend…'],
  ['密钥不能为空。', 'The secret cannot be empty.'],
  ['密钥已写入 Windows 凭据库；界面不会读取或回显原值。', 'The secret was stored in Windows Credential Manager. The interface will not read or reveal its original value.'],
  ['Provider 密钥已从 Windows 凭据库删除。', 'The provider secret was deleted from Windows Credential Manager.'],
  ['本地后端当前未连接，暂时无法读取阶段产物。', 'The local backend is not connected, so stage artifacts cannot be read.'],
])

const dynamicEnglishMessages: Array<[RegExp, (match: RegExpMatchArray) => string]> = [
  [/^演示探测：(.+)。真实处理需连接本地后端。$/u, match => `Demo probe: ${match[1]}. Connect the local backend for real processing.`],
  [/^已探测 (.+)；下载会校验实际格式。$/u, match => `Detected ${match[1]}; the download step will verify the actual format.`],
  [/^无法开始任务：(.+)$/u, match => `Could not start the task: ${match[1]}`],
  [/^依赖预检失败，任务未提交：(.+)$/u, match => `Dependency preflight failed and the task was not submitted: ${match[1]}`],
  [/^运行时发现完成，共找到 (\d+) 个可登记实例。$/u, match => `Runtime discovery completed with ${match[1]} registrable instances.`],
  [/^(.+) 已绑定到指定路径。$/u, match => `${englishProductName(match[1])} was bound to the selected path.`],
  [/^本机依赖路径未能绑定：(.+)$/u, match => `The local dependency path could not be bound. Technical details: ${match[1]}`],
  [/^发现 (\d+) 个模型；发现结果不包含能力声明。$/u, match => `Discovered ${match[1]} models. Discovery results do not include capability declarations.`],
  [/^模型标识 (.+) 已存在。$/u, match => `Model identifier ${match[1]} already exists.`],
  [/^(.+) 已绑定到 (.+)。$/u, match => `${englishProductName(match[1])} was bound to ${match[2]}.`],
  [/^(.+) 已取消绑定。$/u, match => `${englishProductName(match[1])} was unbound.`],
  [/^已导出阶段产物 (.+)。$/u, match => `Exported stage artifact ${match[1]}.`],
  [/^该任务没有可下载的 (.+) 产物。$/u, match => `This task has no downloadable ${match[1]} artifact.`],
]

const failureWords = /失败|错误|无法|不能|不支持|缺少|未连接|未找到|不存在|不能为空|不兼容|未完成|未提交/u
const progressWords = /正在|等待|开始|请求|检查|验证|扫描|读取|创建/u
const successWords = /成功|已完成|已保存|已同步|已连接|已绑定|已取消|已导出|已删除|已登记|已解除|已更新/u

export function localizeUserMessage(message: string, locale: Locale): string {
  if (!message) return message

  if (locale === 'en-US') {
    if (!containsHan(message)) return message
    const exact = englishMessages.get(message)
    if (exact) return exact
    for (const [pattern, format] of dynamicEnglishMessages) {
      const match = message.match(pattern)
      if (match) return format(match)
    }
    if (failureWords.test(message)) return 'The operation could not be completed. Open the relevant panel for details.'
    if (progressWords.test(message)) return 'The operation is in progress.'
    if (successWords.test(message)) return 'The operation completed.'
    return 'The operation status was updated.'
  }

  if (containsHan(message)) return message
  if (/failed|error|invalid|unavailable|timeout|not found|could not/i.test(message)) {
    return `操作未完成。技术详情：${message}`
  }
  if (/success|ready|reachable|connected|saved|completed/i.test(message)) {
    return '操作已完成。'
  }
  return `操作状态已更新。技术详情：${message}`
}

import { useEffect, type RefObject } from 'react'
import {
  Archive,
  Check,
  Download,
  ExternalLink,
  HardDrive,
  LoaderCircle,
  PackageCheck,
  Settings2,
  ShieldAlert,
  TriangleAlert,
  X,
} from 'lucide-react'
import { useI18n } from '../i18n'
import { preferredScrollBehavior } from '../motion'
import { useStudioStore } from '../store'
import { MotionPresence } from './MotionPresence'

const formatBytes = (bytes: number): string => {
  if (bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const exponent = Math.min(
    units.length - 1,
    Math.floor(Math.log(bytes) / Math.log(1024)),
  )
  const value = bytes / 1024 ** exponent
  return `${value.toFixed(value >= 100 || exponent === 0 ? 0 : value >= 10 ? 1 : 2)} ${units[exponent]}`
}

interface DependencyPreflightDialogProps {
  restoreFocusRef?: RefObject<HTMLElement | null>
}

const requirementLabelsEn: Record<string, string> = {
  'tool.ffmpeg': 'FFmpeg video processing',
  'tool.ffprobe': 'FFprobe media probing',
  'download.ytdlp': 'yt-dlp video download',
  'asr.faster_whisper': 'faster-whisper speech recognition',
  'ocr.paddleocr': 'PaddleOCR on-screen text recognition',
  'render.chromium_pdf': 'Chromium PDF rendering',
}

export function DependencyPreflightDialog({
  restoreFocusRef,
}: DependencyPreflightDialogProps) {
  const { locale, text } = useI18n()
  const open = useStudioStore(state => state.jobPreflightDialogOpen)
  const preflight = useStudioStore(state => state.jobPreflight)
  const inventory = useStudioStore(state => state.runtimePackages)
  const runtimePackageError = useStudioStore(state => state.runtimePackageError)
  const autoRetryPending = useStudioStore(
    state => state.preflightAutoRetryPending,
  )
  const backend = useStudioStore(state => state.backend)
  const dismiss = useStudioStore(state => state.dismissJobPreflight)
  const installRequirements = useStudioStore(state => state.installPreflightRequirements)
  const createTask = useStudioStore(state => state.createTask)
  const navigate = useStudioStore(state => state.navigate)

  const visible = Boolean(open && preflight)

  useEffect(() => {
    if (!visible) return

    const dismissOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      dismiss()
    }

    document.addEventListener('keydown', dismissOnEscape)
    return () => document.removeEventListener('keydown', dismissOnEscape)
  }, [dismiss, visible])

  if (!preflight) {
    return (
      <MotionPresence
        show={false}
        className="motion-presence-modal"
        exitMs={160}
        focusMode="modal"
        restoreFocusRef={restoreFocusRef}
      >
        {null}
      </MotionPresence>
    )
  }

  const actionable = preflight.recommendedActions.some(action =>
    ['install', 'bind'].includes(action.kind),
  )
  const packageIds = new Set(
    preflight.recommendedActions
      .map(action => action.packageId)
      .filter((value): value is string => Boolean(value)),
  )
  const activeOperations =
    inventory?.operations.filter(
      operation =>
        ['queued', 'running'].includes(operation.status) &&
        (packageIds.size === 0 || packageIds.has(operation.packageId)),
    ) ?? []
  const installing = activeOperations.length > 0
  const waitingForCompletion = installing || autoRetryPending
  const allMissing = [
    ...preflight.missingRequired.map(item => ({ ...item, required: true })),
    ...preflight.missingOptional.map(item => ({ ...item, required: false })),
  ]
  const preflightDetail = autoRetryPending
    ? text(
        '正在准备所需组件。全部操作完成后会刷新绑定，并自动重新检查同一份任务。',
        'Preparing the required components. When every operation completes, bindings will refresh and the same task will be checked again automatically.',
      )
    : locale === 'zh-CN'
      ? preflight.detail ?? '任务没有提交。安装或绑定完成后，系统会重新检查同一份任务配置。'
      : preflight.state === 'blocked'
        ? 'The task was not submitted because required runtime capabilities are missing. After installation or binding, the same task configuration will be checked again.'
        : preflight.state === 'degraded'
          ? 'The task can continue with reduced capabilities. Missing optional capabilities will be recorded in runtime notices.'
          : 'The runtime capabilities required by this task are ready.'

  const openManager = () => {
    dismiss()
    navigate('models')
    // The settings page renders asynchronously after navigation; poll frames
    // (bounded) until the runtime section exists instead of a fixed timeout.
    const startedAt = performance.now()
    const scrollToRuntimePackages = () => {
      const runtimePackages = document.getElementById('runtime-packages')
      if (runtimePackages) {
        runtimePackages.scrollIntoView?.({
          behavior: preferredScrollBehavior(),
          block: 'start',
        })
        runtimePackages.focus({ preventScroll: true })
        return
      }
      if (performance.now() - startedAt < 500) {
        window.requestAnimationFrame(scrollToRuntimePackages)
      }
    }
    window.requestAnimationFrame(scrollToRuntimePackages)
  }

  return (
    <MotionPresence
      show={visible}
      className="motion-presence-modal"
      exitMs={160}
      focusMode="modal"
      restoreFocusRef={restoreFocusRef}
    >
      <div className="dependency-dialog-backdrop" role="presentation">
        <section
          className="dependency-dialog"
          role="dialog"
          aria-modal="true"
          aria-labelledby="dependency-dialog-title"
          tabIndex={-1}
        >
        <header>
          <span className="dependency-dialog-icon">
            <ShieldAlert size={20} aria-hidden="true" />
          </span>
          <div>
            <span className="section-kicker">{text('任务依赖预检', 'Task dependency preflight')}</span>
            <h2 id="dependency-dialog-title">{text('开始前还需要本地组件', 'Local components are required before starting')}</h2>
            <p>{preflightDetail}</p>
          </div>
          <button
            type="button"
            aria-label={text('关闭依赖检查', 'Close dependency check')}
            title={text('关闭', 'Close')}
            onClick={dismiss}
          >
            <X size={17} aria-hidden="true" />
          </button>
        </header>

        <div className="dependency-size-summary" aria-label={text('预计下载与安装体积', 'Estimated download and installed size')}>
          <div><Download size={16} aria-hidden="true" /><span><small>{text('预计下载', 'Download')}</small><strong>{formatBytes(preflight.estimatedDownloadBytes)}</strong></span></div>
          <div><HardDrive size={16} aria-hidden="true" /><span><small>{text('预计安装后', 'Installed')}</small><strong>{formatBytes(preflight.estimatedInstalledBytes)}</strong></span></div>
          <div><Archive size={16} aria-hidden="true" /><span><small>{text('缺失能力', 'Missing capabilities')}</small><strong>{text(`${preflight.missingRequired.length} 必需 / ${preflight.missingOptional.length} 可选`, `${preflight.missingRequired.length} required / ${preflight.missingOptional.length} optional`)}</strong></span></div>
        </div>

        <div className="dependency-missing-list">
          {allMissing.map(item => (
            <article key={`${item.required ? 'required' : 'optional'}:${item.requirementId}`}>
              <span className={`dependency-requirement-state ${item.required ? 'is-required' : ''}`}>
                {item.required ? <TriangleAlert size={14} aria-hidden="true" /> : <PackageCheck size={14} aria-hidden="true" />}
              </span>
              <div>
                <header><strong>{locale === 'zh-CN' ? item.label : requirementLabelsEn[item.requirementId] ?? item.requirementId}</strong><span>{item.required ? text('必须解决', 'Required') : text('可降级', 'Optional')}</span></header>
                <p>{locale === 'zh-CN' ? item.detail : `This task requires the local capability ${item.capabilityId}.`}</p>
                <dl>
                  <div><dt>{text('能力', 'Capability')}</dt><dd>{item.capabilityId}</dd></div>
                  {item.packageId && <div><dt>{text('建议包', 'Suggested package')}</dt><dd>{item.packageId}{item.version ? ` @ ${item.version}` : ''}</dd></div>}
                  {item.downloadSizeBytes !== undefined && <div><dt>{text('下载', 'Download')}</dt><dd>{formatBytes(item.downloadSizeBytes)}</dd></div>}
                  {item.downloadPartCount !== undefined && item.downloadPartCount > 1 && <div><dt>{text('分片', 'Parts')}</dt><dd>{text(`${item.downloadPartCount} 个固定哈希文件`, `${item.downloadPartCount} fixed-hash files`)}</dd></div>}
                  {item.installedSizeBytes !== undefined && <div><dt>{text('安装后', 'Installed')}</dt><dd>{formatBytes(item.installedSizeBytes)}</dd></div>}
                </dl>
                {item.officialUrl && (
                  <a href={item.officialUrl} target="_blank" rel="noreferrer">
                    <ExternalLink size={11} aria-hidden="true" />
                    {text('查看下载来源', 'View download source')}
                  </a>
                )}
              </div>
            </article>
          ))}
        </div>

        <div className="motion-swap-stack dependency-install-status">
          <MotionPresence
            show={waitingForCompletion}
            className="motion-presence-swap"
            exitMs={140}
            animateInitial={false}
          >
            {waitingForCompletion && (
              <div className="dependency-install-progress" aria-live="polite">
                <LoaderCircle className="spin" size={16} aria-hidden="true" />
                <div>
                  <strong>{text('组件任务正在执行，完成后自动重检', 'Component operations are running; the task will be checked again when they finish')}</strong>
                  <span>
                    {activeOperations.length > 0
                      ? activeOperations.map(operation => `${operation.packageId} ${Math.round(operation.progress * 100)}%`).join('；')
                      : text('正在等待后端返回最新安装状态。', 'Waiting for the backend to return the latest installation status.')}
                  </span>
                </div>
              </div>
            )}
          </MotionPresence>
          <MotionPresence
            show={!waitingForCompletion && Boolean(runtimePackageError)}
            className="motion-presence-swap"
            exitMs={140}
            animateInitial={false}
          >
            {!waitingForCompletion && runtimePackageError && (
              <div className="dependency-install-progress is-error" role="alert">
                <TriangleAlert size={16} aria-hidden="true" />
                <div>
                  <strong>{text('依赖准备未完成', 'Dependency preparation did not complete')}</strong>
                  <span>{runtimePackageError}</span>
                </div>
              </div>
            )}
          </MotionPresence>
        </div>

        <footer>
          <span><Check size={14} aria-hidden="true" />{text('现有视频、模型权重和用户目录不会被安装操作覆盖。', 'Installation will not overwrite existing videos, model weights, or user directories.')}</span>
          <div>
            <button className="button button-secondary" type="button" onClick={openManager}>
              <Settings2 size={14} aria-hidden="true" />
              {text('打开依赖管理', 'Open dependency manager')}
            </button>
            {actionable && (
              <button
                className="button button-primary"
                type="button"
                disabled={backend.mode !== 'real' || waitingForCompletion}
                onClick={installRequirements}
              >
                {waitingForCompletion ? <LoaderCircle className="spin" size={14} aria-hidden="true" /> : <Download size={14} aria-hidden="true" />}
                {waitingForCompletion ? text('正在安装', 'Installing') : text('安装所需组件', 'Install required components')}
              </button>
            )}
            <button
              className="button button-primary dependency-recheck-button"
              type="button"
              disabled={waitingForCompletion}
              onClick={createTask}
            >
              <PackageCheck size={14} aria-hidden="true" />
              {text('重新检查并开始', 'Check again and start')}
            </button>
          </div>
        </footer>
        </section>
      </div>
    </MotionPresence>
  )
}

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
import { useStudioStore } from '../store'

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

export function DependencyPreflightDialog() {
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

  if (!open || !preflight) return null

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

  const openManager = () => {
    dismiss()
    navigate('models')
    window.setTimeout(() => {
      document.getElementById('runtime-packages')?.scrollIntoView({
        behavior: 'smooth',
        block: 'start',
      })
    }, 80)
  }

  return (
    <div className="dependency-dialog-backdrop" role="presentation">
      <section
        className="dependency-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="dependency-dialog-title"
      >
        <header>
          <span className="dependency-dialog-icon">
            <ShieldAlert size={20} aria-hidden="true" />
          </span>
          <div>
            <span className="section-kicker">任务依赖预检</span>
            <h2 id="dependency-dialog-title">开始前还需要本地组件</h2>
            <p>
              {autoRetryPending
                ? '正在准备所需组件。全部操作完成后会刷新绑定，并自动重新检查同一份任务。'
                : preflight.detail ?? '任务没有提交。安装或绑定完成后，系统会重新检查同一份任务配置。'}
            </p>
          </div>
          <button
            type="button"
            aria-label="关闭依赖检查"
            title="关闭"
            onClick={dismiss}
          >
            <X size={17} aria-hidden="true" />
          </button>
        </header>

        <div className="dependency-size-summary" aria-label="预计下载与安装体积">
          <div><Download size={16} aria-hidden="true" /><span><small>预计下载</small><strong>{formatBytes(preflight.estimatedDownloadBytes)}</strong></span></div>
          <div><HardDrive size={16} aria-hidden="true" /><span><small>预计安装后</small><strong>{formatBytes(preflight.estimatedInstalledBytes)}</strong></span></div>
          <div><Archive size={16} aria-hidden="true" /><span><small>缺失能力</small><strong>{preflight.missingRequired.length} 必需 / {preflight.missingOptional.length} 可选</strong></span></div>
        </div>

        <div className="dependency-missing-list">
          {allMissing.map(item => (
            <article key={`${item.required ? 'required' : 'optional'}:${item.requirementId}`}>
              <span className={`dependency-requirement-state ${item.required ? 'is-required' : ''}`}>
                {item.required ? <TriangleAlert size={14} aria-hidden="true" /> : <PackageCheck size={14} aria-hidden="true" />}
              </span>
              <div>
                <header><strong>{item.label}</strong><span>{item.required ? '必须解决' : '可降级'}</span></header>
                <p>{item.detail}</p>
                <dl>
                  <div><dt>能力</dt><dd>{item.capabilityId}</dd></div>
                  {item.packageId && <div><dt>建议包</dt><dd>{item.packageId}{item.version ? ` @ ${item.version}` : ''}</dd></div>}
                  {item.downloadSizeBytes !== undefined && <div><dt>下载</dt><dd>{formatBytes(item.downloadSizeBytes)}</dd></div>}
                  {item.downloadPartCount !== undefined && item.downloadPartCount > 1 && <div><dt>分片</dt><dd>{item.downloadPartCount} 个固定哈希文件</dd></div>}
                  {item.installedSizeBytes !== undefined && <div><dt>安装后</dt><dd>{formatBytes(item.installedSizeBytes)}</dd></div>}
                </dl>
                {item.officialUrl && (
                  <a href={item.officialUrl} target="_blank" rel="noreferrer">
                    <ExternalLink size={11} aria-hidden="true" />
                    查看下载来源
                  </a>
                )}
              </div>
            </article>
          ))}
        </div>

        {waitingForCompletion && (
          <div className="dependency-install-progress" aria-live="polite">
            <LoaderCircle className="spin" size={16} aria-hidden="true" />
            <div>
              <strong>组件任务正在执行，完成后自动重检</strong>
              <span>
                {activeOperations.length > 0
                  ? activeOperations.map(operation => `${operation.packageId} ${Math.round(operation.progress * 100)}%`).join('；')
                  : '正在等待后端返回最新安装状态。'}
              </span>
            </div>
          </div>
        )}

        {!waitingForCompletion && runtimePackageError && (
          <div className="dependency-install-progress is-error" role="alert">
            <TriangleAlert size={16} aria-hidden="true" />
            <div>
              <strong>依赖准备未完成</strong>
              <span>{runtimePackageError}</span>
            </div>
          </div>
        )}

        <footer>
          <span><Check size={14} aria-hidden="true" />现有视频、模型权重和用户目录不会被安装操作覆盖。</span>
          <div>
            <button className="button button-secondary" type="button" onClick={openManager}>
              <Settings2 size={14} aria-hidden="true" />
              打开依赖管理
            </button>
            {actionable && (
              <button
                className="button button-primary"
                type="button"
                disabled={backend.mode !== 'real' || waitingForCompletion}
                onClick={installRequirements}
              >
                {waitingForCompletion ? <LoaderCircle className="spin" size={14} aria-hidden="true" /> : <Download size={14} aria-hidden="true" />}
                {waitingForCompletion ? '正在安装' : '安装所需组件'}
              </button>
            )}
            <button
              className="button button-primary dependency-recheck-button"
              type="button"
              disabled={waitingForCompletion}
              onClick={createTask}
            >
              <PackageCheck size={14} aria-hidden="true" />
              重新检查并开始
            </button>
          </div>
        </footer>
      </section>
    </div>
  )
}

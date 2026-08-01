import {
  AlertTriangle,
  CheckCircle2,
  FileDown,
  FileWarning,
  RotateCcw,
  RouteOff,
} from 'lucide-react'
import type { ProcessingTask, StageOutputArtifact } from '../domain'

interface RunDiagnosticsPanelProps {
  task: ProcessingTask
  compact?: boolean
  onRetry: () => void
  onCreateNew: () => void
  onDownloadArtifact: (artifact: StageOutputArtifact) => void
}

const unique = (values: readonly string[]): string[] =>
  [...new Set(values.filter(Boolean))]

const artifactKey = (artifact: StageOutputArtifact): string =>
  `${artifact.relativePath}:${artifact.sha256}`

const formatBytes = (value: number): string => {
  if (value < 1024) return `${value} B`
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KiB`
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MiB`
  return `${(value / 1024 ** 3).toFixed(1)} GiB`
}

export function RunDiagnosticsPanel({
  task,
  compact = false,
  onRetry,
  onCreateNew,
  onDownloadArtifact,
}: RunDiagnosticsPanelProps) {
  const terminal = task.status === 'failed' || task.status === 'cancelled'
  const failedStages =
    task.failure?.failedStages ??
    task.stages
      .filter(stage => stage.status === 'failed')
      .flatMap(stage =>
        (stage.backendStages?.length ? stage.backendStages : [stage.label]).map(
          (backendStage, index) => ({
            stage: backendStage,
            errorType: stage.errorTypes?.[index] ?? stage.errorTypes?.[0],
          }),
        ),
      )
  const completedStages = unique(
    task.failure?.completedStages ??
      task.stages
        .filter(stage => stage.status === 'completed')
        .flatMap(stage =>
          stage.backendStages?.length ? stage.backendStages : [stage.label],
        ),
  )
  const warnings = unique([
    ...task.runtimeWarnings,
    ...(task.warnings ?? []),
    ...task.stages.flatMap(stage => stage.warnings),
  ])
  const artifacts = [
    ...new Map(
      task.stages
        .flatMap(stage => stage.outputArtifacts)
        .map(artifact => [artifactKey(artifact), artifact]),
    ).values(),
  ]
  const recovery =
    task.recovery ??
    (task.realBackend
      ? {
          canRetry: false,
          strategy: 'manual_recreate' as const,
          reason:
            '当前会话没有可安全重放的认证与采样请求，请回到新建任务重新选择来源。',
        }
      : {
          canRetry: true,
          strategy: 'demo_restart' as const,
          reason: '演示任务可从头重置，但不会写入真实阶段产物。',
        })

  if (!terminal && (!task.realBackend || warnings.length === 0)) return null

  const heading =
    task.status === 'failed'
      ? `处理在 ${failedStages[0]?.stage ?? '未知阶段'} 停止`
      : task.status === 'cancelled'
        ? '任务已取消，已完成工作仍保留'
        : task.status === 'completed'
          ? '任务已完成，但存在运行提示'
          : '任务正在使用降级或兼容配置'
  const errorType =
    task.failure?.errorType ?? failedStages.find(stage => stage.errorType)?.errorType

  return (
    <section
      className={`run-diagnostics ${terminal ? 'is-terminal' : 'is-degraded'} ${
        compact ? 'is-compact' : ''
      }`}
      aria-labelledby={`run-diagnostics-${task.id}`}
    >
      <header className="diagnostics-heading">
        <span className="diagnostics-icon" aria-hidden="true">
          {terminal ? <FileWarning size={18} /> : <AlertTriangle size={18} />}
        </span>
        <div>
          <span className="section-kicker">
            {terminal ? 'RUN RECOVERY' : 'RUNTIME NOTICE'}
          </span>
          <h3 id={`run-diagnostics-${task.id}`}>{heading}</h3>
          <p>
            {task.failure?.message ??
              (terminal
                ? '以下信息直接来自任务与阶段 manifest；已经写入的产物不会被删除。'
                : '以下提示直接来自本机后端与阶段 manifest，任务仍会保留完整追踪信息。')}
          </p>
        </div>
        {errorType && (
          <span className="diagnostics-error-type" title="后端返回的安全异常类型">
            {errorType}
          </span>
        )}
      </header>

      {terminal && (
        <div className="diagnostics-facts">
          <div>
            <span>失败阶段</span>
            <strong>{failedStages.length || '—'}</strong>
            <p>
              {failedStages.length > 0
                ? unique(failedStages.map(stage => stage.stage)).join(' · ')
                : task.status === 'cancelled'
                  ? '用户取消或安全中止'
                  : '后端未返回阶段名'}
            </p>
          </div>
          <div>
            <span>已完成阶段</span>
            <strong>{completedStages.length}</strong>
            <p>
              {completedStages.length > 0
                ? completedStages.join(' · ')
                : '尚无阶段确认完成'}
            </p>
          </div>
          <div>
            <span>可用产物</span>
            <strong>{task.realBackend ? artifacts.length : 0}</strong>
            <p>
              {task.realBackend && artifacts.length > 0
                ? '均来自后端 manifest，可单独导出'
                : task.realBackend
                  ? '尚无已登记产物'
                  : '演示模式不写入真实文件'}
            </p>
          </div>
        </div>
      )}

      {warnings.length > 0 && (
        <div className="diagnostics-warnings" aria-label="运行提示">
          {warnings.slice(0, compact ? 2 : 4).map(warning => (
            <p key={warning}>
              <AlertTriangle size={13} aria-hidden="true" />
              <span>{warning}</span>
            </p>
          ))}
          {warnings.length > (compact ? 2 : 4) && (
            <small>另有 {warnings.length - (compact ? 2 : 4)} 条提示保留在任务记录中</small>
          )}
        </div>
      )}

      {terminal && task.realBackend && artifacts.length > 0 && !compact && (
        <div className="diagnostics-artifacts" aria-label="失败任务保留的产物">
          {artifacts.slice(0, 6).map(artifact => (
            <button
              type="button"
              key={artifactKey(artifact)}
              onClick={() => onDownloadArtifact(artifact)}
              aria-label={`下载产物 ${artifact.relativePath}`}
            >
              <FileDown size={15} aria-hidden="true" />
              <span>
                <strong>{artifact.relativePath.split('/').at(-1)}</strong>
                <small>
                  {artifact.stage} · {artifact.kind} · {formatBytes(artifact.sizeBytes)}
                </small>
              </span>
            </button>
          ))}
        </div>
      )}

      {terminal && (
        <footer className="diagnostics-recovery">
          <span className="recovery-route-icon" aria-hidden="true">
            {recovery.canRetry ? <RotateCcw size={17} /> : <RouteOff size={17} />}
          </span>
          <div>
            <strong>
              {recovery.canRetry
                ? recovery.strategy === 'demo_restart'
                  ? '可重新开始演示任务'
                  : '可按原配置新建重试任务'
                : '无法在当前会话一键重试'}
            </strong>
            <p>{recovery.reason}</p>
            {recovery.strategy !== 'demo_restart' && (
              <small>从失败阶段原地续跑：当前本地 API 未提供该能力</small>
            )}
          </div>
          {recovery.canRetry ? (
            <button className="button button-primary" type="button" onClick={onRetry}>
              <RotateCcw size={15} aria-hidden="true" />
              {recovery.strategy === 'demo_restart'
                ? '重新开始演示'
                : '按原配置新建重试'}
            </button>
          ) : (
            <button className="button button-secondary" type="button" onClick={onCreateNew}>
              <CheckCircle2 size={15} aria-hidden="true" />
              返回新建任务
            </button>
          )}
        </footer>
      )}
    </section>
  )
}

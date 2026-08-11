import { useMemo, useState } from 'react'
import {
  Archive,
  Check,
  CircleStop,
  Clock3,
  Download,
  ExternalLink,
  LoaderCircle,
  Pause,
  Play,
  RefreshCcw,
} from 'lucide-react'
import { EvidenceRail } from '../components/EvidenceRail'
import { MotionPresence } from '../components/MotionPresence'
import { ProcessingFlowPanel } from '../components/ProcessingFlowPanel'
import { RunDiagnosticsPanel } from '../components/RunDiagnosticsPanel'
import { SynchronizedVideo } from '../components/SynchronizedVideo'
import { VisualAsset } from '../components/VisualAsset'
import { usePendingActions } from '../components/usePendingActions'
import type { StageStatus, TaskStatus } from '../domain'
import { formatTime } from '../domain'
import { useI18n } from '../i18n'
import { useStudioStore } from '../store'

const stageStatusCopy: Record<StageStatus, readonly [string, string]> = {
  pending: ['等待', 'Waiting'],
  running: ['正在运行', 'Running'],
  completed: ['已完成', 'Completed'],
  failed: ['失败', 'Failed'],
  cancelled: ['已取消', 'Cancelled'],
}

const taskStatusCopy: Record<TaskStatus, readonly [string, string]> = {
  running: ['处理中', 'Processing'],
  paused: ['已暂停', 'Paused'],
  cancelled: ['已取消', 'Cancelled'],
  completed: ['已完成', 'Completed'],
  failed: ['失败', 'Failed'],
}

const stageCopy: Record<string, { label: readonly [string, string]; detail: readonly [string, string] }> = {
  acquire: {
    label: ['获取', 'Acquire'],
    detail: ['校验来源、字幕与最佳音视频格式', 'Validate the source, subtitles, and best audio/video formats'],
  },
  normalize: {
    label: ['规范化', 'Normalize'],
    detail: ['保存 stream time base 与真实 PTS', 'Preserve stream time bases and real presentation timestamps'],
  },
  speech: {
    label: ['语音', 'Speech'],
    detail: ['VAD、语言识别与词级时间戳', 'Voice activity detection, language identification, and word timestamps'],
  },
  vision: {
    label: ['视觉', 'Vision'],
    detail: ['变化粗扫、精扫、关键帧与 OCR', 'Coarse and fine change scans, keyframes, and OCR'],
  },
  fusion: {
    label: ['融合', 'Fusion'],
    detail: ['按时间交叠生成 EvidenceSpan', 'Build EvidenceSpan records from temporal overlap'],
  },
  draft: {
    label: ['写作', 'Compose'],
    detail: ['证据块、事实卡与章节草稿', 'Evidence blocks, fact cards, and chapter drafts'],
  },
  verify: {
    label: ['验证', 'Verify'],
    detail: ['检查 claim 支持度与内容覆盖', 'Check claim support and content coverage'],
  },
  render: {
    label: ['导出', 'Render'],
    detail: ['渲染 Markdown、HTML 与 PDF', 'Render Markdown, HTML, and PDF'],
  },
}

interface RunStageSelection {
  taskId: string
  selectedStageId?: string
  logStageFilter: string
}

export function RunPage() {
  const { text } = useI18n()
  const tasks = useStudioStore(state => state.tasks)
  const activeTaskId = useStudioStore(state => state.activeTaskId)
  const currentTimeSeconds = useStudioStore(state => state.currentTimeSeconds)
  const selectedEvidenceId = useStudioStore(state => state.selectedEvidenceId)
  const setCurrentTime = useStudioStore(state => state.setCurrentTime)
  const selectEvidence = useStudioStore(state => state.selectEvidence)
  const pauseTask = useStudioStore(state => state.pauseTask)
  const resumeTask = useStudioStore(state => state.resumeTask)
  const cancelTask = useStudioStore(state => state.cancelTask)
  const restartTask = useStudioStore(state => state.restartTask)
  const downloadRunArtifact = useStudioStore(state => state.downloadRunArtifact)
  const selectTask = useStudioStore(state => state.selectTask)
  const refreshTasks = useStudioStore(state => state.refreshTasks)
  const navigate = useStudioStore(state => state.navigate)
  const [stageSelection, setStageSelection] = useState<RunStageSelection>()
  const [showArtifacts, setShowArtifacts] = useState(false)
  const { isPending, track } = usePendingActions()
  const taskActionPending = isPending('run:pause') || isPending('run:resume')
  const cancelPending = isPending('run:cancel')
  const restartPending = isPending('run:restart')
  const runControlBusy = taskActionPending || cancelPending || restartPending
  const stageStatusLabel = (status: StageStatus) => text(...stageStatusCopy[status])
  const taskStatusLabel = (status: TaskStatus) => text(...taskStatusCopy[status])
  const stageLabel = (stage: { id: string; label: string }) => {
    const copy = stageCopy[stage.id]?.label
    return copy ? text(...copy) : stage.label
  }
  const stageDetail = (stage?: { id: string; detail: string }) => {
    if (!stage) return ''
    const copy = stageCopy[stage.id]?.detail
    return copy ? text(...copy) : stage.detail
  }

  const task = tasks.find(item => item.id === activeTaskId) ?? tasks[0]
  const currentStageSelection =
    stageSelection?.taskId === task?.id ? stageSelection : undefined
  const liveStage = task?.stages.find(stage => stage.status === 'running')
  const terminalStage = task?.stages.find(
    stage => stage.status === 'failed' || stage.status === 'cancelled',
  )
  const lastCompletedStage = [...(task?.stages ?? [])]
    .reverse()
    .find(stage => stage.status === 'completed')
  const selectedStage =
    task?.stages.find(stage => stage.id === currentStageSelection?.selectedStageId) ??
    liveStage ??
    terminalStage ??
    lastCompletedStage ??
    task?.stages[0]
  const visibleEvidence = useMemo(() => {
    if (!task) return []
    const processedUntil = task.source.durationSeconds * Math.max(0.08, task.progress / 100)
    return task.evidence.filter(item => item.startSeconds <= processedUntil)
  }, [task])

  if (!task) {
    return (
      <div className="empty-workspace">
        <VisualAsset
          className="empty-workspace-visual"
          asset="emptyTasks"
          width={640}
          height={640}
        />
        <h2>{text('还没有任务', 'No tasks yet')}</h2>
        <p>{text('从新建任务页导入一个视频后，处理进度会显示在这里。', 'Import a video from New task and its processing progress will appear here.')}</p>
      </div>
    )
  }

  const taskAction = () => {
    if (runControlBusy) return
    if (task.status === 'running' && task.realBackend) refreshTasks()
    else if (task.status === 'running') track('run:pause', () => pauseTask(task.id))
    else if (task.status === 'paused') track('run:resume', () => resumeTask(task.id))
    else if (task.status === 'completed') selectTask(task.id, 'reader')
  }

  const selectedArtifacts = task.realBackend
    ? (selectedStage?.outputArtifacts ?? [])
    : []

  const selectStage = (stageId: string) => {
    setStageSelection({
      taskId: task.id,
      selectedStageId: stageId,
      logStageFilter: stageId,
    })
  }

  const setLogStageFilter = (value: string) => {
    const matchingStage = task.stages.find(
      stage =>
        stage.id === value ||
        stage.backendStages?.includes(value),
    )
    setStageSelection(current => ({
      taskId: task.id,
      selectedStageId:
        value === 'all'
          ? current?.taskId === task.id
            ? current.selectedStageId
            : undefined
          : matchingStage?.id ??
            (current?.taskId === task.id ? current.selectedStageId : undefined),
      logStageFilter: value,
    }))
  }

  return (
    <div className="run-page">
      <section className="run-summary">
        <div className="run-summary-title">
          <div className={`status-indicator status-${task.status}`} aria-hidden="true" />
          <div>
            <div className="run-summary-kicker">
              <span className="section-kicker">
                {task.mode.toUpperCase()} / {taskStatusLabel(task.status)}
              </span>
              <span className={`run-scope-badge scope-${task.processingScope}`}>
                {task.processingScope === 'audio_only' ? text('仅音频', 'Audio only') : text('完整音画', 'Audio + video')}
              </span>
            </div>
            <h2>{task.source.title}</h2>
            <p>
              {task.source.quality} · {task.source.audio} · {task.source.authLabel}
            </p>
          </div>
        </div>
        <div className="run-summary-progress">
          <span>
            <strong>{Math.round(task.progress)}%</strong>
            {task.status === 'running' && <> · {text('预计', 'ETA')} {formatTime(task.etaSeconds)}</>}
          </span>
          <div
            className="progress-track"
            role="progressbar"
            aria-label={text(`总体进度 ${Math.round(task.progress)}%`, `Overall progress ${Math.round(task.progress)}%`)}
            aria-valuenow={Math.round(task.progress)}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <span style={{ transform: `scaleX(${Math.min(1, Math.max(0, task.progress / 100))})` }} />
          </div>
        </div>
        <div className="run-actions">
          {!['cancelled', 'failed'].includes(task.status) && (
            <button
              className="button button-primary"
              type="button"
              onClick={taskAction}
              disabled={runControlBusy}
              aria-busy={taskActionPending || undefined}
            >
              {taskActionPending ? (
                <LoaderCircle className="spin" size={16} aria-hidden="true" />
              ) : (
                <>
                  {task.status === 'running' &&
                    (task.realBackend ? (
                      <RefreshCcw size={16} aria-hidden="true" />
                    ) : (
                      <Pause size={16} aria-hidden="true" />
                    ))}
                  {task.status === 'paused' && <Play size={16} aria-hidden="true" />}
                  {task.status === 'completed' && <ExternalLink size={16} aria-hidden="true" />}
                </>
              )}
              {taskActionPending
                ? text('正在执行…', 'Working…')
                : task.status === 'running'
                ? task.realBackend
                  ? text('刷新状态', 'Refresh status')
                  : text('暂停', 'Pause')
                : task.status === 'paused'
                  ? text('继续', 'Resume')
                  : text('阅读笔记', 'Read note')}
            </button>
          )}
          {['running', 'paused'].includes(task.status) && (
            <button
              className="button button-quiet"
              type="button"
              onClick={() => track('run:cancel', () => cancelTask(task.id))}
              disabled={runControlBusy}
              aria-busy={cancelPending || undefined}
            >
              {cancelPending ? (
                <LoaderCircle className="spin" size={16} aria-hidden="true" />
              ) : (
                <CircleStop size={16} aria-hidden="true" />
              )}
              {cancelPending ? text('正在取消…', 'Cancelling…') : text('取消', 'Cancel')}
            </button>
          )}
        </div>
      </section>

      <RunDiagnosticsPanel
        task={task}
        onRetry={() => track('run:restart', () => restartTask(task.id))}
        onCreateNew={() => navigate('create')}
        onDownloadArtifact={artifact => downloadRunArtifact(task.id, artifact)}
        retryPending={restartPending}
      />

      <ProcessingFlowPanel
        task={task}
        className="run-processing-flow"
        downloadFileName={`video2notes-${task.id}-processing-log.jsonl`}
        stageFilter={currentStageSelection?.logStageFilter ?? 'all'}
        onStageFilterChange={setLogStageFilter}
      />

      <section className="stage-rail" aria-label={text('处理阶段', 'Processing stages')}>
        {task.stages.map((stage, index) => (
          <button
            className={`stage-node is-${stage.status} ${
              selectedStage?.id === stage.id ? 'is-selected' : ''
            }`}
            type="button"
            key={stage.id}
            onClick={() => selectStage(stage.id)}
            aria-label={text(
              `${stageLabel(stage)}，${stageStatusLabel(stage.status)}，${stage.progress}%`,
              `${stageLabel(stage)}, ${stageStatusLabel(stage.status)}, ${stage.progress}%`,
            )}
          >
            <span className="stage-number">
              {stage.status === 'completed' ? (
                <Check size={13} aria-hidden="true" />
              ) : (
                (index + 1).toString().padStart(2, '0')
              )}
            </span>
            <span>
              <strong>{stageLabel(stage)}</strong>
              <small>
                {stage.status === 'completed'
                  ? `${(stage.durationSeconds ?? 0).toFixed(1)}s`
                  : stage.status === 'running'
                    ? `${stage.progress}%`
                    : stage.status === 'failed'
                      ? text('失败', 'Failed')
                      : stage.status === 'cancelled'
                        ? text('已取消', 'Cancelled')
                      : text('等待', 'Waiting')}
              </small>
            </span>
          </button>
        ))}
      </section>

      <div className="run-grid">
        <section className="stage-inspector">
          <div className="panel-heading">
            <div>
              <span className="section-kicker">{text('当前阶段', 'CURRENT STAGE')}</span>
              <h3>{selectedStage ? stageLabel(selectedStage) : text('等待开始', 'Waiting to start')}</h3>
            </div>
            <span className={`stage-state state-${selectedStage?.status}`}>
              {stageStatusLabel(selectedStage?.status ?? 'pending')}
            </span>
          </div>
          <p className="stage-description">{stageDetail(selectedStage)}</p>
          <dl className="stage-metrics">
            <div>
              <dt>{text('执行器', 'Executor')}</dt>
              <dd>
                {task.realBackend
                  ? text('按角色绑定的本地/兼容模型', 'Role-bound local or compatible model')
                  : selectedStage?.id === 'speech'
                    ? 'faster-whisper-large-v3'
                    : selectedStage?.id === 'vision'
                      ? 'adaptive-visual-v1 + PaddleOCR'
                      : 'demo-worker-v1'}
              </dd>
            </div>
            <div>
              <dt>{text('加速', 'Acceleration')}</dt>
              <dd>{task.realBackend ? text('由硬件计划与模型配置决定', 'Determined by the hardware plan and model configuration') : 'DEMO · FP16 / INT8'}</dd>
            </div>
            <div>
              <dt>{text('吞吐', 'Throughput')}</dt>
              <dd>{selectedStage?.metric ?? text('按输入动态计算', 'Calculated from the input')}</dd>
            </div>
            <div>
              <dt>{text('产物', 'Artifact')}</dt>
              <dd>{text(`${selectedStage?.artifactCount ?? 0} 个已写入`, `${selectedStage?.artifactCount ?? 0} written`)}</dd>
            </div>
          </dl>
          <button
            className="artifact-toggle"
            type="button"
            onClick={() => setShowArtifacts(value => !value)}
            aria-expanded={showArtifacts}
            aria-controls="run-artifact-list"
          >
            <Archive size={16} aria-hidden="true" />
            {showArtifacts ? text('隐藏阶段 artifact', 'Hide stage artifacts') : text('查看阶段 artifact', 'View stage artifacts')}
          </button>
          <MotionPresence
            show={showArtifacts}
            className="motion-presence-collapse"
            exitMs={120}
          >
            <div className="artifact-list" id="run-artifact-list">
              {selectedArtifacts.map(artifact => (
                <button
                  type="button"
                  key={`${artifact.relativePath}:${artifact.sha256}`}
                  onClick={() => downloadRunArtifact(task.id, artifact)}
                  aria-label={text(`下载产物 ${artifact.relativePath}`, `Download artifact ${artifact.relativePath}`)}
                >
                  <Download size={15} aria-hidden="true" />
                  <span>
                    <strong>{artifact.relativePath.split('/').at(-1)}</strong>
                    <small>
                      {artifact.stage} · {artifact.kind} · {(artifact.sizeBytes / 1024).toFixed(1)} KiB
                    </small>
                  </span>
                  <ExternalLink size={14} aria-hidden="true" />
                </button>
              ))}
              {selectedArtifacts.length === 0 && (
                <p className="artifact-empty">
                  {task.realBackend
                    ? text('该阶段尚未在后端 manifest 中登记产物。', 'No artifact is registered for this stage in the backend manifest yet.')
                    : text('演示模式不会写入真实阶段产物。', 'Demo mode does not write real stage artifacts.')}
                </p>
              )}
            </div>
          </MotionPresence>
          <div className="stage-footnote">
            <Clock3 size={14} aria-hidden="true" />
            {text(
              '列表只显示后端 manifest 已登记的真实文件；阶段内尚未提交的中间状态不承诺可恢复。',
              'This list only shows real files registered in the backend manifest. Uncommitted intermediate stage state is not guaranteed to be recoverable.',
            )}
          </div>
        </section>

        <section className="live-sample">
          <div className="panel-heading">
            <div>
              <span className="section-kicker">{text('实时样本', 'LIVE SAMPLE')}</span>
              <h3>{text('当前处理样本', 'Current processing sample')}</h3>
            </div>
            <span className="live-pulse">
              <span aria-hidden="true" />
              {task.status === 'running' ? 'LIVE' : taskStatusLabel(task.status).toUpperCase()}
            </span>
          </div>
          <SynchronizedVideo
            title={task.source.title}
            durationSeconds={task.source.durationSeconds}
            currentTimeSeconds={currentTimeSeconds}
            onSeek={setCurrentTime}
            src={task.mediaUrl}
            ocrConfidence={task.evidence.find(item => item.kind === 'ocr')?.confidence}
          />
          <div className="sample-facts">
            {(task.realBackend ? visibleEvidence.slice(-2) : visibleEvidence.slice(0, 2)).map(
              item => (
                <div key={item.id}>
                  <span>{item.kind.toUpperCase()}</span>
                  <p>{item.rawText}</p>
                  <strong>{item.confidence.toFixed(2)}</strong>
                </div>
              ),
            )}
            {task.realBackend && visibleEvidence.length === 0 && (
              <div>
                <span>PTS</span>
                <p>{task.lastMessage || text('证据将在对应阶段完成后显示。', 'Evidence appears after the corresponding stage completes.')}</p>
                <strong>{text('实时', 'LIVE')}</strong>
              </div>
            )}
          </div>
        </section>
      </div>

      <section className="run-evidence">
        <div className="panel-heading">
          <div>
            <span className="section-kicker">{text('证据累积', 'EVIDENCE BUILD-UP')}</span>
            <h3>{text('证据轨正在形成', 'Evidence timeline in progress')}</h3>
          </div>
          <div className="rail-legend">
            <span>{text('A 语音', 'A Speech')}</span>
            <span>{text('T 文字', 'T Text')}</span>
            <span>{text('F 画面', 'F Frame')}</span>
            <span>{text('C 章节', 'C Chapter')}</span>
          </div>
        </div>
        <EvidenceRail
          evidence={visibleEvidence}
          durationSeconds={task.source.durationSeconds}
          currentTimeSeconds={currentTimeSeconds}
          selectedEvidenceId={selectedEvidenceId}
          onSeek={setCurrentTime}
          onSelect={selectEvidence}
          compact
        />
      </section>
    </div>
  )
}

import { useMemo, useState } from 'react'
import {
  Archive,
  Check,
  CircleStop,
  Clock3,
  Download,
  ExternalLink,
  Pause,
  Play,
  RefreshCcw,
  TriangleAlert,
} from 'lucide-react'
import { EvidenceRail } from '../components/EvidenceRail'
import { MotionPresence } from '../components/MotionPresence'
import { ProcessingFlowPanel } from '../components/ProcessingFlowPanel'
import { RunDiagnosticsPanel } from '../components/RunDiagnosticsPanel'
import { SynchronizedVideo } from '../components/SynchronizedVideo'
import { formatTime, statusLabel } from '../domain'
import { useStudioStore } from '../store'

const stageStatusLabel = {
  pending: '等待',
  running: '正在运行',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
} as const

interface RunStageSelection {
  taskId: string
  selectedStageId?: string
  logStageFilter: string
}

export function RunPage() {
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
        <TriangleAlert size={24} />
        <h2>还没有任务</h2>
        <p>从新建任务页导入一个视频后，处理进度会显示在这里。</p>
      </div>
    )
  }

  const taskAction = () => {
    if (task.status === 'running' && task.realBackend) refreshTasks()
    else if (task.status === 'running') pauseTask(task.id)
    else if (task.status === 'paused') resumeTask(task.id)
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
                {task.mode.toUpperCase()} / {statusLabel[task.status]}
              </span>
              <span className={`run-scope-badge scope-${task.processingScope}`}>
                {task.processingScope === 'audio_only' ? '仅音频' : '完整音画'}
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
            {task.status === 'running' && <> · 预计 {formatTime(task.etaSeconds)}</>}
          </span>
          <div className="progress-track" aria-label={`总体进度 ${Math.round(task.progress)}%`}>
            <span style={{ transform: `scaleX(${Math.min(1, Math.max(0, task.progress / 100))})` }} />
          </div>
        </div>
        <div className="run-actions">
          {!['cancelled', 'failed'].includes(task.status) && (
            <button className="button button-primary" type="button" onClick={taskAction}>
              {task.status === 'running' &&
                (task.realBackend ? (
                  <RefreshCcw size={16} aria-hidden="true" />
                ) : (
                  <Pause size={16} aria-hidden="true" />
                ))}
              {task.status === 'paused' && <Play size={16} aria-hidden="true" />}
              {task.status === 'completed' && <ExternalLink size={16} aria-hidden="true" />}
              {task.status === 'running'
                ? task.realBackend
                  ? '刷新状态'
                  : '暂停'
                : task.status === 'paused'
                  ? '继续'
                  : '阅读笔记'}
            </button>
          )}
          {['running', 'paused'].includes(task.status) && (
            <button
              className="button button-quiet"
              type="button"
              onClick={() => cancelTask(task.id)}
            >
              <CircleStop size={16} aria-hidden="true" />
              取消
            </button>
          )}
        </div>
      </section>

      <RunDiagnosticsPanel
        task={task}
        onRetry={() => restartTask(task.id)}
        onCreateNew={() => navigate('create')}
        onDownloadArtifact={artifact => downloadRunArtifact(task.id, artifact)}
      />

      <ProcessingFlowPanel
        task={task}
        className="run-processing-flow"
        downloadFileName={`video2notes-${task.id}-processing-log.jsonl`}
        stageFilter={currentStageSelection?.logStageFilter ?? 'all'}
        onStageFilterChange={setLogStageFilter}
      />

      <section className="stage-rail" aria-label="处理阶段">
        {task.stages.map((stage, index) => (
          <button
            className={`stage-node is-${stage.status} ${
              selectedStage?.id === stage.id ? 'is-selected' : ''
            }`}
            type="button"
            key={stage.id}
            onClick={() => selectStage(stage.id)}
            aria-label={`${stage.label}，${stageStatusLabel[stage.status]}，${stage.progress}%`}
          >
            <span className="stage-number">
              {stage.status === 'completed' ? (
                <Check size={13} aria-hidden="true" />
              ) : (
                (index + 1).toString().padStart(2, '0')
              )}
            </span>
            <span>
              <strong>{stage.label}</strong>
              <small>
                {stage.status === 'completed'
                  ? `${(stage.durationSeconds ?? 0).toFixed(1)}s`
                  : stage.status === 'running'
                    ? `${stage.progress}%`
                    : stage.status === 'failed'
                      ? '失败'
                      : stage.status === 'cancelled'
                        ? '已取消'
                      : '等待'}
              </small>
            </span>
          </button>
        ))}
      </section>

      <div className="run-grid">
        <section className="stage-inspector">
          <div className="panel-heading">
            <div>
              <span className="section-kicker">CURRENT STAGE</span>
              <h3>{selectedStage?.label ?? '等待开始'}</h3>
            </div>
            <span className={`stage-state state-${selectedStage?.status}`}>
              {stageStatusLabel[selectedStage?.status ?? 'pending']}
            </span>
          </div>
          <p className="stage-description">{selectedStage?.detail}</p>
          <dl className="stage-metrics">
            <div>
              <dt>执行器</dt>
              <dd>
                {task.realBackend
                  ? '按角色绑定的本地/兼容模型'
                  : selectedStage?.id === 'speech'
                    ? 'faster-whisper-large-v3'
                    : selectedStage?.id === 'vision'
                      ? 'adaptive-visual-v1 + PaddleOCR'
                      : 'demo-worker-v1'}
              </dd>
            </div>
            <div>
              <dt>加速</dt>
              <dd>{task.realBackend ? '由硬件计划与模型配置决定' : 'DEMO · FP16 / INT8'}</dd>
            </div>
            <div>
              <dt>吞吐</dt>
              <dd>{selectedStage?.metric ?? '按输入动态计算'}</dd>
            </div>
            <div>
              <dt>Artifact</dt>
              <dd>{selectedStage?.artifactCount ?? 0} 个已写入</dd>
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
            {showArtifacts ? '隐藏阶段 artifact' : '查看阶段 artifact'}
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
                  aria-label={`下载产物 ${artifact.relativePath}`}
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
                    ? '该阶段尚未在后端 manifest 中登记产物。'
                    : '演示模式不会写入真实阶段产物。'}
                </p>
              )}
            </div>
          </MotionPresence>
          <div className="stage-footnote">
            <Clock3 size={14} aria-hidden="true" />
            列表只显示后端 manifest 已登记的真实文件；阶段内尚未提交的中间状态不承诺可恢复。
          </div>
        </section>

        <section className="live-sample">
          <div className="panel-heading">
            <div>
              <span className="section-kicker">LIVE SAMPLE</span>
              <h3>当前处理样本</h3>
            </div>
            <span className="live-pulse">
              <span aria-hidden="true" />
              {task.status === 'running' ? 'LIVE' : statusLabel[task.status].toUpperCase()}
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
                <p>{task.lastMessage || '证据将在对应阶段完成后显示。'}</p>
                <strong>LIVE</strong>
              </div>
            )}
          </div>
        </section>
      </div>

      <section className="run-evidence">
        <div className="panel-heading">
          <div>
            <span className="section-kicker">EVIDENCE BUILD-UP</span>
            <h3>证据轨正在形成</h3>
          </div>
          <div className="rail-legend">
            <span>A 语音</span>
            <span>T 文字</span>
            <span>F 画面</span>
            <span>C 章节</span>
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

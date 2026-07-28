import { useMemo, useState } from 'react'
import {
  Archive,
  Check,
  CircleStop,
  Clock3,
  ExternalLink,
  FileStack,
  Pause,
  Play,
  RefreshCcw,
  RotateCcw,
  TriangleAlert,
} from 'lucide-react'
import { EvidenceRail } from '../components/EvidenceRail'
import { SynchronizedVideo } from '../components/SynchronizedVideo'
import { formatTime, statusLabel } from '../domain'
import { useStudioStore } from '../store'

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
  const selectTask = useStudioStore(state => state.selectTask)
  const refreshTasks = useStudioStore(state => state.refreshTasks)
  const [selectedStageId, setSelectedStageId] = useState<string>()
  const [showArtifacts, setShowArtifacts] = useState(false)

  const task = tasks.find(item => item.id === activeTaskId) ?? tasks[0]
  const liveStage = task?.stages.find(stage => stage.status === 'running')
  const selectedStage =
    task?.stages.find(stage => stage.id === selectedStageId) ?? liveStage ?? task?.stages.at(-1)
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
    else restartTask(task.id)
  }

  return (
    <div className="run-page">
      <section className="run-summary">
        <div className="run-summary-title">
          <div className={`status-indicator status-${task.status}`} aria-hidden="true" />
          <div>
            <span className="section-kicker">
              {task.mode.toUpperCase()} / {statusLabel[task.status]}
            </span>
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
            <span style={{ width: `${task.progress}%` }} />
          </div>
        </div>
        <div className="run-actions">
          <button className="button button-primary" type="button" onClick={taskAction}>
            {task.status === 'running' &&
              (task.realBackend ? (
                <RefreshCcw size={16} aria-hidden="true" />
              ) : (
                <Pause size={16} aria-hidden="true" />
              ))}
            {task.status === 'paused' && <Play size={16} aria-hidden="true" />}
            {task.status === 'completed' && <ExternalLink size={16} aria-hidden="true" />}
            {['cancelled', 'failed'].includes(task.status) && (
              <RotateCcw size={16} aria-hidden="true" />
            )}
            {task.status === 'running'
              ? task.realBackend
                ? '刷新状态'
                : '暂停'
              : task.status === 'paused'
                ? '继续'
                : task.status === 'completed'
                  ? '阅读笔记'
                  : '重新开始'}
          </button>
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

      <section className="stage-rail" aria-label="处理阶段">
        {task.stages.map((stage, index) => (
          <button
            className={`stage-node is-${stage.status} ${
              selectedStage?.id === stage.id ? 'is-selected' : ''
            }`}
            type="button"
            key={stage.id}
            onClick={() => setSelectedStageId(stage.id)}
            aria-label={`${stage.label}，${stage.status}，${stage.progress}%`}
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
              {selectedStage?.status === 'running' ? '正在运行' : selectedStage?.status}
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
          >
            <Archive size={16} aria-hidden="true" />
            {showArtifacts ? '隐藏阶段 artifact' : '查看阶段 artifact'}
          </button>
          {showArtifacts && (
            <div className="artifact-list">
              <div>
                <FileStack size={15} aria-hidden="true" />
                <span>
                  <strong>stage-manifest.json</strong>
                  <small>输入输出哈希、版本、耗时与恢复点</small>
                </span>
                <Check size={14} aria-hidden="true" />
              </div>
              <div>
                <RefreshCcw size={15} aria-hidden="true" />
                <span>
                  <strong>checkpoint.latest</strong>
                  <small>可从最近成功点恢复</small>
                </span>
                <Check size={14} aria-hidden="true" />
              </div>
            </div>
          )}
          <div className="stage-footnote">
            <Clock3 size={14} aria-hidden="true" />
            每个阶段完成后立即持久化，暂停或退出应用不会丢失已完成工作。
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

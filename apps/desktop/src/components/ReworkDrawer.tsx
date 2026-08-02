import { useEffect, useMemo, useState } from 'react'
import {
  AudioLines,
  CheckCircle2,
  Eye,
  RefreshCcw,
  ScanText,
  SlidersHorizontal,
  TriangleAlert,
  X,
} from 'lucide-react'
import type { ProcessingTask, ReworkOperationKind } from '../domain'
import { formatTime } from '../domain'
import { useStudioStore } from '../store'

interface ReworkDrawerProps {
  task: ProcessingTask
  initialStartSeconds?: number
  initialEndSeconds?: number
  selectedEvidenceId?: string
  onClose: () => void
}

type ReworkTab = 'vision' | 'asr' | 'manual'

const operationLabel: Record<ReworkOperationKind, string> = {
  vision_rescan: '画面重新识别',
  asr_retranscribe: '语音重新转写',
  evidence_correct: '人工文字校正',
}

const clampInitialRange = (
  durationSeconds: number,
  startSeconds?: number,
  endSeconds?: number,
) => {
  const start = Math.max(0, Math.min(startSeconds ?? 0, durationSeconds))
  const proposedEnd = endSeconds ?? Math.min(durationSeconds, start + 60)
  const end = Math.max(start, Math.min(proposedEnd, durationSeconds))
  return { start, end }
}

export function ReworkDrawer({
  task,
  initialStartSeconds,
  initialEndSeconds,
  selectedEvidenceId,
  onClose,
}: ReworkDrawerProps) {
  const runVisionRework = useStudioStore(state => state.runVisionRework)
  const runAsrRework = useStudioStore(state => state.runAsrRework)
  const correctEvidence = useStudioStore(state => state.correctEvidence)
  const refreshOperations = useStudioStore(state => state.refreshOperations)
  const audioOnly = task.processingScope === 'audio_only'
  const initialRange = useMemo(
    () =>
      clampInitialRange(
        task.source.durationSeconds,
        initialStartSeconds,
        initialEndSeconds,
      ),
    [
      initialEndSeconds,
      initialStartSeconds,
      task.source.durationSeconds,
    ],
  )
  const initialEvidenceId =
    task.evidence.find(item => item.id === selectedEvidenceId)?.id ??
    task.evidence[0]?.id ??
    ''
  const [tab, setTab] = useState<ReworkTab>(audioOnly ? 'asr' : 'vision')
  const [startSeconds, setStartSeconds] = useState(initialRange.start)
  const [endSeconds, setEndSeconds] = useState(initialRange.end)
  const [visionMode, setVisionMode] = useState<'adaptive' | 'fixed_interval'>(
    'adaptive',
  )
  const [intervalSeconds, setIntervalSeconds] = useState(0.5)
  const [runOcr, setRunOcr] = useState(true)
  const [languageHints, setLanguageHints] = useState('zh-CN')
  const [evidenceId, setEvidenceId] = useState(initialEvidenceId)
  const [manualText, setManualText] = useState(
    task.evidence.find(item => item.id === initialEvidenceId)?.rawText ?? '',
  )
  const [reason, setReason] = useState('')
  const [pending, setPending] = useState<ReworkTab | null>(null)

  useEffect(() => {
    setStartSeconds(initialRange.start)
    setEndSeconds(initialRange.end)
  }, [initialRange])

  useEffect(() => {
    const item = task.evidence.find(evidence => evidence.id === evidenceId)
    setManualText(item?.rawText ?? '')
  }, [evidenceId, task.evidence])

  useEffect(() => {
    if (pending === null) return
    const timeout = window.setTimeout(() => setPending(null), 1_400)
    return () => window.clearTimeout(timeout)
  }, [pending, task.operations.length])

  const rangeValid =
    Number.isFinite(startSeconds) &&
    Number.isFinite(endSeconds) &&
    startSeconds >= 0 &&
    endSeconds > startSeconds &&
    endSeconds <= task.source.durationSeconds
  const fixedCount =
    rangeValid && intervalSeconds >= 0.1
      ? Math.floor(
          ((endSeconds - startSeconds) * 1_000_000 - 1) /
            Math.round(intervalSeconds * 1_000_000),
        ) + 1
      : 0
  const visionValid =
    !audioOnly &&
    rangeValid &&
    (visionMode === 'adaptive' ||
      (intervalSeconds >= 0.1 && fixedCount <= 5_000))
  const selectedEvidence = task.evidence.find(item => item.id === evidenceId)
  const manualValid = Boolean(selectedEvidence && manualText.trim())

  const submitVision = () => {
    if (audioOnly || !visionValid) return
    setPending('vision')
    runVisionRework(task.id, {
      startSeconds,
      endSeconds,
      mode: visionMode,
      intervalSeconds:
        visionMode === 'fixed_interval' ? intervalSeconds : undefined,
      runOcr,
    })
  }

  const submitAsr = () => {
    if (!rangeValid) return
    setPending('asr')
    runAsrRework(task.id, {
      range: { startSeconds, endSeconds },
      languageHints: languageHints
        .split(/[,，\s]+/)
        .map(item => item.trim())
        .filter(Boolean),
    })
  }

  const submitCorrection = () => {
    if (!manualValid || !selectedEvidence) return
    setPending('manual')
    correctEvidence(task.id, {
      evidenceId: selectedEvidence.id,
      newText: manualText,
      reason,
    })
  }

  return (
    <div className="workbench-overlay">
      <button
        type="button"
        className="workbench-scrim"
        aria-label="关闭局部返工面板"
        onClick={onClose}
      />
      <aside
        className="workbench-drawer rework-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="rework-title"
      >
        <header className="workbench-drawer-header">
          <div>
            <span className="section-kicker">RANGE REWORK</span>
            <h2 id="rework-title">局部返工</h2>
            <p>只重做选中时间段或单条证据，原始结果和 revision 历史会保留。</p>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭局部返工面板">
            <X size={18} aria-hidden="true" />
          </button>
        </header>

        <div className="workbench-drawer-body">
          <section className="rework-range-section">
            <div className="drawer-section-heading">
              <div>
                <span>SELECTED RANGE</span>
                <h3>处理范围</h3>
              </div>
              <SlidersHorizontal size={16} aria-hidden="true" />
            </div>
            <div className="rework-range-fields">
              <label>
                开始（秒）
                <input
                  type="number"
                  min={0}
                  max={task.source.durationSeconds}
                  step={0.1}
                  value={startSeconds}
                  onChange={event => setStartSeconds(Number(event.target.value))}
                />
              </label>
              <span aria-hidden="true">→</span>
              <label>
                结束（秒）
                <input
                  type="number"
                  min={0}
                  max={task.source.durationSeconds}
                  step={0.1}
                  value={endSeconds}
                  onChange={event => setEndSeconds(Number(event.target.value))}
                />
              </label>
            </div>
            <p className={rangeValid ? 'rework-range-status' : 'rework-range-status is-invalid'}>
              {rangeValid ? (
                <>
                  <CheckCircle2 size={14} aria-hidden="true" />
                  {formatTime(startSeconds)} — {formatTime(endSeconds)} ·{' '}
                  {(endSeconds - startSeconds).toFixed(1)} 秒
                </>
              ) : (
                <>
                  <TriangleAlert size={14} aria-hidden="true" />
                  范围必须位于视频时长内，且结束时间晚于开始时间。
                </>
              )}
            </p>
          </section>

          <section className="rework-control-section">
            <div className="rework-tabs" role="tablist" aria-label="返工类型">
              <button
                type="button"
                role="tab"
                aria-selected={tab === 'vision'}
                disabled={audioOnly}
                title={audioOnly ? '仅音频任务不能执行画面返工' : undefined}
                onClick={() => setTab('vision')}
              >
                <Eye size={15} aria-hidden="true" />
                画面
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={tab === 'asr'}
                onClick={() => setTab('asr')}
              >
                <AudioLines size={15} aria-hidden="true" />
                语音
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={tab === 'manual'}
                onClick={() => setTab('manual')}
              >
                <ScanText size={15} aria-hidden="true" />
                文字校正
              </button>
            </div>

            {audioOnly && (
              <p className="rework-explainer" role="status">
                此任务以仅音频范围创建，后端未生成视觉基线，因此不能执行画面重新识别或 OCR
                返工。语音返工与人工文字校正仍可使用。
              </p>
            )}

            {tab === 'vision' && (
              <div className="rework-tab-panel" role="tabpanel">
                <div className="rework-choice-grid">
                  <button
                    type="button"
                    className={visionMode === 'adaptive' ? 'is-selected' : ''}
                    onClick={() => setVisionMode('adaptive')}
                  >
                    <strong>智能关键帧</strong>
                    <span>依据画面与文字变化决定采样点，适合大多数片段。</span>
                  </button>
                  <button
                    type="button"
                    className={
                      visionMode === 'fixed_interval' ? 'is-selected' : ''
                    }
                    onClick={() => setVisionMode('fixed_interval')}
                  >
                    <strong>固定间隔</strong>
                    <span>用于变化密集或需要逐帧核查的指定片段。</span>
                  </button>
                </div>
                {visionMode === 'fixed_interval' && (
                  <div className="rework-interval">
                    <span>每隔</span>
                    {[0.1, 0.5, 1].map(value => (
                      <button
                        key={value}
                        type="button"
                        className={intervalSeconds === value ? 'is-selected' : ''}
                        onClick={() => setIntervalSeconds(value)}
                      >
                        {value} 秒
                      </button>
                    ))}
                    <input
                      aria-label="自定义固定采样间隔（秒）"
                      type="number"
                      min={0.1}
                      step={0.1}
                      value={intervalSeconds}
                      onChange={event =>
                        setIntervalSeconds(Number(event.target.value))
                      }
                    />
                    <small>
                      预计 {fixedCount.toLocaleString()} 帧 / 上限 5,000
                    </small>
                  </div>
                )}
                <label className="material-range-toggle">
                  <input
                    type="checkbox"
                    checked={runOcr}
                    onChange={event => setRunOcr(event.target.checked)}
                  />
                  同时重做该范围的屏幕文字 OCR
                </label>
                <button
                  type="button"
                  className="button button-primary"
                  disabled={audioOnly || !visionValid || pending !== null}
                  onClick={submitVision}
                >
                  <Eye size={15} aria-hidden="true" />
                  {pending === 'vision' ? '正在提交…' : '执行画面返工'}
                </button>
              </div>
            )}

            {tab === 'asr' && (
              <div className="rework-tab-panel" role="tabpanel">
                <label>
                  语言提示（逗号分隔，最多 8 个）
                  <input
                    value={languageHints}
                    onChange={event => setLanguageHints(event.target.value)}
                    placeholder="zh-CN, en, ja"
                  />
                </label>
                <p className="rework-explainer">
                  后端会精确截取此时间段的音轨再转写，只替换该范围内的 ASR
                  证据。
                </p>
                <button
                  type="button"
                  className="button button-primary"
                  disabled={!rangeValid || pending !== null}
                  onClick={submitAsr}
                >
                  <AudioLines size={15} aria-hidden="true" />
                  {pending === 'asr' ? '正在提交…' : '执行语音返工'}
                </button>
              </div>
            )}

            {tab === 'manual' && (
              <div className="rework-tab-panel" role="tabpanel">
                <label>
                  当前有效证据
                  <select
                    value={evidenceId}
                    onChange={event => setEvidenceId(event.target.value)}
                  >
                    {task.evidence.map(item => (
                      <option key={item.id} value={item.id}>
                        {formatTime(item.startSeconds)} · {item.kind.toUpperCase()} ·{' '}
                        {item.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  校正后的文字
                  <textarea
                    value={manualText}
                    onChange={event => setManualText(event.target.value)}
                    placeholder="输入你确认后的准确文字"
                  />
                </label>
                <label>
                  校正原因（可选）
                  <input
                    value={reason}
                    onChange={event => setReason(event.target.value)}
                    placeholder="例如：专有名词被 ASR 误识别"
                  />
                </label>
                <button
                  type="button"
                  className="button button-primary"
                  disabled={!manualValid || pending !== null}
                  onClick={submitCorrection}
                >
                  <ScanText size={15} aria-hidden="true" />
                  {pending === 'manual' ? '正在提交…' : '保存人工校正 revision'}
                </button>
              </div>
            )}
          </section>

          <section className="rework-history">
            <div className="drawer-section-heading">
              <div>
                <span>IMMUTABLE HISTORY</span>
                <h3>返工记录 · {task.operations.length}</h3>
              </div>
              <button
                type="button"
                aria-label="刷新返工记录"
                onClick={() => refreshOperations(task.id)}
              >
                <RefreshCcw size={14} aria-hidden="true" />
              </button>
            </div>
            {task.operations.length > 0 ? (
              <div className="rework-operation-list">
                {[...task.operations].reverse().map(operation => (
                  <article key={operation.id}>
                    <span
                      className={`operation-status is-${operation.status}`}
                      aria-hidden="true"
                    />
                    <div>
                      <strong>{operationLabel[operation.kind]}</strong>
                      <p>
                        {formatTime(operation.startSeconds)} —{' '}
                        {formatTime(operation.endSeconds)}
                        {operation.samplingMode === 'fixed_interval' &&
                          ` · ${operation.intervalSeconds}s/帧`}
                      </p>
                      <span>
                        {operation.status === 'completed'
                          ? `已激活 ${operation.revisionId ?? 'evidence revision'}`
                          : operation.status === 'demo-preview'
                            ? '演示预览 · 未运行模型'
                            : operation.detail ?? '执行失败，原证据未变更'}
                      </span>
                    </div>
                    <time>
                      {new Date(operation.finishedAt).toLocaleTimeString('zh-CN', {
                        hour: '2-digit',
                        minute: '2-digit',
                      })}
                    </time>
                  </article>
                ))}
              </div>
            ) : (
              <div className="material-empty">
                <SlidersHorizontal size={18} aria-hidden="true" />
                <p>还没有局部返工记录。首次处理结果仍是当前有效证据。</p>
              </div>
            )}
          </section>
        </div>

        <footer className="workbench-drawer-footer">
          <span>
            {task.realBackend
              ? '成功后会激活新的有效证据 revision；报告需在“生成报告”中重新生成。'
              : '当前为演示模式；视觉与语音只记录预览，人工校正仅修改内存证据。'}
          </span>
          <button className="button button-secondary" type="button" onClick={onClose}>
            完成
          </button>
        </footer>
      </aside>
    </div>
  )
}

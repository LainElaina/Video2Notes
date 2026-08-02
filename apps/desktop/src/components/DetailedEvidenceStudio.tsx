import { useMemo, useState } from 'react'
import {
  Activity,
  AudioLines,
  ChevronRight,
  CircleAlert,
  FileOutput,
  Gauge,
  Image,
  Layers3,
  RotateCcw,
  ScanText,
  SlidersHorizontal,
} from 'lucide-react'
import type {
  EvidenceItem,
  EvidenceKind,
  NoteDocument,
  ProcessingTask,
  TelemetryValue,
} from '../domain'
import { formatTime } from '../domain'
import { SynchronizedVideo } from './SynchronizedVideo'

interface DetailedEvidenceStudioProps {
  task: ProcessingTask
  note: NoteDocument
  currentTimeSeconds: number
  selectedEvidenceId?: string
  onSeek: (seconds: number) => void
  onSelectEvidence: (evidenceId: string, seconds: number) => void
  onRequestRework: (startSeconds: number, endSeconds: number) => void
}

type TimelineKind = Extract<EvidenceKind, 'asr' | 'ocr' | 'visual'>

const trackMeta: Record<
  TimelineKind,
  { label: string; shortLabel: string; icon: typeof AudioLines }
> = {
  asr: { label: '语音证据', shortLabel: 'SPEECH', icon: AudioLines },
  ocr: { label: '屏幕文字', shortLabel: 'SCREEN TEXT', icon: ScanText },
  visual: { label: '视觉变化', shortLabel: 'VISUAL', icon: Image },
}

const clamp = (value: number, lower: number, upper: number) =>
  Math.min(upper, Math.max(lower, value))

const percentAt = (seconds: number, durationSeconds: number) =>
  `${clamp((seconds / Math.max(1, durationSeconds)) * 100, 0, 100)}%`

const displayMetric = (value: TelemetryValue): string => {
  if (value === null) return '—'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'number') {
    if (value > 0 && value < 1) return `${Math.round(value * 100)}%`
    return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(value)
  }
  return value
}

const buildDensity = (evidence: EvidenceItem[], durationSeconds: number, buckets = 56) => {
  const values = Array.from({ length: buckets }, () => 0)
  evidence.forEach(item => {
    const start = clamp(
      Math.floor((item.startSeconds / Math.max(1, durationSeconds)) * buckets),
      0,
      buckets - 1,
    )
    const end = clamp(
      Math.ceil((item.endSeconds / Math.max(1, durationSeconds)) * buckets),
      start + 1,
      buckets,
    )
    for (let index = start; index < end; index += 1) values[index] += 1
  })
  const maximum = Math.max(1, ...values)
  return values.map(value => 0.18 + (value / maximum) * 0.82)
}

export function DetailedEvidenceStudio({
  task,
  note,
  currentTimeSeconds,
  selectedEvidenceId,
  onSeek,
  onSelectEvidence,
  onRequestRework,
}: DetailedEvidenceStudioProps) {
  const duration = Math.max(1, task.source.durationSeconds)
  const [enabledKinds, setEnabledKinds] = useState<Set<TimelineKind>>(
    new Set(['asr', 'ocr', 'visual']),
  )
  const [confidenceFloor, setConfidenceFloor] = useState(0.7)
  const [rangeStart, setRangeStart] = useState(Math.min(duration * 0.1, 600))
  const [rangeEnd, setRangeEnd] = useState(Math.min(duration * 0.2, 1_200))

  const evidence = useMemo(
    () =>
      task.evidence.filter(
        item =>
          item.kind !== 'chapter' &&
          enabledKinds.has(item.kind) &&
          item.confidence >= confidenceFloor,
      ),
    [confidenceFloor, enabledKinds, task.evidence],
  )
  const density = useMemo(
    () => buildDensity(task.evidence.filter(item => item.kind !== 'chapter'), duration),
    [duration, task.evidence],
  )
  const recentTelemetry = task.telemetry.slice(-7)
  const metrics = task.stages
    .flatMap(stage =>
      Object.entries(stage.metrics).map(([key, value]) => ({
        id: `${stage.id}:${key}`,
        stage: stage.label,
        key,
        value,
      })),
    )
    .slice(-6)
  const warnings = [...task.runtimeWarnings, ...(task.warnings ?? [])].filter(
    (item, index, all) => all.indexOf(item) === index,
  )

  const setRangeBoundary = (boundary: 'start' | 'end', value: number) => {
    if (boundary === 'start') {
      const next = clamp(value, 0, Math.max(0, rangeEnd - 0.1))
      setRangeStart(next)
      onSeek(next)
      return
    }
    const next = clamp(value, Math.min(duration, rangeStart + 0.1), duration)
    setRangeEnd(next)
    onSeek(next)
  }

  const toggleKind = (kind: TimelineKind) => {
    setEnabledKinds(current => {
      const next = new Set(current)
      if (next.has(kind)) next.delete(kind)
      else next.add(kind)
      return next
    })
  }

  return (
    <div className="detailed-evidence-studio">
      <aside className="studio-index" aria-label="笔记结构与元数据">
        <section className="studio-panel studio-note-list">
          <div className="studio-panel-title">
            <span>NOTE</span>
            <strong>{note.title}</strong>
          </div>
          <div className="studio-toc">
            <button type="button" onClick={() => onSeek(0)}>
              <span>概览</span>
              <time>00:00</time>
            </button>
            {note.sections.map((section, index) => {
              const time = section.startSeconds ?? section.claims[0]?.timeSeconds ?? 0
              return (
                <button type="button" key={section.id} onClick={() => onSeek(time)}>
                  <span>
                    <small>{index + 1}</small>
                    {section.title}
                  </span>
                  <time>{formatTime(time)}</time>
                </button>
              )
            })}
          </div>
        </section>

        <section className="studio-panel studio-metadata">
          <div className="studio-panel-title">
            <span>NOTE METADATA</span>
          </div>
          <dl>
            <div>
              <dt>来源</dt>
              <dd>{task.source.platform.toUpperCase()}</dd>
            </div>
            <div>
              <dt>时长</dt>
              <dd>{formatTime(duration)}</dd>
            </div>
            <div>
              <dt>配置</dt>
              <dd>{task.mode.toUpperCase()}</dd>
            </div>
            <div>
              <dt>处理范围</dt>
              <dd>{task.processingScope === 'audio_only' ? '仅音频' : '完整音画'}</dd>
            </div>
            <div>
              <dt>画质</dt>
              <dd>{task.source.quality}</dd>
            </div>
            <div>
              <dt>字幕</dt>
              <dd>{task.source.subtitle}</dd>
            </div>
            <div>
              <dt>证据</dt>
              <dd>{task.evidence.length} 条</dd>
            </div>
          </dl>
        </section>

        <section className="studio-panel studio-warning-summary">
          <div className="studio-panel-title">
            <span>QUALITY NOTES</span>
          </div>
          {warnings.length ? (
            warnings.slice(0, 3).map(warning => (
              <p key={warning}>
                <CircleAlert size={13} aria-hidden="true" />
                {warning}
              </p>
            ))
          ) : (
            <p className="is-clear">
              <Gauge size={13} aria-hidden="true" />
              当前没有运行时质量警告
            </p>
          )}
        </section>
      </aside>

      <section className="studio-center">
        <div className="studio-media-panel">
          <div className="studio-panel-title">
            <span>VIDEO PREVIEW</span>
            <strong>{formatTime(currentTimeSeconds)}</strong>
          </div>
          <SynchronizedVideo
            title={task.source.title}
            durationSeconds={duration}
            currentTimeSeconds={currentTimeSeconds}
            onSeek={onSeek}
            src={task.mediaUrl}
            ocrConfidence={task.evidence.find(item => item.kind === 'ocr')?.confidence}
          />
        </div>

        <section className="evidence-density-panel" aria-label="证据密度">
          <header>
            <div>
              <span className="studio-label">EVIDENCE DENSITY</span>
              <small>依据已生成证据计算，不是模拟音频波形</small>
            </div>
            <strong>PTS / {Math.round(currentTimeSeconds * 1_000_000).toLocaleString()}</strong>
          </header>
          <button
            type="button"
            className="density-plot"
            aria-label="证据密度时间线，点击跳转"
            onClick={event => {
              const bounds = event.currentTarget.getBoundingClientRect()
              onSeek(((event.clientX - bounds.left) / bounds.width) * duration)
            }}
          >
            {density.map((value, index) => (
              <i
                key={`${index}-${value}`}
                style={{ height: `${Math.round(value * 100)}%` }}
                aria-hidden="true"
              />
            ))}
            <span
              className="density-playhead"
              style={{ left: percentAt(currentTimeSeconds, duration) }}
              aria-hidden="true"
            />
          </button>
        </section>

        <section className="evidence-timeline-panel">
          <header className="timeline-toolbar">
            <div>
              <span className="studio-label">EVIDENCE TIMELINE</span>
              <strong>{evidence.length} 个可见证据区间</strong>
            </div>
            <div className="timeline-filters">
              {(Object.keys(trackMeta) as TimelineKind[]).map(kind => (
                <label key={kind}>
                  <input
                    type="checkbox"
                    checked={enabledKinds.has(kind)}
                    onChange={() => toggleKind(kind)}
                  />
                  {trackMeta[kind].label}
                </label>
              ))}
            </div>
          </header>
          <div className="timeline-scale" aria-hidden="true">
            {[0, 0.25, 0.5, 0.75, 1].map(position => (
              <span key={position} style={{ left: `${position * 100}%` }}>
                {formatTime(duration * position)}
              </span>
            ))}
          </div>
          <div className="timeline-tracks">
            {(Object.keys(trackMeta) as TimelineKind[]).map(kind => {
              const Icon = trackMeta[kind].icon
              return (
                <div className={`timeline-track track-${kind}`} key={kind}>
                  <div className="timeline-track-label">
                    <Icon size={17} aria-hidden="true" />
                    <span>
                      <strong>{trackMeta[kind].label}</strong>
                      <small>{trackMeta[kind].shortLabel}</small>
                    </span>
                  </div>
                  <div className="timeline-track-canvas">
                    {evidence
                      .filter(item => item.kind === kind)
                      .map(item => (
                        <button
                          type="button"
                          className={item.id === selectedEvidenceId ? 'is-selected' : ''}
                          key={item.id}
                          title={`${item.label} · ${Math.round(item.confidence * 100)}%`}
                          style={{
                            left: percentAt(item.startSeconds, duration),
                            width: `max(5px, ${percentAt(
                              Math.max(0.5, item.endSeconds - item.startSeconds),
                              duration,
                            )})`,
                          }}
                          onClick={() => onSelectEvidence(item.id, item.startSeconds)}
                        >
                          <span>{item.rawText}</span>
                        </button>
                      ))}
                    <span
                      className="timeline-playhead"
                      style={{ left: percentAt(currentTimeSeconds, duration) }}
                      aria-hidden="true"
                    />
                  </div>
                </div>
              )
            })}
          </div>

          <div className="range-workbench">
            <div className="range-copy">
              <RotateCcw size={16} aria-hidden="true" />
              <span>
                <strong>局部返工选区</strong>
                <small>
                  {formatTime(rangeStart)} — {formatTime(rangeEnd)}
                </small>
              </span>
            </div>
            <div className="dual-range" aria-label="返工时间范围">
              <input
                type="range"
                min={0}
                max={duration}
                step={0.1}
                value={rangeStart}
                onChange={event => setRangeBoundary('start', Number(event.target.value))}
                aria-label="返工开始时间"
              />
              <input
                type="range"
                min={0}
                max={duration}
                step={0.1}
                value={rangeEnd}
                onChange={event => setRangeBoundary('end', Number(event.target.value))}
                aria-label="返工结束时间"
              />
              <span
                style={{
                  left: percentAt(rangeStart, duration),
                  width: percentAt(rangeEnd - rangeStart, duration),
                }}
                aria-hidden="true"
              />
            </div>
            <button
              type="button"
              className="studio-action"
              onClick={() => onRequestRework(rangeStart, rangeEnd)}
            >
              设置返工范围
              <ChevronRight size={14} aria-hidden="true" />
            </button>
          </div>
        </section>
      </section>

      <aside className="studio-note-preview">
        <section className="studio-panel studio-markdown">
          <div className="studio-panel-title">
            <span>NOTE / MARKDOWN</span>
            <FileOutput size={14} aria-hidden="true" />
          </div>
          <article>
            <h2>{note.title}</h2>
            <p>{note.overview}</p>
            {note.sections.map(section => (
              <section key={section.id}>
                <h3>{section.title}</h3>
                <p>{section.summary}</p>
                {section.claims.slice(0, 3).map(claim => (
                  <button
                    type="button"
                    key={claim.id}
                    onClick={() => onSelectEvidence(claim.evidenceIds[0], claim.timeSeconds)}
                  >
                    <time>{formatTime(claim.timeSeconds)}</time>
                    {claim.text}
                  </button>
                ))}
              </section>
            ))}
          </article>
        </section>

        <section className="studio-panel studio-evidence-filter">
          <div className="studio-panel-title">
            <span>EVIDENCE FILTER</span>
            <SlidersHorizontal size={14} aria-hidden="true" />
          </div>
          <label>
            <span>
              最低置信度
              <strong>{Math.round(confidenceFloor * 100)}%</strong>
            </span>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={confidenceFloor}
              onChange={event => setConfidenceFloor(Number(event.target.value))}
            />
          </label>
          <div className="filter-counts">
            {(Object.keys(trackMeta) as TimelineKind[]).map(kind => (
              <span key={kind}>
                {trackMeta[kind].label}
                <strong>{task.evidence.filter(item => item.kind === kind).length}</strong>
              </span>
            ))}
          </div>
        </section>
      </aside>

      <footer className="studio-telemetry">
        <div className="telemetry-title">
          <Activity size={15} aria-hidden="true" />
          <span>
            <strong>STAGE TELEMETRY</strong>
            <small>{task.realBackend ? '真实后端事件' : '明确标记的演示样本'}</small>
          </span>
        </div>
        <div className="telemetry-events">
          {recentTelemetry.length ? (
            recentTelemetry.map(sample => (
              <div key={`${sample.sequence}-${sample.stage}`}>
                <span className={`telemetry-state state-${sample.state}`} aria-hidden="true" />
                <span>
                  <strong>{sample.stage}</strong>
                  <small>{sample.message || `事件 #${sample.sequence}`}</small>
                </span>
              </div>
            ))
          ) : (
            <div className="telemetry-empty">
              <Layers3 size={15} aria-hidden="true" />
              尚无后端事件
            </div>
          )}
        </div>
        <div className="telemetry-metrics">
          {metrics.length ? (
            metrics.map(metric => (
              <div key={metric.id}>
                <span>{metric.stage}</span>
                <strong>{displayMetric(metric.value)}</strong>
                <small>{metric.key.replaceAll('_', ' ')}</small>
              </div>
            ))
          ) : (
            <div className="telemetry-empty">
              <Gauge size={15} aria-hidden="true" />
              后端尚未报告指标
            </div>
          )}
        </div>
      </footer>
    </div>
  )
}

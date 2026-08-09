import {
  memo,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent,
} from 'react'
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
import { useI18n } from '../i18n'
import {
  bucketEvidenceByPixel,
  pickEvidenceAtTime,
  type EvidencePixelBucket,
} from './evidenceTimeline'
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
  { label: readonly [string, string]; shortLabel: readonly [string, string]; icon: typeof AudioLines }
> = {
  asr: { label: ['语音证据', 'Speech evidence'], shortLabel: ['语音', 'SPEECH'], icon: AudioLines },
  ocr: { label: ['屏幕文字', 'On-screen text'], shortLabel: ['屏幕文字', 'SCREEN TEXT'], icon: ScanText },
  visual: { label: ['视觉变化', 'Visual changes'], shortLabel: ['视觉', 'VISUAL'], icon: Image },
}

const stageLabels: Record<string, readonly [string, string]> = {
  acquire: ['获取', 'Acquire'],
  normalize: ['规范化', 'Normalize'],
  speech: ['语音', 'Speech'],
  vision: ['视觉', 'Vision'],
  fusion: ['融合', 'Fusion'],
  draft: ['写作', 'Compose'],
  verify: ['验证', 'Verify'],
  render: ['导出', 'Render'],
}

const modeLabels: Record<ProcessingTask['mode'], readonly [string, string]> = {
  fast: ['快速', 'FAST'],
  balanced: ['均衡', 'BALANCED'],
  accurate: ['精确', 'ACCURATE'],
}

const metadataValueCopy: Record<string, readonly [string, string]> = {
  中文平台字幕: ['中文平台字幕', 'Chinese platform captions'],
  'English auto captions': ['英文自动字幕', 'English auto captions'],
  未发现平台字幕: ['未发现平台字幕', 'No platform captions found'],
  字幕清单: ['字幕清单', 'Caption inventory'],
  检查同名字幕文件: ['检查同名字幕文件', 'Check matching caption files'],
  '从任务 artifact 读取': ['从任务 artifact 读取', 'Read from task artifact'],
  '1920 × 1080 · 待完整探测': ['1920 × 1080 · 待完整探测', '1920 × 1080 · pending full probe'],
}

const timelineKinds = Object.keys(trackMeta) as TimelineKind[]

const clamp = (value: number, lower: number, upper: number) =>
  Math.min(upper, Math.max(lower, value))

const percentAt = (seconds: number, durationSeconds: number) =>
  `${clamp((seconds / Math.max(1, durationSeconds)) * 100, 0, 100)}%`

const displayMetric = (
  value: TelemetryValue,
  locale: string,
  text: (zh: string, en: string) => string,
): string => {
  if (value === null) return '—'
  if (typeof value === 'boolean') return value ? text('是', 'Yes') : text('否', 'No')
  if (typeof value === 'number') {
    if (value > 0 && value < 1) return `${Math.round(value * 100)}%`
    return new Intl.NumberFormat(locale, { maximumFractionDigits: 2 }).format(value)
  }
  return value
}

const buildDensity = (evidence: EvidenceItem[], durationSeconds: number, buckets = 56) => {
  const deltas = Array.from({ length: buckets + 1 }, () => 0)
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
    deltas[start] += 1
    deltas[end] -= 1
  })
  let active = 0
  const values = deltas.slice(0, buckets).map(delta => {
    active += delta
    return active
  })
  const maximum = Math.max(1, ...values)
  return values.map(value => 0.18 + (value / maximum) * 0.82)
}

const bucketCountForWidth = (width: number) => {
  return Math.min(220, Math.max(48, Math.floor(Math.max(0, width) / 7)))
}

interface TimelineEventLayerProps {
  buckets: EvidencePixelBucket[]
  durationSeconds: number
  kind: TimelineKind
  onSelectEvidence: DetailedEvidenceStudioProps['onSelectEvidence']
}

const TimelineEventLayer = memo(function TimelineEventLayer({
  buckets,
  durationSeconds,
  kind,
  onSelectEvidence,
}: TimelineEventLayerProps) {
  const { text } = useI18n()
  const selectBucketEvidence = (
    event: MouseEvent<HTMLButtonElement>,
    bucket: EvidencePixelBucket,
  ) => {
    const bounds = event.currentTarget.parentElement?.getBoundingClientRect()
    const hasPointerPosition = Boolean(bounds && bounds.width > 0 && event.detail > 0)
    const pointerSeconds =
      hasPointerPosition && bounds
        ? ((event.clientX - bounds.left) / bounds.width) * Math.max(1, durationSeconds)
        : bucket.displayItem.startSeconds
    const item = hasPointerPosition
      ? pickEvidenceAtTime(bucket.items, pointerSeconds)
      : (bucket.selectedItem ?? bucket.displayItem)
    onSelectEvidence(item.id, item.startSeconds)
  }

  return (
    <>
      {buckets.map(bucket => {
        const item = bucket.displayItem
        const grouped = bucket.items.length > 1
        const leftSeconds = grouped && !bucket.selectedItem ? bucket.windowStartSeconds : item.startSeconds
        const widthSeconds =
          grouped && !bucket.selectedItem
            ? bucket.windowEndSeconds - bucket.windowStartSeconds
            : Math.max(0.5, item.endSeconds - item.startSeconds)
        const groupDescription = grouped
          ? text(
              `，同一时间像素桶内共 ${bucket.items.length} 条证据，范围 ${formatTime(bucket.evidenceStartSeconds)} 至 ${formatTime(bucket.evidenceEndSeconds)}`,
              `, ${bucket.items.length} evidence items in the same time pixel bucket, ranging from ${formatTime(bucket.evidenceStartSeconds)} to ${formatTime(bucket.evidenceEndSeconds)}`,
            )
          : ''

        return (
          <button
            type="button"
            className={bucket.selectedItem ? 'is-selected' : ''}
            key={`${kind}-${bucket.index}`}
            title={`${item.label} · ${Math.round(item.confidence * 100)}%${grouped ? text(` · 聚合 ${bucket.items.length} 条`, ` · ${bucket.items.length} grouped items`) : ''}`}
            aria-label={`${text(...trackMeta[kind].label)} ${item.id}${text('，', ', ')}${formatTime(item.startSeconds)}${text('，', ', ')}${item.label}${groupDescription}`}
            style={{
              left: percentAt(leftSeconds, durationSeconds),
              width: `max(5px, ${percentAt(widthSeconds, durationSeconds)})`,
            }}
            onClick={event => selectBucketEvidence(event, bucket)}
          >
            <span>{item.rawText}</span>
          </button>
        )
      })}
    </>
  )
})

export function DetailedEvidenceStudio({
  task,
  note,
  currentTimeSeconds,
  selectedEvidenceId,
  onSeek,
  onSelectEvidence,
  onRequestRework,
}: DetailedEvidenceStudioProps) {
  const { locale, text } = useI18n()
  const duration = Math.max(1, task.source.durationSeconds)
  const [enabledKinds, setEnabledKinds] = useState<Set<TimelineKind>>(
    () => new Set(['asr', 'ocr', 'visual']),
  )
  const [confidenceFloor, setConfidenceFloor] = useState(0.7)
  const [rangeStart, setRangeStart] = useState(Math.min(duration * 0.1, 600))
  const [rangeEnd, setRangeEnd] = useState(Math.min(duration * 0.2, 1_200))
  const timelineCanvasRef = useRef<HTMLDivElement>(null)
  const densityPlotRef = useRef<HTMLButtonElement>(null)
  const [timelineBucketCount, setTimelineBucketCount] = useState(168)
  const [timelineCanvasWidth, setTimelineCanvasWidth] = useState(0)
  const [densityWidth, setDensityWidth] = useState(0)

  useEffect(() => {
    const timelineElement = timelineCanvasRef.current
    const densityElement = densityPlotRef.current
    if (!timelineElement && !densityElement) return

    const measure = () => {
      const timelineWidth = timelineElement?.getBoundingClientRect().width ?? 0
      const densityPlotWidth = densityElement?.getBoundingClientRect().width ?? 0
      if (timelineWidth > 0) {
        setTimelineCanvasWidth(current =>
          current === timelineWidth ? current : timelineWidth,
        )
        const nextBucketCount = bucketCountForWidth(timelineWidth)
        setTimelineBucketCount(current =>
          current === nextBucketCount ? current : nextBucketCount,
        )
      }
      if (densityPlotWidth > 0) {
        setDensityWidth(current =>
          current === densityPlotWidth ? current : densityPlotWidth,
        )
      }
    }

    measure()
    if (typeof ResizeObserver === 'undefined') return

    const observer = new ResizeObserver(measure)
    if (timelineElement) observer.observe(timelineElement)
    if (densityElement) observer.observe(densityElement)
    return () => observer.disconnect()
  }, [])

  const playheadRatio = clamp(currentTimeSeconds / duration, 0, 1)
  const densityPlayheadX = densityWidth * playheadRatio
  const timelinePlayheadX = timelineCanvasWidth * playheadRatio

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
  const evidenceBucketsByKind = useMemo(() => {
    const grouped = Object.fromEntries(
      timelineKinds.map(kind => [
        kind,
        bucketEvidenceByPixel(
          evidence.filter(item => item.kind === kind),
          duration,
          timelineBucketCount,
          selectedEvidenceId,
        ),
      ]),
    )
    return grouped as Record<TimelineKind, EvidencePixelBucket[]>
  }, [duration, evidence, selectedEvidenceId, timelineBucketCount])
  const density = useMemo(
    () => buildDensity(task.evidence.filter(item => item.kind !== 'chapter'), duration),
    [duration, task.evidence],
  )
  const recentTelemetry = useMemo(() => task.telemetry.slice(-7), [task.telemetry])
  const metrics = useMemo(
    () =>
      task.stages
        .flatMap(stage =>
          Object.entries(stage.metrics).map(([key, value]) => ({
            id: `${stage.id}:${key}`,
            stage: stageLabels[stage.id] ? text(...stageLabels[stage.id]) : stage.label,
            key,
            value,
          })),
        )
        .slice(-6),
    [task.stages, text],
  )
  const warnings = useMemo(
    () =>
      [...task.runtimeWarnings, ...(task.warnings ?? [])].filter(
        (item, index, all) => all.indexOf(item) === index,
      ),
    [task.runtimeWarnings, task.warnings],
  )
  const evidenceCounts = useMemo(
    () =>
      Object.fromEntries(
        timelineKinds.map(kind => [kind, task.evidence.filter(item => item.kind === kind).length]),
      ) as Record<TimelineKind, number>,
    [task.evidence],
  )
  const ocrConfidence = useMemo(
    () => task.evidence.find(item => item.kind === 'ocr')?.confidence,
    [task.evidence],
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

  const metadataValue = (value: string) =>
    metadataValueCopy[value] ? text(...metadataValueCopy[value]) : value

  return (
    <div className="detailed-evidence-studio">
      <aside className="studio-index" aria-label={text('笔记结构与元数据', 'Note structure and metadata')}>
        <section className="studio-panel studio-note-list">
          <div className="studio-panel-title">
            <span>{text('笔记', 'NOTE')}</span>
            <strong>{note.title}</strong>
          </div>
          <div className="studio-toc">
            <button type="button" onClick={() => onSeek(0)}>
              <span>{text('概览', 'Overview')}</span>
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
            <span>{text('笔记元数据', 'NOTE METADATA')}</span>
          </div>
          <dl>
            <div>
              <dt>{text('来源', 'Source')}</dt>
              <dd>{task.source.platform.toUpperCase()}</dd>
            </div>
            <div>
              <dt>{text('时长', 'Duration')}</dt>
              <dd>{formatTime(duration)}</dd>
            </div>
            <div>
              <dt>{text('配置', 'Profile')}</dt>
              <dd>{text(...modeLabels[task.mode])}</dd>
            </div>
            <div>
              <dt>{text('处理范围', 'Processing scope')}</dt>
              <dd>{task.processingScope === 'audio_only' ? text('仅音频', 'Audio only') : text('完整音画', 'Audio + video')}</dd>
            </div>
            <div>
              <dt>{text('画质', 'Video quality')}</dt>
              <dd>{metadataValue(task.source.quality)}</dd>
            </div>
            <div>
              <dt>{text('字幕', 'Captions')}</dt>
              <dd>{metadataValue(task.source.subtitle)}</dd>
            </div>
            <div>
              <dt>{text('证据', 'Evidence')}</dt>
              <dd>{task.evidence.length} {text('条', task.evidence.length === 1 ? 'item' : 'items')}</dd>
            </div>
          </dl>
        </section>

        <section className="studio-panel studio-warning-summary">
          <div className="studio-panel-title">
            <span>{text('质量提示', 'QUALITY NOTES')}</span>
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
              {text('当前没有运行时质量警告', 'There are no runtime quality warnings')}
            </p>
          )}
        </section>
      </aside>

      <section className="studio-center">
        <div className="studio-media-panel">
          <div className="studio-panel-title">
            <span>{text('视频预览', 'VIDEO PREVIEW')}</span>
            <strong>{formatTime(currentTimeSeconds)}</strong>
          </div>
          <SynchronizedVideo
            title={task.source.title}
            durationSeconds={duration}
            currentTimeSeconds={currentTimeSeconds}
            onSeek={onSeek}
            src={task.mediaUrl}
            ocrConfidence={ocrConfidence}
          />
        </div>

        <section className="evidence-density-panel" aria-label={text('证据密度', 'Evidence density')}>
          <header>
            <div>
              <span className="studio-label">{text('证据密度', 'EVIDENCE DENSITY')}</span>
              <small>{text('依据已生成证据计算，不是模拟音频波形', 'Calculated from generated evidence, not a simulated audio waveform')}</small>
            </div>
            <strong>PTS / {Math.round(currentTimeSeconds * 1_000_000).toLocaleString()}</strong>
          </header>
          <button
            type="button"
            className="density-plot"
            ref={densityPlotRef}
            aria-label={text('证据密度时间线，点击跳转', 'Evidence density timeline; click to seek')}
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
              style={{
                opacity: densityWidth > 0 ? 1 : 0,
                transform: `translate3d(${densityPlayheadX}px, 0, 0)`,
              }}
              aria-hidden="true"
            />
          </button>
        </section>

        <section className="evidence-timeline-panel">
          <header className="timeline-toolbar">
            <div>
              <span className="studio-label">{text('证据时间线', 'EVIDENCE TIMELINE')}</span>
              <strong>{evidence.length} {text('个可见证据区间', evidence.length === 1 ? 'visible evidence interval' : 'visible evidence intervals')}</strong>
            </div>
            <div className="timeline-filters">
              {timelineKinds.map(kind => (
                <label key={kind}>
                  <input
                    type="checkbox"
                    checked={enabledKinds.has(kind)}
                    onChange={() => toggleKind(kind)}
                  />
                  {text(...trackMeta[kind].label)}
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
            {timelineKinds.map(kind => {
              const Icon = trackMeta[kind].icon
              return (
                <div className={`timeline-track track-${kind}`} key={kind}>
                  <div className="timeline-track-label">
                    <Icon size={17} aria-hidden="true" />
                    <span>
                      <strong>{text(...trackMeta[kind].label)}</strong>
                      <small>{text(...trackMeta[kind].shortLabel)}</small>
                    </span>
                  </div>
                  <div
                    className="timeline-track-canvas"
                    ref={kind === timelineKinds[0] ? timelineCanvasRef : undefined}
                  >
                    <TimelineEventLayer
                      buckets={evidenceBucketsByKind[kind]}
                      durationSeconds={duration}
                      kind={kind}
                      onSelectEvidence={onSelectEvidence}
                    />
                    <span
                      className="timeline-playhead"
                      style={{
                        opacity: timelineCanvasWidth > 0 ? 1 : 0,
                        transform: `translate3d(${timelinePlayheadX}px, 0, 0)`,
                      }}
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
                <strong>{text('局部返工选区', 'Range rework selection')}</strong>
                <small>
                  {formatTime(rangeStart)} — {formatTime(rangeEnd)}
                </small>
              </span>
            </div>
            <div className="dual-range" aria-label={text('返工时间范围', 'Rework time range')}>
              <input
                type="range"
                min={0}
                max={duration}
                step={0.1}
                value={rangeStart}
                onChange={event => setRangeBoundary('start', Number(event.target.value))}
                aria-label={text('返工开始时间', 'Rework start time')}
              />
              <input
                type="range"
                min={0}
                max={duration}
                step={0.1}
                value={rangeEnd}
                onChange={event => setRangeBoundary('end', Number(event.target.value))}
                aria-label={text('返工结束时间', 'Rework end time')}
              />
              <span
                style={{
                  clipPath: `inset(0 ${100 - clamp((rangeEnd / duration) * 100, 0, 100)}% 0 ${clamp((rangeStart / duration) * 100, 0, 100)}%)`,
                }}
                aria-hidden="true"
              />
            </div>
            <button
              type="button"
              className="studio-action"
              onClick={() => onRequestRework(rangeStart, rangeEnd)}
            >
              {text('设置返工范围', 'Set rework range')}
              <ChevronRight size={14} aria-hidden="true" />
            </button>
          </div>
        </section>
      </section>

      <aside className="studio-note-preview">
        <section className="studio-panel studio-markdown">
          <div className="studio-panel-title">
            <span>{text('笔记 / MARKDOWN', 'NOTE / MARKDOWN')}</span>
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
            <span>{text('证据筛选', 'EVIDENCE FILTER')}</span>
            <SlidersHorizontal size={14} aria-hidden="true" />
          </div>
          <label>
            <span>
              {text('最低置信度', 'Minimum confidence')}
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
            {timelineKinds.map(kind => (
              <span key={kind}>
                {text(...trackMeta[kind].label)}
                <strong>{evidenceCounts[kind]}</strong>
              </span>
            ))}
          </div>
        </section>
      </aside>

      <footer className="studio-telemetry">
        <div className="telemetry-title">
          <Activity size={15} aria-hidden="true" />
          <span>
            <strong>{text('阶段遥测', 'STAGE TELEMETRY')}</strong>
            <small>{task.realBackend ? text('真实后端事件', 'Real backend events') : text('明确标记的演示样本', 'Explicitly marked demo samples')}</small>
          </span>
        </div>
        <div className="telemetry-events">
          {recentTelemetry.length ? (
            recentTelemetry.map(sample => (
              <div key={`${sample.sequence}-${sample.stage}`}>
                <span className={`telemetry-state state-${sample.state}`} aria-hidden="true" />
                <span>
                  <strong>{sample.stage}</strong>
                  <small>{sample.message || text(`事件 #${sample.sequence}`, `Event #${sample.sequence}`)}</small>
                </span>
              </div>
            ))
          ) : (
            <div className="telemetry-empty">
              <Layers3 size={15} aria-hidden="true" />
              {text('尚无后端事件', 'No backend events yet')}
            </div>
          )}
        </div>
        <div className="telemetry-metrics">
          {metrics.length ? (
            metrics.map(metric => (
              <div key={metric.id}>
                <span>{metric.stage}</span>
                <strong>{displayMetric(metric.value, locale, text)}</strong>
                <small>{metric.key.replaceAll('_', ' ')}</small>
              </div>
            ))
          ) : (
            <div className="telemetry-empty">
              <Gauge size={15} aria-hidden="true" />
              {text('后端尚未报告指标', 'The backend has not reported metrics yet')}
            </div>
          )}
        </div>
      </footer>
    </div>
  )
}

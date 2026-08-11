import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from 'react'
import {
  Activity,
  AlertTriangle,
  Check,
  ChevronDown,
  CircleAlert,
  Copy,
  Download,
  FileClock,
  Search,
} from 'lucide-react'
import type {
  ProcessingTask,
  TelemetrySample,
  TelemetryValue,
} from '../domain'
import { useI18n } from '../i18n'
import { preferredScrollBehavior } from '../motion'
import { VisualAsset } from './VisualAsset'
import './ProcessingFlowPanel.css'

export type RunEventLogLevel = 'info' | 'warning' | 'error'

export interface RunEventLogEntry {
  id?: string
  sequence?: number
  stage: string
  stageLabel?: string
  level?: RunEventLogLevel
  state?: TelemetrySample['state']
  progress?: number
  message: string
  errorType?: string
  metrics?: Record<string, TelemetryValue>
  createdAt: string
}

export type ProcessingFlowTask = Pick<
  ProcessingTask,
  'id' | 'status' | 'telemetry' | 'stages' | 'eventLog' | 'realBackend'
>

export interface ProcessingFlowPanelProps {
  task: ProcessingFlowTask
  events?: readonly RunEventLogEntry[]
  initialAutoFollow?: boolean
  className?: string
  downloadFileName?: string
  stageFilter?: string
  onStageFilterChange?: (stageId: string) => void
  onCopy?: (content: string) => void | Promise<void>
  onDownload?: (fileName: string, content: string) => void
}

interface NormalizedRunEvent extends Required<Pick<RunEventLogEntry, 'id' | 'stage' | 'stageLabel' | 'level' | 'message' | 'createdAt'>> {
  sequence?: number
  state?: TelemetrySample['state']
  progress?: number
  errorType?: string
  metrics: Record<string, TelemetryValue>
}

type LevelFilter = 'all' | Extract<RunEventLogLevel, 'warning' | 'error'>

type Localize = (zh: string, en: string) => string

const levelCopy: Record<RunEventLogLevel, { label: readonly [string, string]; exportLabel: string }> = {
  info: { label: ['信息', 'Info'], exportLabel: 'INFO' },
  warning: { label: ['警告', 'Warning'], exportLabel: 'WARNING' },
  error: { label: ['错误', 'Error'], exportLabel: 'ERROR' },
}

// The demo worker only exposes the backend event name, while real stage records
// carry this mapping explicitly.  Keep the fallback here so both modes share
// the same stage-rail interaction.
const defaultStageBackendAliases: Record<string, readonly string[]> = {
  acquire: ['source.acquire'],
  normalize: ['media.probe'],
  speech: ['audio.extract', 'captions.parse', 'audio.asr'],
  vision: ['vision.scan', 'ocr.extract'],
  fusion: ['evidence.fuse'],
  draft: ['notes.compose'],
  verify: ['notes.compose'],
  render: ['render.outputs'],
}

const stateCopy: Record<TelemetrySample['state'], readonly [string, string]> = {
  queued: ['排队', 'Queued'],
  running: ['运行中', 'Running'],
  completed: ['完成', 'Completed'],
  failed: ['失败', 'Failed'],
  cancelled: ['已取消', 'Cancelled'],
}

const stageCopy: Record<string, readonly [string, string]> = {
  acquire: ['获取', 'Acquire'],
  'source.acquire': ['获取视频', 'Acquire video'],
  normalize: ['规范化', 'Normalize'],
  'media.probe': ['媒体探测', 'Probe media'],
  speech: ['语音', 'Speech'],
  'audio.extract': ['提取音频', 'Extract audio'],
  'captions.parse': ['解析字幕', 'Parse captions'],
  'audio.asr': ['语音识别', 'Speech recognition'],
  vision: ['视觉', 'Vision'],
  'vision.scan': ['视觉扫描', 'Visual scan'],
  'ocr.extract': ['OCR 提取', 'OCR extraction'],
  fusion: ['融合', 'Fusion'],
  'evidence.fuse': ['证据融合', 'Fuse evidence'],
  draft: ['写作', 'Compose'],
  verify: ['验证', 'Verify'],
  'notes.compose': ['生成笔记', 'Compose note'],
  render: ['导出', 'Render'],
  'render.outputs': ['渲染输出', 'Render outputs'],
}

const localizedStageLabel = (
  stage: string,
  fallback: string | undefined,
  text: Localize,
): string => stageCopy[stage] ? text(...stageCopy[stage]) : (fallback ?? stage)

const levelFromState = (
  state?: TelemetrySample['state'],
): RunEventLogLevel => {
  if (state === 'failed') return 'error'
  if (state === 'cancelled') return 'warning'
  return 'info'
}

const formatMetricValue = (
  value: TelemetryValue,
  locale: string,
  text: Localize,
): string => {
  if (value === null) return '—'
  if (typeof value === 'boolean') return value ? text('是', 'Yes') : text('否', 'No')
  if (typeof value === 'number') {
    return new Intl.NumberFormat(locale, {
      maximumFractionDigits: 3,
    }).format(value)
  }
  return value
}

const formatProgress = (value: number): string => {
  const percentage = value <= 1 ? value * 100 : value
  return `${Math.round(Math.min(100, Math.max(0, percentage)))}%`
}

const formatClockTime = (value: string, locale: string): string => {
  const timestamp = new Date(value)
  if (Number.isNaN(timestamp.getTime())) return value
  return new Intl.DateTimeFormat(locale, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(timestamp)
}

const exportTimestamp = (value: string): string => {
  const timestamp = new Date(value)
  return Number.isNaN(timestamp.getTime()) ? value : timestamp.toISOString()
}

const normalizeSearchValue = (value: string, locale: string): string => value.trim().toLocaleLowerCase(locale)

const eventSearchText = (
  event: NormalizedRunEvent,
  locale: string,
  text: Localize,
): string =>
  normalizeSearchValue(
    [
      event.stage,
      event.stageLabel,
      event.message,
      event.errorType ?? '',
      event.state ? text(...stateCopy[event.state]) : '',
      ...Object.entries(event.metrics).flatMap(([key, value]) => [
        key,
        formatMetricValue(value, locale, text),
      ]),
    ].join(' '),
    locale,
  )

const serializeEventsText = (
  events: readonly NormalizedRunEvent[],
  locale: string,
  text: Localize,
): string =>
  events
    .map(event => {
      const sequence = event.sequence === undefined ? '' : ` #${event.sequence}`
      const progress = event.progress === undefined ? '' : ` progress=${formatProgress(event.progress)}`
      const errorType = event.errorType ? ` error_type=${event.errorType}` : ''
      const heading = `[${exportTimestamp(event.createdAt)}] [${levelCopy[event.level].exportLabel}] [${event.stage}]${sequence}${progress}${errorType} ${event.message}`
      const metrics = Object.entries(event.metrics).map(
        ([key, value]) => `  ${key}=${formatMetricValue(value, locale, text)}`,
      )
      return [heading, ...metrics].join('\n')
    })
    .join('\n')

const serializeEventsJsonl = (
  runId: string,
  events: readonly NormalizedRunEvent[],
): string => {
  if (events.length === 0) return ''
  return `${events
    .map(event =>
      JSON.stringify({
        run_id: runId,
        sequence: event.sequence ?? null,
        created_at: exportTimestamp(event.createdAt),
        level: event.level,
        state: event.state ?? null,
        stage: event.stage,
        stage_label: event.stageLabel,
        progress: event.progress ?? null,
        message: event.message,
        error_type: event.errorType ?? null,
        metrics: event.metrics,
      }),
    )
    .join('\n')}\n`
}

const defaultDownload = (fileName: string, content: string) => {
  const blob = new Blob([content], {
    type: 'application/x-ndjson;charset=utf-8',
  })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = fileName
  anchor.hidden = true
  document.body.append(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

const safeFileToken = (value: string): string =>
  value.replace(/[^a-zA-Z0-9._-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 72) || 'run'

export function ProcessingFlowPanel({
  task,
  events,
  initialAutoFollow = true,
  className = '',
  downloadFileName,
  stageFilter: controlledStageFilter,
  onStageFilterChange,
  onCopy,
  onDownload,
}: ProcessingFlowPanelProps) {
  const { locale, text } = useI18n()
  const generatedId = useId().replaceAll(':', '')
  const titleId = `processing-flow-title-${generatedId}`
  const viewportRef = useRef<HTMLDivElement>(null)
  const autoFocusedTaskIds = useRef<Set<string>>(new Set())
  const [internalStageFilter, setInternalStageFilter] = useState('all')
  const [levelFilter, setLevelFilter] = useState<LevelFilter>('all')
  const [search, setSearch] = useState('')
  const [autoFollow, setAutoFollow] = useState(initialAutoFollow)
  const [expandedEvents, setExpandedEvents] = useState<Set<string>>(() => new Set())
  const [copyState, setCopyState] = useState<'idle' | 'success' | 'error'>('idle')
  const [focusedEventId, setFocusedEventId] = useState<string>()
  const stageFilter = controlledStageFilter ?? internalStageFilter

  const setStageFilter = useCallback(
    (value: string) => {
      setInternalStageFilter(value)
      onStageFilterChange?.(value)
    },
    [onStageFilterChange],
  )

  const stagePresentation = useMemo(() => {
    const labels = new Map<string, string>()
    const filters = new Map<string, { label: string; stages: Set<string> }>()
    task.stages.forEach(stage => {
      const localizedLabel = localizedStageLabel(stage.id, stage.label, text)
      labels.set(stage.id, localizedLabel)
      labels.set(stage.label, localizedLabel)
      const backendStages = new Set([
        ...defaultStageBackendAliases[stage.id] ?? [],
        ...(stage.backendStages ?? []),
      ])
      backendStages.forEach(backendStage =>
        labels.set(backendStage, localizedStageLabel(backendStage, localizedLabel, text)),
      )
      filters.set(stage.id, {
        label: localizedLabel,
        stages: new Set([stage.id, stage.label, ...backendStages]),
      })
    })
    return { labels, filters }
  }, [task.stages, text])

  const normalizedEvents = useMemo<NormalizedRunEvent[]>(() => {
    const source: readonly RunEventLogEntry[] =
      events ??
      task.telemetry.map(sample => ({
        id: `${sample.sequence}:${sample.stage}:${sample.createdAt}`,
        sequence: sample.sequence,
        stage: sample.stage,
        level: levelFromState(sample.state),
        state: sample.state,
        progress: sample.progress,
        message: sample.message || text(`事件 #${sample.sequence}`, `Event #${sample.sequence}`),
        errorType: sample.errorType,
        metrics: sample.metrics,
        createdAt: sample.createdAt,
      }))

    return source.map((event, index) => ({
      id: event.id ?? `${event.sequence ?? index}:${event.stage}:${event.createdAt}`,
      sequence: event.sequence,
      stage: event.stage,
      stageLabel:
        stagePresentation.labels.get(event.stage) ??
        localizedStageLabel(event.stage, event.stageLabel, text),
      level: event.level ?? levelFromState(event.state),
      state: event.state,
      progress: event.progress,
      message: event.message,
      errorType: event.errorType,
      metrics: { ...(event.metrics ?? {}) },
      createdAt: event.createdAt,
    }))
  }, [events, stagePresentation.labels, task.telemetry, text])

  const stageOptions = useMemo(() => {
    const stages = new Map<string, { label: string; count: number }>(
      [...stagePresentation.filters.entries()].map(([value, filter]) => [
        value,
        {
          label: filter.label,
          count: normalizedEvents.filter(event => filter.stages.has(event.stage)).length,
        },
      ]),
    )
    normalizedEvents.forEach(event => {
      const represented = [...stagePresentation.filters.values()].some(filter =>
        filter.stages.has(event.stage),
      )
      if (represented) return
      const current = stages.get(event.stage)
      stages.set(event.stage, {
        label: event.stageLabel,
        count: (current?.count ?? 0) + 1,
      })
    })
    return [...stages.entries()].map(([value, option]) => ({ value, ...option }))
  }, [normalizedEvents, stagePresentation.filters])

  const eventMatchesStageFilter = useCallback(
    (event: NormalizedRunEvent, filterValue: string): boolean => {
      if (filterValue === 'all') return true
      const filter = stagePresentation.filters.get(filterValue)
      if (filter) return filter.stages.has(event.stage)
      const logicalFilter = [...stagePresentation.filters.values()].find(candidate =>
        candidate.stages.has(filterValue),
      )
      return logicalFilter ? logicalFilter.stages.has(event.stage) : event.stage === filterValue
    },
    [stagePresentation.filters],
  )

  const filterValueForEvent = useCallback(
    (event: NormalizedRunEvent): string => {
      const matchingStages = task.stages.filter(stage =>
        stagePresentation.filters.get(stage.id)?.stages.has(event.stage),
      )
      return (
        matchingStages.find(stage => stage.status === 'failed')?.id ??
        matchingStages[0]?.id ??
        event.stage
      )
    },
    [stagePresentation.filters, task.stages],
  )

  useEffect(() => {
    const knownStageValue =
      stageFilter === 'all' ||
      stageOptions.some(option => option.value === stageFilter) ||
      [...stagePresentation.filters.values()].some(filter =>
        filter.stages.has(stageFilter),
      )
    if (!knownStageValue) {
      setStageFilter('all')
    }
  }, [setStageFilter, stageFilter, stageOptions, stagePresentation.filters])

  useEffect(() => {
    setInternalStageFilter('all')
    setLevelFilter('all')
    setSearch('')
    setAutoFollow(initialAutoFollow)
    setExpandedEvents(new Set())
    setCopyState('idle')
    setFocusedEventId(undefined)
  }, [initialAutoFollow, task.id])

  const levelCounts = useMemo(
    () => ({
      warning: normalizedEvents.filter(event => event.level === 'warning').length,
      error: normalizedEvents.filter(event => event.level === 'error').length,
    }),
    [normalizedEvents],
  )

  const normalizedSearch = normalizeSearchValue(search, locale)
  const filteredEvents = useMemo(
    () =>
      normalizedEvents.filter(event => {
        if (!eventMatchesStageFilter(event, stageFilter)) return false
        if (levelFilter !== 'all' && event.level !== levelFilter) return false
        return !normalizedSearch || eventSearchText(event, locale, text).includes(normalizedSearch)
      }),
    [
      eventMatchesStageFilter,
      levelFilter,
      normalizedEvents,
      normalizedSearch,
      stageFilter,
      locale,
      text,
    ],
  )

  useEffect(() => {
    if (task.status !== 'failed' || autoFocusedTaskIds.current.has(task.id)) return
    const firstError = normalizedEvents.find(event => event.level === 'error')
    if (!firstError) return
    autoFocusedTaskIds.current.add(task.id)
    setStageFilter(filterValueForEvent(firstError))
    setLevelFilter('error')
    setSearch('')
    setAutoFollow(false)
    setFocusedEventId(firstError.id)
  }, [filterValueForEvent, normalizedEvents, setStageFilter, task.id, task.status])

  useEffect(() => {
    if (!autoFollow) return
    const viewport = viewportRef.current
    if (!viewport) return
    viewport.scrollTo({
      top: viewport.scrollHeight,
      behavior: preferredScrollBehavior(),
    })
  }, [autoFollow, filteredEvents.length])

  useEffect(() => {
    if (!focusedEventId) return
    const viewport = viewportRef.current
    if (!viewport) return
    const target = [...viewport.querySelectorAll<HTMLElement>('[data-flow-event-id]')]
      .find(node => node.dataset.flowEventId === focusedEventId)
    if (!target) return
    target.scrollIntoView?.({
      behavior: preferredScrollBehavior(),
      block: 'center',
    })
    target.focus({ preventScroll: true })
  }, [filteredEvents, focusedEventId])

  const clearFilters = () => {
    setStageFilter('all')
    setLevelFilter('all')
    setSearch('')
  }

  const toggleMetrics = (eventId: string) => {
    setExpandedEvents(current => {
      const next = new Set(current)
      if (next.has(eventId)) next.delete(eventId)
      else next.add(eventId)
      return next
    })
  }

  const copyVisibleEvents = async () => {
    const content = serializeEventsText(filteredEvents, locale, text)
    if (!content) return
    try {
      if (onCopy) await onCopy(content)
      else if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(content)
      else throw new Error('Clipboard API is unavailable')
      setCopyState('success')
    } catch {
      setCopyState('error')
    }
  }

  const downloadVisibleEvents = () => {
    const content = serializeEventsJsonl(task.id, filteredEvents)
    if (!content) return
    const fileName =
      downloadFileName ?? `video2notes-${safeFileToken(task.id)}-processing-log.jsonl`
    if (onDownload) onDownload(fileName, content)
    else defaultDownload(fileName, content)
  }

  const panelClassName = ['processing-flow-panel', className].filter(Boolean).join(' ')
  const running = task.status === 'running'

  return (
    <section className={panelClassName} aria-labelledby={titleId}>
      <header className="processing-flow-heading">
        <span className={`processing-flow-heading-icon ${running ? 'is-live' : ''}`} aria-hidden="true">
          <Activity size={18} />
        </span>
        <div>
          <span className="section-kicker">{text('处理流程', 'PROCESS FLOW')}</span>
          <h3 id={titleId}>{text('处理流程日志', 'Processing flow log')}</h3>
          <p>{task.realBackend ? text('本机后端事件与阶段指标', 'Local backend events and stage metrics') : text('当前任务保存的处理事件', 'Processing events saved with this task')}</p>
        </div>
        <div className="processing-flow-summary" aria-label={text('日志统计', 'Log summary')}>
          <strong>{normalizedEvents.length}</strong>
          <span>{text('条事件', normalizedEvents.length === 1 ? 'event' : 'events')}</span>
          {(levelCounts.warning > 0 || levelCounts.error > 0) && (
            <small>
              {levelCounts.warning} {text('警告', levelCounts.warning === 1 ? 'warning' : 'warnings')} · {levelCounts.error} {text('错误', levelCounts.error === 1 ? 'error' : 'errors')}
            </small>
          )}
        </div>
      </header>

      {task.eventLog &&
        (!task.eventLog.available || task.eventLog.corruptLineCount > 0) && (
          <div
            className={`processing-flow-log-health ${
              task.eventLog.corruptLineCount > 0 ? 'is-warning' : 'is-unavailable'
            }`}
            role="status"
            aria-live="polite"
          >
            {task.eventLog.corruptLineCount > 0 ? (
              <AlertTriangle size={15} aria-hidden="true" />
            ) : (
              <FileClock size={15} aria-hidden="true" />
            )}
            <span>
              {task.eventLog.corruptLineCount > 0
                ? text(`读取持久化日志时跳过了 ${task.eventLog.corruptLineCount} 条损坏记录；其余事件仍可筛选和导出。`, `${task.eventLog.corruptLineCount} corrupt ${task.eventLog.corruptLineCount === 1 ? 'record was' : 'records were'} skipped while reading the persisted log. The remaining events can still be filtered and exported.`)
                : text('未找到持久化事件日志；当前仅显示本次会话仍保留的实时事件。', 'No persisted event log was found. Only live events retained in this session are shown.')}
            </span>
          </div>
        )}

      <div className="processing-flow-toolbar">
        <label className="processing-flow-stage-filter">
          <span>{text('阶段', 'Stage')}</span>
          <select
            aria-label={text('阶段', 'Stage')}
            value={stageFilter}
            disabled={normalizedEvents.length === 0}
            onChange={event => setStageFilter(event.target.value)}
          >
            <option value="all">{text('全部阶段', 'All stages')} · {normalizedEvents.length}</option>
            {stageOptions.map(option => (
              <option value={option.value} key={option.value}>
                {option.label} · {option.count}
              </option>
            ))}
          </select>
        </label>

        <div className="processing-flow-levels" role="group" aria-label={text('日志级别', 'Log level')}>
          {(
            [
              ['all', text('全部', 'All'), normalizedEvents.length],
              ['warning', text('警告', 'Warnings'), levelCounts.warning],
              ['error', text('错误', 'Errors'), levelCounts.error],
            ] as const
          ).map(([value, label, count]) => (
            <button
              type="button"
              key={value}
              aria-pressed={levelFilter === value}
              onClick={() => setLevelFilter(value)}
              disabled={normalizedEvents.length === 0}
            >
              <span>{label}</span>
              <strong>{count}</strong>
            </button>
          ))}
        </div>

        <label className="processing-flow-search">
          <Search size={15} aria-hidden="true" />
          <input
            type="search"
            aria-label={text('搜索日志', 'Search logs')}
            placeholder={text('搜索阶段、消息或指标', 'Search stages, messages, or metrics')}
            value={search}
            disabled={normalizedEvents.length === 0}
            onChange={event => setSearch(event.target.value)}
          />
        </label>

        <label className="processing-flow-follow">
          <input
            type="checkbox"
            checked={autoFollow}
            disabled={normalizedEvents.length === 0}
            onChange={event => setAutoFollow(event.target.checked)}
          />
          <span aria-hidden="true"><i /></span>
          {text('自动跟随', 'Auto-follow')}
        </label>

        <div className="processing-flow-actions">
          <button
            className="processing-flow-icon-button"
            type="button"
            aria-label={text('复制当前日志', 'Copy current logs')}
            title={copyState === 'success' ? text('已复制当前筛选结果', 'Copied current filtered results') : text('复制当前筛选结果', 'Copy current filtered results')}
            disabled={filteredEvents.length === 0}
            onClick={() => void copyVisibleEvents()}
          >
            {copyState === 'success' ? <Check size={16} aria-hidden="true" /> : <Copy size={16} aria-hidden="true" />}
          </button>
          <button
            className="processing-flow-icon-button"
            type="button"
            aria-label={text('下载当前日志', 'Download current logs')}
            title={text('下载当前筛选结果（JSONL）', 'Download current filtered results (JSONL)')}
            disabled={filteredEvents.length === 0}
            onClick={downloadVisibleEvents}
          >
            <Download size={16} aria-hidden="true" />
          </button>
        </div>
        <span className="processing-flow-copy-status" role="status" aria-live="polite">
          {copyState === 'success' ? text('日志已复制', 'Logs copied') : copyState === 'error' ? text('无法访问剪贴板', 'Could not access the clipboard') : ''}
        </span>
      </div>

      <div className="processing-flow-viewport" ref={viewportRef}>
        {normalizedEvents.length === 0 ? (
          <div className="processing-flow-empty">
            <VisualAsset className="inline-empty-visual" asset="emptyProcessingLog" width={192} height={124} />
            <div>
              <FileClock size={22} aria-hidden="true" />
              <strong>{text('这个任务没有流程日志', 'This task has no processing flow log')}</strong>
              <p>{text('早期版本创建的任务可能只保留阶段状态和产物；新任务会在处理时持续写入事件。', 'Tasks created by earlier versions may retain only stage states and artifacts. New tasks continuously write events while processing.')}</p>
            </div>
          </div>
        ) : filteredEvents.length === 0 ? (
          <div className="processing-flow-empty is-filtered">
            <Search size={22} aria-hidden="true" />
            <strong>{text('没有匹配的日志', 'No matching logs')}</strong>
            <p>{text('当前阶段、级别和搜索条件没有交集。', 'No events match the current stage, level, and search filters.')}</p>
            <button className="button button-secondary" type="button" onClick={clearFilters}>
              {text('清除筛选', 'Clear filters')}
            </button>
          </div>
        ) : (
          <ol className="processing-flow-list" aria-label={text('处理事件', 'Processing events')}>
            {filteredEvents.map((event, index) => {
              const metricEntries = Object.entries(event.metrics)
              const expanded = expandedEvents.has(event.id)
              const metricId = `processing-flow-metrics-${generatedId}-${index}`
              const sequenceLabel = event.sequence === undefined
                ? (index + 1).toString().padStart(2, '0')
                : event.sequence.toString().padStart(2, '0')

              return (
                <li
                  className={`processing-flow-event is-${event.level} ${
                    focusedEventId === event.id ? 'is-focused' : ''
                  }`}
                  data-flow-event-id={event.id}
                  tabIndex={focusedEventId === event.id ? -1 : undefined}
                  key={event.id}
                >
                  <div className="processing-flow-rail" aria-hidden="true">
                    <span />
                    <time>{formatClockTime(event.createdAt, locale)}</time>
                  </div>
                  <article
                    aria-label={text(
                      `${text(...levelCopy[event.level].label)}日志，${event.stageLabel}${event.errorType ? `，${event.errorType}` : ''}`,
                      `${text(...levelCopy[event.level].label)} log, ${event.stageLabel}${event.errorType ? `, ${event.errorType}` : ''}`,
                    )}
                  >
                    <header>
                      <div className="processing-flow-event-tags">
                        <span className="processing-flow-stage-name" title={event.stage}>
                          {event.stageLabel}
                        </span>
                        {event.state && (
                          <span className={`processing-flow-state state-${event.state}`}>
                            {text(...stateCopy[event.state])}
                          </span>
                        )}
                        {event.progress !== undefined && (
                          <span className="processing-flow-progress">
                            {formatProgress(event.progress)}
                          </span>
                        )}
                        {event.errorType && (
                          <span
                            className="processing-flow-error-type"
                            title={text('后端返回的安全异常类型', 'Safe exception type returned by the backend')}
                          >
                            {event.errorType}
                          </span>
                        )}
                      </div>
                      <span className="processing-flow-sequence">#{sequenceLabel}</span>
                    </header>
                    <p>{event.message}</p>
                    {metricEntries.length > 0 && (
                      <button
                        className="processing-flow-metrics-toggle"
                        type="button"
                        aria-expanded={expanded}
                        aria-controls={metricId}
                        aria-label={text(
                          `${expanded ? '收起' : '展开'}事件 #${sequenceLabel} 指标`,
                          `${expanded ? 'Collapse' : 'Expand'} metrics for event #${sequenceLabel}`,
                        )}
                        onClick={() => toggleMetrics(event.id)}
                      >
                        <ChevronDown size={14} aria-hidden="true" />
                        {metricEntries.length} {text('项指标', metricEntries.length === 1 ? 'metric' : 'metrics')}
                      </button>
                    )}
                    {expanded && metricEntries.length > 0 && (
                      <dl className="processing-flow-metrics" id={metricId}>
                        {metricEntries.map(([key, value]) => (
                          <div key={key}>
                            <dt title={key}>{key.replaceAll('_', ' ')}</dt>
                            <dd>{formatMetricValue(value, locale, text)}</dd>
                          </div>
                        ))}
                      </dl>
                    )}
                  </article>
                  {event.level === 'warning' && (
                    <AlertTriangle className="processing-flow-level-icon" size={15} aria-hidden="true" />
                  )}
                  {event.level === 'error' && (
                    <CircleAlert className="processing-flow-level-icon" size={15} aria-hidden="true" />
                  )}
                </li>
              )
            })}
          </ol>
        )}
      </div>
    </section>
  )
}

export const RunEventLog = ProcessingFlowPanel

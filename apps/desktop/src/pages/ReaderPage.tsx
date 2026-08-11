import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  CheckCircle2,
  Download,
  Eye,
  FileCode2,
  FileOutput,
  FileText,
  Focus,
  Image,
  Paperclip,
  Printer,
  Search,
  Sparkles,
  Wrench,
} from 'lucide-react'
import { DetailedEvidenceStudio } from '../components/DetailedEvidenceStudio'
import { EvidenceRail } from '../components/EvidenceRail'
import { MotionPresence } from '../components/MotionPresence'
import {
  ReportRevisionDrawer,
  type ReportRevisionGenerateRequest,
  type ReportRevisionSummary,
} from '../components/ReportRevisionDrawer'
import { ReworkDrawer } from '../components/ReworkDrawer'
import { RunDiagnosticsPanel } from '../components/RunDiagnosticsPanel'
import { SupportingMaterialsDrawer } from '../components/SupportingMaterialsDrawer'
import { SynchronizedVideo } from '../components/SynchronizedVideo'
import { VisualAsset } from '../components/VisualAsset'
import { usePopoverFocus } from '../components/usePopoverFocus'
import type { EvidenceKind, NoteDocument, ProcessingTask } from '../domain'
import { formatTime } from '../domain'
import { useI18n } from '../i18n'
import { useStudioStore } from '../store'
import { useUiPreferences } from '../stores/uiPreferences'

const buildMarkdown = (
  task: ProcessingTask,
  note: NoteDocument,
  labels: { selectedFrame: string; source: string },
) => {
  const sections = note.sections
    .map(section => {
      const claims = section.claims
        .map(
          claim =>
            `- ${claim.text} ([${formatTime(claim.timeSeconds)}](#t=${Math.floor(
              claim.timeSeconds,
            )})) <!-- evidence: ${claim.evidenceIds.join(', ')} -->`,
        )
        .join('\n')
      const screenshot =
        section.screenshotAt !== undefined
          ? `\n\n![${labels.selectedFrame}](${
              section.screenshotPath || `assets/frame-${Math.floor(section.screenshotAt)}.jpg`
            })\n`
        : ''
      return `## ${section.title}\n\n${section.summary}${screenshot}\n${claims}`
    })
    .join('\n\n')
  return `# ${note.title}\n\n> ${note.sourceSummary}\n\n${note.overview}\n\n${sections}\n\n---\n\n${labels.source}: ${task.source.sourceLabel}\n`
}

const downloadText = (name: string, content: string, type: string) => {
  const blob = new Blob([content], { type })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = name
  anchor.click()
  URL.revokeObjectURL(url)
}

export function ReaderPage() {
  const { text } = useI18n()
  const tasks = useStudioStore(state => state.tasks)
  const activeTaskId = useStudioStore(state => state.activeTaskId)
  const currentTimeSeconds = useStudioStore(state => state.currentTimeSeconds)
  const selectedEvidenceId = useStudioStore(state => state.selectedEvidenceId)
  const setCurrentTime = useStudioStore(state => state.setCurrentTime)
  const selectEvidence = useStudioStore(state => state.selectEvidence)
  const toggleContext = useStudioStore(state => state.toggleContext)
  const downloadArtifact = useStudioStore(state => state.downloadArtifact)
  const downloadRunArtifact = useStudioStore(state => state.downloadRunArtifact)
  const restartTask = useStudioStore(state => state.restartTask)
  const navigate = useStudioStore(state => state.navigate)
  const refreshReportRevisions = useStudioStore(
    state => state.refreshReportRevisions,
  )
  const generateReportRevision = useStudioStore(
    state => state.generateReportRevision,
  )
  const downloadReportRevisionArtifact = useStudioStore(
    state => state.downloadReportRevisionArtifact,
  )
  const workspaceMode = useUiPreferences(state => state.workspaceMode)
  const [query, setQuery] = useState('')
  const [evidenceFilter, setEvidenceFilter] = useState<EvidenceKind | 'all'>('all')
  const [exportOpen, setExportOpen] = useState(false)
  const [materialsOpen, setMaterialsOpen] = useState(false)
  const [reworkOpen, setReworkOpen] = useState(false)
  const [reportOpen, setReportOpen] = useState(false)
  const [reportBusy, setReportBusy] = useState(false)
  const [reportError, setReportError] = useState<string>()
  const [reworkRange, setReworkRange] = useState<{
    startSeconds: number
    endSeconds: number
  }>()
  const exportButtonRef = useRef<HTMLButtonElement>(null)
  const exportMenuRef = useRef<HTMLDivElement>(null)
  const closeExport = useCallback(() => setExportOpen(false), [])
  const dismissExport = usePopoverFocus({
    open: exportOpen,
    triggerRef: exportButtonRef,
    panelRef: exportMenuRef,
    onDismiss: closeExport,
  })

  const activeTask = tasks.find(task => task.id === activeTaskId)
  const terminalTask =
    activeTask &&
    !activeTask.note &&
    (activeTask.status === 'failed' || activeTask.status === 'cancelled')
      ? activeTask
      : undefined
  const task = activeTask?.note ? activeTask : tasks.find(item => item.note)
  const note = task?.note

  const visibleSections = useMemo(() => {
    if (!note || !query.trim()) return note?.sections ?? []
    const normalized = query.toLowerCase()
    return note.sections.filter(section =>
      `${section.title} ${section.summary} ${section.claims.map(claim => claim.text).join(' ')}`
        .toLowerCase()
        .includes(normalized),
    )
  }, [note, query])

  const selectedEvidence = task?.evidence.find(item => item.id === selectedEvidenceId)
  const evidenceList =
    task?.evidence.filter(item => evidenceFilter === 'all' || item.kind === evidenceFilter) ?? []

  useEffect(() => {
    if (!materialsOpen && !reworkOpen && !reportOpen) return

    const dismissTopLayer = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      if (reportOpen) setReportOpen(false)
      else if (reworkOpen) setReworkOpen(false)
      else setMaterialsOpen(false)
    }

    document.addEventListener('keydown', dismissTopLayer)
    return () => document.removeEventListener('keydown', dismissTopLayer)
  }, [materialsOpen, reportOpen, reworkOpen])

  if (terminalTask) {
    return (
      <div className="reader-page reader-recovery-page">
        <RunDiagnosticsPanel
          task={terminalTask}
          onRetry={() => restartTask(terminalTask.id)}
          onCreateNew={() => navigate('create')}
          onDownloadArtifact={artifact =>
            downloadRunArtifact(terminalTask.id, artifact)
          }
        />
      </div>
    )
  }

  if (!task || !note) {
    return (
      <div className="empty-workspace">
        <VisualAsset
          className="empty-workspace-visual"
          asset="emptyReader"
          width={640}
          height={640}
        />
        <h2>{text('还没有可阅读的笔记', 'No readable notes yet')}</h2>
        <p>{text('任务完成并通过验证后，Markdown 和证据会出现在这里。', 'Markdown and supporting evidence appear here after a task completes and passes verification.')}</p>
      </div>
    )
  }

  const exportMarkdown = () => {
    dismissExport('action')
    if (task.realBackend) {
      downloadArtifact(task.id, 'markdown')
      return
    }
    downloadText(
      'video2notes-demo.md',
      buildMarkdown(task, note, {
        selectedFrame: text('精选关键帧', 'Selected keyframe'),
        source: text('来源', 'Source'),
      }),
      'text/markdown;charset=utf-8',
    )
  }
  const exportHtml = () => {
    dismissExport('action')
    if (task.realBackend) {
      downloadArtifact(task.id, 'html')
      return
    }
    const markdown = buildMarkdown(task, note, {
      selectedFrame: text('精选关键帧', 'Selected keyframe'),
      source: text('来源', 'Source'),
    })
    const escaped = markdown.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    downloadText(
      'video2notes-demo.html',
      `<!doctype html><meta charset="utf-8"><title>${note.title}</title><pre>${escaped}</pre>`,
      'text/html;charset=utf-8',
    )
  }
  const openRework = () => {
    dismissExport('transfer')
    setMaterialsOpen(false)
    setReportOpen(false)
    const startSeconds =
      selectedEvidence?.startSeconds ?? Math.max(0, currentTimeSeconds)
    const endSeconds =
      selectedEvidence?.endSeconds ??
      Math.min(task.source.durationSeconds, startSeconds + 60)
    setReworkRange({
      startSeconds,
      endSeconds:
        endSeconds > startSeconds
          ? endSeconds
          : Math.min(task.source.durationSeconds, startSeconds + 0.1),
    })
    setReworkOpen(true)
  }
  const openMaterials = () => {
    dismissExport('transfer')
    setReworkOpen(false)
    setReportOpen(false)
    setMaterialsOpen(true)
  }
  const openReport = () => {
    dismissExport('transfer')
    setMaterialsOpen(false)
    setReworkOpen(false)
    setReportOpen(true)
    setReportError(undefined)
    if (!task.realBackend) return
    setReportBusy(true)
    void refreshReportRevisions(task.id)
      .catch(error =>
        setReportError(
          error instanceof Error ? error.message : text('报告历史刷新失败。', 'Could not refresh report history.'),
        ),
      )
      .finally(() => setReportBusy(false))
  }
  const generateReport = (request: ReportRevisionGenerateRequest) => {
    setReportBusy(true)
    setReportError(undefined)
    void generateReportRevision(task.id, request)
      .catch(error =>
        setReportError(
          error instanceof Error ? error.message : text('报告生成失败。', 'Could not generate the report.'),
        ),
      )
      .finally(() => setReportBusy(false))
  }
  const reportRevisions: ReportRevisionSummary[] = (
    task.reportRevisions ?? []
  ).map(revision => ({
    id: revision.id,
    preset: revision.preset,
    createdAt: revision.createdAt,
    formats: revision.formats,
    fallback: revision.fallback,
    warnings: revision.warnings,
    evidenceRevisionId: revision.evidenceRevisionId,
    artifactPaths: revision.artifactPaths,
  }))

  return (
    <div className="reader-page">
      <div className="reader-toolbar">
        <label className="reader-search">
          <Search size={15} aria-hidden="true" />
          <span className="sr-only">{text('搜索当前笔记', 'Search this note')}</span>
          <input
            value={query}
            onChange={event => setQuery(event.target.value)}
            placeholder={text('搜索当前笔记', 'Search this note')}
          />
        </label>
        <div className="reader-toolbar-actions">
          <button
            className="button button-quiet"
            type="button"
            onClick={openRework}
          >
            <Wrench size={15} aria-hidden="true" />
            {text('局部返工', 'Reprocess selection')}
          </button>
          <button
            className="button button-quiet"
            type="button"
            onClick={openMaterials}
          >
            <Paperclip size={15} aria-hidden="true" />
            {text('补充资料', 'Supporting materials')}
            {task.materials.length > 0 && (
              <span className="button-count">{task.materials.length}</span>
            )}
          </button>
          <button
            className="button button-quiet"
            type="button"
            onClick={openReport}
          >
            <FileOutput size={15} aria-hidden="true" />
            {text('生成报告', 'Generate report')}
          </button>
          <button className="button button-quiet" type="button" onClick={toggleContext}>
            <Focus size={15} aria-hidden="true" />
            {text('专注阅读', 'Focus reading')}
          </button>
          <div className="export-control">
            <button
              ref={exportButtonRef}
              className="button button-secondary"
              type="button"
              onClick={() => setExportOpen(value => !value)}
              aria-expanded={exportOpen}
              aria-controls="reader-export-menu"
              aria-haspopup="menu"
            >
              <Download size={15} aria-hidden="true" />
              {text('导出', 'Export')}
            </button>
            <MotionPresence
              show={exportOpen}
              className="motion-presence-popover"
              exitMs={120}
            >
              <div
                ref={exportMenuRef}
                className="export-menu"
                id="reader-export-menu"
                role="menu"
                aria-label={text('导出格式', 'Export formats')}
              >
                <button type="button" role="menuitem" onClick={exportMarkdown}>
                  <FileText size={15} aria-hidden="true" />
                  Markdown
                </button>
                <button type="button" role="menuitem" onClick={exportHtml}>
                  <FileCode2 size={15} aria-hidden="true" />
                  {text('离线 HTML', 'Offline HTML')}
                </button>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    dismissExport('action')
                    if (task.realBackend && task.artifactPaths?.pdf) {
                      downloadArtifact(task.id, 'pdf')
                    } else {
                      window.print()
                    }
                  }}
                >
                  <Printer size={15} aria-hidden="true" />
                  {text('打印 / PDF', 'Print / PDF')}
                </button>
              </div>
            </MotionPresence>
          </div>
        </div>
      </div>

      <div className="reader-mode-stack">
        <MotionPresence
          show={workspaceMode === 'professional'}
          className="motion-presence-reader-mode"
          exitMs={140}
          animateInitial={false}
        >
          {workspaceMode === 'professional' && (
            <div className="reader-mode-surface">
              <DetailedEvidenceStudio
                task={task}
                note={note}
                currentTimeSeconds={currentTimeSeconds}
                selectedEvidenceId={selectedEvidenceId}
                onSeek={setCurrentTime}
                onSelectEvidence={selectEvidence}
                onRequestRework={(startSeconds, endSeconds) => {
                  setReworkRange({ startSeconds, endSeconds })
                  setReworkOpen(true)
                }}
              />
            </div>
          )}
        </MotionPresence>
        <MotionPresence
          show={workspaceMode === 'guided'}
          className="motion-presence-reader-mode"
          exitMs={140}
          animateInitial={false}
        >
          {workspaceMode === 'guided' && (
            <div className="reader-mode-surface">
              <div className="reader-split">
        <article className="note-paper">
          <header className="note-title-block" id="note-overview">
            <span className="section-kicker">VERIFIED NOTE / {note.generatedAt}</span>
            <h2>{note.title}</h2>
            <p className="note-source">{note.sourceSummary}</p>
            <p className="note-overview">{note.overview}</p>
            <div className="note-verification">
              <CheckCircle2 size={16} aria-hidden="true" />
              <span>
                <strong>{text('事实支持度检查已通过', 'Evidence support check passed')}</strong>
                {text('关键 claim 均包含可跳转的原始证据', 'Every key claim links to its original evidence')}
              </span>
            </div>
          </header>

          {visibleSections.map((section, sectionIndex) => (
            <section className="note-section" id={section.id} key={section.id}>
              <div className="note-section-index">
                {(sectionIndex + 1).toString().padStart(2, '0')}
              </div>
              <h3>{section.title}</h3>
              <p>{section.summary}</p>
              {section.screenshotAt !== undefined && (
                <button
                  type="button"
                  className="note-frame"
                  onClick={() => setCurrentTime(section.screenshotAt ?? 0)}
                  aria-label={text(`跳转到精选关键帧 ${formatTime(section.screenshotAt)}`, `Jump to selected keyframe ${formatTime(section.screenshotAt)}`)}
                >
                  <span className="frame-toolbar">
                    <span>
                      <Image size={14} aria-hidden="true" />
                      {text('精选关键帧', 'Selected keyframe')}
                    </span>
                    <span>{formatTime(section.screenshotAt)}</span>
                  </span>
                  {section.screenshotUrl ? (
                    <img
                      className="frame-image"
                      src={section.screenshotUrl}
                      alt={section.screenshotCaption || text(`精选关键帧：${section.title}`, `Selected keyframe: ${section.title}`)}
                      loading="lazy"
                      decoding="async"
                    />
                  ) : (
                    <span className="frame-content">
                      <small>{text('规范时间线', 'CANONICAL TIMELINE')}</small>
                      <strong>{section.title}</strong>
                      <span className="frame-flow" aria-hidden="true">
                        <i>ASR</i>
                        <b />
                        <i>OCR</i>
                        <b />
                        <i>{text('论点', 'CLAIM')}</i>
                      </span>
                    </span>
                  )}
                  <span className="frame-caption">
                    {section.screenshotCaption || text(
                      '在画面稳定后选取的原分辨率截图',
                      'Original-resolution frame selected after the image stabilized',
                    )}{' '}
                    · {text('点击同步视频', 'Select to sync the video')}
                  </span>
                </button>
              )}
              <div className="claim-list">
                {section.claims.map(claim => {
                  const active = claim.evidenceIds.includes(selectedEvidenceId ?? '')
                  return (
                    <button
                      type="button"
                      className={`claim-row ${claim.emphasis ? 'is-emphasis' : ''} ${
                        active ? 'is-active' : ''
                      }`}
                      key={claim.id}
                      onClick={() => selectEvidence(claim.evidenceIds[0], claim.timeSeconds)}
                    >
                      <span className="claim-evidence">E·{claim.evidenceIds[0].split('-').at(-1)}</span>
                      <span>{claim.text}</span>
                      <time>{formatTime(claim.timeSeconds)}</time>
                    </button>
                  )
                })}
              </div>
            </section>
          ))}
          {visibleSections.length === 0 && (
            <div className="note-no-results">
              <Search size={18} aria-hidden="true" />
              {text(`当前笔记中没有“${query}”`, `No matches for “${query}” in this note`)}
            </div>
          )}
        </article>

        <aside className="evidence-dock" aria-label={text('证据检查台', 'Evidence inspector')}>
          <SynchronizedVideo
            title={task.source.title}
            durationSeconds={task.source.durationSeconds}
            currentTimeSeconds={currentTimeSeconds}
            onSeek={setCurrentTime}
            src={task.mediaUrl}
            ocrConfidence={task.evidence.find(item => item.kind === 'ocr')?.confidence}
          />
          <EvidenceRail
            evidence={task.evidence}
            durationSeconds={task.source.durationSeconds}
            currentTimeSeconds={currentTimeSeconds}
            selectedEvidenceId={selectedEvidenceId}
            onSeek={setCurrentTime}
            onSelect={selectEvidence}
          />
          <section className="evidence-inspector">
            <div className="evidence-tabs" role="tablist" aria-label={text('证据类型', 'Evidence types')}>
              {(
                [
                  ['all', text('全部', 'All')],
                  ['asr', 'ASR'],
                  ['ocr', 'OCR'],
                  ['visual', text('视觉', 'Visual')],
                ] as const
              ).map(([value, label]) => (
                <button
                  type="button"
                  role="tab"
                  id={`evidence-tab-${value}`}
                  aria-controls="evidence-list-panel"
                  aria-selected={evidenceFilter === value}
                  key={value}
                  onClick={() => setEvidenceFilter(value)}
                >
                  {label}
                </button>
              ))}
            </div>
            {selectedEvidence && (
              <div className="selected-evidence">
                <div>
                  <span>{selectedEvidence.id}</span>
                  <strong>{selectedEvidence.label}</strong>
                </div>
                <p>{selectedEvidence.rawText}</p>
                <dl>
                  <div>
                    <dt>{text('时间', 'Time')}</dt>
                    <dd>{formatTime(selectedEvidence.startSeconds)}</dd>
                  </div>
                  <div>
                    <dt>{text('置信度', 'Confidence')}</dt>
                    <dd>{selectedEvidence.confidence.toFixed(2)}</dd>
                  </div>
                  <div>
                    <dt>{text('来源', 'Source')}</dt>
                    <dd>{selectedEvidence.provider}</dd>
                  </div>
                </dl>
              </div>
            )}
            <div
              className="evidence-list"
              role="tabpanel"
              id="evidence-list-panel"
              aria-labelledby={`evidence-tab-${evidenceFilter}`}
            >
              {evidenceList.slice(0, 8).map(item => (
                <button
                  type="button"
                  className={item.id === selectedEvidenceId ? 'is-selected' : ''}
                  key={item.id}
                  onClick={() => selectEvidence(item.id, item.startSeconds)}
                >
                  <span className={`evidence-kind kind-${item.kind}`}>{item.kind.slice(0, 1)}</span>
                  <span>
                    <strong>{item.id}</strong>
                    <small>{item.label}</small>
                  </span>
                  <time>{formatTime(item.startSeconds)}</time>
                </button>
              ))}
            </div>
          </section>
          <div className="dock-footnote">
            <Eye size={14} aria-hidden="true" />
            <span>
              <strong>{text(`${task.evidence.length} 条主要证据`, `${task.evidence.length} primary evidence items`)}</strong>
              {text('原始候选与校正结果均保留', 'Original candidates and corrections are retained')}
            </span>
            <Sparkles size={15} aria-hidden="true" />
          </div>
                </aside>
              </div>
            </div>
          )}
        </MotionPresence>
      </div>
      <MotionPresence
        show={materialsOpen}
        className="motion-presence-overlay"
        exitMs={160}
        focusMode="modal"
      >
        <SupportingMaterialsDrawer
          task={task}
          initialStartSeconds={reworkRange?.startSeconds}
          initialEndSeconds={reworkRange?.endSeconds}
          onClose={() => setMaterialsOpen(false)}
        />
      </MotionPresence>
      <MotionPresence
        show={reworkOpen}
        className="motion-presence-overlay"
        exitMs={160}
        focusMode="modal"
      >
        <ReworkDrawer
          task={task}
          initialStartSeconds={reworkRange?.startSeconds}
          initialEndSeconds={reworkRange?.endSeconds}
          selectedEvidenceId={selectedEvidenceId}
          onClose={() => setReworkOpen(false)}
        />
      </MotionPresence>
      <MotionPresence
        show={reportOpen}
        className="motion-presence-overlay"
        exitMs={160}
        focusMode="modal"
      >
        <ReportRevisionDrawer
          taskTitle={task.source.title}
          materialsCount={task.materials.length}
          activeEvidenceRevisionId={
            task.evidenceRevisionId ?? 'base-evidence'
          }
          revisions={reportRevisions}
          busy={reportBusy}
          error={reportError}
          onClose={() => setReportOpen(false)}
          onGenerate={generateReport}
          onDownload={(revision, format) => {
            const path = revision.artifactPaths[format]
            if (path) {
              downloadReportRevisionArtifact(task.id, path, format)
            }
          }}
        />
      </MotionPresence>
    </div>
  )
}

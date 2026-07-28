import { useMemo, useState } from 'react'
import {
  CheckCircle2,
  Download,
  Eye,
  FileCode2,
  FileText,
  Focus,
  Image,
  Printer,
  Search,
  Sparkles,
} from 'lucide-react'
import { EvidenceRail } from '../components/EvidenceRail'
import { SynchronizedVideo } from '../components/SynchronizedVideo'
import type { EvidenceKind, NoteDocument, ProcessingTask } from '../domain'
import { formatTime } from '../domain'
import { useStudioStore } from '../store'

const buildMarkdown = (task: ProcessingTask, note: NoteDocument) => {
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
          ? `\n\n![精选关键帧](${
              section.screenshotPath || `assets/frame-${Math.floor(section.screenshotAt)}.jpg`
            })\n`
        : ''
      return `## ${section.title}\n\n${section.summary}${screenshot}\n${claims}`
    })
    .join('\n\n')
  return `# ${note.title}\n\n> ${note.sourceSummary}\n\n${note.overview}\n\n${sections}\n\n---\n\n来源：${task.source.sourceLabel}\n`
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
  const tasks = useStudioStore(state => state.tasks)
  const activeTaskId = useStudioStore(state => state.activeTaskId)
  const currentTimeSeconds = useStudioStore(state => state.currentTimeSeconds)
  const selectedEvidenceId = useStudioStore(state => state.selectedEvidenceId)
  const setCurrentTime = useStudioStore(state => state.setCurrentTime)
  const selectEvidence = useStudioStore(state => state.selectEvidence)
  const toggleContext = useStudioStore(state => state.toggleContext)
  const downloadArtifact = useStudioStore(state => state.downloadArtifact)
  const [query, setQuery] = useState('')
  const [evidenceFilter, setEvidenceFilter] = useState<EvidenceKind | 'all'>('all')
  const [exportOpen, setExportOpen] = useState(false)

  const activeTask = tasks.find(task => task.id === activeTaskId)
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

  if (!task || !note) {
    return (
      <div className="empty-workspace">
        <FileText size={26} aria-hidden="true" />
        <h2>还没有可阅读的笔记</h2>
        <p>任务完成并通过验证后，Markdown 和证据会出现在这里。</p>
      </div>
    )
  }

  const exportMarkdown = () => {
    if (task.realBackend) {
      downloadArtifact(task.id, 'markdown')
      return
    }
    downloadText('video2notes-demo.md', buildMarkdown(task, note), 'text/markdown;charset=utf-8')
  }
  const exportHtml = () => {
    if (task.realBackend) {
      downloadArtifact(task.id, 'html')
      return
    }
    const markdown = buildMarkdown(task, note)
    const escaped = markdown.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    downloadText(
      'video2notes-demo.html',
      `<!doctype html><meta charset="utf-8"><title>${note.title}</title><pre>${escaped}</pre>`,
      'text/html;charset=utf-8',
    )
  }

  return (
    <div className="reader-page">
      <div className="reader-toolbar">
        <label className="reader-search">
          <Search size={15} aria-hidden="true" />
          <span className="sr-only">搜索当前笔记</span>
          <input
            value={query}
            onChange={event => setQuery(event.target.value)}
            placeholder="搜索当前笔记"
          />
        </label>
        <div className="reader-toolbar-actions">
          <button className="button button-quiet" type="button" onClick={toggleContext}>
            <Focus size={15} aria-hidden="true" />
            专注阅读
          </button>
          <div className="export-control">
            <button
              className="button button-secondary"
              type="button"
              onClick={() => setExportOpen(value => !value)}
              aria-expanded={exportOpen}
            >
              <Download size={15} aria-hidden="true" />
              导出
            </button>
            {exportOpen && (
              <div className="export-menu">
                <button type="button" onClick={exportMarkdown}>
                  <FileText size={15} aria-hidden="true" />
                  Markdown
                </button>
                <button type="button" onClick={exportHtml}>
                  <FileCode2 size={15} aria-hidden="true" />
                  离线 HTML
                </button>
                <button
                  type="button"
                  onClick={() =>
                    task.realBackend && task.artifactPaths?.pdf
                      ? downloadArtifact(task.id, 'pdf')
                      : window.print()
                  }
                >
                  <Printer size={15} aria-hidden="true" />
                  打印 / PDF
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

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
                <strong>事实支持度检查已通过</strong>
                关键 claim 均包含可跳转的原始证据
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
                  aria-label={`跳转到精选关键帧 ${formatTime(section.screenshotAt)}`}
                >
                  <span className="frame-toolbar">
                    <span>
                      <Image size={14} aria-hidden="true" />
                      精选关键帧
                    </span>
                    <span>{formatTime(section.screenshotAt)}</span>
                  </span>
                  {section.screenshotUrl ? (
                    <img
                      className="frame-image"
                      src={section.screenshotUrl}
                      alt={section.screenshotCaption || `精选关键帧：${section.title}`}
                    />
                  ) : (
                    <span className="frame-content">
                      <small>CANONICAL TIMELINE</small>
                      <strong>{section.title}</strong>
                      <span className="frame-flow" aria-hidden="true">
                        <i>ASR</i>
                        <b />
                        <i>OCR</i>
                        <b />
                        <i>CLAIM</i>
                      </span>
                    </span>
                  )}
                  <span className="frame-caption">
                    {section.screenshotCaption ||
                      '在画面稳定后选取的原分辨率截图'}{' '}
                    · 点击同步视频
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
              当前笔记中没有“{query}”
            </div>
          )}
        </article>

        <aside className="evidence-dock" aria-label="证据检查台">
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
            <div className="evidence-tabs" role="tablist" aria-label="证据类型">
              {(
                [
                  ['all', '全部'],
                  ['asr', 'ASR'],
                  ['ocr', 'OCR'],
                  ['visual', '视觉'],
                ] as const
              ).map(([value, label]) => (
                <button
                  type="button"
                  role="tab"
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
                    <dt>时间</dt>
                    <dd>{formatTime(selectedEvidence.startSeconds)}</dd>
                  </div>
                  <div>
                    <dt>置信度</dt>
                    <dd>{selectedEvidence.confidence.toFixed(2)}</dd>
                  </div>
                  <div>
                    <dt>来源</dt>
                    <dd>{selectedEvidence.provider}</dd>
                  </div>
                </dl>
              </div>
            )}
            <div className="evidence-list">
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
              <strong>{task.evidence.length} 条主要证据</strong>
              原始候选与校正结果均保留
            </span>
            <Sparkles size={15} aria-hidden="true" />
          </div>
        </aside>
      </div>
    </div>
  )
}

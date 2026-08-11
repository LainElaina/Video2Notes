import { useId, useState } from 'react'
import {
  AlertTriangle,
  Check,
  Clock3,
  Download,
  FileCode2,
  FileText,
  Layers3,
  LoaderCircle,
  RefreshCcw,
  X,
} from 'lucide-react'
import { useI18n } from '../i18n'
import { MotionPresence } from './MotionPresence'
import { VisualAsset } from './VisualAsset'

export type ReportRevisionPreset =
  | 'concise'
  | 'detailed'
  | 'professional'
  | 'beginner'
  | 'executive'

export type ReportRevisionFormat = 'markdown' | 'html' | 'pdf'

export interface ReportRevisionArtifactPaths {
  markdown?: string
  html?: string
  pdf?: string
}

export interface ReportRevisionSummary {
  id: string
  preset: ReportRevisionPreset
  createdAt: string
  formats: ReportRevisionFormat[]
  fallback: boolean
  warnings: string[]
  evidenceRevisionId: string
  artifactPaths: ReportRevisionArtifactPaths
}

export interface ReportRevisionGenerateRequest {
  preset: ReportRevisionPreset
  includeScreenshots: boolean
  includePdf: boolean
}

export interface ReportRevisionDrawerProps {
  taskTitle: string
  materialsCount: number
  activeEvidenceRevisionId: string
  revisions: ReportRevisionSummary[]
  busy: boolean
  error?: string
  onClose: () => void
  onGenerate: (request: ReportRevisionGenerateRequest) => void
  onDownload: (
    revision: ReportRevisionSummary,
    format: ReportRevisionFormat,
  ) => void
}

interface PresetOption {
  value: ReportRevisionPreset
  label: readonly [string, string]
  description: readonly [string, string]
}

const presetOptions: PresetOption[] = [
  {
    value: 'concise',
    label: ['简洁', 'Concise'],
    description: ['压缩背景，只保留结论、必要依据与最短上下文。', 'Compress background context and retain only conclusions, essential evidence, and the shortest necessary context.'],
  },
  {
    value: 'detailed',
    label: ['详细', 'Detailed'],
    description: ['完整覆盖时间顺序、术语、限制与关键证据。', 'Cover chronology, terminology, limitations, and key evidence in full.'],
  },
  {
    value: 'professional',
    label: ['专业', 'Professional'],
    description: ['突出方法、数据、假设、冲突与不确定性。', 'Emphasize methods, data, assumptions, conflicts, and uncertainty.'],
  },
  {
    value: 'beginner',
    label: ['入门', 'Beginner'],
    description: ['解释术语和推理步骤，但不补造视频之外的背景。', 'Explain terminology and reasoning steps without inventing context beyond the video.'],
  },
  {
    value: 'executive',
    label: ['领导', 'Executive'],
    description: ['优先呈现结论、影响、风险、决策点与行动项。', 'Prioritize conclusions, impact, risks, decision points, and action items.'],
  },
]

const formatOrder: ReportRevisionFormat[] = ['markdown', 'html', 'pdf']

const formatLabels: Record<ReportRevisionFormat, string> = {
  markdown: 'Markdown',
  html: 'HTML',
  pdf: 'PDF',
}

const formatCreatedAt = (value: string, locale: string) => {
  const timestamp = new Date(value)
  if (Number.isNaN(timestamp.getTime())) return value
  return new Intl.DateTimeFormat(locale, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(timestamp)
}

const compactId = (value: string) =>
  value.length > 18 ? `${value.slice(0, 8)}…${value.slice(-6)}` : value

const artifactFileName = (path: string) =>
  path.split(/[\\/]/).filter(Boolean).at(-1) ?? path

export function ReportRevisionDrawer({
  taskTitle,
  materialsCount,
  activeEvidenceRevisionId,
  revisions,
  busy,
  error,
  onClose,
  onGenerate,
  onDownload,
}: ReportRevisionDrawerProps) {
  const { locale, text } = useI18n()
  const titleId = useId()
  const descriptionId = useId()
  const presetName = useId()
  const [preset, setPreset] = useState<ReportRevisionPreset>('detailed')
  const [includeScreenshots, setIncludeScreenshots] = useState(true)
  const [includePdf, setIncludePdf] = useState(true)
  const selectedPreset =
    presetOptions.find(option => option.value === preset) ?? presetOptions[1]
  const safeMaterialsCount = Math.max(0, Math.trunc(materialsCount))

  return (
    <div className="workbench-overlay report-revision-overlay">
      <button
        type="button"
        className="workbench-scrim"
        aria-hidden="true"
        tabIndex={-1}
        onClick={onClose}
      />
      <aside
        className="workbench-drawer report-revision-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        aria-busy={busy}
        tabIndex={-1}
      >
        <header className="workbench-drawer-header report-revision-header">
          <div>
            <span className="section-kicker">{text('报告版本', 'REPORT REVISION')}</span>
            <h2 id={titleId}>{text('重新生成报告', 'Regenerate report')}</h2>
            <p id={descriptionId}>
              {text('复用已经完成的证据分析，只重新组织笔记内容与输出格式。', 'Reuse the completed evidence analysis and reorganize only the note content and output formats.')}
            </p>
            <strong className="report-revision-task" title={taskTitle}>
              {taskTitle}
            </strong>
          </div>
          <button type="button" onClick={onClose} aria-label={text('关闭报告重生成面板', 'Close report regeneration panel')}>
            <X size={18} aria-hidden="true" />
          </button>
        </header>

        <div className="workbench-drawer-body">
          <section className="report-revision-inputs">
            <div className="drawer-section-heading">
              <div>
                <span>{text('输入快照', 'INPUT SNAPSHOT')}</span>
                <h3>{text('本次报告使用的输入', 'Inputs for this report')}</h3>
              </div>
              <Layers3 size={16} aria-hidden="true" />
            </div>
            <div className="report-revision-route">
              <div>
                <span>{text('有效证据 revision', 'Active evidence revision')}</span>
                <strong title={activeEvidenceRevisionId}>
                  {compactId(activeEvidenceRevisionId)}
                </strong>
                <small>{text('语音、字幕、OCR 与关键帧沿用此版本', 'Speech, captions, OCR, and keyframes use this revision')}</small>
              </div>
              <div>
                <span>{text('补充资料', 'Supporting materials')}</span>
                <strong>{safeMaterialsCount} {text('份', safeMaterialsCount === 1 ? 'item' : 'items')}</strong>
                <small>
                  {safeMaterialsCount > 0
                    ? text('当前有效资料会作为外部补充依据参与生成', 'Active materials will be included as external supporting evidence')
                    : text('当前没有额外文字或图片资料', 'There are no additional text or image materials')}
                </small>
              </div>
            </div>
            <p className="report-revision-scope-note">
              <Check size={14} aria-hidden="true" />
              {text('当前有效证据与补充资料都会参与；此操作不会重新下载视频，也不会覆盖既有报告。', 'The active evidence and supporting materials will both be used. This does not download the video again or overwrite existing reports.')}
            </p>
          </section>

          <MotionPresence
            show={Boolean(error)}
            className="motion-presence-inline-feedback"
            exitMs={120}
            animateInitial={false}
          >
            {error && (
              <section className="report-revision-error" role="alert">
                <AlertTriangle size={17} aria-hidden="true" />
                <div>
                  <strong>{text('报告生成失败', 'Report generation failed')}</strong>
                  <p>{text(`底层详情：${error}`, `Technical details: ${error}`)}</p>
                </div>
              </section>
            )}
          </MotionPresence>

          <section className="report-revision-contract">
            <div className="drawer-section-heading">
              <div>
                <span>{text('写作配置', 'WRITING PROFILE')}</span>
                <h3>{text('选择报告类型', 'Choose a report type')}</h3>
              </div>
              <FileText size={16} aria-hidden="true" />
            </div>
            <div
              className="report-revision-presets"
              role="radiogroup"
              aria-label={text('报告类型', 'Report type')}
            >
              {presetOptions.map((option, index) => {
                const checked = preset === option.value
                return (
                  <label
                    className={`report-revision-preset ${checked ? 'is-selected' : ''}`}
                    key={option.value}
                  >
                    <input
                      type="radio"
                      name={presetName}
                      value={option.value}
                      checked={checked}
                      disabled={busy}
                      onChange={() => setPreset(option.value)}
                    />
                    <span className="report-revision-preset-index">
                      {(index + 1).toString().padStart(2, '0')}
                    </span>
                    <span>
                      <strong>{text(...option.label)}</strong>
                      <small>{text(...option.description)}</small>
                    </span>
                    <Check
                      className="report-revision-preset-check"
                      size={15}
                      aria-hidden="true"
                    />
                  </label>
                )
              })}
            </div>
          </section>

          <section className="report-revision-outputs">
            <div className="drawer-section-heading">
              <div>
                <span>{text('输出约定', 'OUTPUT CONTRACT')}</span>
                <h3>{text('输出与图像', 'Outputs and images')}</h3>
              </div>
              <FileCode2 size={16} aria-hidden="true" />
            </div>
            <div className="report-revision-output-grid">
              <div className="report-revision-output is-locked">
                <Check size={15} aria-hidden="true" />
                <span>
                  <strong>Markdown</strong>
                  <small>{text('固定生成 · canonical 笔记', 'Always generated · canonical note')}</small>
                </span>
              </div>
              <div className="report-revision-output is-locked">
                <Check size={15} aria-hidden="true" />
                <span>
                  <strong>HTML</strong>
                  <small>{text('固定生成 · 浏览与打印来源', 'Always generated · source for browsing and printing')}</small>
                </span>
              </div>
              <label className="report-revision-output is-toggle">
                <input
                  type="checkbox"
                  checked={includePdf}
                  disabled={busy}
                  onChange={event => setIncludePdf(event.currentTarget.checked)}
                />
                <span>
                  <strong>PDF</strong>
                  <small>{text('生成可打印的本地离线版本', 'Generate a printable local offline version')}</small>
                </span>
              </label>
              <label className="report-revision-output is-toggle">
                <input
                  type="checkbox"
                  checked={includeScreenshots}
                  disabled={busy}
                  onChange={event =>
                    setIncludeScreenshots(event.currentTarget.checked)
                  }
                />
                <span>
                  <strong>{text('关键帧截图', 'Keyframe screenshots')}</strong>
                  <small>{text('只嵌入证据筛选后的画面', 'Embed only frames selected by the evidence pipeline')}</small>
                </span>
              </label>
            </div>
            <p className="report-revision-selection-note">
              {text('当前选择：', 'Current selection: ')}{text(...selectedPreset.label)}{text('报告', ' report')} · Markdown + HTML
              {includePdf ? ' + PDF' : ''} ·
              {includeScreenshots ? text(' 包含关键帧', ' includes keyframes') : text(' 不包含关键帧', ' excludes keyframes')}
            </p>
          </section>

          <section className="report-revision-history">
            <div className="drawer-section-heading">
              <div>
                <span>{text('不可变历史', 'IMMUTABLE HISTORY')}</span>
                <h3>{text('历史报告', 'Report history')} · {revisions.length}</h3>
              </div>
              <Clock3 size={16} aria-hidden="true" />
            </div>
            <div className="motion-swap-stack report-revision-history-stack">
              <MotionPresence
                show={revisions.length > 0}
                className="motion-presence-swap"
                exitMs={140}
                animateInitial={false}
              >
                {revisions.length > 0 && (
                  <div className="report-revision-list">
                {revisions.map((revision, index) => {
                  const isCurrentEvidence =
                    revision.evidenceRevisionId === activeEvidenceRevisionId
                  const availableFormats = formatOrder.filter(format =>
                    revision.formats.includes(format),
                  )
                  return (
                    <article className="report-revision-record" key={revision.id}>
                      <header>
                        <span className="report-revision-record-index">
                          R{(revisions.length - index).toString().padStart(2, '0')}
                        </span>
                        <div>
                          <strong>{text(...(presetOptions.find(option => option.value === revision.preset)?.label ?? presetOptions[1].label))}{text('报告', ' report')}</strong>
                          <span>{formatCreatedAt(revision.createdAt, locale)}</span>
                        </div>
                        <code title={revision.id}>{compactId(revision.id)}</code>
                      </header>
                      <div className="report-revision-record-meta">
                        <span
                          className={
                            isCurrentEvidence
                              ? 'is-current-evidence'
                              : 'is-historical-evidence'
                          }
                          title={revision.evidenceRevisionId}
                        >
                          {isCurrentEvidence ? text('当前证据', 'Current evidence') : text('历史证据', 'Historical evidence')} ·{' '}
                          {compactId(revision.evidenceRevisionId)}
                        </span>
                        {revision.fallback && (
                          <span className="is-fallback">{text('安全回退版本', 'Safe fallback version')}</span>
                        )}
                      </div>
                      {revision.warnings.length > 0 && (
                        <div className="report-revision-warnings">
                          <AlertTriangle size={14} aria-hidden="true" />
                          <ul>
                            {revision.warnings.map((warning, warningIndex) => (
                              <li key={`${revision.id}-warning-${warningIndex}`}>
                                {text(`警告：${warning}`, `Warning: ${warning}`)}
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                      <div className="report-revision-downloads">
                        {availableFormats.map(format => {
                          const artifactPath = revision.artifactPaths[format]
                          return (
                            <button
                              type="button"
                              key={format}
                              disabled={!artifactPath}
                              title={
                                artifactPath
                                  ? artifactPath
                                  : text(`${formatLabels[format]} 产物路径不可用`, `${formatLabels[format]} artifact path is unavailable`)
                              }
                              onClick={() => onDownload(revision, format)}
                            >
                              <Download size={14} aria-hidden="true" />
                              <span>
                                <strong>{formatLabels[format]}</strong>
                                <small>
                                  {artifactPath
                                    ? artifactFileName(artifactPath)
                                    : text('产物不可用', 'Artifact unavailable')}
                                </small>
                              </span>
                            </button>
                          )
                        })}
                      </div>
                    </article>
                  )
                })}
                  </div>
                )}
              </MotionPresence>
              <MotionPresence
                show={revisions.length === 0}
                className="motion-presence-swap"
                exitMs={140}
                animateInitial={false}
              >
                {revisions.length === 0 && (
                  <div className="report-revision-empty">
                    <VisualAsset className="inline-empty-visual" asset="emptyReportHistory" width={192} height={124} />
                    <div>
                      <RefreshCcw size={19} aria-hidden="true" />
                      <strong>{text('还没有重新生成的报告', 'No regenerated reports yet')}</strong>
                      <p>
                        {text('选择报告类型和输出格式后创建第一个不可变 revision；原始报告不会被覆盖。', 'Choose a report type and output formats to create the first immutable revision. The original report will not be overwritten.')}
                      </p>
                    </div>
                  </div>
                )}
              </MotionPresence>
            </div>
          </section>
        </div>

        <footer className="workbench-drawer-footer report-revision-footer">
          <span>
            {text('新报告将写入独立的本地 revision 目录。Markdown 与 HTML 始终生成。', 'The new report is written to a separate local revision directory. Markdown and HTML are always generated.')}
          </span>
          <button
            className="button button-primary"
            type="button"
            disabled={busy}
            onClick={() =>
              onGenerate({
                preset,
                includeScreenshots,
                includePdf,
              })
            }
          >
            {busy ? (
              <LoaderCircle
                className="report-revision-spinner"
                size={15}
                aria-hidden="true"
              />
            ) : (
              <RefreshCcw size={15} aria-hidden="true" />
            )}
            {busy ? text('正在生成…', 'Generating…') : text('生成新 revision', 'Generate new revision')}
          </button>
        </footer>
      </aside>
    </div>
  )
}

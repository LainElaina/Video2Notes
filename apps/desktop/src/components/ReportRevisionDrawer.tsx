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
  label: string
  description: string
}

const presetOptions: PresetOption[] = [
  {
    value: 'concise',
    label: '简洁',
    description: '压缩背景，只保留结论、必要依据与最短上下文。',
  },
  {
    value: 'detailed',
    label: '详细',
    description: '完整覆盖时间顺序、术语、限制与关键证据。',
  },
  {
    value: 'professional',
    label: '专业',
    description: '突出方法、数据、假设、冲突与不确定性。',
  },
  {
    value: 'beginner',
    label: '入门',
    description: '解释术语和推理步骤，但不补造视频之外的背景。',
  },
  {
    value: 'executive',
    label: '领导',
    description: '优先呈现结论、影响、风险、决策点与行动项。',
  },
]

const formatOrder: ReportRevisionFormat[] = ['markdown', 'html', 'pdf']

const formatLabels: Record<ReportRevisionFormat, string> = {
  markdown: 'Markdown',
  html: 'HTML',
  pdf: 'PDF',
}

const presetLabels = Object.fromEntries(
  presetOptions.map(option => [option.value, option.label]),
) as Record<ReportRevisionPreset, string>

const formatCreatedAt = (value: string) => {
  const timestamp = new Date(value)
  if (Number.isNaN(timestamp.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
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
        aria-label="关闭报告重生成面板"
        onClick={onClose}
      />
      <aside
        className="workbench-drawer report-revision-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        aria-busy={busy}
      >
        <header className="workbench-drawer-header report-revision-header">
          <div>
            <span className="section-kicker">REPORT REVISION</span>
            <h2 id={titleId}>重新生成报告</h2>
            <p id={descriptionId}>
              复用已经完成的证据分析，只重新组织笔记内容与输出格式。
            </p>
            <strong className="report-revision-task" title={taskTitle}>
              {taskTitle}
            </strong>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭报告重生成面板">
            <X size={18} aria-hidden="true" />
          </button>
        </header>

        <div className="workbench-drawer-body">
          <section className="report-revision-inputs">
            <div className="drawer-section-heading">
              <div>
                <span>INPUT SNAPSHOT</span>
                <h3>本次报告使用的输入</h3>
              </div>
              <Layers3 size={16} aria-hidden="true" />
            </div>
            <div className="report-revision-route">
              <div>
                <span>有效证据 revision</span>
                <strong title={activeEvidenceRevisionId}>
                  {compactId(activeEvidenceRevisionId)}
                </strong>
                <small>语音、字幕、OCR 与关键帧沿用此版本</small>
              </div>
              <div>
                <span>补充资料</span>
                <strong>{safeMaterialsCount} 份</strong>
                <small>
                  {safeMaterialsCount > 0
                    ? '当前有效资料会作为外部补充依据参与生成'
                    : '当前没有额外文字或图片资料'}
                </small>
              </div>
            </div>
            <p className="report-revision-scope-note">
              <Check size={14} aria-hidden="true" />
              当前有效证据与补充资料都会参与；此操作不会重新下载视频，也不会覆盖既有报告。
            </p>
          </section>

          {error && (
            <section className="report-revision-error" role="alert">
              <AlertTriangle size={17} aria-hidden="true" />
              <div>
                <strong>报告生成失败</strong>
                <p>{error}</p>
              </div>
            </section>
          )}

          <section className="report-revision-contract">
            <div className="drawer-section-heading">
              <div>
                <span>WRITING PROFILE</span>
                <h3>选择报告类型</h3>
              </div>
              <FileText size={16} aria-hidden="true" />
            </div>
            <div
              className="report-revision-presets"
              role="radiogroup"
              aria-label="报告类型"
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
                      <strong>{option.label}</strong>
                      <small>{option.description}</small>
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
                <span>OUTPUT CONTRACT</span>
                <h3>输出与图像</h3>
              </div>
              <FileCode2 size={16} aria-hidden="true" />
            </div>
            <div className="report-revision-output-grid">
              <div className="report-revision-output is-locked">
                <Check size={15} aria-hidden="true" />
                <span>
                  <strong>Markdown</strong>
                  <small>固定生成 · canonical 笔记</small>
                </span>
              </div>
              <div className="report-revision-output is-locked">
                <Check size={15} aria-hidden="true" />
                <span>
                  <strong>HTML</strong>
                  <small>固定生成 · 浏览与打印来源</small>
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
                  <small>生成可打印的本地离线版本</small>
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
                  <strong>关键帧截图</strong>
                  <small>只嵌入证据筛选后的画面</small>
                </span>
              </label>
            </div>
            <p className="report-revision-selection-note">
              当前选择：{selectedPreset.label}报告 · Markdown + HTML
              {includePdf ? ' + PDF' : ''} ·
              {includeScreenshots ? ' 包含关键帧' : ' 不包含关键帧'}
            </p>
          </section>

          <section className="report-revision-history">
            <div className="drawer-section-heading">
              <div>
                <span>IMMUTABLE HISTORY</span>
                <h3>历史报告 · {revisions.length}</h3>
              </div>
              <Clock3 size={16} aria-hidden="true" />
            </div>
            {revisions.length > 0 ? (
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
                          <strong>{presetLabels[revision.preset]}报告</strong>
                          <span>{formatCreatedAt(revision.createdAt)}</span>
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
                          {isCurrentEvidence ? '当前证据' : '历史证据'} ·{' '}
                          {compactId(revision.evidenceRevisionId)}
                        </span>
                        {revision.fallback && (
                          <span className="is-fallback">安全回退版本</span>
                        )}
                      </div>
                      {revision.warnings.length > 0 && (
                        <div className="report-revision-warnings">
                          <AlertTriangle size={14} aria-hidden="true" />
                          <ul>
                            {revision.warnings.map((warning, warningIndex) => (
                              <li key={`${revision.id}-warning-${warningIndex}`}>
                                {warning}
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
                                  : `${formatLabels[format]} 产物路径不可用`
                              }
                              onClick={() => onDownload(revision, format)}
                            >
                              <Download size={14} aria-hidden="true" />
                              <span>
                                <strong>{formatLabels[format]}</strong>
                                <small>
                                  {artifactPath
                                    ? artifactFileName(artifactPath)
                                    : '产物不可用'}
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
            ) : (
              <div className="report-revision-empty">
                <RefreshCcw size={19} aria-hidden="true" />
                <div>
                  <strong>还没有重新生成的报告</strong>
                  <p>
                    选择报告类型和输出格式后创建第一个不可变 revision；原始报告不会被覆盖。
                  </p>
                </div>
              </div>
            )}
          </section>
        </div>

        <footer className="workbench-drawer-footer report-revision-footer">
          <span>
            新报告将写入独立的本地 revision 目录。Markdown 与 HTML 始终生成。
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
            {busy ? '正在生成…' : '生成新 revision'}
          </button>
        </footer>
      </aside>
    </div>
  )
}

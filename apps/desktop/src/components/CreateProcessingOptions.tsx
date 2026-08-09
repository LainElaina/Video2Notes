import { AlertTriangle, AudioLines, Check, Plus, Trash2 } from 'lucide-react'
import type { ReportPreset, SamplingMode } from '../domain'
import { useI18n } from '../i18n'
import {
  MAX_FIXED_SAMPLES,
  MIN_FIXED_INTERVAL_SECONDS,
  validateSamplingDraft,
} from '../sampling'
import { useStudioStore } from '../store'
import { MotionPresence } from './MotionPresence'
import { VisualAsset } from './VisualAsset'

const samplingModes: Array<{
  value: SamplingMode
  labelZh: string
  labelEn: string
  descriptionZh: string
  descriptionEn: string
}> = [
  {
    value: 'adaptive',
    labelZh: '智能变化',
    labelEn: 'Adaptive changes',
    descriptionZh: '根据画面状态与文字变化自适应取样',
    descriptionEn: 'Sample adaptively from visual-state and text changes',
  },
  {
    value: 'fixed_interval',
    labelZh: '固定间隔',
    labelEn: 'Fixed interval',
    descriptionZh: '按指定秒数取样，适合已知节奏的片段',
    descriptionEn: 'Sample at a specified interval for segments with a known cadence',
  },
  {
    value: 'skip',
    labelZh: '跳过画面',
    labelEn: 'Skip visuals',
    descriptionZh: '不做视觉取样，仍保留音频与字幕处理',
    descriptionEn: 'Skip visual sampling while retaining audio and subtitle processing',
  },
]

const reportPresets: Array<{
  value: ReportPreset
  labelZh: string
  labelEn: string
  descriptionZh: string
  descriptionEn: string
}> = [
  { value: 'concise', labelZh: '简洁', labelEn: 'Concise', descriptionZh: '只保留结论、必要依据和最短上下文', descriptionEn: 'Keep conclusions, essential support, and the shortest useful context' },
  { value: 'detailed', labelZh: '详细', labelEn: 'Detailed', descriptionZh: '完整覆盖时间顺序、术语、限制与证据', descriptionEn: 'Cover chronology, terminology, limitations, and evidence in full' },
  { value: 'professional', labelZh: '专业', labelEn: 'Professional', descriptionZh: '突出方法、数据、假设和不确定性', descriptionEn: 'Emphasize methods, data, assumptions, and uncertainty' },
  { value: 'beginner', labelZh: '入门', labelEn: 'Beginner', descriptionZh: '用清晰步骤解释术语，不补造背景', descriptionEn: 'Explain terminology in clear steps without inventing context' },
  { value: 'executive', labelZh: '领导', labelEn: 'Executive', descriptionZh: '优先结论、影响、风险、决策与行动项', descriptionEn: 'Prioritize conclusions, impact, risks, decisions, and actions' },
]

const reportVisualByPreset = {
  concise: 'reportConcise',
  detailed: 'reportDetailed',
  professional: 'reportProfessional',
  beginner: 'reportBeginner',
  executive: 'reportExecutive',
} as const

const intervalPresets = [0.1, 0.5, 1] as const

const numberValue = (value: string): number => {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : 0
}

export function CreateProcessingOptions() {
  const { locale, text } = useI18n()
  const draft = useStudioStore(state => state.draft)
  const setSamplingMode = useStudioStore(state => state.setSamplingMode)
  const setSamplingIntervalSeconds = useStudioStore(
    state => state.setSamplingIntervalSeconds,
  )
  const addSamplingOverride = useStudioStore(state => state.addSamplingOverride)
  const updateSamplingOverride = useStudioStore(state => state.updateSamplingOverride)
  const removeSamplingOverride = useStudioStore(state => state.removeSamplingOverride)
  const setReportPreset = useStudioStore(state => state.setReportPreset)
  const setIncludeScreenshots = useStudioStore(state => state.setIncludeScreenshots)
  const setGeneratePdf = useStudioStore(state => state.setGeneratePdf)
  const audioOnly = draft.processingScope === 'audio_only'
  const validation = validateSamplingDraft(draft, locale)
  const modeLabel = (mode: SamplingMode) => {
    const option = samplingModes.find(item => item.value === mode)
    return option ? text(option.labelZh, option.labelEn) : mode
  }
  const reportLanguage =
    draft.languageHints
      .split(',')
      .map(value => value.trim())
      .find(value => value && value.toLowerCase() !== 'auto') ?? 'zh-CN'

  return (
    <div className="create-processing-options">
      <section
        className={`create-advanced-panel ${audioOnly ? 'is-audio-only' : ''}`}
        aria-labelledby="sampling-plan-title"
      >
        <div className="create-advanced-panel-heading">
          <div>
            <span className="section-kicker">
              {audioOnly ? 'VISUAL BYPASS' : 'VISUAL SAMPLING'}
            </span>
            <h4 id="sampling-plan-title">
              {audioOnly ? text('本任务已跳过画面处理', 'Visual processing skipped for this task') : text('画面采样计划', 'Visual sampling plan')}
            </h4>
            <p>
              {audioOnly
                ? text('后端以“仅识别音频”为准，不执行视觉扫描、OCR 或关键帧截图。', 'The backend follows the audio-only scope and does not run visual scans, OCR, or keyframe capture.')
                : text('默认使用变化检测；只在确有节奏规律的片段使用固定间隔。', 'Use change detection by default. Apply fixed intervals only to segments with a known cadence.')}
            </p>
          </div>
          <div className="create-sampling-budget">
            <span>
              {audioOnly
                ? text('视觉预算 0 帧', 'Visual budget: 0 frames')
                : draft.manifest
                ? text(`视频 ${draft.manifest.durationSeconds.toFixed(1)}s`, `Video ${draft.manifest.durationSeconds.toFixed(1)}s`)
                : text('探测后校验时长', 'Duration validated after probing')}
            </span>
            <strong>
              {audioOnly ? (
                text('OCR 与截图关闭', 'OCR and screenshots disabled')
              ) : (
                <>
                  {text('固定预算', 'Fixed budget')}{' '}
                  {validation.fixedSampleCount === null
                    ? '—'
                    : validation.fixedSampleCount.toLocaleString(locale)}{' '}
                  / {MAX_FIXED_SAMPLES.toLocaleString(locale)}
                </>
              )}
            </strong>
          </div>
        </div>

        <div className="motion-swap-stack create-processing-contract-stack">
          <MotionPresence
            show={audioOnly}
            className="motion-presence-swap"
            exitMs={140}
            animateInitial={false}
          >
            {audioOnly && (
          <div className="create-audio-only-contract" role="note">
            <AudioLines size={20} aria-hidden="true" />
            <div>
              <strong>{text('保留音频识别与报告生成', 'Audio recognition and report generation remain enabled')}</strong>
              <p>
                {text(
                  '平台字幕、音轨提取、语言识别、ASR、时间戳、事实融合与 Markdown / HTML / PDF 输出仍会运行。切回“完整音画”后，原来的画面采样设置会恢复。',
                  'Platform subtitles, audio extraction, language identification, ASR, timestamps, fact fusion, and Markdown / HTML / PDF output still run. Switching back to Audio + video restores the previous visual sampling settings.',
                )}
              </p>
            </div>
          </div>
            )}
          </MotionPresence>
          <MotionPresence
            show={!audioOnly}
            className="motion-presence-swap"
            exitMs={140}
            animateInitial={false}
          >
            {!audioOnly && (
          <div className="create-visual-contract">
            <div className="sampling-visual-rail" aria-label={text('采样策略预览', 'Sampling strategy preview')}>
              {(
                [
                  ['samplingAdaptive', text('智能变化', 'Adaptive changes'), text('只在真实变化峰值取帧', 'Capture frames only at real change peaks')],
                  ['samplingFixed', text('固定间隔', 'Fixed interval'), text('按秒数保持稳定节奏', 'Maintain a steady cadence by seconds')],
                  ['samplingSkip', text('跳过画面', 'Skip visuals'), text('保留音频与字幕轨', 'Keep the audio and subtitle tracks')],
                ] as const
              ).map(([asset, label, detail]) => (
                <div className="sampling-visual-card" key={asset}>
                  <VisualAsset asset={asset} width={116} height={92} />
                  <span>
                    <strong>{label}</strong>
                    <span>{detail}</span>
                  </span>
                </div>
              ))}
            </div>
            <div className="create-sampling-default">
          <label>
            {text('全局采样方式', 'Global sampling mode')}
            <select
              value={draft.samplingMode}
              aria-label={text('全局采样方式', 'Global sampling mode')}
              onChange={event => setSamplingMode(event.currentTarget.value as SamplingMode)}
            >
              {samplingModes.map(option => (
                <option key={option.value} value={option.value}>
                  {text(option.labelZh, option.labelEn)} · {text(option.descriptionZh, option.descriptionEn)}
                </option>
              ))}
            </select>
          </label>
          <MotionPresence
            show={draft.samplingMode === 'fixed_interval'}
            className="motion-presence-field"
            exitMs={120}
          >
            {draft.samplingMode === 'fixed_interval' && (
              <IntervalEditor
                label={text('全局固定采样间隔（秒）', 'Global fixed sampling interval (seconds)')}
                value={draft.samplingIntervalSeconds}
                onChange={setSamplingIntervalSeconds}
              />
            )}
          </MotionPresence>
          <p className="create-sampling-mode-note">
            {
              (() => {
                const option = samplingModes.find(item => item.value === draft.samplingMode)
                return option ? text(option.descriptionZh, option.descriptionEn) : undefined
              })()
            }
          </p>
            </div>

            <div className="create-sampling-overrides">
          <div className="create-sampling-overrides-heading">
            <div>
              <strong>{text('时间段覆盖', 'Segment overrides')}</strong>
              <span>{text('覆盖全局方式；相邻区间可以首尾相接，但不能重叠。', 'Override the global mode. Adjacent segments may touch but cannot overlap.')}</span>
            </div>
            <button
              className="button button-secondary create-add-segment"
              type="button"
              onClick={addSamplingOverride}
            >
              <Plus size={14} aria-hidden="true" />
              {text('添加时间段', 'Add segment')}
            </button>
          </div>

          {draft.samplingOverrides.length === 0 ? (
            <p className="create-sampling-empty">{text('当前没有分段覆盖，整段视频使用全局设置。', 'There are no segment overrides; the entire video uses the global setting.')}</p>
          ) : (
            <div className="create-sampling-segment-list">
              {draft.samplingOverrides.map((override, index) => (
                <div
                  className="create-sampling-segment motion-list-item"
                  role="group"
                  aria-label={text(`采样时间段 ${index + 1}`, `Sampling segment ${index + 1}`)}
                  key={override.id}
                >
                  <span className="create-sampling-index">
                    {(index + 1).toString().padStart(2, '0')}
                  </span>
                  <label>
                    {text('开始 / 秒', 'Start / sec')}
                    <input
                      type="number"
                      min="0"
                      step="0.1"
                      value={override.startSeconds}
                      aria-label={text(`时间段 ${index + 1} 开始时间（秒）`, `Segment ${index + 1} start time (seconds)`)}
                      onChange={event =>
                        updateSamplingOverride(override.id, {
                          startSeconds: numberValue(event.currentTarget.value),
                        })
                      }
                    />
                  </label>
                  <label>
                    {text('结束 / 秒', 'End / sec')}
                    <input
                      type="number"
                      min="0"
                      step="0.1"
                      value={override.endSeconds}
                      aria-label={text(`时间段 ${index + 1} 结束时间（秒）`, `Segment ${index + 1} end time (seconds)`)}
                      onChange={event =>
                        updateSamplingOverride(override.id, {
                          endSeconds: numberValue(event.currentTarget.value),
                        })
                      }
                    />
                  </label>
                  <label>
                    {text('采样方式', 'Sampling mode')}
                    <select
                      value={override.mode}
                      aria-label={text(`时间段 ${index + 1} 采样方式`, `Segment ${index + 1} sampling mode`)}
                      onChange={event =>
                        updateSamplingOverride(override.id, {
                          mode: event.currentTarget.value as SamplingMode,
                        })
                      }
                    >
                      {samplingModes.map(option => (
                        <option key={option.value} value={option.value}>
                          {text(option.labelZh, option.labelEn)}
                        </option>
                      ))}
                    </select>
                  </label>
                  {override.mode === 'fixed_interval' ? (
                    <IntervalEditor
                      compact
                      label={text(`时间段 ${index + 1} 固定采样间隔（秒）`, `Segment ${index + 1} fixed sampling interval (seconds)`)}
                      value={override.intervalSeconds}
                      onChange={intervalSeconds =>
                        updateSamplingOverride(override.id, { intervalSeconds })
                      }
                    />
                  ) : (
                    <div className="create-sampling-segment-mode">
                      <span>{text('执行方式', 'Execution mode')}</span>
                      <strong>{modeLabel(override.mode)}</strong>
                    </div>
                  )}
                  <button
                    className="icon-button create-remove-segment"
                    type="button"
                    aria-label={text(`删除时间段 ${index + 1}`, `Delete segment ${index + 1}`)}
                    title={text(`删除时间段 ${index + 1}`, `Delete segment ${index + 1}`)}
                    onClick={() => removeSamplingOverride(override.id)}
                  >
                    <Trash2 size={16} aria-hidden="true" />
                  </button>
                </div>
              ))}
            </div>
          )}
            </div>

            <MotionPresence
              show={validation.errors.length > 0}
              className="motion-presence-inline-feedback"
              exitMs={120}
            >
              {validation.errors.length > 0 && (
                <div className="create-sampling-errors" role="alert">
                  <AlertTriangle size={16} aria-hidden="true" />
                  <div>
                    <strong>{text('采样计划尚不能提交', 'The sampling plan cannot be submitted yet')}</strong>
                    <ul>
                      {validation.errors.map(error => (
                        <li key={error}>{error}</li>
                      ))}
                    </ul>
                  </div>
                </div>
              )}
            </MotionPresence>
          </div>
            )}
          </MotionPresence>
        </div>
      </section>

      <section className="create-advanced-panel" aria-labelledby="report-preset-title">
        <div className="create-advanced-panel-heading">
          <div>
            <span className="section-kicker">{text('报告约定', 'REPORT CONTRACT')}</span>
            <h4 id="report-preset-title">{text('报告类型与输出', 'Report type and output')}</h4>
            <p>{text('报告类型会改变内容预算与表达对象，不改变证据真实性要求。', 'The report type changes the content budget and audience, but not the evidence requirements.')}</p>
          </div>
          <span className="create-report-language">{text('报告语言', 'Report language')} {reportLanguage}</span>
        </div>

        <div className="create-report-presets" role="radiogroup" aria-label={text('报告类型', 'Report type')}>
          {reportPresets.map(preset => {
            const checked = draft.reportPreset === preset.value
            return (
              <label
                className={`create-report-preset ${checked ? 'is-selected' : ''}`}
                key={preset.value}
              >
                <input
                  type="radio"
                  name="report-preset"
                  value={preset.value}
                  checked={checked}
                  onChange={() => setReportPreset(preset.value)}
                />
                <VisualAsset
                  className="report-preset-visual"
                  asset={reportVisualByPreset[preset.value]}
                  width={112}
                  height={88}
                />
                <span>
                  <strong>{text(preset.labelZh, preset.labelEn)}</strong>
                  <small>{text(preset.descriptionZh, preset.descriptionEn)}</small>
                </span>
              </label>
            )
          })}
        </div>

        <div className="create-output-contract" aria-label={text('输出格式', 'Output formats')}>
          <div className="create-output-locked" aria-label={text('Markdown 固定开启', 'Markdown is always enabled')}>
            <Check size={14} aria-hidden="true" />
            <span>
              <strong>Markdown</strong>
              <small>{text('固定开启', 'Always enabled')} · canonical</small>
            </span>
          </div>
          <div className="create-output-locked" aria-label={text('HTML 固定开启', 'HTML is always enabled')}>
            <Check size={14} aria-hidden="true" />
            <span>
              <strong>HTML</strong>
              <small>{text('固定开启 · 阅读与 PDF 来源', 'Always enabled · source for reading and PDF')}</small>
            </span>
          </div>
          <label className="create-output-toggle">
            <input
              type="checkbox"
              aria-label={text('同时生成离线 PDF', 'Also generate an offline PDF')}
              checked={draft.generatePdf}
              onChange={event => setGeneratePdf(event.currentTarget.checked)}
            />
            <span>
              <strong>PDF</strong>
              <small>{text('同时生成可打印离线版本', 'Also generate a printable offline version')}</small>
            </span>
          </label>
          <label className={`create-output-toggle ${audioOnly ? 'is-disabled' : ''}`}>
            <input
              type="checkbox"
              aria-label={text('嵌入关键帧截图', 'Embed keyframe screenshots')}
              checked={audioOnly ? false : draft.includeScreenshots}
              disabled={audioOnly}
              onChange={event => setIncludeScreenshots(event.currentTarget.checked)}
            />
            <span>
              <strong>{text('关键帧截图', 'Keyframe screenshots')}</strong>
              <small>
                {audioOnly ? text('仅音频模式自动关闭', 'Disabled automatically in audio-only mode') : text('只嵌入证据筛选后的画面', 'Embed only frames selected by evidence filtering')}
              </small>
            </span>
          </label>
        </div>
      </section>
    </div>
  )
}

function IntervalEditor({
  compact = false,
  label,
  value,
  onChange,
}: {
  compact?: boolean
  label: string
  value: number
  onChange: (value: number) => void
}) {
  const { text } = useI18n()
  return (
    <div className={`create-interval-editor ${compact ? 'is-compact' : ''}`}>
      <label>
        {compact ? text('间隔 / 秒', 'Interval / sec') : text('固定采样间隔 / 秒', 'Fixed sampling interval / sec')}
        <input
          type="number"
          min={MIN_FIXED_INTERVAL_SECONDS}
          step="0.1"
          value={value}
          aria-label={label}
          onChange={event => onChange(numberValue(event.currentTarget.value))}
        />
      </label>
      <div className="create-interval-presets" aria-label={text(`${label}快捷值`, `${label} presets`)}>
        {intervalPresets.map(interval => (
          <button
            type="button"
            key={interval}
            aria-label={text(`${label}设为 ${interval} 秒`, `Set ${label} to ${interval} seconds`)}
            aria-pressed={value === interval}
            onClick={() => onChange(interval)}
          >
            {interval}s
          </button>
        ))}
      </div>
    </div>
  )
}

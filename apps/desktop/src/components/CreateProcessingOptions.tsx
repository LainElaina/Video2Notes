import { AlertTriangle, AudioLines, Check, Plus, Trash2 } from 'lucide-react'
import type { ReportPreset, SamplingMode } from '../domain'
import {
  MAX_FIXED_SAMPLES,
  MIN_FIXED_INTERVAL_SECONDS,
  validateSamplingDraft,
} from '../sampling'
import { useStudioStore } from '../store'

const samplingModes: Array<{ value: SamplingMode; label: string; description: string }> = [
  {
    value: 'adaptive',
    label: '智能变化',
    description: '根据画面状态与文字变化自适应取样',
  },
  {
    value: 'fixed_interval',
    label: '固定间隔',
    description: '按指定秒数取样，适合已知节奏的片段',
  },
  {
    value: 'skip',
    label: '跳过画面',
    description: '不做视觉取样，仍保留音频与字幕处理',
  },
]

const reportPresets: Array<{ value: ReportPreset; label: string; description: string }> = [
  { value: 'concise', label: '简洁', description: '只保留结论、必要依据和最短上下文' },
  { value: 'detailed', label: '详细', description: '完整覆盖时间顺序、术语、限制与证据' },
  { value: 'professional', label: '专业', description: '突出方法、数据、假设和不确定性' },
  { value: 'beginner', label: '入门', description: '用清晰步骤解释术语，不补造背景' },
  { value: 'executive', label: '领导', description: '优先结论、影响、风险、决策与行动项' },
]

const intervalPresets = [0.1, 0.5, 1] as const

const modeLabel = (mode: SamplingMode) =>
  samplingModes.find(option => option.value === mode)?.label ?? mode

const numberValue = (value: string): number => {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : 0
}

export function CreateProcessingOptions() {
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
  const validation = validateSamplingDraft(draft)
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
              {audioOnly ? '本任务已跳过画面处理' : '画面采样计划'}
            </h4>
            <p>
              {audioOnly
                ? '后端以“仅识别音频”为准，不执行视觉扫描、OCR 或关键帧截图。'
                : '默认使用变化检测；只在确有节奏规律的片段使用固定间隔。'}
            </p>
          </div>
          <div className="create-sampling-budget">
            <span>
              {audioOnly
                ? '视觉预算 0 帧'
                : draft.manifest
                ? `视频 ${draft.manifest.durationSeconds.toFixed(1)}s`
                : '探测后校验时长'}
            </span>
            <strong>
              {audioOnly ? (
                'OCR 与截图关闭'
              ) : (
                <>
                  固定预算{' '}
                  {validation.fixedSampleCount === null
                    ? '—'
                    : validation.fixedSampleCount.toLocaleString('zh-CN')}{' '}
                  / {MAX_FIXED_SAMPLES.toLocaleString('zh-CN')}
                </>
              )}
            </strong>
          </div>
        </div>

        {audioOnly ? (
          <div className="create-audio-only-contract" role="note">
            <AudioLines size={20} aria-hidden="true" />
            <div>
              <strong>保留音频识别与报告生成</strong>
              <p>
                平台字幕、音轨提取、语言识别、ASR、时间戳、事实融合与 Markdown / HTML / PDF
                输出仍会运行。切回“完整音画”后，原来的画面采样设置会恢复。
              </p>
            </div>
          </div>
        ) : (
          <>
            <div className="create-sampling-default">
          <label>
            全局采样方式
            <select
              value={draft.samplingMode}
              aria-label="全局采样方式"
              onChange={event => setSamplingMode(event.currentTarget.value as SamplingMode)}
            >
              {samplingModes.map(option => (
                <option key={option.value} value={option.value}>
                  {option.label} · {option.description}
                </option>
              ))}
            </select>
          </label>
          {draft.samplingMode === 'fixed_interval' && (
            <IntervalEditor
              label="全局固定采样间隔（秒）"
              value={draft.samplingIntervalSeconds}
              onChange={setSamplingIntervalSeconds}
            />
          )}
          <p className="create-sampling-mode-note">
            {
              samplingModes.find(option => option.value === draft.samplingMode)
                ?.description
            }
          </p>
            </div>

            <div className="create-sampling-overrides">
          <div className="create-sampling-overrides-heading">
            <div>
              <strong>时间段覆盖</strong>
              <span>覆盖全局方式；相邻区间可以首尾相接，但不能重叠。</span>
            </div>
            <button
              className="button button-secondary create-add-segment"
              type="button"
              onClick={addSamplingOverride}
            >
              <Plus size={14} aria-hidden="true" />
              添加时间段
            </button>
          </div>

          {draft.samplingOverrides.length === 0 ? (
            <p className="create-sampling-empty">当前没有分段覆盖，整段视频使用全局设置。</p>
          ) : (
            <div className="create-sampling-segment-list">
              {draft.samplingOverrides.map((override, index) => (
                <div
                  className="create-sampling-segment"
                  role="group"
                  aria-label={`采样时间段 ${index + 1}`}
                  key={override.id}
                >
                  <span className="create-sampling-index">
                    {(index + 1).toString().padStart(2, '0')}
                  </span>
                  <label>
                    开始 / 秒
                    <input
                      type="number"
                      min="0"
                      step="0.1"
                      value={override.startSeconds}
                      aria-label={`时间段 ${index + 1} 开始时间（秒）`}
                      onChange={event =>
                        updateSamplingOverride(override.id, {
                          startSeconds: numberValue(event.currentTarget.value),
                        })
                      }
                    />
                  </label>
                  <label>
                    结束 / 秒
                    <input
                      type="number"
                      min="0"
                      step="0.1"
                      value={override.endSeconds}
                      aria-label={`时间段 ${index + 1} 结束时间（秒）`}
                      onChange={event =>
                        updateSamplingOverride(override.id, {
                          endSeconds: numberValue(event.currentTarget.value),
                        })
                      }
                    />
                  </label>
                  <label>
                    采样方式
                    <select
                      value={override.mode}
                      aria-label={`时间段 ${index + 1} 采样方式`}
                      onChange={event =>
                        updateSamplingOverride(override.id, {
                          mode: event.currentTarget.value as SamplingMode,
                        })
                      }
                    >
                      {samplingModes.map(option => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  {override.mode === 'fixed_interval' ? (
                    <IntervalEditor
                      compact
                      label={`时间段 ${index + 1} 固定采样间隔（秒）`}
                      value={override.intervalSeconds}
                      onChange={intervalSeconds =>
                        updateSamplingOverride(override.id, { intervalSeconds })
                      }
                    />
                  ) : (
                    <div className="create-sampling-segment-mode">
                      <span>执行方式</span>
                      <strong>{modeLabel(override.mode)}</strong>
                    </div>
                  )}
                  <button
                    className="icon-button create-remove-segment"
                    type="button"
                    aria-label={`删除时间段 ${index + 1}`}
                    title={`删除时间段 ${index + 1}`}
                    onClick={() => removeSamplingOverride(override.id)}
                  >
                    <Trash2 size={16} aria-hidden="true" />
                  </button>
                </div>
              ))}
            </div>
          )}
            </div>

            {validation.errors.length > 0 && (
              <div className="create-sampling-errors" role="alert">
                <AlertTriangle size={16} aria-hidden="true" />
                <div>
                  <strong>采样计划尚不能提交</strong>
                  <ul>
                    {validation.errors.map(error => (
                      <li key={error}>{error}</li>
                    ))}
                  </ul>
                </div>
              </div>
            )}
          </>
        )}
      </section>

      <section className="create-advanced-panel" aria-labelledby="report-preset-title">
        <div className="create-advanced-panel-heading">
          <div>
            <span className="section-kicker">REPORT CONTRACT</span>
            <h4 id="report-preset-title">报告类型与输出</h4>
            <p>报告类型会改变内容预算与表达对象，不改变证据真实性要求。</p>
          </div>
          <span className="create-report-language">报告语言 {reportLanguage}</span>
        </div>

        <div className="create-report-presets" role="radiogroup" aria-label="报告类型">
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
                <span>
                  <strong>{preset.label}</strong>
                  <small>{preset.description}</small>
                </span>
              </label>
            )
          })}
        </div>

        <div className="create-output-contract" aria-label="输出格式">
          <div className="create-output-locked" aria-label="Markdown 固定开启">
            <Check size={14} aria-hidden="true" />
            <span>
              <strong>Markdown</strong>
              <small>固定开启 · canonical</small>
            </span>
          </div>
          <div className="create-output-locked" aria-label="HTML 固定开启">
            <Check size={14} aria-hidden="true" />
            <span>
              <strong>HTML</strong>
              <small>固定开启 · 阅读与 PDF 来源</small>
            </span>
          </div>
          <label className="create-output-toggle">
            <input
              type="checkbox"
              aria-label="同时生成离线 PDF"
              checked={draft.generatePdf}
              onChange={event => setGeneratePdf(event.currentTarget.checked)}
            />
            <span>
              <strong>PDF</strong>
              <small>同时生成可打印离线版本</small>
            </span>
          </label>
          <label className={`create-output-toggle ${audioOnly ? 'is-disabled' : ''}`}>
            <input
              type="checkbox"
              aria-label="嵌入关键帧截图"
              checked={audioOnly ? false : draft.includeScreenshots}
              disabled={audioOnly}
              onChange={event => setIncludeScreenshots(event.currentTarget.checked)}
            />
            <span>
              <strong>关键帧截图</strong>
              <small>
                {audioOnly ? '仅音频模式自动关闭' : '只嵌入证据筛选后的画面'}
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
  return (
    <div className={`create-interval-editor ${compact ? 'is-compact' : ''}`}>
      <label>
        {compact ? '间隔 / 秒' : '固定采样间隔 / 秒'}
        <input
          type="number"
          min={MIN_FIXED_INTERVAL_SECONDS}
          step="0.1"
          value={value}
          aria-label={label}
          onChange={event => onChange(numberValue(event.currentTarget.value))}
        />
      </label>
      <div className="create-interval-presets" aria-label={`${label}快捷值`}>
        {intervalPresets.map(interval => (
          <button
            type="button"
            key={interval}
            aria-label={`${label}设为 ${interval} 秒`}
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

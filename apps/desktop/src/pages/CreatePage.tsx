import { useId, useRef, useState } from 'react'
import {
  AudioLines,
  ArrowRight,
  Check,
  ChevronDown,
  Gauge,
  Globe2,
  MonitorUp,
  RefreshCw,
  ScanText,
  ShieldCheck,
  Sparkles,
  Timer,
  Upload,
} from 'lucide-react'
import { CreateProcessingOptions } from '../components/CreateProcessingOptions'
import { DependencyPreflightDialog } from '../components/DependencyPreflightDialog'
import { MotionPresence } from '../components/MotionPresence'
import { ProcessingBenchmarkGuide } from '../components/ProcessingBenchmarkGuide'
import { VisualAsset } from '../components/VisualAsset'
import { formatTime, platformLabel } from '../domain'
import type { ProcessingMode } from '../domain'
import { useI18n } from '../i18n'
import { validateSamplingDraft } from '../sampling'
import { backendSupportsAudioOnly, useStudioStore } from '../store'

const modeCopy: Record<
  ProcessingMode,
  {
    label: string
    description: string
    descriptionEn: string
    audioDescription: string
    audioDescriptionEn: string
    estimate: string
    estimateEn: string
    bullets: string[]
    bulletsEn: string[]
    audioBullets: string[]
    audioBulletsEn: string[]
    icon: typeof Gauge
  }
> = {
  fast: {
    label: 'Fast',
    description: '预期精度：语音主线可靠，减少视觉复核与截图密度',
    descriptionEn: 'Expected accuracy: reliable speech narrative with fewer visual reviews and screenshots',
    audioDescription: '速度优先：优先现有字幕，并使用轻量语音识别策略',
    audioDescriptionEn: 'Speed first: prioritize existing subtitles and use a lightweight speech-recognition strategy',
    estimate: '基准实测 1.17× · 探测后显示本机预算',
    estimateEn: 'Benchmark 1.17× · local budget appears after probing',
    bullets: ['平台字幕优先', '单路本地 ASR', '精选关键画面'],
    bulletsEn: ['Platform subtitles first', 'Single local ASR pass', 'Selected keyframes'],
    audioBullets: ['平台字幕优先', '轻量 ASR 搜索', '跳过视觉与 OCR'],
    audioBulletsEn: ['Platform subtitles first', 'Lightweight ASR search', 'Skip visuals and OCR'],
    icon: Gauge,
  },
  balanced: {
    label: 'Balanced',
    description: '预期精度：高，平衡视觉密度、冲突复核与资源消耗',
    descriptionEn: 'Expected accuracy: high, balancing visual density, conflict review, and resource use',
    audioDescription: '默认推荐：平衡语音搜索、时间戳质量与字幕/语言冲突复核',
    audioDescriptionEn: 'Recommended default: balance speech search, timestamp quality, and subtitle/language conflict review',
    estimate: '基准实测 3.68× · 探测后显示本机预算',
    estimateEn: 'Benchmark 3.68× · local budget appears after probing',
    bullets: ['字幕 + 本地 ASR', '冲突片段复核', '每章精选截图'],
    bulletsEn: ['Subtitles + local ASR', 'Review conflicting segments', 'Selected screenshot per chapter'],
    audioBullets: ['字幕 + 本地 ASR', '时间戳对齐', '字幕/语言冲突复核'],
    audioBulletsEn: ['Subtitles + local ASR', 'Timestamp alignment', 'Subtitle/language conflict review'],
    icon: Timer,
  },
  accurate: {
    label: 'Accurate',
    description: '预期精度：最高，把额外算力集中在低置信与内容变化处',
    descriptionEn: 'Expected accuracy: highest, focusing extra compute on low-confidence and changing content',
    audioDescription: '语音优先：把额外 ASR 计算集中在低置信、多语言和冲突片段',
    audioDescriptionEn: 'Speech first: focus extra ASR work on low-confidence, multilingual, and conflicting segments',
    estimate: '基准实测 6.66× · 探测后显示本机预算',
    estimateEn: 'Benchmark 6.66× · local budget appears after probing',
    bullets: ['选择性二次识别', '更细视觉与 OCR', '事实支持度验证'],
    bulletsEn: ['Selective reprocessing', 'Finer visual scan and OCR', 'Claim support verification'],
    audioBullets: ['更完整 ASR 搜索', '多语言提示', '选择性二次识别'],
    audioBulletsEn: ['Broader ASR search', 'Multilingual hints', 'Selective reprocessing'],
    icon: Sparkles,
  },
}

const sourceVisualByPlatform = {
  bilibili: 'sourceBilibili',
  youtube: 'sourceYoutube',
  x: 'sourceX',
  local: 'sourceLocal',
} as const

const modeVisualByMode = {
  fast: 'modeFast',
  balanced: 'modeBalanced',
  accurate: 'modeAccurate',
} as const

export function CreatePage() {
  const { locale, text } = useI18n()
  const draft = useStudioStore(state => state.draft)
  const setDraftInput = useStudioStore(state => state.setDraftInput)
  const setDraftMode = useStudioStore(state => state.setDraftMode)
  const setProcessingScope = useStudioStore(state => state.setProcessingScope)
  const probeSource = useStudioStore(state => state.probeSource)
  const chooseLocalFile = useStudioStore(state => state.chooseLocalFile)
  const chooseBundledDemo = useStudioStore(state => state.chooseBundledDemo)
  const selectLocalFile = useStudioStore(state => state.selectLocalFile)
  const createTask = useStudioStore(state => state.createTask)
  const backend = useStudioStore(state => state.backend)
  const submissionInFlight = useStudioStore(state => state.submissionInFlight)
  const jobPreflight = useStudioStore(state => state.jobPreflight)
  const jobPreflightStatus = useStudioStore(state => state.jobPreflightStatus)
  const machine = useStudioStore(state => state.machine)
  const processingEstimates = useStudioStore(state => state.processingEstimates)
  const processingEstimateStatus = useStudioStore(state => state.processingEstimateStatus)
  const processingEstimateError = useStudioStore(state => state.processingEstimateError)
  const browserProfiles = useStudioStore(state => state.browserProfiles)
  const setDraftAuthKind = useStudioStore(state => state.setDraftAuthKind)
  const setDraftBrowser = useStudioStore(state => state.setDraftBrowser)
  const setDraftProfile = useStudioStore(state => state.setDraftProfile)
  const setDraftCookieFile = useStudioStore(state => state.setDraftCookieFile)
  const setLanguageHints = useStudioStore(state => state.setLanguageHints)
  const [dragActive, setDragActive] = useState(false)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const createTaskButtonRef = useRef<HTMLButtonElement>(null)
  const advancedOptionsId = useId()
  const audioOnlySupported = backendSupportsAudioOnly(backend)
  const scopeLocked = submissionInFlight || draft.status === 'submitting'
  const samplingValidation = validateSamplingDraft(draft, locale)
  const manifestNeedsRefresh = Boolean(
    draft.manifest && draft.status !== 'ready' && draft.status !== 'submitting',
  )

  const estimateLabel = (mode: ProcessingMode): string => {
    const estimate = processingEstimates[mode]
    if (!estimate) {
      return draft.processingScope === 'audio_only'
        ? text('仅音频 · 等待本机估算', 'Audio only · awaiting local estimate')
        : text(modeCopy[mode].estimate, modeCopy[mode].estimateEn)
    }
    const lowerRtf = estimate.lowerRealtimeFactor.toFixed(
      estimate.lowerRealtimeFactor >= 1 ? 1 : 2,
    )
    const upperRtf = estimate.upperRealtimeFactor.toFixed(
      estimate.upperRealtimeFactor >= 1 ? 1 : 2,
    )
    return text(
      `本机工程预算 ${formatTime(Math.ceil(estimate.lowerSeconds))}–${formatTime(Math.ceil(estimate.upperSeconds))} · ${lowerRtf}–${upperRtf}×`,
      `Local engineering budget ${formatTime(Math.ceil(estimate.lowerSeconds))}–${formatTime(Math.ceil(estimate.upperSeconds))} · ${lowerRtf}–${upperRtf}×`,
    )
  }

  const handleFile = (file?: File) => {
    if (!file) return
    const localPath = (file as File & { path?: string }).path || file.name
    selectLocalFile(localPath, file.size)
  }

  const matchingProfiles = browserProfiles.filter(profile => profile.browser === draft.browser)

  return (
    <div className="create-page">
      <section className="create-intro">
        <div className="create-intro-copy">
          <span className="section-kicker">{text('证据优先的视频笔记', 'EVIDENCE-FIRST VIDEO NOTES')}</span>
          <h2>{text('将视频变成可核查的笔记', 'Turn video into verifiable notes')}</h2>
          <p>
            {text(
              '下载或导入视频后，语音、屏幕文字和关键画面会进入同一条真实时间轴，再生成可回看的 Markdown。',
              'After a video is downloaded or imported, speech, on-screen text, and keyframes share one physical timeline before a reviewable Markdown note is produced.',
            )}
          </p>
        </div>
        <VisualAsset
          className="create-intro-visual"
          asset="heroEvidenceTimeline"
          width={640}
          height={640}
          loading="eager"
          fetchPriority="high"
        />
      </section>

      <section
        className={`source-composer ${dragActive ? 'is-dragging' : ''}`}
        onDragEnter={event => {
          event.preventDefault()
          setDragActive(true)
        }}
        onDragOver={event => event.preventDefault()}
        onDragLeave={event => {
          if (event.currentTarget === event.target) setDragActive(false)
        }}
        onDrop={event => {
          event.preventDefault()
          setDragActive(false)
          handleFile(event.dataTransfer.files[0])
        }}
      >
        <div className="source-composer-icon" aria-hidden="true">
          <MonitorUp size={22} />
        </div>
        <div className="source-input-wrap">
          <label htmlFor="source-url">{text('视频链接', 'Video link')}</label>
          <input
            id="source-url"
            value={draft.input}
            onChange={event => setDraftInput(event.target.value)}
            onKeyDown={event => {
              if (event.key === 'Enter' && draft.status !== 'probing') probeSource()
            }}
            placeholder={text('粘贴 Bilibili、YouTube 或 X 链接', 'Paste a Bilibili, YouTube, or X link')}
          />
          <span>{text('也可以把视频直接拖到此区域', 'You can also drag a video directly into this area')}</span>
        </div>
        <button
          className="button button-primary"
          type="button"
          onClick={probeSource}
          disabled={draft.status === 'probing'}
          aria-busy={draft.status === 'probing' || undefined}
        >
          {draft.status === 'probing' ? (
            <RefreshCw className="spin" size={16} aria-hidden="true" />
          ) : (
            <Globe2 size={16} aria-hidden="true" />
          )}
          {draft.status === 'probing' ? text('正在探测…', 'Probing…') : text('探测来源', 'Probe source')}
        </button>
        <div className="source-divider" aria-hidden="true">
          <span>{text('或', 'or')}</span>
        </div>
        <div className="source-local-actions">
          <button
            className="button button-secondary"
            type="button"
            onClick={chooseLocalFile}
          >
            <Upload size={16} aria-hidden="true" />
            {text('选择本地视频', 'Choose local video')}
          </button>
          <button
            className="source-demo-button"
            type="button"
            onClick={chooseBundledDemo}
          >
            {text('内置样例', 'Built-in demo')}
          </button>
        </div>
      </section>

      <div className="source-platform-visuals" aria-label={text('支持的视频来源', 'Supported video sources')}>
        {(
          [
            ['sourceBilibili', 'Bilibili'],
            ['sourceYoutube', 'YouTube'],
            ['sourceX', 'X'],
            ['sourceLocal', text('本地文件', 'Local file')],
          ] as const
        ).map(([asset, label]) => (
          <div className="source-platform-visual" key={asset} title={label}>
            <VisualAsset asset={asset} width={320} height={140} />
          </div>
        ))}
      </div>

      <MotionPresence
        show={Boolean(draft.error)}
        className="motion-presence-inline-feedback"
        exitMs={120}
      >
        {draft.error && (
          <div className="inline-error" role="alert">
            {draft.error}
          </div>
        )}
      </MotionPresence>

      <MotionPresence
        show={Boolean(draft.manifest)}
        className="motion-presence-source-manifest"
        exitMs={140}
      >
        {draft.manifest && (
          <section
            className={`source-manifest ${manifestNeedsRefresh ? 'is-stale' : ''}`}
            aria-label={manifestNeedsRefresh ? text('来源探测结果，需要重新探测', 'Source probe result; probing is required again') : text('来源探测结果', 'Source probe result')}
          >
          <div className="manifest-thumb" aria-hidden="true">
            <VisualAsset
              className="visual-manifest-thumb"
              asset={sourceVisualByPlatform[draft.manifest.platform]}
              width={120}
              height={90}
            />
            <span>{draft.manifest.platform === 'local' ? text('本地文件', 'Local file') : platformLabel[draft.manifest.platform]}</span>
          </div>
          <div className="manifest-main">
            <span className="section-kicker">
              {manifestNeedsRefresh
                ? draft.status === 'probing'
                  ? 'SOURCE RECHECKING'
                  : 'SOURCE NEEDS REFRESH'
                : 'SOURCE VERIFIED'}
            </span>
            <h3 title={draft.manifest.title}>{draft.manifest.title}</h3>
            <p
              title={`${draft.manifest.author} · ${formatTime(draft.manifest.durationSeconds)} · ${draft.manifest.sourceLabel}`}
            >
              {draft.manifest.author} · {formatTime(draft.manifest.durationSeconds)} ·{' '}
              {draft.manifest.sourceLabel}
            </p>
          </div>
          <dl className="manifest-specs">
            <div>
              <dt>{text('实际画质', 'Actual quality')}</dt>
              <dd>{draft.manifest.quality}</dd>
            </div>
            <div>
              <dt>{text('音视频', 'Audio / video')}</dt>
              <dd>
                {draft.manifest.codec} · {draft.manifest.audio}
              </dd>
            </div>
            <div>
              <dt>{text('字幕', 'Subtitles')}</dt>
              <dd>{draft.manifest.subtitle}</dd>
            </div>
            <div>
              <dt>{text('浏览器身份', 'Browser identity')}</dt>
              <dd>{draft.manifest.authLabel}</dd>
            </div>
          </dl>
          <div className={`quality-promise ${manifestNeedsRefresh ? 'is-stale' : ''}`}>
            {manifestNeedsRefresh ? (
              <RefreshCw
                className={draft.status === 'probing' ? 'spin' : ''}
                size={16}
                aria-hidden="true"
              />
            ) : (
              <ShieldCheck size={16} aria-hidden="true" />
            )}
            <span>
              {manifestNeedsRefresh
                ? draft.status === 'probing'
                  ? text('正在按当前来源、身份和处理模式重新探测；旧结果暂仅供参考。', 'Probing again with the current source, identity, and processing mode. The previous result is for reference only.')
                  : text('来源或探测策略已变化，当前信息仅供参考；重新探测后才能开始处理。', 'The source or probe strategy changed. Probe again before processing; the current result is for reference only.')
                : text('已锁定当前可访问的最佳画质；若下载结果降低清晰度，任务会停止并说明原因。', 'The best currently accessible quality is locked. The task will stop and explain why if the downloaded quality is lower.')}
            </span>
            {manifestNeedsRefresh && draft.status !== 'probing' && (
              <button className="manifest-reprobe" type="button" onClick={probeSource}>
                {text('重新探测', 'Probe again')}
              </button>
            )}
          </div>
          </section>
        )}
      </MotionPresence>

      <section className="mode-section">
        <div className="section-heading">
          <div>
            <span className="section-kicker">{text('处理配置', 'PROCESSING PROFILE')}</span>
            <h3>{text('选择处理模式', 'Choose a processing mode')}</h3>
          </div>
          <div className="hardware-fit">
            <Check size={14} aria-hidden="true" />
            {machine.backend === 'ready' ? text(`已根据 ${machine.gpu} 调整`, `Adjusted for ${machine.gpu}`) : backend.detail}
          </div>
        </div>
        <ProcessingBenchmarkGuide />

        <section className="processing-scope" aria-labelledby="processing-scope-title">
          <div className="processing-scope-heading">
            <div>
              <span className="section-kicker">{text('处理范围', 'PROCESSING SCOPE')}</span>
              <h4 id="processing-scope-title">{text('选择要识别的内容', 'Choose what to recognize')}</h4>
            </div>
            <span>{text('仅音频可跳过本次基准中最耗时的视觉与 OCR 阶段', 'Audio-only mode skips the visual and OCR stages that dominated this benchmark')}</span>
          </div>
          <div className="processing-scope-options" role="radiogroup" aria-label={text('处理范围', 'Processing scope')}>
            <label
              className={draft.processingScope === 'audio_visual' ? 'is-selected' : ''}
            >
              <input
                type="radio"
                name="processing-scope"
                value="audio_visual"
                checked={draft.processingScope === 'audio_visual'}
                disabled={scopeLocked}
                onChange={() => setProcessingScope('audio_visual')}
              />
              <span className="processing-scope-icon">
                <ScanText size={18} aria-hidden="true" />
              </span>
              <VisualAsset
                className="scope-visual"
                asset="scopeAudioVisual"
                width={144}
                height={112}
              />
              <span>
                <strong>{text('完整音画', 'Audio + video')}</strong>
                <small>{text('语音、字幕、画面变化、屏幕文字与关键帧进入同一时间轴', 'Speech, subtitles, visual changes, on-screen text, and keyframes share one timeline')}</small>
              </span>
            </label>
            <label
              className={`${draft.processingScope === 'audio_only' ? 'is-selected' : ''} ${!audioOnlySupported ? 'is-disabled' : ''}`.trim()}
            >
              <input
                type="radio"
                name="processing-scope"
                value="audio_only"
                checked={draft.processingScope === 'audio_only'}
                disabled={scopeLocked || !audioOnlySupported}
                onChange={() => setProcessingScope('audio_only')}
              />
              <span className="processing-scope-icon">
                <AudioLines size={18} aria-hidden="true" />
              </span>
              <VisualAsset
                className="scope-visual"
                asset="scopeAudioOnly"
                width={144}
                height={112}
              />
              <span>
                <strong>{text('仅识别音频', 'Audio only')}</strong>
                <small>{text('跳过视觉扫描、OCR 与截图，只处理平台字幕、音轨和语音时间戳', 'Skip visual scans, OCR, and screenshots; process platform subtitles, audio, and speech timestamps only')}</small>
              </span>
            </label>
          </div>
          <p className={`processing-scope-note scope-${draft.processingScope}`} aria-live="polite">
            {!audioOnlySupported
              ? text('后端版本不支持仅音频；请升级本地后端后再选择。完整音画仍可继续使用。', 'This backend version does not support audio-only mode. Upgrade the local backend to enable it; Audio + video remains available.')
              : draft.processingScope === 'audio_only'
              ? text('适合播客、访谈、配图无关或已确认画面没有重要信息的视频。Fast / Balanced / Accurate 仍会控制 ASR 搜索与复核策略；准确度取决于字幕质量、音质、语言提示和所选 ASR 模型，专有名词仍应人工复核。', 'Suitable for podcasts, interviews, unrelated B-roll, or videos whose visuals are known to contain no important information. Fast / Balanced / Accurate still control ASR search and review. Accuracy depends on subtitle quality, audio quality, language hints, and the selected ASR model; review proper nouns manually.')
              : text('适合软件教程、演示文稿、带字幕图表或界面操作。系统会对齐音频与视觉证据，因此处理时间通常明显高于仅音频。', 'Suitable for software tutorials, presentations, captioned charts, or interface interactions. Audio and visual evidence are aligned, so processing usually takes substantially longer than audio only.')}
          </p>
        </section>

        <div className="mode-grid" role="radiogroup" aria-label={text('处理模式', 'Processing mode')}>
          {(Object.keys(modeCopy) as ProcessingMode[]).map(mode => {
            const item = modeCopy[mode]
            const Icon = item.icon
            const checked = draft.mode === mode
            return (
              <label className={`mode-option ${checked ? 'is-selected' : ''}`} key={mode}>
                <input
                  type="radio"
                  name="processing-mode"
                  value={mode}
                  checked={checked}
                  onChange={() => setDraftMode(mode)}
                />
                <VisualAsset
                  className="mode-visual"
                  asset={modeVisualByMode[mode]}
                  width={132}
                  height={132}
                />
                <span className="mode-icon">
                  <Icon size={19} aria-hidden="true" />
                </span>
                <span className="mode-copy">
                  <span className="mode-title-line">
                    <strong>{item.label}</strong>
                    <span>
                      <Timer size={13} aria-hidden="true" />
                      {estimateLabel(mode)}
                    </span>
                  </span>
                  <span className="mode-description">
                    {draft.processingScope === 'audio_only'
                      ? text(item.audioDescription, item.audioDescriptionEn)
                      : text(item.description, item.descriptionEn)}
                  </span>
                  <span className="mode-bullets">
                    {(draft.processingScope === 'audio_only'
                      ? locale === 'zh-CN' ? item.audioBullets : item.audioBulletsEn
                      : locale === 'zh-CN' ? item.bullets : item.bulletsEn
                    ).map(bullet => (
                      <span key={bullet}>
                        <Check size={12} aria-hidden="true" />
                        {bullet}
                      </span>
                    ))}
                  </span>
                </span>
              </label>
            )
          })}
        </div>
        {draft.manifest && processingEstimateStatus !== 'idle' && (
          <p
            className={`mode-estimate-note estimate-${processingEstimateStatus}`}
            aria-live="polite"
            title={processingEstimateError}
          >
            {processingEstimateStatus === 'loading'
              ? text(
                  `正在根据这台电脑、当前视频与${draft.processingScope === 'audio_only' ? '仅音频' : '完整音画'}范围计算三档耗时区间…`,
                  `Calculating three processing-time ranges for this computer, video, and ${draft.processingScope === 'audio_only' ? 'audio-only' : 'audio + video'} scope…`,
                )
              : processingEstimateStatus === 'ready'
                ? text(
                    `${draft.processingScope === 'audio_only' ? '仅音频' : '完整音画'}三档均为当前硬件的宽区间工程预算，不是当前模型实测或速度保证。`,
                    `All three ${draft.processingScope === 'audio_only' ? 'audio-only' : 'audio + video'} estimates are broad engineering budgets for the current hardware, not measured performance or a speed guarantee for the selected models.`,
                  )
                : processingEstimateStatus === 'partial'
                  ? text('部分本机估算暂不可用；未返回的模式继续显示通用区间。', 'Some local estimates are unavailable; modes without results continue to show generic ranges.')
                  : text('本机估算暂不可用，当前显示通用区间；这不会阻止处理。', 'Local estimates are unavailable, so generic ranges are shown. This does not block processing.')}
          </p>
        )}
      </section>

      <section className="advanced-options" data-expanded={advancedOpen}>
        <button
          className="advanced-options-trigger"
          type="button"
          aria-expanded={advancedOpen}
          aria-controls={advancedOptionsId}
          onClick={() => setAdvancedOpen(value => !value)}
        >
          <span>
            {text('高级选项', 'Advanced options')}
            <small
              className={
                samplingValidation.errors.length > 0
                  ? 'create-advanced-error-summary'
                  : undefined
              }
            >
              {samplingValidation.errors.length > 0
                ? text(`采样计划有 ${samplingValidation.errors.length} 项错误，修正后才能开始`, `The sampling plan has ${samplingValidation.errors.length} errors to fix before starting`)
                : draft.processingScope === 'audio_only'
                  ? text('身份、语言、语音识别策略与报告输出', 'Identity, language, speech-recognition strategy, and report output')
                  : text('身份、语言、画面采样计划与报告输出', 'Identity, language, visual sampling plan, and report output')}
            </small>
          </span>
          <ChevronDown size={17} aria-hidden="true" />
        </button>
        <MotionPresence
          show={advancedOpen}
          className="motion-presence-advanced-options"
          exitMs={140}
        >
          {advancedOpen && (
            <div className="advanced-options-content" id={advancedOptionsId}>
              <div className="advanced-grid">
          <label>
            {text('语言提示', 'Language hints')}
            <input
              value={draft.languageHints}
              onChange={event => setLanguageHints(event.target.value)}
              placeholder={text('自动；或 zh-CN,en（逗号分隔）', 'Auto, or zh-CN,en (comma-separated)')}
            />
          </label>
          <label>
            {text('账号读取方式', 'Account access method')}
            <select
              value={draft.authKind}
              onChange={event =>
                setDraftAuthKind(event.target.value as 'none' | 'browser_profile' | 'cookie_file')
              }
            >
              <option value="none">{text('游客模式', 'Guest mode')}</option>
              <option value="browser_profile">{text('本机已登录浏览器 Profile', 'Signed-in local browser profile')}</option>
              <option value="cookie_file">{text('显式 cookies.txt', 'Explicit cookies.txt')}</option>
            </select>
          </label>
          <div className="motion-swap-stack advanced-auth-fields">
            <MotionPresence
              show={draft.authKind === 'browser_profile'}
              className="motion-presence-swap"
              exitMs={140}
              animateInitial={false}
            >
              {draft.authKind === 'browser_profile' && (
                <div className="advanced-auth-browser-fields">
                  <label>
                    {text('浏览器', 'Browser')}
                    <select
                      value={draft.browser}
                      onChange={event =>
                        setDraftBrowser(event.target.value as 'chrome' | 'edge' | 'firefox')
                      }
                    >
                      <option value="edge">Microsoft Edge</option>
                      <option value="chrome">Google Chrome</option>
                      <option value="firefox">Mozilla Firefox</option>
                    </select>
                  </label>
                  <label>
                    {text('已登录 Profile', 'Signed-in profile')}
                    <select
                      value={draft.profile}
                      onChange={event => setDraftProfile(event.target.value)}
                    >
                      <option value="">{text('请选择', 'Select a profile')}</option>
                      {matchingProfiles.map(profile => (
                        <option value={profile.profileId} key={`${profile.browser}-${profile.path}`}>
                          {profile.displayName}
                          {profile.isDefault ? ` · ${text('默认', 'Default')}` : ''}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
              )}
            </MotionPresence>
            <MotionPresence
              show={draft.authKind === 'cookie_file'}
              className="motion-presence-swap"
              exitMs={140}
              animateInitial={false}
            >
              {draft.authKind === 'cookie_file' && (
                <label className="advanced-span">
                  {text('cookies.txt 绝对路径', 'Absolute cookies.txt path')}
                  <input
                    value={draft.cookieFile}
                    onChange={event => setDraftCookieFile(event.target.value)}
                    placeholder="D:\Private\youtube.cookies.txt"
                  />
                </label>
              )}
            </MotionPresence>
          </div>
              </div>
              <CreateProcessingOptions />
            </div>
          )}
        </MotionPresence>
      </section>

      <footer className="create-actions">
        <div>
          <span>{machine.gpu}</span>
          <span>{text('产物仅保存在本机', 'Artifacts stay on this computer')}</span>
        </div>
        <button
          ref={createTaskButtonRef}
          className="button button-primary button-large"
          type="button"
          onClick={createTask}
          disabled={
            submissionInFlight ||
            jobPreflightStatus === 'loading' ||
            draft.status !== 'ready' ||
            samplingValidation.errors.length > 0 ||
            (draft.processingScope === 'audio_only' && !audioOnlySupported)
          }
        >
          {jobPreflightStatus === 'loading'
            ? text('正在检查依赖…', 'Checking dependencies…')
            : submissionInFlight || draft.status === 'submitting'
            ? text('正在提交…', 'Submitting…')
            : jobPreflight?.state === 'blocked'
              ? text('重新检查依赖', 'Check dependencies again')
            : manifestNeedsRefresh
              ? text('需重新探测', 'Probe again first')
              : text('开始处理', 'Start processing')}
          {jobPreflightStatus === 'loading' || submissionInFlight || draft.status === 'submitting' ? (
            <RefreshCw className="spin" size={17} aria-hidden="true" />
          ) : (
            <ArrowRight size={17} aria-hidden="true" />
          )}
        </button>
      </footer>

      <MotionPresence
        show={jobPreflightStatus === 'error'}
        className="motion-presence-inline-feedback"
        exitMs={120}
      >
        {jobPreflightStatus === 'error' && (
          <div className="inline-error create-preflight-error" role="alert">
            {text(
              '依赖检查失败，任务没有提交。请确认本机后端可用后重试，或打开“设置”检查运行时。',
              'Dependency checking failed and the task was not submitted. Confirm that the local backend is available, then retry or open Settings to inspect runtimes.',
            )}
          </div>
        )}
      </MotionPresence>

      <DependencyPreflightDialog restoreFocusRef={createTaskButtonRef} />
    </div>
  )
}

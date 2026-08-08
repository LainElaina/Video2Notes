import { useId, useState } from 'react'
import {
  AudioLines,
  ArrowRight,
  Check,
  ChevronDown,
  FileVideo2,
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
import { formatTime, platformLabel } from '../domain'
import type { ProcessingMode } from '../domain'
import { validateSamplingDraft } from '../sampling'
import { backendSupportsAudioOnly, useStudioStore } from '../store'

const modeCopy: Record<
  ProcessingMode,
  {
    label: string
    description: string
    audioDescription: string
    estimate: string
    bullets: string[]
    audioBullets: string[]
    icon: typeof Gauge
  }
> = {
  fast: {
    label: 'Fast',
    description: '预期精度：语音主线可靠，减少视觉复核与截图密度',
    audioDescription: '速度优先：优先现有字幕，并使用轻量语音识别策略',
    estimate: '基准实测 1.17× · 探测后显示本机预算',
    bullets: ['平台字幕优先', '单路本地 ASR', '精选关键画面'],
    audioBullets: ['平台字幕优先', '轻量 ASR 搜索', '跳过视觉与 OCR'],
    icon: Gauge,
  },
  balanced: {
    label: 'Balanced',
    description: '预期精度：高，平衡视觉密度、冲突复核与资源消耗',
    audioDescription: '默认推荐：平衡语音搜索、时间戳质量与字幕/语言冲突复核',
    estimate: '基准实测 3.68× · 探测后显示本机预算',
    bullets: ['字幕 + 本地 ASR', '冲突片段复核', '每章精选截图'],
    audioBullets: ['字幕 + 本地 ASR', '时间戳对齐', '字幕/语言冲突复核'],
    icon: Timer,
  },
  accurate: {
    label: 'Accurate',
    description: '预期精度：最高，把额外算力集中在低置信与内容变化处',
    audioDescription: '语音优先：把额外 ASR 计算集中在低置信、多语言和冲突片段',
    estimate: '基准实测 6.66× · 探测后显示本机预算',
    bullets: ['选择性二次识别', '更细视觉与 OCR', '事实支持度验证'],
    audioBullets: ['更完整 ASR 搜索', '多语言提示', '选择性二次识别'],
    icon: Sparkles,
  },
}

export function CreatePage() {
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
  const advancedOptionsId = useId()
  const audioOnlySupported = backendSupportsAudioOnly(backend)
  const scopeLocked = submissionInFlight || draft.status === 'submitting'
  const samplingValidation = validateSamplingDraft(draft)
  const manifestNeedsRefresh = Boolean(
    draft.manifest && draft.status !== 'ready' && draft.status !== 'submitting',
  )

  const estimateLabel = (mode: ProcessingMode): string => {
    const estimate = processingEstimates[mode]
    if (!estimate) {
      return draft.processingScope === 'audio_only'
        ? '仅音频 · 等待本机估算'
        : modeCopy[mode].estimate
    }
    const lowerRtf = estimate.lowerRealtimeFactor.toFixed(
      estimate.lowerRealtimeFactor >= 1 ? 1 : 2,
    )
    const upperRtf = estimate.upperRealtimeFactor.toFixed(
      estimate.upperRealtimeFactor >= 1 ? 1 : 2,
    )
    return `本机工程预算 ${formatTime(Math.ceil(estimate.lowerSeconds))}–${formatTime(
      Math.ceil(estimate.upperSeconds),
    )} · ${lowerRtf}–${upperRtf}×`
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
        <span className="section-kicker">EVIDENCE-FIRST VIDEO NOTES</span>
        <h2>将视频变成可核查的笔记</h2>
        <p>
          下载或导入视频后，语音、屏幕文字和关键画面会进入同一条真实时间轴，再生成可回看的
          Markdown。
        </p>
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
          <label htmlFor="source-url">视频链接</label>
          <input
            id="source-url"
            value={draft.input}
            onChange={event => setDraftInput(event.target.value)}
            onKeyDown={event => {
              if (event.key === 'Enter') probeSource()
            }}
            placeholder="粘贴 Bilibili、YouTube 或 X 链接"
          />
          <span>也可以把视频直接拖到此区域</span>
        </div>
        <button className="button button-primary" type="button" onClick={probeSource}>
          {draft.status === 'probing' ? (
            <RefreshCw className="spin" size={16} aria-hidden="true" />
          ) : (
            <Globe2 size={16} aria-hidden="true" />
          )}
          {draft.status === 'probing' ? '正在探测…' : '探测来源'}
        </button>
        <div className="source-divider" aria-hidden="true">
          <span>或</span>
        </div>
        <div className="source-local-actions">
          <button
            className="button button-secondary"
            type="button"
            onClick={chooseLocalFile}
          >
            <Upload size={16} aria-hidden="true" />
            选择本地视频
          </button>
          <button
            className="source-demo-button"
            type="button"
            onClick={chooseBundledDemo}
          >
            内置样例
          </button>
        </div>
      </section>

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
            aria-label={manifestNeedsRefresh ? '来源探测结果，需要重新探测' : '来源探测结果'}
          >
          <div className="manifest-thumb" aria-hidden="true">
            <FileVideo2 size={28} />
            <span>{platformLabel[draft.manifest.platform]}</span>
          </div>
          <div className="manifest-main">
            <span className="section-kicker">
              {manifestNeedsRefresh
                ? draft.status === 'probing'
                  ? 'SOURCE RECHECKING'
                  : 'SOURCE NEEDS REFRESH'
                : 'SOURCE VERIFIED'}
            </span>
            <h3>{draft.manifest.title}</h3>
            <p>
              {draft.manifest.author} · {formatTime(draft.manifest.durationSeconds)} ·{' '}
              {draft.manifest.sourceLabel}
            </p>
          </div>
          <dl className="manifest-specs">
            <div>
              <dt>实际画质</dt>
              <dd>{draft.manifest.quality}</dd>
            </div>
            <div>
              <dt>音视频</dt>
              <dd>
                {draft.manifest.codec} · {draft.manifest.audio}
              </dd>
            </div>
            <div>
              <dt>字幕</dt>
              <dd>{draft.manifest.subtitle}</dd>
            </div>
            <div>
              <dt>浏览器身份</dt>
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
                  ? '正在按当前来源、身份和处理模式重新探测；旧结果暂仅供参考。'
                  : '来源或探测策略已变化，当前信息仅供参考；重新探测后才能开始处理。'
                : '已锁定当前可访问的最佳画质；若下载结果降低清晰度，任务会停止并说明原因。'}
            </span>
            {manifestNeedsRefresh && draft.status !== 'probing' && (
              <button className="manifest-reprobe" type="button" onClick={probeSource}>
                重新探测
              </button>
            )}
          </div>
          </section>
        )}
      </MotionPresence>

      <section className="mode-section">
        <div className="section-heading">
          <div>
            <span className="section-kicker">PROCESSING PROFILE</span>
            <h3>选择处理模式</h3>
          </div>
          <div className="hardware-fit">
            <Check size={14} aria-hidden="true" />
            {machine.backend === 'ready' ? `已根据 ${machine.gpu} 调整` : backend.detail}
          </div>
        </div>
        <ProcessingBenchmarkGuide />

        <section className="processing-scope" aria-labelledby="processing-scope-title">
          <div className="processing-scope-heading">
            <div>
              <span className="section-kicker">PROCESSING SCOPE</span>
              <h4 id="processing-scope-title">选择要识别的内容</h4>
            </div>
            <span>仅音频可跳过本次基准中最耗时的视觉与 OCR 阶段</span>
          </div>
          <div className="processing-scope-options" role="radiogroup" aria-label="处理范围">
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
              <span>
                <strong>完整音画</strong>
                <small>语音、字幕、画面变化、屏幕文字与关键帧进入同一时间轴</small>
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
              <span>
                <strong>仅识别音频</strong>
                <small>跳过视觉扫描、OCR 与截图，只处理平台字幕、音轨和语音时间戳</small>
              </span>
            </label>
          </div>
          <p className={`processing-scope-note scope-${draft.processingScope}`} aria-live="polite">
            {!audioOnlySupported
              ? '后端版本不支持仅音频；请升级本地后端后再选择。完整音画仍可继续使用。'
              : draft.processingScope === 'audio_only'
              ? '适合播客、访谈、配图无关或已确认画面没有重要信息的视频。Fast / Balanced / Accurate 仍会控制 ASR 搜索与复核策略；准确度取决于字幕质量、音质、语言提示和所选 ASR 模型，专有名词仍应人工复核。'
              : '适合软件教程、演示文稿、带字幕图表或界面操作。系统会对齐音频与视觉证据，因此处理时间通常明显高于仅音频。'}
          </p>
        </section>

        <div className="mode-grid" role="radiogroup" aria-label="处理模式">
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
                      ? item.audioDescription
                      : item.description}
                  </span>
                  <span className="mode-bullets">
                    {(draft.processingScope === 'audio_only'
                      ? item.audioBullets
                      : item.bullets
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
              ? `正在根据这台电脑、当前视频与${draft.processingScope === 'audio_only' ? '仅音频' : '完整音画'}范围计算三档耗时区间…`
              : processingEstimateStatus === 'ready'
                ? `${draft.processingScope === 'audio_only' ? '仅音频' : '完整音画'}三档均为当前硬件的宽区间工程预算，不是当前模型实测或速度保证。`
                : processingEstimateStatus === 'partial'
                  ? '部分本机估算暂不可用；未返回的模式继续显示通用区间。'
                  : '本机估算暂不可用，当前显示通用区间；这不会阻止处理。'}
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
            高级选项
            <small
              className={
                samplingValidation.errors.length > 0
                  ? 'create-advanced-error-summary'
                  : undefined
              }
            >
              {samplingValidation.errors.length > 0
                ? `采样计划有 ${samplingValidation.errors.length} 项错误，修正后才能开始`
                : draft.processingScope === 'audio_only'
                  ? '身份、语言、语音识别策略与报告输出'
                  : '身份、语言、画面采样计划与报告输出'}
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
            语言提示
            <input
              value={draft.languageHints}
              onChange={event => setLanguageHints(event.target.value)}
              placeholder="自动；或 zh-CN,en（逗号分隔）"
            />
          </label>
          <label>
            账号读取方式
            <select
              value={draft.authKind}
              onChange={event =>
                setDraftAuthKind(event.target.value as 'none' | 'browser_profile' | 'cookie_file')
              }
            >
              <option value="none">游客模式</option>
              <option value="browser_profile">本机已登录浏览器 Profile</option>
              <option value="cookie_file">显式 cookies.txt</option>
            </select>
          </label>
          {draft.authKind === 'browser_profile' && (
            <>
              <label className="motion-field-enter">
                浏览器
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
              <label className="motion-field-enter">
                已登录 Profile
                <select
                  value={draft.profile}
                  onChange={event => setDraftProfile(event.target.value)}
                >
                  <option value="">请选择</option>
                  {matchingProfiles.map(profile => (
                    <option value={profile.profileId} key={`${profile.browser}-${profile.path}`}>
                      {profile.displayName}
                      {profile.isDefault ? ' · 默认' : ''}
                    </option>
                  ))}
                </select>
              </label>
            </>
          )}
          {draft.authKind === 'cookie_file' && (
            <label className="advanced-span motion-field-enter">
              cookies.txt 绝对路径
              <input
                value={draft.cookieFile}
                onChange={event => setDraftCookieFile(event.target.value)}
                placeholder="D:\Private\youtube.cookies.txt"
              />
            </label>
          )}
              </div>
              <CreateProcessingOptions />
            </div>
          )}
        </MotionPresence>
      </section>

      <footer className="create-actions">
        <div>
          <span>{machine.gpu}</span>
          <span>产物仅保存在本机</span>
        </div>
        <button
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
            ? '正在检查依赖…'
            : submissionInFlight || draft.status === 'submitting'
            ? '正在提交…'
            : jobPreflight?.state === 'blocked'
              ? '重新检查依赖'
            : manifestNeedsRefresh
              ? '需重新探测'
              : '开始处理'}
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
            依赖检查失败，任务没有提交。请确认本机后端可用后重试，或打开“模型与性能”检查运行时。
          </div>
        )}
      </MotionPresence>

      <DependencyPreflightDialog />
    </div>
  )
}

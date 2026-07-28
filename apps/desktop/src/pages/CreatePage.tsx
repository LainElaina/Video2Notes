import { useState } from 'react'
import {
  ArrowRight,
  Check,
  ChevronDown,
  FileVideo2,
  Gauge,
  Globe2,
  MonitorUp,
  ShieldCheck,
  Sparkles,
  Timer,
  Upload,
} from 'lucide-react'
import { formatTime, platformLabel } from '../domain'
import type { ProcessingMode } from '../domain'
import { useStudioStore } from '../store'

const modeCopy: Record<
  ProcessingMode,
  {
    label: string
    description: string
    estimate: string
    bullets: string[]
    icon: typeof Gauge
  }
> = {
  fast: {
    label: 'Fast',
    description: '快速获得结构化、带时间戳的可靠笔记',
    estimate: '预计 8–12 分钟',
    bullets: ['平台字幕优先', '单路本地 ASR', '精选关键画面'],
    icon: Gauge,
  },
  balanced: {
    label: 'Balanced',
    description: '日常默认：在速度、视觉密度与证据校验之间取平衡',
    estimate: '预计 12–20 分钟',
    bullets: ['字幕 + 本地 ASR', '冲突片段复核', '每章精选截图'],
    icon: Timer,
  },
  accurate: {
    label: 'Accurate',
    description: '把更多算力集中在低置信与内容变化处',
    estimate: '预计 20–31 分钟',
    bullets: ['选择性二次识别', '更细视觉与 OCR', '事实支持度验证'],
    icon: Sparkles,
  },
}

export function CreatePage() {
  const draft = useStudioStore(state => state.draft)
  const setDraftInput = useStudioStore(state => state.setDraftInput)
  const setDraftMode = useStudioStore(state => state.setDraftMode)
  const probeSource = useStudioStore(state => state.probeSource)
  const chooseLocalFile = useStudioStore(state => state.chooseLocalFile)
  const chooseBundledDemo = useStudioStore(state => state.chooseBundledDemo)
  const selectLocalFile = useStudioStore(state => state.selectLocalFile)
  const createTask = useStudioStore(state => state.createTask)
  const backend = useStudioStore(state => state.backend)
  const machine = useStudioStore(state => state.machine)
  const browserProfiles = useStudioStore(state => state.browserProfiles)
  const setDraftAuthKind = useStudioStore(state => state.setDraftAuthKind)
  const setDraftBrowser = useStudioStore(state => state.setDraftBrowser)
  const setDraftProfile = useStudioStore(state => state.setDraftProfile)
  const setDraftCookieFile = useStudioStore(state => state.setDraftCookieFile)
  const setLanguageHints = useStudioStore(state => state.setLanguageHints)
  const setIncludeScreenshots = useStudioStore(state => state.setIncludeScreenshots)
  const setGeneratePdf = useStudioStore(state => state.setGeneratePdf)
  const [dragActive, setDragActive] = useState(false)

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
          <Globe2 size={16} aria-hidden="true" />
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

      {draft.error && (
        <div className="inline-error" role="alert">
          {draft.error}
        </div>
      )}

      {draft.manifest && (
        <section className="source-manifest" aria-label="来源探测结果">
          <div className="manifest-thumb" aria-hidden="true">
            <FileVideo2 size={28} />
            <span>{platformLabel[draft.manifest.platform]}</span>
          </div>
          <div className="manifest-main">
            <span className="section-kicker">SOURCE VERIFIED</span>
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
          <div className="quality-promise">
            <ShieldCheck size={16} aria-hidden="true" />
            已锁定当前可访问的最佳画质；若下载结果降低清晰度，任务会停止并说明原因。
          </div>
        </section>
      )}

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
                      {item.estimate}
                    </span>
                  </span>
                  <span className="mode-description">{item.description}</span>
                  <span className="mode-bullets">
                    {item.bullets.map(bullet => (
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
      </section>

      <details className="advanced-options">
        <summary>
          <span>
            高级选项
            <small>语言提示、截图预算和阶段角色覆盖</small>
          </span>
          <ChevronDown size={17} aria-hidden="true" />
        </summary>
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
              <label>
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
              <label>
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
            <label className="advanced-span">
              cookies.txt 绝对路径
              <input
                value={draft.cookieFile}
                onChange={event => setDraftCookieFile(event.target.value)}
                placeholder="D:\Private\youtube.cookies.txt"
              />
            </label>
          )}
          <label className="advanced-check">
            <input
              type="checkbox"
              checked={draft.includeScreenshots}
              onChange={event => setIncludeScreenshots(event.target.checked)}
            />
            将证据筛选出的关键帧嵌入笔记
          </label>
          <label className="advanced-check">
            <input
              type="checkbox"
              checked={draft.generatePdf}
              onChange={event => setGeneratePdf(event.target.checked)}
            />
            同时生成离线 PDF
          </label>
        </div>
      </details>

      <footer className="create-actions">
        <div>
          <span>{machine.gpu}</span>
          <span>产物仅保存在本机</span>
        </div>
        <button
          className="button button-primary button-large"
          type="button"
          onClick={createTask}
          disabled={draft.status !== 'ready'}
        >
          {draft.status === 'submitting' ? '正在提交…' : '开始处理'}
          <ArrowRight size={17} aria-hidden="true" />
        </button>
      </footer>
    </div>
  )
}

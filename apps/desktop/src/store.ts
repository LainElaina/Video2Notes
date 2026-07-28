import { create } from 'zustand'
import {
  Video2NotesApi,
  Video2NotesApiError,
  bundledDemoVideoPath,
  localArtifactUrl,
  pickVideoWithNativeDialog,
  resolveBackendConnection,
} from './api'
import type {
  ApiAcquisitionPolicy,
  ApiAuthSpec,
  ApiEvidenceSpan,
  ApiJobSnapshot,
  ApiModelRegistry,
  ApiNoteDocument,
  ApiProcessingRun,
  ApiRunManifest,
  ApiSourceInput,
  ApiSourceManifest,
  PipelineSubmission,
} from './api'
import {
  evidenceFixture,
  machineFixture,
  makeStages,
  makeTaskFixtures,
  modelFixtures,
  noteFixture,
  providerFixtures,
  roleFixtures,
} from './fixtures'
import type {
  BackendProfile,
  BrowserProfile,
  DraftState,
  EvidenceItem,
  MachineProfile,
  ModelDefinition,
  NoteDocument,
  ProcessingMode,
  ProcessingTask,
  ProviderDefinition,
  RoleBinding,
  SourceManifest,
  TaskStage,
  ViewId,
} from './domain'

interface StudioData {
  view: ViewId
  contextCollapsed: boolean
  activeTaskId: string
  selectedProviderId: string
  selectedEvidenceId?: string
  currentTimeSeconds: number
  taskSearch: string
  notice?: string
  backend: BackendProfile
  machine: MachineProfile
  browserProfiles: BrowserProfile[]
  draft: DraftState
  tasks: ProcessingTask[]
  providers: ProviderDefinition[]
  models: ModelDefinition[]
  roles: RoleBinding[]
}

interface StudioActions {
  initializeBackend: () => void
  refreshTasks: () => void
  navigate: (view: ViewId) => void
  toggleContext: () => void
  selectTask: (taskId: string, destination?: 'tasks' | 'reader') => void
  setTaskSearch: (value: string) => void
  setDraftInput: (input: string) => void
  setDraftMode: (mode: ProcessingMode) => void
  setDraftAuthKind: (kind: DraftState['authKind']) => void
  setDraftBrowser: (browser: DraftState['browser']) => void
  setDraftProfile: (profile: string) => void
  setDraftCookieFile: (path: string) => void
  setLanguageHints: (value: string) => void
  setIncludeScreenshots: (value: boolean) => void
  setGeneratePdf: (value: boolean) => void
  probeSource: () => void
  chooseLocalFile: () => void
  chooseBundledDemo: () => void
  selectLocalFile: (fileName: string, sizeBytes: number) => void
  createTask: () => void
  advanceTasks: () => void
  pauseTask: (taskId: string) => void
  resumeTask: (taskId: string) => void
  cancelTask: (taskId: string) => void
  restartTask: (taskId: string) => void
  setCurrentTime: (seconds: number) => void
  selectEvidence: (evidenceId: string, timeSeconds?: number) => void
  selectProvider: (providerId: string) => void
  addProvider: (input: {
    id: string
    name: string
    kind: 'openai_compatible' | 'ollama'
    baseUrl: string
    modelId: string
    vision: boolean
  }) => void
  testProvider: (providerId: string) => void
  saveProviderSecret: (providerId: string, secret: string) => void
  deleteProviderSecret: (providerId: string) => void
  bindRole: (roleId: string, modelId: string) => void
  downloadArtifact: (taskId: string, kind: 'markdown' | 'html' | 'pdf') => void
  clearNotice: () => void
  resetDemo: () => void
}

export type StudioStore = StudioData & StudioActions

let api: Video2NotesApi | undefined
let registry: ApiModelRegistry | undefined
let pollInFlight = false
const sourceByRun = new Map<string, SourceManifest>()
const submissionByRun = new Map<string, PipelineSubmission>()
const objectUrls = new Map<string, string>()

const emptyDraft = (): DraftState => ({
  input: '',
  mode: 'balanced',
  status: 'idle',
  sourceKind: 'url',
  authKind: 'none',
  browser: 'edge',
  profile: '',
  cookieFile: '',
  languageHints: '',
  includeScreenshots: true,
  generatePdf: true,
})

const demoDraft = (): DraftState => ({
  ...emptyDraft(),
  input: 'https://www.bilibili.com/video/BV1EvidenceDemo',
  mode: 'accurate',
})

const initialData = (demo = false): StudioData => ({
  view: 'create',
  contextCollapsed: false,
  activeTaskId: demo ? 'task-running' : '',
  selectedProviderId: demo ? 'provider-local' : '',
  currentTimeSeconds: demo ? 92 : 0,
  taskSearch: '',
  backend: demo
    ? { mode: 'demo', version: 'fixture', detail: '显式视觉演示；未连接处理后端' }
    : { mode: 'connecting', detail: '正在启动本机处理后端…' },
  machine: demo
    ? machineFixture
    : {
        cpu: '正在探测本机硬件',
        gpu: '后端连接中',
        memory: '—',
        backend: 'starting',
      },
  browserProfiles: [],
  draft: demo ? demoDraft() : emptyDraft(),
  tasks: demo ? makeTaskFixtures() : [],
  providers: demo ? providerFixtures.map(provider => ({ ...provider })) : [],
  models: demo
    ? modelFixtures.map(model => ({ ...model, capabilities: [...model.capabilities] }))
    : [],
  roles: demo
    ? roleFixtures.map(role => ({
        ...role,
        requiredCapabilities: [...role.requiredCapabilities],
      }))
    : [],
})

const isUrl = (value: string): boolean => /^https?:\/\//i.test(value.trim())
const isWindowsAbsolutePath = (value: string): boolean =>
  /^(?:[A-Za-z]:[\\/]|\\\\)/.test(value.trim())

const detectSourceFixture = (input: string): SourceManifest | undefined => {
  const normalized = input.trim().toLowerCase()
  if (normalized.includes('bilibili.com') || normalized.includes('b23.tv')) {
    return {
      id: `source-${Date.now()}`,
      platform: 'bilibili',
      title: '从视频到可追溯知识：统一证据时间轴',
      author: 'Lain Lab',
      durationSeconds: 1518,
      quality: '1080p · 60 fps',
      codec: 'AVC / yuv420p',
      audio: 'AAC · 48 kHz',
      subtitle: '中文平台字幕',
      authLabel: 'Edge · Profile 2',
      sourceLabel: input.trim(),
    }
  }
  if (normalized.includes('youtube.com') || normalized.includes('youtu.be')) {
    return {
      id: `source-${Date.now()}`,
      platform: 'youtube',
      title: 'Technical lecture · detected source',
      author: 'YouTube creator',
      durationSeconds: 2142,
      quality: '1440p · 30 fps',
      codec: 'VP9 / yuv420p',
      audio: 'Opus · 48 kHz',
      subtitle: 'English auto captions',
      authLabel: 'Chrome · Default',
      sourceLabel: input.trim(),
    }
  }
  if (normalized.includes('x.com') || normalized.includes('twitter.com')) {
    return {
      id: `source-${Date.now()}`,
      platform: 'x',
      title: 'X video · detected source',
      author: 'X creator',
      durationSeconds: 184,
      quality: '720p · 30 fps',
      codec: 'H.264 / yuv420p',
      audio: 'AAC · 44.1 kHz',
      subtitle: '未发现平台字幕',
      authLabel: 'Edge · Profile 2',
      sourceLabel: input.trim(),
    }
  }
  return undefined
}

const formatMemory = (bytes?: number): string =>
  bytes ? `${(bytes / 1024 ** 3).toFixed(1)} GB RAM` : '内存未知'

const mapMachine = (report: Awaited<ReturnType<Video2NotesApi['system']>>): MachineProfile => {
  const gpu = report.hardware.gpus[0]
  const gpuMemory = gpu?.memory_total_bytes
    ? ` · ${(gpu.memory_total_bytes / 1024 ** 3).toFixed(1)} GB`
    : ''
  return {
    cpu: `${report.hardware.cpu_name} · ${report.hardware.logical_cores} threads`,
    gpu: gpu ? `${gpu.name}${gpuMemory}` : 'CPU / 核显回退',
    memory: formatMemory(report.hardware.memory_total_bytes),
    backend: 'ready',
  }
}

const selectedFormats = (source: ApiSourceManifest) => {
  const selected = new Set(source.selected_format_ids)
  const formats = source.formats.filter(item => selected.has(item.format_id))
  return formats.length > 0 ? formats : source.formats
}

const authLabel = (draft: DraftState, source?: ApiSourceManifest): string => {
  if ((source?.auth_kind ?? draft.authKind) === 'browser_profile') {
    return `${draft.browser} · ${draft.profile || '已登录 profile'}`
  }
  if ((source?.auth_kind ?? draft.authKind) === 'cookie_file') return 'cookies.txt · 本机引用'
  return '游客模式'
}

const mapSource = (source: ApiSourceManifest, draft: DraftState): SourceManifest => {
  const formats = selectedFormats(source)
  const video = [...formats]
    .filter(item => item.height || item.width)
    .sort((left, right) => (right.height ?? 0) - (left.height ?? 0))[0]
  const audio = formats.find(item => item.acodec && item.acodec !== 'none')
  const subtitleLanguages = Object.keys(source.subtitles)
  const automaticLanguages = Object.keys(source.automatic_captions)
  const subtitle =
    subtitleLanguages.length > 0
      ? `平台字幕 · ${subtitleLanguages.slice(0, 3).join(', ')}`
      : automaticLanguages.length > 0
        ? `自动字幕 · ${automaticLanguages.slice(0, 3).join(', ')}`
        : '未发现平台字幕'
  return {
    id: source.source_id,
    platform: source.platform,
    title: source.title || source.source_id,
    author: source.author || '未知作者',
    durationSeconds: source.duration_seconds ?? 0,
    quality: video?.height
      ? `${video.height}p${video.fps ? ` · ${video.fps.toFixed(2)} fps` : ''}`
      : '下载后确认实际画质',
    codec: video?.vcodec || '下载后读取视频编码',
    audio: audio?.acodec || '未发现独立音轨信息',
    subtitle,
    authLabel: authLabel(draft, source),
    sourceLabel: source.canonical_url || source.source.value,
  }
}

const sourceInput = (draft: DraftState): ApiSourceInput => ({
  kind: draft.sourceKind,
  value: draft.input.trim(),
})

const authSpec = (draft: DraftState): ApiAuthSpec => {
  if (draft.authKind === 'browser_profile') {
    return {
      kind: 'browser_profile',
      browser: draft.browser,
      profile: draft.profile,
    }
  }
  if (draft.authKind === 'cookie_file') {
    return { kind: 'cookie_file', cookie_file: draft.cookieFile.trim() }
  }
  return { kind: 'none' }
}

const acquisitionPolicy = (mode: ProcessingMode): ApiAcquisitionPolicy => ({
  mode: mode === 'fast' ? 'fast' : 'accurate',
  ...(mode === 'balanced' ? { max_height: 1080 } : {}),
  include_subtitles: true,
  include_automatic_captions: true,
  allow_quality_change: false,
  prefer_hardlink: true,
})

const submissionForDraft = (draft: DraftState): PipelineSubmission => ({
  source: sourceInput(draft),
  auth: authSpec(draft),
  acquisition: acquisitionPolicy(draft.mode),
  quality_mode: draft.mode,
  language_hints: draft.languageHints
    .split(',')
    .map(value => value.trim())
    .filter(Boolean),
  include_screenshots: draft.includeScreenshots,
  generate_pdf: draft.generatePdf,
})

const pipelineStages = [
  'source.acquire',
  'media.probe',
  'vision.scan',
  'audio.extract',
  'captions.parse',
  'audio.asr',
  'ocr.extract',
  'evidence.fuse',
  'notes.compose',
  'render.outputs',
] as const

const stageGroups: Record<string, readonly string[]> = {
  acquire: ['source.acquire'],
  normalize: ['media.probe'],
  speech: ['audio.extract', 'captions.parse', 'audio.asr'],
  vision: ['vision.scan', 'ocr.extract'],
  fusion: ['evidence.fuse'],
  draft: ['notes.compose'],
  verify: ['notes.compose'],
  render: ['render.outputs'],
}

const mapStages = (run: ApiRunManifest, job?: ApiJobSnapshot): TaskStage[] =>
  makeStages(0).map(stage => {
    const backendNames = stageGroups[stage.id] ?? []
    const records = backendNames
      .map(name => run.stages[name])
      .filter((record): record is NonNullable<typeof record> => Boolean(record))
    const failed = records.some(record => record.status === 'failed')
    const cancelled = records.some(record => record.status === 'cancelled')
    const active = Boolean(job && backendNames.includes(job.stage) && job.state === 'running')
    const completed =
      records.length === backendNames.length &&
      records.length > 0 &&
      records.every(record => record.status === 'completed')
    const status: TaskStage['status'] =
      failed || cancelled ? 'failed' : active ? 'running' : completed ? 'completed' : 'pending'
    return {
      ...stage,
      status,
      progress: completed ? 100 : active ? Math.round((job?.progress ?? 0.05) * 100) : 0,
      durationSeconds: records.reduce(
        (sum, record) => sum + (record.wall_time_seconds ?? 0),
        0,
      ),
      artifactCount: records.reduce((sum, record) => sum + record.outputs.length, 0),
      metric: active ? job?.message : records.flatMap(record => record.warnings)[0],
    }
  })

const overallProgress = (run: ApiRunManifest, job?: ApiJobSnapshot): number => {
  if (run.status === 'completed' || job?.state === 'completed') return 100
  const completed = pipelineStages.filter(name => run.stages[name]?.status === 'completed').length
  const activeIndex = job ? pipelineStages.indexOf(job.stage as (typeof pipelineStages)[number]) : -1
  const base = Math.max(completed, activeIndex)
  const partial = activeIndex >= 0 ? (job?.progress ?? 0) : 0
  return Math.min(99, Math.max(0, ((base + partial) / pipelineStages.length) * 100))
}

const taskStatus = (
  run: ApiRunManifest,
  job?: ApiJobSnapshot,
): ProcessingTask['status'] => {
  const value = job?.state ?? run.status
  if (value === 'completed') return 'completed'
  if (value === 'failed') return 'failed'
  if (value === 'cancelled') return 'cancelled'
  return 'running'
}

const inferPlatform = (run: ApiRunManifest): SourceManifest['platform'] => {
  const platform = run.source.platform
  if (platform === 'bilibili' || platform === 'youtube' || platform === 'x') return platform
  const locator = run.source.locator.toLowerCase()
  if (locator.includes('bilibili.com') || locator.includes('b23.tv')) return 'bilibili'
  if (locator.includes('youtube.com') || locator.includes('youtu.be')) return 'youtube'
  if (locator.includes('x.com') || locator.includes('twitter.com')) return 'x'
  return 'local'
}

const fallbackSource = (run: ApiRunManifest): SourceManifest => ({
  id: run.source.source_id || run.run_id,
  platform: inferPlatform(run),
  title: run.source.title || run.source.source_id || run.source.locator.split(/[\\/]/).at(-1) || run.run_id,
  author: run.source.author || '未知作者',
  durationSeconds: 0,
  quality: '从任务 artifact 读取',
  codec: '媒体清单',
  audio: '媒体清单',
  subtitle: '字幕清单',
  authLabel: '本机任务',
  sourceLabel: run.source.canonical_url || run.source.locator,
})

const evidenceKind = (item: ApiEvidenceSpan): EvidenceItem['kind'] => {
  if (item.modality === 'ocr') return 'ocr'
  if (item.modality === 'visual') return 'visual'
  if (item.modality === 'metadata') return 'chapter'
  return 'asr'
}

const mapEvidence = (item: ApiEvidenceSpan): EvidenceItem => ({
  id: item.id,
  kind: evidenceKind(item),
  startSeconds: item.start_us / 1_000_000,
  endSeconds: item.end_us / 1_000_000,
  label:
    item.modality === 'platform_caption'
      ? '平台字幕'
      : item.modality === 'asr'
        ? '语音识别'
        : item.modality === 'ocr'
          ? '屏幕文字'
          : item.modality === 'visual'
            ? '视觉状态'
            : '来源信息',
  rawText: item.normalized_text || item.raw_text || '该证据没有可显示的文本。',
  confidence: item.confidence ?? 1,
  provider: [item.provider, item.model].filter(Boolean).join(' · ') || item.modality,
})

const mapNote = (document: ApiNoteDocument): NoteDocument => {
  const facts = new Map(document.facts.map(fact => [fact.id, fact]))
  const evidence = new Map(document.evidence.map(item => [item.id, item]))
  return {
    title: document.metadata.title,
    overview: document.abstract,
    sourceSummary: [
      document.metadata.source_kind,
      `${Math.round(document.metadata.duration_us / 1_000_000)} 秒`,
      document.metadata.source_resolution,
      document.metadata.quality_mode,
      `${document.evidence.length} 条证据`,
    ]
      .filter(Boolean)
      .join(' · '),
    generatedAt: new Date(document.metadata.created_at).toLocaleString('zh-CN'),
    sections: document.sections.map(section => {
      const factClaims = section.fact_ids
        .map(id => facts.get(id))
        .filter((fact): fact is NonNullable<typeof fact> => Boolean(fact))
        .map(fact => {
          const evidenceStartUs = fact.evidence_ids
            .map(id => evidence.get(id)?.start_us)
            .filter((value): value is number => value !== undefined)
            .sort((left, right) => left - right)[0]
          return {
            id: fact.id,
            text: fact.claim,
            timeSeconds: (evidenceStartUs ?? section.start_us) / 1_000_000,
            evidenceIds: fact.evidence_ids,
            emphasis: !fact.needs_review,
          }
        })
      const evidenceClaims = section.evidence_ids
        .map(id => evidence.get(id))
        .filter((item): item is NonNullable<typeof item> => Boolean(item?.raw_text))
        .slice(0, 5)
        .map(item => ({
          id: `claim-${section.id}-${item.id}`,
          text: item.normalized_text || item.raw_text || item.id,
          timeSeconds: item.start_us / 1_000_000,
          evidenceIds: [item.id],
        }))
      return {
        id: section.id,
        title: section.title,
        summary: section.summary || section.body_markdown,
        claims: factClaims.length > 0 ? factClaims : evidenceClaims,
        startSeconds: section.start_us / 1_000_000,
        endSeconds: section.end_us / 1_000_000,
        screenshotAt:
          section.screenshots[0]?.timestamp_us !== undefined
            ? section.screenshots[0].timestamp_us / 1_000_000
            : undefined,
        screenshotPath: section.screenshots[0]?.relative_path,
        screenshotCaption: section.screenshots[0]?.caption,
      }
    }),
  }
}

const stageArtifact = (
  run: ApiRunManifest,
  kind: string,
  suffix?: string,
): string | undefined => {
  for (const stage of Object.values(run.stages)) {
    const match = stage.outputs.find(
      output => output.kind === kind && (!suffix || output.relative_path.endsWith(suffix)),
    )
    if (match) return match.relative_path
  }
  return undefined
}

const taskFromRun = (
  run: ApiRunManifest,
  job?: ApiJobSnapshot,
  sourceOverride?: SourceManifest,
): ProcessingTask => {
  const source = sourceOverride || sourceByRun.get(run.run_id) || fallbackSource(run)
  const media = stageArtifact(run, 'media', undefined)
  return {
    id: run.run_id,
    source,
    mode: run.profile,
    status: taskStatus(run, job),
    progress: overallProgress(run, job),
    etaSeconds: 0,
    createdAt: new Date(run.created_at).toLocaleString('zh-CN'),
    stages: mapStages(run, job),
    evidence: [],
    lastMessage: job?.message,
    warnings: run.warnings,
    artifactPaths: {
      markdown: stageArtifact(run, 'note', '.md'),
      html: stageArtifact(run, 'render', '.html'),
      pdf: stageArtifact(run, 'render', '.pdf'),
      noteDocument: stageArtifact(run, 'note', 'document.json'),
      media,
    },
    realBackend: true,
  }
}

const mapCapabilities = (values: string[]): ModelDefinition['capabilities'] => {
  const result = new Set<ModelDefinition['capabilities'][number]>()
  for (const value of values) {
    if (value === 'text') result.add('text')
    if (value === 'vision' || value === 'video_frame_metrics') result.add('vision')
    if (value === 'asr') result.add('asr')
    if (value === 'ocr' || value.startsWith('ocr_')) result.add('ocr')
    if (value === 'structured_output') result.add('json')
    if (value.includes('timestamp')) result.add('timestamps')
  }
  return [...result]
}

const rolePresentation = (
  id: string,
): Pick<RoleBinding, 'group' | 'label' | 'description' | 'requiredCapabilities'> => {
  if (id.startsWith('asr.')) {
    return {
      group: '语音',
      label: id === 'asr.primary' ? '主要语音识别' : '低置信复核',
      description: id === 'asr.primary' ? '输出分段/词级时间戳' : '只处理不确定或冲突窗口',
      requiredCapabilities: ['asr', 'timestamps'],
    }
  }
  if (id.startsWith('ocr.') || id.startsWith('vision.')) {
    return {
      group: '视觉',
      label: id === 'ocr.primary' ? '屏幕文字' : id.replace('vision.', ''),
      description: '关键帧、变化或屏幕文字处理',
      requiredCapabilities: id === 'ocr.primary' ? ['ocr'] : ['vision'],
    }
  }
  return {
    group: '笔记',
    label: id.endsWith('fact_extractor')
      ? '事实提取'
      : id.endsWith('drafter')
        ? '章节写作'
        : id.endsWith('verifier')
          ? '事实验证'
          : id,
    description: '证据约束的结构化笔记角色',
    requiredCapabilities: id.endsWith('drafter') ? ['text'] : ['text', 'json'],
  }
}

const registryPresentation = (
  value: ApiModelRegistry,
  secretStates: Record<string, string> = {},
): Pick<StudioData, 'providers' | 'models' | 'roles' | 'selectedProviderId'> => {
  const providers: ProviderDefinition[] = Object.values(value.providers).map(provider => ({
    id: provider.id,
    name: provider.display_name,
    kind:
      provider.kind === 'openai_compatible'
        ? 'openai-compatible'
        : provider.kind,
    endpoint: provider.base_url || '本机 worker',
    status: provider.kind === 'local' && provider.enabled ? 'connected' : 'disconnected',
    credentialState:
      provider.kind === 'local'
        ? 'not-needed'
        : secretStates[provider.id] === 'configured' || Boolean(provider.credential_ref)
          ? 'stored-locally'
          : 'missing',
  }))
  const models: ModelDefinition[] = Object.values(value.models).map(model => ({
    id: model.id,
    providerId: model.provider_id,
    label: model.display_name,
    modelId: model.model_id,
    locality: model.locality,
    capabilities: mapCapabilities(model.capabilities),
  }))
  const roles = Object.entries(value.roles).map(([id, binding]) => ({
    id,
    ...rolePresentation(id),
    modelId: binding.primary_model_id,
    fallbackModelId: binding.fallback_model_ids[0],
  }))
  return {
    providers,
    models,
    roles,
    selectedProviderId: providers[0]?.id || '',
  }
}

const errorMessage = (error: unknown): string => {
  if (error instanceof Video2NotesApiError) {
    if (error.status === 401) return '本地会话令牌无效，请重启桌面应用与后端。'
    if (error.status === 422) return `请求未通过校验：${error.message}`
    return error.message
  }
  return error instanceof Error ? error.message : '本地操作失败。'
}

const hydrateTask = async (task: ProcessingTask): Promise<ProcessingTask> => {
  if (!api || !task.realBackend) return task
  let note = task.note
  let evidence = task.evidence
  let source = task.source
  const notePath = task.artifactPaths?.noteDocument || 'notes/document.json'
  if (task.status === 'completed' && !note) {
    try {
      const document = await api.artifactJson<ApiNoteDocument>(task.id, notePath)
      note = mapNote(document)
      evidence = document.evidence.map(mapEvidence)
      source = {
        ...source,
        title: document.metadata.title,
        author: document.metadata.author || source.author,
        durationSeconds: document.metadata.duration_us / 1_000_000,
        quality: document.metadata.source_resolution || source.quality,
      }
    } catch {
      // A completed legacy run may predate NoteDocument persistence.
    }
  }
  if (note?.sections.some(section => section.screenshotPath && !section.screenshotUrl)) {
    note = {
      ...note,
      sections: await Promise.all(
        note.sections.map(async section => {
          if (!section.screenshotPath || section.screenshotUrl) return section
          try {
            const screenshotUrl =
              (await localArtifactUrl(task.id, section.screenshotPath)) ||
              (await api?.artifactBlobUrl(task.id, section.screenshotPath))
            if (screenshotUrl) {
              objectUrls.set(`${task.id}:${section.screenshotPath}`, screenshotUrl)
            }
            return { ...section, screenshotUrl }
          } catch {
            return section
          }
        }),
      ),
    }
  }
  let mediaUrl = task.mediaUrl
  const mediaPath = task.artifactPaths?.media
  if (mediaPath && !mediaUrl) {
    try {
      mediaUrl =
        (await localArtifactUrl(task.id, mediaPath)) ||
        (await api.artifactBlobUrl(task.id, mediaPath))
      objectUrls.set(task.id, mediaUrl)
    } catch {
      // Notes remain readable when the media asset cannot be previewed.
    }
  }
  return { ...task, source, note, evidence, mediaUrl }
}

export const useStudioStore = create<StudioStore>()((set, get) => ({
  ...initialData(false),

  initializeBackend: () => {
    if (get().backend.mode === 'demo' || get().backend.mode === 'real') return
    set({
      backend: { mode: 'connecting', detail: '正在启动本机处理后端…' },
      machine: { ...get().machine, backend: 'starting' },
    })
    void (async () => {
      try {
        const resolved = await resolveBackendConnection()
        if (resolved.mode === 'demo') {
          api = undefined
          registry = undefined
          set(initialData(true))
          return
        }
        const nextApi = new Video2NotesApi(resolved.connection)
        const health = await nextApi.waitForHealth()
        const [system, runtime, profiles, loadedRegistry, runs] = await Promise.all([
          nextApi.system(),
          nextApi.runtime(),
          nextApi.browserProfiles(),
          nextApi.providers(),
          nextApi.listRuns(),
        ])
        api = nextApi
        registry = loadedRegistry
        const secretPairs = await Promise.all(
          Object.values(loadedRegistry.providers)
            .filter(provider => provider.kind !== 'local')
            .map(async provider => {
              try {
                const status = await nextApi.providerSecretStatus(provider.id)
                return [provider.id, status.status] as const
              } catch {
                return [provider.id, 'not_configured'] as const
              }
            }),
        )
        const secretStates = Object.fromEntries(secretPairs)
        const presentation = registryPresentation(loadedRegistry, secretStates)
        const tasks = await Promise.all(
          runs.map(run => hydrateTask(taskFromRun(run))),
        )
        const warnings = runtime.warnings
        set({
          ...presentation,
          backend: {
            mode: 'real',
            version: health.version,
            detail:
              warnings.length > 0
                ? `本地后端可用 · ${warnings.length} 条模型配置提示`
                : '本地后端与模型路由已就绪',
            dataRoot: resolved.connection.dataRoot,
          },
          machine: mapMachine(system),
          browserProfiles: profiles.map(profile => ({
            browser: profile.browser,
            profileId: profile.profile_id,
            displayName: profile.display_name,
            path: profile.path,
            isDefault: profile.is_default,
          })),
          tasks,
          activeTaskId: tasks[0]?.id || '',
          notice:
            warnings.length > 0
              ? `后端已连接；${warnings[0]}`
              : '本地后端已连接，任务和模型配置已同步。',
        })
        get().refreshTasks()
      } catch (error) {
        api = undefined
        set({
          backend: { mode: 'offline', detail: errorMessage(error) },
          machine: {
            cpu: '后端未连接',
            gpu: '离线',
            memory: '—',
            backend: 'offline',
          },
          notice: errorMessage(error),
        })
      }
    })()
  },

  refreshTasks: () => {
    if (!api || get().backend.mode !== 'real' || pollInFlight) return
    pollInFlight = true
    void (async () => {
      try {
        const jobs = await api!.listJobs()
        const responses = await Promise.all(
          jobs.map(async job => {
            try {
              return await api!.jobResult(job.run_id)
            } catch {
              return undefined
            }
          }),
        )
        const responseById = new Map(
          responses
            .filter((item): item is ApiProcessingRun => Boolean(item))
            .map(item => [item.run.run_id, item]),
        )
        const jobById = new Map(jobs.map(job => [job.run_id, job]))
        const existing = get().tasks
        const merged = existing.map(task => {
          const response = responseById.get(task.id)
          const job = jobById.get(task.id)
          if (!response) return task
          return {
            ...taskFromRun(response.run, job, task.source),
            note: task.note,
            evidence: task.evidence,
            mediaUrl: task.mediaUrl,
            warnings: [...response.run.warnings, ...response.runtime_warnings],
          }
        })
        for (const response of responseById.values()) {
          if (!merged.some(task => task.id === response.run.run_id)) {
            merged.unshift(taskFromRun(response.run, response.job))
          }
        }
        const hydrated = await Promise.all(
          merged.map(task =>
            task.status === 'completed' && !task.note ? hydrateTask(task) : Promise.resolve(task),
          ),
        )
        set({ tasks: hydrated })
      } catch (error) {
        set({
          backend: {
            ...get().backend,
            mode: 'offline',
            detail: errorMessage(error),
          },
        })
      } finally {
        pollInFlight = false
      }
    })()
  },

  navigate: view => set({ view }),
  toggleContext: () => set(state => ({ contextCollapsed: !state.contextCollapsed })),
  selectTask: (taskId, destination = 'tasks') => {
    set({
      activeTaskId: taskId,
      view: destination,
      selectedEvidenceId: undefined,
      currentTimeSeconds: 0,
    })
    const task = get().tasks.find(item => item.id === taskId)
    if (task?.realBackend && (task.status === 'completed' || task.artifactPaths?.media)) {
      void hydrateTask(task).then(hydrated =>
        set(state => ({
          tasks: state.tasks.map(item => (item.id === taskId ? hydrated : item)),
        })),
      )
    }
  },
  setTaskSearch: taskSearch => set({ taskSearch }),
  setDraftInput: input =>
    set(state => ({
      draft: {
        ...state.draft,
        input,
        sourceKind: isUrl(input)
          ? 'url'
          : isWindowsAbsolutePath(input)
            ? 'local'
            : state.draft.sourceKind,
        status: 'idle',
        error: undefined,
        manifest: undefined,
      },
    })),
  setDraftMode: mode => set(state => ({ draft: { ...state.draft, mode } })),
  setDraftAuthKind: authKind =>
    set(state => ({ draft: { ...state.draft, authKind, status: 'idle' } })),
  setDraftBrowser: browser =>
    set(state => ({ draft: { ...state.draft, browser, profile: '', status: 'idle' } })),
  setDraftProfile: profile =>
    set(state => ({ draft: { ...state.draft, profile, status: 'idle' } })),
  setDraftCookieFile: cookieFile =>
    set(state => ({ draft: { ...state.draft, cookieFile, status: 'idle' } })),
  setLanguageHints: languageHints =>
    set(state => ({ draft: { ...state.draft, languageHints } })),
  setIncludeScreenshots: includeScreenshots =>
    set(state => ({ draft: { ...state.draft, includeScreenshots } })),
  setGeneratePdf: generatePdf =>
    set(state => ({ draft: { ...state.draft, generatePdf } })),

  probeSource: () => {
    const draft = get().draft
    const input = draft.input.trim()
    if (!input) {
      set(state => ({
        draft: { ...state.draft, status: 'error', error: '请输入视频链接或选择本地视频。' },
      }))
      return
    }
    if (get().backend.mode === 'demo') {
      const manifest = detectSourceFixture(input)
      if (!manifest) {
        set(state => ({
          draft: {
            ...state.draft,
            status: 'error',
            error: '无法识别来源。当前支持 Bilibili、YouTube、X 和本地视频。',
          },
        }))
        return
      }
      set(state => ({
        draft: { ...state.draft, status: 'ready', error: undefined, manifest },
        notice: `演示探测：${manifest.quality}。真实处理需连接本地后端。`,
      }))
      return
    }
    if (!api) {
      set(state => ({
        draft: { ...state.draft, status: 'error', error: '本地后端尚未连接。' },
      }))
      return
    }
    if (draft.authKind === 'browser_profile' && !draft.profile) {
      set(state => ({
        draft: { ...state.draft, status: 'error', error: '请选择一个已登录的浏览器 Profile。' },
      }))
      return
    }
    if (draft.authKind === 'cookie_file' && !draft.cookieFile.trim()) {
      set(state => ({
        draft: { ...state.draft, status: 'error', error: '请填写 cookies.txt 的本机路径。' },
      }))
      return
    }
    set(state => ({ draft: { ...state.draft, status: 'probing', error: undefined } }))
    void api
      .probeSource(sourceInput(draft), authSpec(draft), acquisitionPolicy(draft.mode))
      .then(raw => {
        const manifest = mapSource(raw, draft)
        set(state => ({
          draft: { ...state.draft, status: 'ready', manifest, error: undefined },
          notice: raw.quality_warning || `已探测 ${manifest.quality}；下载会校验实际格式。`,
        }))
      })
      .catch(error =>
        set(state => ({
          draft: { ...state.draft, status: 'error', error: errorMessage(error) },
        })),
      )
  },

  chooseLocalFile: () => {
    if (get().backend.mode === 'demo') {
      get().selectLocalFile('evidence-demo.mp4', 48 * 1024 * 1024)
      return
    }
    void pickVideoWithNativeDialog()
      .then(path => {
        if (path) get().selectLocalFile(path, 0)
      })
      .catch(error => set({ notice: errorMessage(error) }))
  },

  chooseBundledDemo: () => {
    if (get().backend.mode === 'demo') {
      get().selectLocalFile('evidence-demo.mp4', 71_613)
      return
    }
    void bundledDemoVideoPath()
      .then(path => {
        if (path) {
          get().selectLocalFile(path, 71_613)
        } else {
          set({
            notice: '内置样例随 Tauri 安装包提供；浏览器开发模式请使用 samples/evidence-demo.mp4。',
          })
        }
      })
      .catch(error => set({ notice: errorMessage(error) }))
  },

  selectLocalFile: (fileName, sizeBytes) => {
    if (get().backend.mode === 'demo') {
      const sizeMb = Math.max(1, Math.round(sizeBytes / 1024 / 1024))
      const manifest: SourceManifest = {
        id: `local-${Date.now()}`,
        platform: 'local',
        title: fileName.replace(/\.[^.]+$/, ''),
        author: '本地文件',
        durationSeconds: 420,
        quality: '1920 × 1080 · 待完整探测',
        codec: '容器探测将在任务开始后完成',
        audio: '原始音轨',
        subtitle: '检查同名字幕文件',
        authLabel: '无需登录',
        sourceLabel: `${fileName} · ${sizeMb} MB`,
      }
      set(state => ({
        draft: {
          ...state.draft,
          input: fileName,
          sourceKind: 'local',
          status: 'ready',
          error: undefined,
          manifest,
        },
        notice: `演示选择：${fileName}`,
      }))
      return
    }
    if (!isWindowsAbsolutePath(fileName)) {
      set(state => ({
        draft: {
          ...state.draft,
          status: 'error',
          error: '浏览器无法向本地后端提供绝对路径；请使用 Tauri 的系统文件选择器。',
        },
      }))
      return
    }
    set(state => ({
      draft: {
        ...state.draft,
        input: fileName,
        sourceKind: 'local',
        status: 'idle',
        error: undefined,
        manifest: undefined,
      },
    }))
    get().probeSource()
  },

  createTask: () => {
    const draft = get().draft
    if (draft.status !== 'ready' || !draft.manifest) {
      set(state => ({
        draft: { ...state.draft, status: 'error', error: '请先探测来源，再开始处理。' },
      }))
      return
    }
    if (get().backend.mode === 'demo') {
      const taskId = `task-${Date.now().toString(36)}`
      const task: ProcessingTask = {
        id: taskId,
        source: draft.manifest,
        mode: draft.mode,
        status: 'running',
        progress: 0,
        etaSeconds: draft.mode === 'fast' ? 520 : draft.mode === 'balanced' ? 780 : 1140,
        createdAt: '刚刚',
        stages: makeStages(0),
        evidence: evidenceFixture.slice(0, 3),
      }
      set(state => ({
        tasks: [task, ...state.tasks],
        activeTaskId: taskId,
        view: 'tasks',
        notice: `演示任务已按 ${draft.mode} 模式开始；未执行真实媒体处理。`,
      }))
      return
    }
    if (!api) {
      set({ notice: '本地后端尚未连接。' })
      return
    }
    const submission = submissionForDraft(draft)
    set(state => ({ draft: { ...state.draft, status: 'submitting', error: undefined } }))
    void api
      .submitJob(submission)
      .then(response => {
        sourceByRun.set(response.run.run_id, draft.manifest!)
        submissionByRun.set(response.run.run_id, submission)
        const task = taskFromRun(response.run, response.job, draft.manifest)
        set(state => ({
          draft: { ...state.draft, status: 'ready' },
          tasks: [task, ...state.tasks],
          activeTaskId: task.id,
          view: 'tasks',
          notice:
            response.runtime_warnings[0] || `真实任务 ${task.id} 已提交到本机处理队列。`,
        }))
      })
      .catch(error =>
        set(state => ({
          draft: { ...state.draft, status: 'error', error: errorMessage(error) },
        })),
      )
  },

  advanceTasks: () => {
    if (get().backend.mode === 'real') {
      get().refreshTasks()
      return
    }
    if (get().backend.mode !== 'demo') return
    set(state => ({
      tasks: state.tasks.map(task => {
        if (task.status !== 'running') return task
        const step = task.mode === 'fast' ? 1.1 : task.mode === 'balanced' ? 0.82 : 0.65
        const progress = Math.min(100, Number((task.progress + step).toFixed(2)))
        const completed = progress >= 100
        return {
          ...task,
          progress,
          status: completed ? 'completed' : 'running',
          etaSeconds: completed ? 0 : Math.max(0, task.etaSeconds - 1),
          stages: makeStages(progress),
          evidence:
            progress > 55 && task.evidence.length < evidenceFixture.length
              ? evidenceFixture.slice(
                  0,
                  Math.max(3, Math.ceil((progress / 100) * evidenceFixture.length)),
                )
              : task.evidence,
          note: completed ? noteFixture : task.note,
        }
      }),
    }))
  },

  pauseTask: taskId => {
    if (get().backend.mode === 'real') {
      set({ notice: '真实任务当前支持协作取消与阶段恢复，不提供伪暂停。' })
      return
    }
    set(state => ({
      tasks: state.tasks.map(task =>
        task.id === taskId && task.status === 'running' ? { ...task, status: 'paused' } : task,
      ),
      notice: '演示任务已暂停。',
    }))
  },
  resumeTask: taskId => {
    if (get().backend.mode === 'real') {
      set({ notice: '请从任务 artifact 的最近成功阶段恢复；不会在 UI 中伪造继续状态。' })
      return
    }
    set(state => ({
      tasks: state.tasks.map(task =>
        task.id === taskId && task.status === 'paused' ? { ...task, status: 'running' } : task,
      ),
      notice: '演示任务已继续。',
    }))
  },
  cancelTask: taskId => {
    if (get().backend.mode !== 'real' || !api) {
      set(state => ({
        tasks: state.tasks.map(task =>
          task.id === taskId && ['running', 'paused'].includes(task.status)
            ? { ...task, status: 'cancelled', etaSeconds: 0 }
            : task,
        ),
        notice: '任务已取消；已产生的 artifact 仍保留在本地。',
      }))
      return
    }
    set({ notice: '正在请求协作取消；当前媒体操作安全退出后会确认状态。' })
    void api
      .cancelJob(taskId)
      .then(snapshot =>
        set(state => ({
          tasks: state.tasks.map(task =>
            task.id === taskId ? { ...task, lastMessage: snapshot.message } : task,
          ),
        })),
      )
      .catch(error => set({ notice: errorMessage(error) }))
  },
  restartTask: taskId => {
    if (get().backend.mode !== 'real' || !api) {
      set(state => ({
        tasks: state.tasks.map(task =>
          task.id === taskId
            ? {
                ...task,
                status: 'running',
                progress: 0,
                etaSeconds: task.mode === 'fast' ? 520 : 1140,
                stages: makeStages(0),
                note: undefined,
              }
            : task,
        ),
        notice: '演示任务已重新开始。',
      }))
      return
    }
    const previous = submissionByRun.get(taskId)
    if (!previous) {
      set({ notice: '该历史任务没有可安全复用的认证引用，请在新建任务页重新提交。' })
      return
    }
    void api
      .submitJob(previous)
      .then(response => {
        const source = get().tasks.find(task => task.id === taskId)?.source
        if (source) sourceByRun.set(response.run.run_id, source)
        submissionByRun.set(response.run.run_id, previous)
        const next = taskFromRun(response.run, response.job, source)
        set(state => ({
          tasks: [next, ...state.tasks],
          activeTaskId: next.id,
          notice: '已创建新的可追溯重试任务；原任务 artifact 保持不变。',
        }))
      })
      .catch(error => set({ notice: errorMessage(error) }))
  },

  setCurrentTime: currentTimeSeconds => set({ currentTimeSeconds }),
  selectEvidence: (selectedEvidenceId, timeSeconds) =>
    set(state => ({
      selectedEvidenceId,
      currentTimeSeconds: timeSeconds ?? state.currentTimeSeconds,
    })),
  selectProvider: selectedProviderId => set({ selectedProviderId }),
  addProvider: input => {
    if (!api || !registry || get().backend.mode !== 'real') {
      set({ notice: '只有已连接的真实本地后端会持久化 Provider。' })
      return
    }
    const id = input.id.trim().toLowerCase().replace(/[^a-z0-9._-]+/g, '-')
    const modelKey = `${id}-notes`
    if (!id || !input.name.trim() || !input.baseUrl.trim() || !input.modelId.trim()) {
      set({ notice: 'Provider ID、名称、地址和模型 ID 都不能为空。' })
      return
    }
    if (registry.providers[id] || registry.models[modelKey]) {
      set({ notice: `Provider 或模型标识 ${id} 已存在。` })
      return
    }
    const capabilities = ['text', 'structured_output', 'long_context']
    if (input.vision) capabilities.push('vision')
    const next: ApiModelRegistry = {
      ...registry,
      providers: {
        ...registry.providers,
        [id]: {
          id,
          display_name: input.name.trim(),
          kind: input.kind,
          base_url: input.baseUrl.trim().replace(/\/+$/, ''),
          endpoint_style: 'chat_completions',
          request_timeout_seconds: 180,
          locality: input.kind === 'ollama' ? 'local' : 'cloud',
          enabled: true,
        },
      },
      models: {
        ...registry.models,
        [modelKey]: {
          id: modelKey,
          provider_id: id,
          model_id: input.modelId.trim(),
          display_name: input.modelId.trim(),
          capabilities,
          locality: input.kind === 'ollama' ? 'local' : 'cloud',
          settings: {},
          enabled: true,
        },
      },
    }
    void api
      .saveProviders(next)
      .then(saved => {
        registry = saved
        const presentation = registryPresentation(saved)
        set({
          ...presentation,
          selectedProviderId: id,
          notice: `${input.name.trim()} 已保存；现在可以设置密钥并绑定处理角色。`,
        })
      })
      .catch(error => set({ notice: errorMessage(error) }))
  },
  testProvider: providerId => {
    if (get().backend.mode !== 'real' || !api) {
      set(state => ({
        providers: state.providers.map(provider =>
          provider.id === providerId ? { ...provider, status: 'testing' } : provider,
        ),
        notice: '正在运行显式演示连接测试…',
      }))
      window.setTimeout(() => {
        set(state => ({
          providers: state.providers.map(provider =>
            provider.id === providerId
              ? { ...provider, status: 'connected', lastChecked: '刚刚' }
              : provider,
          ),
          notice: '演示连接成功；没有发起真实 Provider 请求。',
        }))
      }, 700)
      return
    }
    set(state => ({
      providers: state.providers.map(provider =>
        provider.id === providerId ? { ...provider, status: 'testing' } : provider,
      ),
      notice: '正在由本地后端测试 Provider 连接…',
    }))
    void api
      .testProvider(providerId)
      .then(result =>
        set(state => ({
          providers: state.providers.map(provider =>
            provider.id === providerId
              ? {
                  ...provider,
                  status: result.status === 'connected' ? 'connected' : 'disconnected',
                  lastChecked: '刚刚',
                }
              : provider,
          ),
          notice: result.detail,
        })),
      )
      .catch(error =>
        set(state => ({
          providers: state.providers.map(provider =>
            provider.id === providerId ? { ...provider, status: 'disconnected' } : provider,
          ),
          notice: errorMessage(error),
        })),
      )
  },
  saveProviderSecret: (providerId, secret) => {
    if (!api || get().backend.mode !== 'real') {
      set({ notice: '演示模式不会保存密钥。' })
      return
    }
    if (!secret.trim()) {
      set({ notice: '密钥不能为空。' })
      return
    }
    void api
      .saveProviderSecret(providerId, secret)
      .then(() =>
        set(state => ({
          providers: state.providers.map(provider =>
            provider.id === providerId
              ? { ...provider, credentialState: 'stored-locally' }
              : provider,
          ),
          notice: '密钥已写入 Windows 凭据库；界面不会读取或回显原值。',
        })),
      )
      .catch(error => set({ notice: errorMessage(error) }))
  },
  deleteProviderSecret: providerId => {
    if (!api || get().backend.mode !== 'real') {
      set({ notice: '演示模式没有可删除的密钥。' })
      return
    }
    void api
      .deleteProviderSecret(providerId)
      .then(() =>
        set(state => ({
          providers: state.providers.map(provider =>
            provider.id === providerId ? { ...provider, credentialState: 'missing' } : provider,
          ),
          notice: 'Provider 密钥已从 Windows 凭据库删除。',
        })),
      )
      .catch(error => set({ notice: errorMessage(error) }))
  },
  bindRole: (roleId, modelId) => {
    const role = get().roles.find(item => item.id === roleId)
    const model = get().models.find(item => item.id === modelId)
    if (!role || !model) return
    const compatible = role.requiredCapabilities.every(capability =>
      model.capabilities.includes(capability),
    )
    if (!compatible) {
      set({ notice: `${model.label} 缺少该角色需要的能力，绑定未保存。` })
      return
    }
    if (get().backend.mode !== 'real' || !api || !registry) {
      set(state => ({
        roles: state.roles.map(item => (item.id === roleId ? { ...item, modelId } : item)),
        notice: `${role.label} 已绑定到 ${model.label}。`,
      }))
      return
    }
    const existing = registry.roles[roleId]
    const next: ApiModelRegistry = {
      ...registry,
      roles: {
        ...registry.roles,
        [roleId]: {
          role: roleId,
          primary_model_id: modelId,
          fallback_model_ids: existing?.fallback_model_ids ?? [],
          escalation_rule: existing?.escalation_rule,
        },
      },
    }
    void api
      .saveProviders(next)
      .then(saved => {
        registry = saved
        set(state => ({
          roles: state.roles.map(item => (item.id === roleId ? { ...item, modelId } : item)),
          notice: `${role.label} 已持久化绑定到 ${model.label}。`,
        }))
      })
      .catch(error => set({ notice: errorMessage(error) }))
  },

  downloadArtifact: (taskId, kind) => {
    const task = get().tasks.find(item => item.id === taskId)
    const path = task?.artifactPaths?.[kind]
    if (!task || !path || !api) {
      set({ notice: `该任务没有可下载的 ${kind.toUpperCase()} 产物。` })
      return
    }
    void api
      .artifactBlobUrl(taskId, path)
      .then(url => {
        const anchor = document.createElement('a')
        anchor.href = url
        anchor.download = path.split('/').at(-1) || `note.${kind}`
        anchor.click()
        window.setTimeout(() => URL.revokeObjectURL(url), 10_000)
        set({ notice: `已从 canonical artifact 导出 ${anchor.download}。` })
      })
      .catch(error => set({ notice: errorMessage(error) }))
  },

  clearNotice: () => set({ notice: undefined }),
  resetDemo: () => {
    api = undefined
    registry = undefined
    sourceByRun.clear()
    submissionByRun.clear()
    for (const url of objectUrls.values()) URL.revokeObjectURL(url)
    objectUrls.clear()
    set(initialData(true))
  },
}))

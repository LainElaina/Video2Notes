import { create } from 'zustand'
import {
  Video2NotesApi,
  Video2NotesApiError,
  bundledDemoVideoPath,
  localArtifactUrl,
  pickRuntimeDirectoryWithNativeDialog,
  pickVideoWithNativeDialog,
  resolveBackendConnection,
} from './api'
import type {
  ApiAcquisitionPolicy,
  ApiArtifactRef,
  ApiAuthSpec,
  ApiComponentReport,
  ApiComponentPreparationResponse,
  ApiEvidenceSpan,
  ApiConfigurationCatalog,
  ApiExecutionPlan,
  ApiJobEvent,
  ApiJobPreflight,
  ApiJobSnapshot,
  ApiModelRegistry,
  ApiPerformanceSettings,
  ApiProcessingEstimate,
  ApiNoteDocument,
  ApiProcessingRun,
  ApiReportRevision,
  ApiRunMaterial,
  ApiRunManifest,
  ApiRunOperation,
  ApiRunOperationRequest,
  ApiRuntimePackageOperation,
  ApiRuntimePackageReport,
  ApiStageRecord,
  ApiSourceInput,
  ApiSourceManifest,
  ApiSystemReport,
  PipelineSubmission,
} from './api'
import {
  componentReportFixture,
  demoEvidenceForScope,
  demoNoteForScope,
  demoRuntimeWarnings,
  machineFixture,
  makeDemoMaterials,
  makeDemoTelemetry,
  makeStages,
  makeTaskFixtures,
  modelFixtures,
  providerFixtures,
  roleFixtures,
} from './fixtures'
import type {
  AccelerationCapabilities,
  BackendProfile,
  BrowserProfile,
  ComponentPrepareResultDefinition,
  ComponentPreparationDefinition,
  ComponentReportDefinition,
  ConfigurationCatalogDefinition,
  DiscoveredModelDefinition,
  DraftState,
  EvidenceItem,
  JobPreflightDefinition,
  MachineProfile,
  ModelDefinition,
  ModelCapability,
  NoteDocument,
  ProcessingMode,
  ProcessingEstimate,
  ProcessingEstimateStatus,
  ProcessingTask,
  PerformanceSettings,
  PerformanceSystemReport,
  ProviderAuthScheme,
  ProviderDefinition,
  ProviderKind,
  ProviderProtocol,
  ReportPreset,
  ReportRevision,
  ReportRevisionFormat,
  ReworkOperation,
  RoleBinding,
  RuntimePackageInventoryDefinition,
  RuntimePackageOperationDefinition,
  SamplingOverrideDraft,
  SourceManifest,
  StageOutputArtifact,
  SupportingMaterial,
  TaskStage,
  TelemetrySample,
  TelemetryValue,
  ViewId,
} from './domain'
import {
  nextSamplingOverride,
  serializeSamplingPlan,
  validateSamplingDraft,
} from './sampling'

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
  configurationCatalog: ConfigurationCatalogDefinition
  performance: PerformanceSettings
  systemReport?: PerformanceSystemReport
  componentReport?: ComponentReportDefinition
  componentPreparationStatus: 'idle' | 'preparing' | 'success' | 'error'
  componentPreparationResults: ComponentPrepareResultDefinition[]
  componentPreparationActivated: boolean
  componentPreparationActivatedRoles: string[]
  componentPreparationBlockedRoles: string[]
  componentPreparationWarnings: string[]
  componentPreparationError?: string
  selectedComponentIds: string[]
  runtimePackages?: RuntimePackageInventoryDefinition
  runtimePackagesStatus: 'idle' | 'loading' | 'ready' | 'error'
  runtimePackageError?: string
  jobPreflight?: JobPreflightDefinition
  jobPreflightStatus: 'idle' | 'loading' | 'ready' | 'error'
  jobPreflightDialogOpen: boolean
  preflightRuntimeOperationIds: string[]
  preflightAutoRetryPending: boolean
  processingEstimates: Partial<Record<ProcessingMode, ProcessingEstimate>>
  processingEstimateStatus: ProcessingEstimateStatus
  processingEstimateError?: string
  submissionInFlight: boolean
  discoveredModels: DiscoveredModelDefinition[]
  discoveryProviderId?: string
  providerDiscoveryStatus: 'idle' | 'loading' | 'ready' | 'error'
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
  setProcessingScope: (scope: DraftState['processingScope']) => void
  setDraftAuthKind: (kind: DraftState['authKind']) => void
  setDraftBrowser: (browser: DraftState['browser']) => void
  setDraftProfile: (profile: string) => void
  setDraftCookieFile: (path: string) => void
  setLanguageHints: (value: string) => void
  setIncludeScreenshots: (value: boolean) => void
  setGeneratePdf: (value: boolean) => void
  setSamplingMode: (mode: DraftState['samplingMode']) => void
  setSamplingIntervalSeconds: (seconds: number) => void
  addSamplingOverride: () => void
  updateSamplingOverride: (
    id: string,
    patch: Partial<Omit<SamplingOverrideDraft, 'id'>>,
  ) => void
  removeSamplingOverride: (id: string) => void
  setReportPreset: (preset: DraftState['reportPreset']) => void
  probeSource: () => void
  refreshProcessingEstimates: () => void
  chooseLocalFile: () => void
  chooseBundledDemo: () => void
  selectLocalFile: (fileName: string, sizeBytes: number) => void
  createTask: () => void
  advanceTasks: () => void
  pauseTask: (taskId: string) => void
  resumeTask: (taskId: string) => void
  cancelTask: (taskId: string) => void
  restartTask: (taskId: string) => void
  refreshOperations: (taskId: string) => void
  runVisionRework: (
    taskId: string,
    input: {
      startSeconds: number
      endSeconds: number
      mode: 'adaptive' | 'fixed_interval'
      intervalSeconds?: number
      runOcr: boolean
    },
  ) => void
  runAsrRework: (
    taskId: string,
    input: {
      range: {
        startSeconds: number
        endSeconds: number
      }
      languageHints: string[]
    },
  ) => void
  correctEvidence: (
    taskId: string,
    input: {
      evidenceId: string
      newText: string
      reason?: string
    },
  ) => void
  refreshReportRevisions: (taskId: string) => Promise<void>
  generateReportRevision: (
    taskId: string,
    input: {
      preset: ReportPreset
      includeScreenshots: boolean
      includePdf: boolean
    },
  ) => Promise<void>
  downloadReportRevisionArtifact: (
    taskId: string,
    relativePath: string,
    format: ReportRevisionFormat,
  ) => void
  refreshMaterials: (taskId: string) => void
  addTextMaterial: (
    taskId: string,
    input: {
      title: string
      content: string
      startUs?: number
      endUs?: number
    },
  ) => void
  addFileMaterial: (
    taskId: string,
    file: File,
    options?: {
      title?: string
      startUs?: number
      endUs?: number
    },
  ) => void
  deleteMaterial: (taskId: string, materialId: string) => void
  setCurrentTime: (seconds: number) => void
  selectEvidence: (evidenceId: string, timeSeconds?: number) => void
  selectProvider: (providerId: string) => void
  refreshComponents: () => void
  setComponentSelected: (componentId: string, selected: boolean) => void
  prepareLocalComponents: (componentIds?: string[]) => void
  refreshRuntimePackages: () => void
  discoverRuntimePackages: () => void
  registerCustomRuntimeDirectory: () => void
  installRuntimePackage: (
    packageId: string,
    version?: string,
    bindRequirements?: string[],
  ) => void
  upgradeRuntimePackage: (instanceId: string) => void
  cancelRuntimeOperation: (operationId: string) => void
  bindRuntimeRequirement: (
    requirementId: string,
    instanceId: string,
    capabilityId?: string,
  ) => void
  unbindRuntimeRequirement: (requirementId: string) => void
  removeRuntimePackage: (instanceId: string) => void
  forgetRuntimePackage: (instanceId: string) => void
  installPreflightRequirements: () => void
  dismissJobPreflight: () => void
  savePerformance: (settings: PerformanceSettings) => void
  saveProvider: (input: {
    id: string
    name: string
    kind: ProviderKind
    protocol: ProviderProtocol
    authScheme: ProviderAuthScheme
    baseUrl: string
    locality: 'local' | 'cloud'
    enabled: boolean
    timeoutSeconds: number
    protocolOptions: Record<string, unknown>
    credential?: string
  }) => void
  discoverProviderModels: (providerId: string) => void
  createModel: (input: {
    providerId: string
    modelId: string
    displayName: string
    contextWindow?: number
    capabilities: ModelCapability[]
    locality: 'local' | 'cloud'
  }) => void
  testProvider: (providerId: string) => void
  saveProviderSecret: (providerId: string, secret: string) => void
  deleteProviderSecret: (providerId: string) => void
  bindRole: (roleId: string, modelId: string) => void
  downloadRunArtifact: (taskId: string, artifact: StageOutputArtifact) => void
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
let demoMaterialSequence = 0
let demoOperationSequence = 0
let samplingOverrideSequence = 0
let sourceProbeSequence = 0
let processingEstimateSequence = 0
let submissionSequence = 0
let runtimeInventoryPollTimer: number | undefined

const stopRuntimeInventoryPolling = (): void => {
  if (runtimeInventoryPollTimer !== undefined && typeof window !== 'undefined') {
    window.clearTimeout(runtimeInventoryPollTimer)
  }
  runtimeInventoryPollTimer = undefined
}

interface ProcessingEstimateContext {
  manifestId: string
  mediaProfile: { source_height?: number; source_fps?: number }
}

let processingEstimateContext: ProcessingEstimateContext | undefined

export const AUDIO_ONLY_PROCESSING_CAPABILITY = 'processing_scope_audio_only'

export const backendSupportsAudioOnly = (backend: BackendProfile): boolean =>
  backend.mode === 'demo' ||
  Boolean(backend.capabilities?.includes(AUDIO_ONLY_PROCESSING_CAPABILITY))

const invalidateProcessingEstimateContext = (): void => {
  processingEstimateSequence += 1
  processingEstimateContext = undefined
}

const clearedJobPreflight = () => ({
  jobPreflight: undefined,
  jobPreflightStatus: 'idle' as const,
  jobPreflightDialogOpen: false,
  preflightRuntimeOperationIds: [],
  preflightAutoRetryPending: false,
})

const MICROSECONDS_PER_SECOND = 1_000_000
const MIN_OPERATION_INTERVAL_US = 100_000
const MAX_OPERATION_FIXED_SAMPLES = 5_000

const modelCapabilities: ModelCapability[] = [
  'text',
  'vision',
  'structured_output',
  'long_context',
  'embeddings',
  'asr',
  'language_id',
  'segment_timestamps',
  'word_timestamps',
  'word_confidence',
  'ocr',
  'ocr_boxes',
  'ocr_confidence',
  'video_frame_metrics',
]

const demoConfigurationCatalog: ConfigurationCatalogDefinition = {
  protocols: [
    ['local', '本机进程内引擎', 'none', undefined, undefined],
    ['openai_responses', 'OpenAI Responses', 'bearer', 'https://api.openai.com/v1', '/models'],
    ['openai_chat_completions', 'OpenAI Chat Completions', 'bearer', 'https://api.openai.com/v1', '/models'],
    ['openai_audio_transcriptions', 'OpenAI Audio Transcriptions', 'bearer', 'https://api.openai.com/v1', '/models'],
    ['anthropic_messages', 'Anthropic Messages', 'x_api_key', 'https://api.anthropic.com/v1', '/models'],
    ['gemini_generate_content', 'Gemini generateContent', 'x_goog_api_key', 'https://generativelanguage.googleapis.com/v1beta', '/models'],
    ['gemini_interactions', 'Gemini Interactions', 'x_goog_api_key', 'https://generativelanguage.googleapis.com/v1beta', '/models'],
    ['ollama_native_chat', 'Ollama 原生对话', 'none', 'http://127.0.0.1:11434', '/api/tags'],
    ['custom_http', '自定义 HTTP（实验）', 'custom_header', undefined, undefined],
  ].map(([protocol, displayName, defaultAuthScheme, defaultBaseUrl, discoveryPath]) => ({
    protocol: protocol as ProviderProtocol,
    displayName: String(displayName),
    defaultAuthScheme: defaultAuthScheme as ProviderAuthScheme,
    defaultBaseUrl: defaultBaseUrl as string | undefined,
    discoveryPath: discoveryPath as string | undefined,
    requestContentType: protocol === 'local' ? 'in-process' : 'application/json',
    structuredGenerationAdapter: protocol !== 'local' && protocol !== 'custom_http',
    supportsJsonSchemaTransport: protocol !== 'local' && protocol !== 'custom_http',
    supportsImageTransport: [
      'openai_responses',
      'openai_chat_completions',
      'anthropic_messages',
      'gemini_generate_content',
      'gemini_interactions',
      'ollama_native_chat',
    ].includes(String(protocol)),
    supportsStreamingTransport: protocol !== 'local' && protocol !== 'custom_http',
    streamTransport: protocol === 'ollama_native_chat' ? 'ndjson' : protocol === 'local' ? 'none' : 'sse',
  })),
  roles: [
    ['vision.change_detector', ['video_frame_metrics']],
    ['vision.text_detector', ['ocr', 'ocr_boxes']],
    ['vision.frame_explainer', ['text', 'vision']],
    ['ocr.primary', ['ocr', 'ocr_boxes']],
    ['ocr.escalation', ['text', 'vision']],
    ['asr.primary', ['asr', 'segment_timestamps']],
    ['asr.secondary', ['asr', 'segment_timestamps']],
    ['asr.adjudicator', ['text']],
    ['notes.fact_extractor', ['text', 'structured_output']],
    ['notes.drafter', ['text', 'long_context']],
    ['notes.verifier', ['text', 'structured_output']],
    ['translation', ['text']],
  ].map(([role, requiredCapabilities]) => ({
    role: String(role),
    requiredCapabilities: requiredCapabilities as ModelCapability[],
  })),
  capabilities: modelCapabilities,
}

const balancedReserve = {
  cpuReserveRatio: 0.25,
  memoryReserveRatio: 0.25,
  gpuReserveRatio: 0.2,
  vramReserveRatio: 0.2,
  diskReserveRatio: 0.1,
  cpuReserveCores: 1,
  memoryReserveBytes: 3 * 1024 ** 3,
  vramReserveBytes: 1024 ** 3,
  diskReserveBytes: 10 * 1024 ** 3,
  cpuSafetyFactor: 0.9,
  memorySafetyFactor: 0.9,
  gpuSafetyFactor: 0.9,
  vramSafetyFactor: 0.85,
  diskSafetyFactor: 0.9,
}

const defaultPerformance = (): PerformanceSettings => ({
  schemaVersion: 1,
  experienceMode: 'guided',
  preference: 'balanced',
  reserve: { ...balancedReserve },
  overrides: {},
})

const unavailableAcceleration = (): AccelerationCapabilities => ({
  asr: {
    engine: 'faster-whisper/CTranslate2',
    cudaAvailable: false,
    deviceCount: 0,
    supportedComputeTypes: [],
    reason: '当前后端未返回 ASR 加速能力探测结果；已按 CPU 回退显示。',
  },
  ocr: {
    engine: 'PaddleOCR/PaddlePaddle',
    cudaAvailable: false,
    deviceCount: 0,
    supportedComputeTypes: [],
    reason: '当前后端未返回 OCR 加速能力探测结果；已按 CPU 回退显示。',
  },
})

const demoSystemReport = (): PerformanceSystemReport => ({
  recommendedTier: 'gpu_24gb_plus',
  acceleration: {
    asr: {
      engine: 'faster-whisper/CTranslate2',
      cudaAvailable: true,
      deviceCount: 1,
      supportedComputeTypes: ['float16', 'int8_float16'],
      reason: '演示环境中的 NVIDIA ASR CUDA 已就绪。',
    },
    ocr: {
      engine: 'PaddleOCR/PaddlePaddle',
      cudaAvailable: false,
      deviceCount: 0,
      supportedComputeTypes: [],
      reason: '演示环境使用 CPU OCR。',
    },
  },
  performance: defaultPerformance(),
  recommendation: {
    experienceMode: 'guided',
    preference: 'balanced',
    reserve: { ...balancedReserve },
    budget: {
      cpuAvailableEquivalent: 28,
      cpuBudgetEquivalent: 20,
      cpuWorkers: 16,
      memoryAvailableBytes: 48 * 1024 ** 3,
      memoryBudgetBytes: 38 * 1024 ** 3,
      gpuName: 'RTX 5090 D',
      gpuComputeAvailableRatio: 0.92,
      gpuComputeBudgetRatio: 0.72,
      vramAvailableBytes: 22 * 1024 ** 3,
      vramBudgetBytes: 18 * 1024 ** 3,
      gpuStageSlots: 3,
      remoteModelConcurrency: 4,
    },
    notes: ['保留前台交互余量，视觉粗扫与语音识别可并行。'],
  },
  plans: {
    balanced: {
      hardwareTier: 'gpu_24gb_plus',
      qualityMode: 'balanced',
      experienceMode: 'guided',
      resourcePreference: 'balanced',
      decodeBackend: 'auto_hw',
      concurrentGpuStages: 1,
      cpuWorkers: 16,
      remoteModelConcurrency: 4,
      visualDecodeThreads: 8,
      maxFixedSamples: 5_000,
      analysisWidth: 1_280,
      ocrInferenceMaxWidth: 1_280,
      cheapScanFps: 3,
      expensiveScanFps: 12,
      ocrModelClass: 'mobile',
      ocrDevice: 'cpu',
      ocrBatchSize: 1,
      ocrCpuThreads: 8,
      asrModelClass: 'large-v3-turbo',
      asrDevice: 'cuda',
      asrComputeType: 'float16',
      asrBatchSize: 8,
      asrCpuThreads: 8,
      asrBeamSize: 5,
      verificationPasses: 2,
      screenshotBudgetPerSection: 4,
      learnedSceneDetector: true,
      notes: ['平衡模式使用 NVIDIA CUDA 加速 ASR，OCR 由 CPU 执行并为前台保留资源。'],
    },
  },
})

const roleMetadata = (
  id: string,
): Pick<RoleBinding, 'group' | 'label' | 'description'> => {
  const metadata: Record<
    string,
    Pick<RoleBinding, 'group' | 'label' | 'description'>
  > = {
    'vision.change_detector': {
      group: '视觉',
      label: '画面变化检测',
      description: '低成本扫描内容变化并定位候选区间',
    },
    'vision.text_detector': {
      group: '视觉',
      label: '屏幕文字检测',
      description: '定位文本区域并保留文字框坐标',
    },
    'vision.frame_explainer': {
      group: '视觉',
      label: '关键帧解释',
      description: '解释已筛选的高价值画面',
    },
    'ocr.primary': {
      group: '视觉',
      label: '主要 OCR',
      description: '读取屏幕文字与位置',
    },
    'ocr.escalation': {
      group: '视觉',
      label: 'OCR 视觉复核',
      description: '仅复核低置信或复杂版面',
    },
    'asr.primary': {
      group: '语音',
      label: '主要语音识别',
      description: '默认输出分段时间戳与文本',
    },
    'asr.secondary': {
      group: '语音',
      label: '低置信复核',
      description: '只处理噪声、冲突与不确定片段',
    },
    'asr.adjudicator': {
      group: '语音',
      label: '语音结果仲裁',
      description: '在多个识别候选之间判断可信文本',
    },
    'notes.fact_extractor': {
      group: '笔记',
      label: '事实提取',
      description: '把对齐证据转为结构化事实卡',
    },
    'notes.drafter': {
      group: '笔记',
      label: '章节写作',
      description: '根据事实卡生成长篇结构化笔记',
    },
    'notes.verifier': {
      group: '笔记',
      label: '事实验证',
      description: '检查陈述支持度与章节覆盖率',
    },
    translation: {
      group: '笔记',
      label: '翻译与术语统一',
      description: '处理多语言内容并统一术语',
    },
  }
  return metadata[id] ?? {
    group: '笔记',
    label: id,
    description: '由后端配置目录提供的处理角色',
  }
}

const demoRoles = (): RoleBinding[] =>
  demoConfigurationCatalog.roles.map(catalogRole => {
    const binding = roleFixtures.find(role => role.id === catalogRole.role)
    return {
      id: catalogRole.role,
      ...roleMetadata(catalogRole.role),
      requiredCapabilities: [...catalogRole.requiredCapabilities],
      modelId: binding?.modelId ?? '',
      fallbackModelId: binding?.fallbackModelId,
    }
  })

const emptyDraft = (): DraftState => ({
  input: '',
  mode: 'balanced',
  processingScope: 'audio_visual',
  status: 'idle',
  sourceKind: 'url',
  authKind: 'none',
  browser: 'edge',
  profile: '',
  cookieFile: '',
  languageHints: '',
  includeScreenshots: true,
  generatePdf: true,
  samplingMode: 'adaptive',
  samplingIntervalSeconds: 0.5,
  samplingOverrides: [],
  reportPreset: 'detailed',
})

const demoDraft = (): DraftState => ({
  ...emptyDraft(),
  input: 'https://www.bilibili.com/video/BV1EvidenceDemo',
  mode: 'accurate',
})

const demoRuntimePackages = (): RuntimePackageInventoryDefinition => ({
  managedRoot: 'D:\\Video2Notes\\runtime-packages\\managed',
  instances: [
    {
      instanceId: 'bundled:core-tools:0.1.0',
      packageId: 'core-tools-win-x64',
      version: '0.1.0',
      displayName: 'Core media tools',
      source: 'bundled',
      root: 'C:\\Program Files\\Video2Notes\\backend',
      state: 'ready',
      ready: true,
      targetTriple: 'win-x64',
      runtimeProtocolVersion: 1,
      transport: 'executable',
      capabilities: [
        'tool.ffmpeg',
        'tool.ffprobe',
        'download.ytdlp',
        'render.chromium_pdf',
      ],
      boundRequirements: [
        'tool.ffmpeg',
        'tool.ffprobe',
        'download.ytdlp',
        'render.chromium_pdf',
      ],
      leased: false,
      removable: false,
    },
    {
      instanceId: 'managed:local-inference-nvidia-asr:0.1.0',
      packageId: 'local-inference-nvidia-asr-cu129-win-x64',
      version: '0.1.0',
      displayName: 'NVIDIA ASR worker',
      source: 'managed',
      root: 'D:\\Video2Notes\\runtime-packages\\managed\\local-inference-nvidia-asr-cu129-win-x64\\0.1.0',
      state: 'ready',
      ready: true,
      targetTriple: 'win-x64',
      runtimeProtocolVersion: 1,
      transport: 'worker',
      capabilities: ['asr.faster_whisper'],
      boundRequirements: ['asr.faster_whisper'],
      leased: false,
      removable: true,
      availableVersion: '0.2.0',
    },
    {
      instanceId: 'system:ffmpeg:7.1',
      packageId: 'system-ffmpeg',
      version: '7.1',
      displayName: 'System FFmpeg',
      source: 'system',
      root: 'C:\\Tools\\ffmpeg',
      state: 'ready',
      ready: true,
      transport: 'executable',
      capabilities: ['tool.ffmpeg', 'tool.ffprobe'],
      boundRequirements: [],
      leased: false,
      removable: false,
    },
    {
      instanceId: 'custom:paddleocr:3.0.0',
      packageId: 'custom-paddleocr-worker',
      version: '3.0.0',
      displayName: 'Custom PaddleOCR worker',
      source: 'custom',
      root: 'D:\\AI-Runtimes\\paddleocr-worker',
      state: 'ready',
      ready: true,
      targetTriple: 'win-x64',
      runtimeProtocolVersion: 1,
      transport: 'worker',
      capabilities: ['ocr.paddleocr'],
      boundRequirements: ['ocr.paddleocr'],
      leased: false,
      removable: false,
    },
  ],
  bindings: {
    'tool.ffmpeg': {
      requirementId: 'tool.ffmpeg',
      capabilityId: 'tool.ffmpeg',
      instanceId: 'bundled:core-tools:0.1.0',
      packageId: 'core-tools-win-x64',
      packageVersion: '0.1.0',
      source: 'bundled',
      manifestSha256: 'demo-bundled',
      boundAtUtc: '2026-08-04T12:00:00Z',
    },
    'tool.ffprobe': {
      requirementId: 'tool.ffprobe',
      capabilityId: 'tool.ffprobe',
      instanceId: 'bundled:core-tools:0.1.0',
      packageId: 'core-tools-win-x64',
      packageVersion: '0.1.0',
      source: 'bundled',
      manifestSha256: 'demo-bundled',
      boundAtUtc: '2026-08-04T12:00:00Z',
    },
    'download.ytdlp': {
      requirementId: 'download.ytdlp',
      capabilityId: 'download.ytdlp',
      instanceId: 'bundled:core-tools:0.1.0',
      packageId: 'core-tools-win-x64',
      packageVersion: '0.1.0',
      source: 'bundled',
      manifestSha256: 'demo-bundled',
      boundAtUtc: '2026-08-04T12:00:00Z',
    },
    'asr.faster_whisper': {
      requirementId: 'asr.faster_whisper',
      capabilityId: 'asr.faster_whisper',
      instanceId: 'managed:local-inference-nvidia-asr:0.1.0',
      packageId: 'local-inference-nvidia-asr-cu129-win-x64',
      packageVersion: '0.1.0',
      source: 'managed',
      manifestSha256: 'demo-managed',
      boundAtUtc: '2026-08-04T12:00:00Z',
    },
    'ocr.paddleocr': {
      requirementId: 'ocr.paddleocr',
      capabilityId: 'ocr.paddleocr',
      instanceId: 'custom:paddleocr:3.0.0',
      packageId: 'custom-paddleocr-worker',
      packageVersion: '3.0.0',
      source: 'custom',
      manifestSha256: 'demo-custom',
      boundAtUtc: '2026-08-04T12:00:00Z',
    },
    'render.chromium_pdf': {
      requirementId: 'render.chromium_pdf',
      capabilityId: 'render.chromium_pdf',
      instanceId: 'bundled:core-tools:0.1.0',
      packageId: 'core-tools-win-x64',
      packageVersion: '0.1.0',
      source: 'bundled',
      manifestSha256: 'demo-bundled',
      boundAtUtc: '2026-08-04T12:00:00Z',
    },
  },
  operations: [
    {
      operationId: 'runtime-op-demo',
      kind: 'install',
      packageId: 'local-inference-nvidia-asr-cu129-win-x64',
      targetVersion: '0.1.0',
      status: 'succeeded',
      phase: 'completed',
      progress: 1,
      downloadedBytes: 0,
      totalBytes: 0,
      targetRoot: 'D:\\Video2Notes\\runtime-packages\\managed\\local-inference-nvidia-asr-cu129-win-x64\\0.1.0',
      requestedBindings: ['asr.faster_whisper'],
      resumable: true,
      cancelRequested: false,
      createdAtUtc: '2026-08-04T11:58:00Z',
      startedAtUtc: '2026-08-04T11:58:01Z',
      finishedAtUtc: '2026-08-04T12:00:00Z',
      resultInstanceId: 'managed:local-inference-nvidia-asr:0.1.0',
      detail: '演示记录：组件已安装并通过 worker 探测。',
    },
  ],
  availableReleases: [
    {
      packageId: 'local-inference-cpu-win-x64',
      version: '0.2.0',
      displayName: 'CPU local inference worker',
      targetTriple: 'win-x64',
      runtimeProtocolVersion: 1,
      capabilities: [
        {
          capabilityId: 'asr.faster_whisper',
          engineId: 'faster-whisper',
          protocolVersion: 1,
          transport: 'worker',
          supportedDevices: ['cpu'],
        },
        {
          capabilityId: 'ocr.paddleocr',
          engineId: 'paddleocr',
          protocolVersion: 1,
          transport: 'worker',
          supportedDevices: ['cpu'],
        },
      ],
      archiveFileName: 'local-inference-cpu-win-x64-0.2.0.zip',
      downloadSizeBytes: 786 * 1024 ** 2,
      installedSizeBytes: 1_462 * 1024 ** 2,
      installRoot: 'D:\\Video2Notes\\runtime-packages\\managed\\local-inference-cpu-win-x64\\0.2.0',
      offlineOnly: true,
      upstreamSources: [
        'https://github.com/SYSTRAN/faster-whisper',
        'https://github.com/PaddlePaddle/PaddleOCR',
      ],
    },
    {
      packageId: 'local-inference-nvidia-asr-cu129-win-x64',
      version: '0.2.0',
      displayName: 'NVIDIA ASR worker',
      targetTriple: 'win-x64',
      runtimeProtocolVersion: 1,
      capabilities: [
        {
          capabilityId: 'asr.faster_whisper',
          engineId: 'faster-whisper',
          protocolVersion: 1,
          transport: 'worker',
          supportedDevices: ['cpu', 'cuda'],
        },
      ],
      archiveFileName: 'local-inference-nvidia-asr-cu129-win-x64-0.2.0.zip',
      downloadSizeBytes: 1_884 * 1024 ** 2,
      installedSizeBytes: 3_474 * 1024 ** 2,
      installRoot: 'D:\\Video2Notes\\runtime-packages\\managed\\local-inference-nvidia-asr-cu129-win-x64\\0.2.0',
      offlineOnly: true,
      upstreamSources: [
        'https://github.com/SYSTRAN/faster-whisper',
        'https://developer.nvidia.com/cuda-toolkit',
      ],
    },
  ],
})

const initialData = (demo = false): StudioData => ({
  view: 'create',
  contextCollapsed: false,
  activeTaskId: demo ? 'task-running' : '',
  selectedProviderId: demo ? 'provider-local' : '',
  currentTimeSeconds: demo ? 92 : 0,
  taskSearch: '',
  backend: demo
    ? {
        mode: 'demo',
        version: 'fixture',
        capabilities: [AUDIO_ONLY_PROCESSING_CAPABILITY],
        detail: '显式视觉演示；未连接处理后端',
      }
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
  roles: demo ? demoRoles() : [],
  configurationCatalog: demo
    ? structuredClone(demoConfigurationCatalog)
    : { protocols: [], roles: [], capabilities: [] },
  performance: defaultPerformance(),
  systemReport: demo ? demoSystemReport() : undefined,
  componentReport: demo ? structuredClone(componentReportFixture) : undefined,
  componentPreparationStatus: 'idle',
  componentPreparationResults: [],
  componentPreparationActivated: false,
  componentPreparationActivatedRoles: [],
  componentPreparationBlockedRoles: [],
  componentPreparationWarnings: [],
  selectedComponentIds: demo
    ? componentReportFixture.inventory.items
        .filter(item => item.kind === 'local_model' && !item.ready)
        .map(item => item.id)
    : [],
  runtimePackages: demo ? demoRuntimePackages() : undefined,
  runtimePackagesStatus: demo ? 'ready' : 'idle',
  jobPreflightStatus: 'idle',
  jobPreflightDialogOpen: false,
  preflightRuntimeOperationIds: [],
  preflightAutoRetryPending: false,
  processingEstimates: {},
  processingEstimateStatus: 'idle',
  submissionInFlight: false,
  discoveredModels: [],
  providerDiscoveryStatus: 'idle',
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

const formatMemory = (bytes?: number | null): string =>
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

const processingModes: ProcessingMode[] = ['fast', 'balanced', 'accurate']

const mapProcessingEstimate = (value: ApiProcessingEstimate): ProcessingEstimate => ({
  hardwareTier: value.hardware_tier,
  qualityMode: value.quality_mode,
  mediaDurationSeconds: value.media_duration_seconds,
  lowerSeconds: value.lower_seconds,
  upperSeconds: value.upper_seconds,
  lowerRealtimeFactor: value.lower_realtime_factor,
  upperRealtimeFactor: value.upper_realtime_factor,
  basis: value.basis,
  precisionIntent: value.precision_intent,
  notes: [...value.notes],
})

const estimateMediaProfile = (
  source: ApiSourceManifest,
): { source_height?: number; source_fps?: number } => {
  const video = [...selectedFormats(source)]
    .filter(item => item.height || item.width)
    .sort((left, right) => {
      const heightDelta = (right.height ?? 0) - (left.height ?? 0)
      return heightDelta || (right.fps ?? 0) - (left.fps ?? 0)
    })[0]
  return {
    ...(video?.height ? { source_height: video.height } : {}),
    ...(video?.fps ? { source_fps: video.fps } : {}),
  }
}

const invalidateProbedDraft = (
  draft: DraftState,
  patch: Partial<DraftState>,
): DraftState => ({
  ...draft,
  ...patch,
  status: draft.manifest ? 'stale' : 'idle',
  error: undefined,
})

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

export const buildPipelineSubmission = (draft: DraftState): PipelineSubmission => {
  const languageHints = draft.languageHints
    .split(',')
    .map(value => value.trim())
    .filter(Boolean)
  const reportLanguage =
    languageHints.find(value => value.toLowerCase() !== 'auto') ?? 'zh-CN'
  const includeScreenshots =
    draft.processingScope === 'audio_visual' && draft.includeScreenshots
  return {
    source: sourceInput(draft),
    auth: authSpec(draft),
    acquisition: acquisitionPolicy(draft.mode),
    quality_mode: draft.mode,
    processing_scope: draft.processingScope,
    language_hints: languageHints,
    sampling_plan: serializeSamplingPlan(draft),
    include_screenshots: includeScreenshots,
    generate_pdf: draft.generatePdf,
    report_spec: {
      preset: draft.reportPreset,
      language: reportLanguage,
      include_screenshots: includeScreenshots,
      output_formats: draft.generatePdf
        ? ['markdown', 'html', 'pdf']
        : ['markdown', 'html'],
    },
  }
}

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

interface NamedStageRecord {
  backendName: string
  record: ApiStageRecord
}

const stageMetrics = (records: NamedStageRecord[]): Record<string, TelemetryValue> => {
  if (records.length === 1) return { ...records[0].record.metrics }
  return Object.fromEntries(
    records.flatMap(({ backendName, record }) =>
      Object.entries(record.metrics).map(([key, value]) => [`${backendName}.${key}`, value]),
    ),
  )
}

const stageOutputArtifacts = (records: NamedStageRecord[]): StageOutputArtifact[] =>
  records.flatMap(({ backendName, record }) =>
    record.outputs.map((output: ApiArtifactRef) => ({
      stage: record.stage_name || backendName,
      kind: output.kind,
      relativePath: output.relative_path,
      sha256: output.sha256,
      sizeBytes: output.size_bytes,
      mediaType: output.media_type,
      createdAt: output.created_at,
    })),
  )

const mapSupportingMaterial = (material: ApiRunMaterial): SupportingMaterial => ({
  id: material.id,
  runId: material.run_id,
  kind: material.kind,
  title: material.title,
  originalName: material.original_name,
  mediaType: material.media_type,
  artifact: {
    kind: material.artifact.kind,
    relativePath: material.artifact.relative_path,
    sha256: material.artifact.sha256,
    sizeBytes: material.artifact.size_bytes,
    mediaType: material.artifact.media_type ?? null,
    createdAt: material.artifact.created_at,
  },
  sha256: material.sha256,
  sizeBytes: material.size_bytes,
  textContent: material.text_content,
  startUs: material.start_us,
  endUs: material.end_us,
  status: material.status,
  createdAt: material.created_at,
  deletedAt: material.deleted_at,
  storage: 'run-artifact',
})

const mapTelemetrySample = (event: ApiJobEvent): TelemetrySample => ({
  sequence: event.sequence,
  runId: event.run_id,
  state: event.state,
  stage: event.stage,
  progress: event.progress,
  message: event.message,
  metrics: { ...event.metrics },
  createdAt: event.created_at,
})

const telemetryFromJob = (job?: ApiJobSnapshot): TelemetrySample[] =>
  mergeTelemetry([], job?.events.map(mapTelemetrySample) ?? [])

const mergeTelemetry = (
  existing: readonly TelemetrySample[],
  incoming: readonly TelemetrySample[],
): TelemetrySample[] => {
  const bySequence = new Map<number, TelemetrySample>()
  for (const sample of existing) bySequence.set(sample.sequence, sample)
  for (const sample of incoming) bySequence.set(sample.sequence, sample)
  return [...bySequence.values()].sort((left, right) => left.sequence - right.sequence)
}

const mapStages = (run: ApiRunManifest, job?: ApiJobSnapshot): TaskStage[] =>
  makeStages(0).map(stage => {
    const backendNames = stageGroups[stage.id] ?? []
    const records = backendNames
      .map(backendName => {
        const record = run.stages[backendName]
        return record ? { backendName, record } : undefined
      })
      .filter((record): record is NamedStageRecord => Boolean(record))
    const failed = records.some(({ record }) => record.status === 'failed')
    const cancelled = records.some(({ record }) => record.status === 'cancelled')
    const active = Boolean(job && backendNames.includes(job.stage) && job.state === 'running')
    const completed =
      records.length === backendNames.length &&
      records.length > 0 &&
      records.every(({ record }) => record.status === 'completed')
    const status: TaskStage['status'] = failed
      ? 'failed'
      : cancelled
        ? 'cancelled'
        : active
          ? 'running'
          : completed
            ? 'completed'
            : 'pending'
    const durations = records
      .map(({ record }) => record.wall_time_seconds)
      .filter((value): value is number => value !== undefined)
    const metrics = stageMetrics(records)
    const warnings = records.flatMap(({ record }) => record.warnings)
    const outputArtifacts = stageOutputArtifacts(records)
    const backendStages = records.map(
      ({ backendName, record }) => record.stage_name || backendName,
    )
    const errorTypes = records
      .map(({ record }) => record.error)
      .filter((error): error is string => Boolean(error))
    return {
      ...stage,
      status,
      progress: completed ? 100 : active ? Math.round((job?.progress ?? 0) * 100) : 0,
      durationSeconds:
        durations.length > 0 ? durations.reduce((sum, value) => sum + value, 0) : undefined,
      artifactCount: outputArtifacts.length,
      metrics,
      warnings,
      outputArtifacts,
      backendStages,
      errorTypes,
      metric: active ? job?.message : errorTypes[0] ?? warnings[0],
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

const taskFailureDiagnostic = (
  run: ApiRunManifest,
  job?: ApiJobSnapshot,
): ProcessingTask['failure'] => {
  const failedStages = Object.entries(run.stages)
    .filter(([, record]) => record.status === 'failed')
    .map(([backendName, record]) => ({
      stage: record.stage_name || backendName,
      errorType: record.error,
    }))
  if (failedStages.length === 0 && job?.state !== 'failed' && run.status !== 'failed') {
    return undefined
  }
  if (failedStages.length === 0 && job?.stage) {
    failedStages.push({ stage: job.stage, errorType: job.error_type })
  }
  const completedStages = Object.entries(run.stages)
    .filter(([, record]) => record.status === 'completed')
    .map(([backendName, record]) => record.stage_name || backendName)
  return {
    failedStages,
    completedStages,
    errorType:
      job?.error_type ?? failedStages.find(stage => stage.errorType)?.errorType,
    message: job?.message,
  }
}

const taskRecoveryCapability = (
  run: ApiRunManifest,
  job?: ApiJobSnapshot,
): ProcessingTask['recovery'] => {
  const status = taskStatus(run, job)
  if (status !== 'failed' && status !== 'cancelled') return undefined
  if (submissionByRun.has(run.run_id)) {
    return {
      canRetry: true,
      strategy: 'new_run',
      reason:
        '本地 API 暂未提供同一任务从失败阶段续跑；可用当前会话保留的来源、认证引用与处理参数创建一个新的可追溯任务，原任务与产物保持不变。',
    }
  }
  return {
    canRetry: false,
    strategy: 'manual_recreate',
    reason:
      '这是应用启动前创建的历史任务，当前会话没有可安全重放的认证与采样请求；请回到新建任务重新选择来源。',
  }
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

const mapReworkOperation = (record: ApiRunOperation): ReworkOperation => ({
  id: record.operation_id,
  runId: record.run_id,
  kind: record.request.kind,
  status: record.status,
  source: 'backend',
  startSeconds: record.request.range.start_us / MICROSECONDS_PER_SECOND,
  endSeconds: record.request.range.end_us / MICROSECONDS_PER_SECOND,
  samplingMode: record.request.sampling?.mode,
  intervalSeconds:
    record.request.sampling?.mode === 'fixed_interval'
      ? record.request.sampling.interval_us / MICROSECONDS_PER_SECOND
      : undefined,
  runOcr:
    record.request.kind === 'vision_rescan'
      ? record.request.run_ocr
      : undefined,
  languageHints: [...(record.request.language_hints ?? [])],
  evidenceId: record.request.evidence_id ?? undefined,
  newText: record.request.new_text ?? undefined,
  reason: record.request.reason ?? undefined,
  createdAt: record.created_at,
  finishedAt: record.finished_at,
  revisionId: record.revision_id ?? undefined,
  artifactPaths: [...record.artifact_paths],
  replacedEvidenceIds: [...record.replaced_evidence_ids],
  newEvidenceIds: [...record.new_evidence_ids],
  visualStateCount: record.visual_state_count,
  errorType: record.error_type ?? undefined,
  detail: record.detail ?? undefined,
})

const mapReportRevision = (record: ApiReportRevision): ReportRevision => ({
  id: record.id,
  runId: record.run_id,
  preset: record.report_spec.preset,
  createdAt: record.created_at,
  formats: [...record.report_spec.output_formats],
  fallback: record.used_deterministic_fallback,
  warnings: [...record.warnings],
  evidenceRevisionId: record.evidence_revision_id ?? 'base-evidence',
  materialIds: [...record.material_ids],
  artifactPaths: {
    document: record.document.relative_path,
    markdown: record.markdown.relative_path,
    html: record.html.relative_path,
    pdf: record.pdf?.relative_path,
  },
  source: 'backend',
})

const newestReportFirst = (
  revisions: readonly ReportRevision[],
): ReportRevision[] =>
  [...revisions].sort(
    (left, right) =>
      Date.parse(right.createdAt) - Date.parse(left.createdAt),
  )

const upsertReworkOperation = (
  operations: readonly ReworkOperation[],
  operation: ReworkOperation,
): ReworkOperation[] => [
  ...operations.filter(existing => existing.id !== operation.id),
  operation,
]

const includeReworkOperation = (
  operations: ReworkOperation[],
  operation: ReworkOperation,
): ReworkOperation[] =>
  operations.some(existing => existing.id === operation.id)
    ? operations
    : [...operations, operation]

const makeDemoReworkOperation = (input: {
  taskId: string
  kind: ReworkOperation['kind']
  status?: ReworkOperation['status']
  startSeconds: number
  endSeconds: number
  samplingMode?: ReworkOperation['samplingMode']
  intervalSeconds?: number
  runOcr?: boolean
  languageHints?: string[]
  evidenceId?: string
  newText?: string
  reason?: string
  replacedEvidenceIds?: string[]
  newEvidenceIds?: string[]
}): ReworkOperation => {
  demoOperationSequence += 1
  const now = new Date().toISOString()
  return {
    id: `operation-demo-${demoOperationSequence}`,
    runId: input.taskId,
    kind: input.kind,
    status: input.status ?? 'demo-preview',
    source: 'demo-memory',
    startSeconds: input.startSeconds,
    endSeconds: input.endSeconds,
    samplingMode: input.samplingMode,
    intervalSeconds: input.intervalSeconds,
    runOcr: input.runOcr,
    languageHints: [...(input.languageHints ?? [])],
    evidenceId: input.evidenceId,
    newText: input.newText,
    reason: input.reason,
    createdAt: now,
    finishedAt: now,
    artifactPaths: [],
    replacedEvidenceIds: [...(input.replacedEvidenceIds ?? [])],
    newEvidenceIds: [...(input.newEvidenceIds ?? [])],
    visualStateCount: 0,
    detail:
      input.status === 'completed'
        ? 'manual correction stored in demo memory'
        : 'preview only; no model was executed',
  }
}

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
  runtimeWarnings: readonly string[] = [],
): ProcessingTask => {
  const source = sourceOverride || sourceByRun.get(run.run_id) || fallbackSource(run)
  const media = stageArtifact(run, 'media', undefined)
  return {
    id: run.run_id,
    source,
    mode: run.profile,
    processingScope: run.processing_scope ?? 'audio_visual',
    status: taskStatus(run, job),
    progress: overallProgress(run, job),
    etaSeconds: 0,
    createdAt: new Date(run.created_at).toLocaleString('zh-CN'),
    stages: mapStages(run, job),
    telemetry: telemetryFromJob(job),
    runtimeWarnings: [...runtimeWarnings],
    materials: [],
    operations: [],
    evidence: [],
    lastMessage: job?.message,
    warnings: [...run.warnings, ...runtimeWarnings],
    artifactPaths: {
      markdown: stageArtifact(run, 'note', '.md'),
      html: stageArtifact(run, 'render', '.html'),
      pdf: stageArtifact(run, 'render', '.pdf'),
      noteDocument: stageArtifact(run, 'note', 'document.json'),
      media,
    },
    failure: taskFailureDiagnostic(run, job),
    recovery: taskRecoveryCapability(run, job),
    realBackend: true,
  }
}

const rolePresentation = (
  id: string,
  requiredCapabilities: ModelCapability[],
): Pick<RoleBinding, 'group' | 'label' | 'description' | 'requiredCapabilities'> => {
  return {
    ...roleMetadata(id),
    requiredCapabilities: [...requiredCapabilities],
  }
}

const mapConfigurationCatalog = (
  value: ApiConfigurationCatalog,
): ConfigurationCatalogDefinition => ({
  protocols: value.protocols.map(protocol => ({
    protocol: protocol.protocol,
    displayName: protocol.display_name,
    defaultAuthScheme: protocol.default_auth_scheme,
    defaultBaseUrl: protocol.default_base_url ?? undefined,
    requestPath: protocol.request_path ?? undefined,
    discoveryPath: protocol.discovery_path ?? undefined,
    requestContentType: protocol.request_content_type,
    structuredGenerationAdapter: protocol.structured_generation_adapter,
    supportsJsonSchemaTransport: protocol.supports_json_schema_transport,
    supportsImageTransport: protocol.supports_image_transport,
    supportsStreamingTransport: protocol.supports_streaming_transport,
    streamTransport: protocol.stream_transport,
  })),
  roles: value.roles.map(role => ({
    role: role.role,
    requiredCapabilities: [...role.required_capabilities],
  })),
  capabilities: [...value.capabilities],
})

const mapReserve = (
  reserve: NonNullable<ApiPerformanceSettings['reserve']>,
): NonNullable<PerformanceSettings['reserve']> => ({
  cpuReserveRatio: reserve.cpu_reserve_ratio,
  memoryReserveRatio: reserve.memory_reserve_ratio,
  gpuReserveRatio: reserve.gpu_reserve_ratio,
  vramReserveRatio: reserve.vram_reserve_ratio,
  diskReserveRatio: reserve.disk_reserve_ratio,
  cpuReserveCores: reserve.cpu_reserve_cores,
  memoryReserveBytes: reserve.memory_reserve_bytes,
  vramReserveBytes: reserve.vram_reserve_bytes,
  diskReserveBytes: reserve.disk_reserve_bytes,
  cpuSafetyFactor: reserve.cpu_safety_factor,
  memorySafetyFactor: reserve.memory_safety_factor,
  gpuSafetyFactor: reserve.gpu_safety_factor,
  vramSafetyFactor: reserve.vram_safety_factor,
  diskSafetyFactor: reserve.disk_safety_factor,
})

const serializeReserve = (
  reserve: NonNullable<PerformanceSettings['reserve']>,
): NonNullable<ApiPerformanceSettings['reserve']> => ({
  cpu_reserve_ratio: reserve.cpuReserveRatio,
  memory_reserve_ratio: reserve.memoryReserveRatio,
  gpu_reserve_ratio: reserve.gpuReserveRatio,
  vram_reserve_ratio: reserve.vramReserveRatio,
  disk_reserve_ratio: reserve.diskReserveRatio,
  cpu_reserve_cores: reserve.cpuReserveCores,
  memory_reserve_bytes: reserve.memoryReserveBytes,
  vram_reserve_bytes: reserve.vramReserveBytes,
  disk_reserve_bytes: reserve.diskReserveBytes,
  cpu_safety_factor: reserve.cpuSafetyFactor,
  memory_safety_factor: reserve.memorySafetyFactor,
  gpu_safety_factor: reserve.gpuSafetyFactor,
  vram_safety_factor: reserve.vramSafetyFactor,
  disk_safety_factor: reserve.diskSafetyFactor,
})

const defined = <T,>(value: T | null | undefined): T | undefined =>
  value ?? undefined

const mapPerformance = (value: ApiPerformanceSettings): PerformanceSettings => ({
  schemaVersion: 1,
  experienceMode: value.experience_mode,
  preference: value.preference,
  reserve: value.reserve ? mapReserve(value.reserve) : undefined,
  overrides: {
    decodeBackend: defined(value.overrides.decode_backend),
    concurrentGpuStages: defined(value.overrides.concurrent_gpu_stages),
    cpuWorkers: defined(value.overrides.cpu_workers),
    remoteModelConcurrency: defined(value.overrides.remote_model_concurrency),
    visualDecodeThreads: defined(value.overrides.visual_decode_threads),
    maxFixedSamples: defined(value.overrides.max_fixed_samples),
    analysisWidth: defined(value.overrides.analysis_width),
    ocrInferenceMaxWidth: defined(value.overrides.ocr_inference_max_width),
    cheapScanFps: defined(value.overrides.cheap_scan_fps),
    expensiveScanFps: defined(value.overrides.expensive_scan_fps),
    ocrModelClass: defined(value.overrides.ocr_model_class),
    ocrDevice: defined(value.overrides.ocr_device),
    ocrBatchSize: defined(value.overrides.ocr_batch_size),
    ocrCpuThreads: defined(value.overrides.ocr_cpu_threads),
    asrModelClass: defined(value.overrides.asr_model_class),
    asrDevice: defined(value.overrides.asr_device),
    asrComputeType: defined(value.overrides.asr_compute_type),
    asrBatchSize: defined(value.overrides.asr_batch_size),
    asrCpuThreads: defined(value.overrides.asr_cpu_threads),
    asrBeamSize: defined(value.overrides.asr_beam_size),
    verificationPasses: defined(value.overrides.verification_passes),
    screenshotBudgetPerSection: defined(value.overrides.screenshot_budget_per_section),
    learnedSceneDetector: defined(value.overrides.learned_scene_detector),
  },
})

const serializePerformance = (value: PerformanceSettings): ApiPerformanceSettings => ({
  schema_version: 1,
  experience_mode: value.experienceMode,
  preference: value.preference,
  reserve: value.reserve ? serializeReserve(value.reserve) : null,
  overrides:
    value.experienceMode === 'guided'
      ? {}
      : {
          concurrent_gpu_stages: value.overrides.concurrentGpuStages,
          cpu_workers: value.overrides.cpuWorkers,
          remote_model_concurrency: value.overrides.remoteModelConcurrency,
          visual_decode_threads: value.overrides.visualDecodeThreads,
          max_fixed_samples: value.overrides.maxFixedSamples,
          analysis_width: value.overrides.analysisWidth,
          ocr_inference_max_width: value.overrides.ocrInferenceMaxWidth,
          cheap_scan_fps: value.overrides.cheapScanFps,
          expensive_scan_fps: value.overrides.expensiveScanFps,
          ocr_device: value.overrides.ocrDevice,
          ocr_cpu_threads: value.overrides.ocrCpuThreads,
          asr_device: value.overrides.asrDevice,
          asr_compute_type: value.overrides.asrComputeType,
          asr_cpu_threads: value.overrides.asrCpuThreads,
          asr_beam_size: value.overrides.asrBeamSize,
          verification_passes: value.overrides.verificationPasses,
          screenshot_budget_per_section: value.overrides.screenshotBudgetPerSection,
        },
})

const mapExecutionPlan = (plan: ApiExecutionPlan) => ({
  hardwareTier: plan.hardware_tier,
  qualityMode: plan.quality_mode,
  experienceMode: plan.experience_mode,
  resourcePreference: plan.resource_preference,
  decodeBackend: plan.decode_backend,
  concurrentGpuStages: plan.concurrent_gpu_stages,
  cpuWorkers: plan.cpu_workers,
  remoteModelConcurrency: plan.remote_model_concurrency,
  visualDecodeThreads: plan.visual_decode_threads,
  maxFixedSamples: plan.max_fixed_samples,
  analysisWidth: plan.analysis_width,
  ocrInferenceMaxWidth: plan.ocr_inference_max_width,
  cheapScanFps: plan.cheap_scan_fps,
  expensiveScanFps: plan.expensive_scan_fps,
  ocrModelClass: plan.ocr_model_class,
  ocrDevice: plan.ocr_device,
  ocrBatchSize: plan.ocr_batch_size,
  ocrCpuThreads: plan.ocr_cpu_threads,
  asrModelClass: plan.asr_model_class,
  asrDevice: plan.asr_device,
  asrComputeType: plan.asr_compute_type,
  asrBatchSize: plan.asr_batch_size,
  asrCpuThreads: plan.asr_cpu_threads,
  asrBeamSize: plan.asr_beam_size,
  verificationPasses: plan.verification_passes,
  screenshotBudgetPerSection: plan.screenshot_budget_per_section,
  learnedSceneDetector: plan.learned_scene_detector,
  notes: [...(plan.notes ?? [])],
})

const mapSystemReport = (
  report: ApiSystemReport,
  persistedPerformance?: ApiPerformanceSettings,
): PerformanceSystemReport => {
  const fallback = defaultPerformance()
  const performance = persistedPerformance
    ? mapPerformance(persistedPerformance)
    : report.performance
      ? mapPerformance(report.performance)
      : fallback
  const recommendation = report.recommendation
  return {
    recommendedTier: report.recommended_tier,
    acceleration: report.acceleration
      ? {
          asr: {
            engine: report.acceleration.asr.engine,
            cudaAvailable: report.acceleration.asr.cuda_available,
            deviceCount: report.acceleration.asr.device_count,
            supportedComputeTypes: [...report.acceleration.asr.supported_compute_types],
            reason: report.acceleration.asr.reason,
          },
          ocr: {
            engine: report.acceleration.ocr.engine,
            cudaAvailable: report.acceleration.ocr.cuda_available,
            deviceCount: report.acceleration.ocr.device_count,
            supportedComputeTypes: [...report.acceleration.ocr.supported_compute_types],
            reason: report.acceleration.ocr.reason,
          },
        }
      : unavailableAcceleration(),
    performance,
    recommendation: recommendation
      ? {
          experienceMode: recommendation.experience_mode,
          preference: recommendation.preference,
          reserve: mapReserve(recommendation.reserve),
          budget: {
            cpuAvailableEquivalent: recommendation.budget.cpu_available_equivalent,
            cpuBudgetEquivalent: recommendation.budget.cpu_budget_equivalent,
            cpuWorkers: recommendation.budget.cpu_workers,
            memoryAvailableBytes: defined(recommendation.budget.memory_available_bytes),
            memoryBudgetBytes: defined(recommendation.budget.memory_budget_bytes),
            diskAvailableBytes: defined(recommendation.budget.disk_available_bytes),
            diskBudgetBytes: defined(recommendation.budget.disk_budget_bytes),
            gpuName: defined(recommendation.budget.gpu_name),
            gpuComputeAvailableRatio: defined(recommendation.budget.gpu_compute_available_ratio),
            gpuComputeBudgetRatio: defined(recommendation.budget.gpu_compute_budget_ratio),
            vramAvailableBytes: defined(recommendation.budget.vram_available_bytes),
            vramBudgetBytes: defined(recommendation.budget.vram_budget_bytes),
            gpuStageSlots: recommendation.budget.gpu_stage_slots,
            remoteModelConcurrency: recommendation.budget.remote_model_concurrency,
          },
          notes: [...recommendation.notes],
        }
      : demoSystemReport().recommendation,
    plans: Object.fromEntries(
      Object.entries(report.plans ?? {}).map(([mode, plan]) => [mode, mapExecutionPlan(plan)]),
    ),
  }
}

const mapComponentReport = (
  value: ApiComponentReport,
): ComponentReportDefinition => ({
  hardwareTier: value.hardware_tier,
  recommendation: {
    hardwareTier: value.recommendation.hardware_tier,
    asrComponentId: value.recommendation.asr_component_id,
    ocrComponentId: value.recommendation.ocr_component_id,
    asrDevice: value.recommendation.asr_device,
    asrComputeType: value.recommendation.asr_compute_type,
    ocrDevice: value.recommendation.ocr_device,
    reason: value.recommendation.reason,
  },
  inventory: {
    ready: value.inventory.ready,
    degraded: value.inventory.degraded,
    capabilities: { ...value.inventory.capabilities },
    items: value.inventory.items.map(item => ({
      id: item.id,
      displayName: item.display_name,
      kind: item.kind,
      state: item.state,
      ready: item.ready,
      degraded: item.degraded,
      required: item.required,
      version: item.version ?? undefined,
      path: item.path ?? undefined,
      detail: item.detail ?? undefined,
      actions: item.actions.map(action => ({
        id: action.id,
        kind: action.kind,
        componentId: action.component_id,
        label: action.label,
        automatic: action.automatic,
      })),
    })),
    actions: value.inventory.actions.map(action => ({
      id: action.id,
      kind: action.kind,
      componentId: action.component_id,
      label: action.label,
      automatic: action.automatic,
    })),
  },
})

const mapComponentPreparation = (
  value: ApiComponentPreparationResponse,
): ComponentPreparationDefinition => ({
  hardwareTier: value.hardware_tier,
  results: value.results.map(result => ({
    componentId: result.component_id,
    status: result.status,
    path: result.path ?? undefined,
    resumed: result.resumed,
    detail: result.detail ?? undefined,
  })),
  activated: value.activated,
  activatedRoles: [...(value.activated_roles ?? [])],
  blockedRoles: [...(value.blocked_roles ?? [])],
  warnings: [...(value.warnings ?? [])],
  report: mapComponentReport(value.report),
})

const selectableComponentIds = (report: ComponentReportDefinition): string[] =>
  report.inventory.items
    .filter(item => item.kind === 'local_model' && !item.ready)
    .map(item => item.id)

const runtimeRequirementLabels: Record<string, string> = {
  'tool.ffmpeg': 'FFmpeg 视频处理',
  'tool.ffprobe': 'FFprobe 媒体探测',
  'download.ytdlp': 'yt-dlp 视频下载',
  'asr.faster_whisper': 'faster-whisper 语音识别',
  'ocr.paddleocr': 'PaddleOCR 画面文字识别',
  'render.chromium_pdf': 'Chromium PDF 渲染',
}

const runtimeOperationKindLabels: Record<RuntimePackageOperationDefinition['kind'], string> = {
  install: '安装',
  upgrade: '升级',
  uninstall: '卸载',
  verify: '验证',
}

const mapRuntimeOperation = (
  value: ApiRuntimePackageOperation,
): RuntimePackageOperationDefinition => ({
  operationId: value.operation_id,
  kind: value.kind,
  packageId: value.package_id,
  targetVersion: value.target_version ?? undefined,
  instanceId: value.instance_id ?? undefined,
  sourceInstanceId: value.source_instance_id ?? undefined,
  status: value.status,
  phase: value.phase ?? undefined,
  progress: value.progress,
  downloadedBytes: value.downloaded_bytes,
  totalBytes: value.total_bytes ?? undefined,
  transferSpeedBytesPerSecond:
    value.transfer_speed_bytes_per_second ?? undefined,
  etaSeconds: value.eta_seconds ?? undefined,
  expectedInstalledBytes: value.expected_installed_bytes ?? undefined,
  targetRoot: value.target_root ?? undefined,
  requestedBindings: [...(value.requested_bindings ?? [])],
  resumable: value.resumable ?? value.kind !== 'uninstall',
  cancelRequested: value.cancel_requested,
  createdAtUtc: value.created_at_utc,
  startedAtUtc: value.started_at_utc ?? undefined,
  finishedAtUtc: value.finished_at_utc ?? undefined,
  resultInstanceId: value.result_instance_id ?? undefined,
  detail: value.detail ?? undefined,
  errorCode: value.error_code ?? undefined,
})

const mapRuntimePackages = (
  value: ApiRuntimePackageReport,
): RuntimePackageInventoryDefinition => ({
  managedRoot: value.managed_root,
  instances: value.inventory.instances.map(instance => ({
    instanceId: instance.instance_id,
    packageId: instance.package_id,
    version: instance.version,
    displayName: instance.display_name,
    source: instance.source,
    root: instance.root,
    state: instance.state,
    ready: instance.ready,
    detail: instance.detail ?? undefined,
    manifestSha256: instance.manifest_sha256 ?? undefined,
    targetTriple: instance.target_triple ?? undefined,
    runtimeProtocolVersion: instance.runtime_protocol_version ?? undefined,
    transport: instance.transport ?? undefined,
    capabilities: [...instance.capabilities],
    boundRequirements: [...instance.bound_requirements],
    leased: instance.leased,
    removable: instance.removable,
    availableVersion: instance.available_version ?? undefined,
  })),
  bindings: Object.fromEntries(
    Object.entries(value.inventory.bindings).map(([requirementId, binding]) => [
      requirementId,
      {
        requirementId: binding.requirement_id,
        capabilityId: binding.capability_id,
        instanceId: binding.instance_id,
        packageId: binding.package_id,
        packageVersion: binding.package_version,
        source: binding.source,
        manifestSha256: binding.manifest_sha256,
        boundAtUtc: binding.bound_at_utc,
      },
    ]),
  ),
  operations: value.inventory.operations.map(mapRuntimeOperation),
  availableReleases: value.releases.map(release => ({
    packageId: release.package_id,
    version: release.version,
    displayName: release.display_name,
    capabilities: release.capabilities.map(capabilityId => ({
      capabilityId,
      engineId: capabilityId,
      protocolVersion: 1,
      transport: 'worker',
      supportedDevices: [...release.supported_devices],
    })),
    archiveFileName: release.archive_file_name,
    officialUrl: release.source_url ?? undefined,
    downloadSizeBytes: release.download_size_bytes,
    installedSizeBytes: release.installed_size_bytes,
    installRoot: release.install_root,
    offlineOnly: release.offline_only,
    upstreamSources: [...release.upstream_sources],
  })),
})

const mapJobPreflight = (value: ApiJobPreflight): JobPreflightDefinition => {
  const requirementById = new Map(
    value.requirements.map(requirement => [requirement.requirement_id, requirement]),
  )
  const actionByRequirement = new Map(
    value.recommended_actions.flatMap(action =>
      action.requirement_ids.map(requirementId => [requirementId, action] as const),
    ),
  )
  const mapMissing = (requirementId: string) => {
    const requirement = requirementById.get(requirementId)
    const action = actionByRequirement.get(requirementId)
    return {
      requirementId,
      capabilityId: requirement?.capability_id ?? requirementId,
      label: runtimeRequirementLabels[requirementId] ?? requirementId,
      detail: requirement?.detail ?? '当前任务需要此本地运行能力。',
      packageId: action?.package_id,
      version: action?.version,
      officialUrl: action?.source_url ?? undefined,
      downloadSizeBytes: action?.download_size_bytes,
      installedSizeBytes: action?.installed_size_bytes,
    }
  }
  return {
    state: value.state,
    requirements: value.requirements.map(requirement => requirement.requirement_id),
    missingRequired: value.missing_required.map(mapMissing),
    missingOptional: value.missing_optional.map(mapMissing),
    selectedInstances: { ...value.selected_instances },
    bindingSnapshot: Object.fromEntries(
      Object.entries(value.binding_snapshot).map(([requirementId, binding]) => [
        requirementId,
        binding.instance_id,
      ]),
    ),
    recommendedActions: value.recommended_actions.map(action => ({
      kind: 'install',
      label: `安装 ${action.display_name}`,
      detail: `归档 ${action.archive_file_name} 将安装到 ${action.install_root}`,
      packageId: action.package_id,
      version: action.version,
      bindRequirements: [...action.requirement_ids],
      downloadSizeBytes: action.download_size_bytes,
      installedSizeBytes: action.installed_size_bytes,
      targetRoot: action.install_root,
      officialUrl: action.source_url ?? undefined,
      supportedDevices: [...action.supported_devices],
    })),
    estimatedDownloadBytes: value.estimated_download_bytes,
    estimatedInstalledBytes: value.estimated_installed_bytes,
    detail:
      value.state === 'blocked'
        ? '当前配置缺少必需运行能力，任务不会提交。'
        : value.state === 'degraded'
          ? '任务可以降级执行，缺失的可选能力会写入运行提示。'
          : '当前任务所需运行能力已就绪。',
  }
}

const mergeRuntimeOperation = (
  inventory: RuntimePackageInventoryDefinition | undefined,
  operation: ApiRuntimePackageOperation,
): RuntimePackageInventoryDefinition | undefined => {
  if (!inventory) return inventory
  const mapped = mapRuntimeOperation(operation)
  return {
    ...inventory,
    operations: [
      mapped,
      ...inventory.operations.filter(item => item.operationId !== mapped.operationId),
    ],
  }
}

const registryPresentation = (
  value: ApiModelRegistry,
  catalog: ConfigurationCatalogDefinition,
  secretStates: Record<string, string> = {},
): Pick<StudioData, 'providers' | 'models' | 'roles' | 'selectedProviderId'> => {
  const providers: ProviderDefinition[] = Object.values(value.providers).map(provider => ({
    id: provider.id,
    name: provider.display_name,
    kind: provider.kind,
    protocol:
      provider.protocol ??
      (provider.kind === 'local'
        ? 'local'
        : provider.kind === 'ollama'
          ? 'ollama_native_chat'
          : provider.endpoint_style === 'responses'
            ? 'openai_responses'
            : 'openai_chat_completions'),
    authScheme:
      provider.auth_scheme ??
      (provider.kind === 'local' || provider.kind === 'ollama' ? 'none' : 'bearer'),
    endpoint: provider.base_url || '本机 worker',
    locality: provider.locality,
    enabled: provider.enabled,
    timeoutSeconds: provider.request_timeout_seconds,
    protocolOptions: { ...(provider.protocol_options ?? {}) },
    status: provider.kind === 'local' && provider.enabled ? 'connected' : 'disconnected',
    credentialState:
      (provider.auth_scheme ?? (provider.kind === 'local' ? 'none' : 'bearer')) === 'none'
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
    capabilities: [...model.capabilities],
    contextWindow: model.context_window ?? undefined,
    enabled: model.enabled,
  }))
  const roles = catalog.roles.map(catalogRole => {
    const binding = value.roles[catalogRole.role]
    return {
      id: catalogRole.role,
      ...rolePresentation(catalogRole.role, catalogRole.requiredCapabilities),
      modelId: binding?.primary_model_id ?? '',
      fallbackModelId: binding?.fallback_model_ids[0],
    }
  })
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

const reworkErrorMessage = (error: unknown): string => {
  if (error instanceof Video2NotesApiError && error.status === 409) {
    if (error.message.toLowerCase().includes('backend is not configured')) {
      return '返工未执行：缺少所需的 ASR/OCR 模型后端。请先在模型设置中配置并绑定对应模型；现有证据保持不变。'
    }
    return `当前任务无法执行返工：${error.message}；现有证据保持不变。`
  }
  return `返工未执行：${errorMessage(error)}；现有证据保持不变。`
}

type OperationRangeValidation =
  | {
      ok: true
      startUs: number
      endUs: number
    }
  | {
      ok: false
      error: string
    }

const secondsToSafeMicroseconds = (
  seconds: number,
  label: string,
): { ok: true; value: number } | { ok: false; error: string } => {
  if (!Number.isFinite(seconds)) {
    return { ok: false, error: `${label}必须是有限秒数。` }
  }
  if (seconds < 0) {
    return { ok: false, error: `${label}不能为负数。` }
  }
  const scaled = seconds * MICROSECONDS_PER_SECOND
  const rounded = Math.round(scaled)
  const floatingPointTolerance = Math.min(
    0.001,
    Number.EPSILON * Math.max(1, Math.abs(scaled)) * 4,
  )
  if (
    !Number.isSafeInteger(rounded) ||
    Math.abs(scaled - rounded) > floatingPointTolerance
  ) {
    return {
      ok: false,
      error: `${label}必须能精确、安全地转换为整数微秒（最多 6 位小数）。`,
    }
  }
  return { ok: true, value: rounded }
}

const validateOperationRange = (
  task: ProcessingTask,
  startSeconds: number,
  endSeconds: number,
): OperationRangeValidation => {
  const start = secondsToSafeMicroseconds(startSeconds, '开始时间')
  if (!start.ok) return start
  const end = secondsToSafeMicroseconds(endSeconds, '结束时间')
  if (!end.ok) return end
  if (end.value <= start.value) {
    return { ok: false, error: '结束时间必须晚于开始时间。' }
  }
  if (task.source.durationSeconds > 0 && endSeconds > task.source.durationSeconds) {
    return { ok: false, error: '返工区间不能超过视频时长。' }
  }
  return { ok: true, startUs: start.value, endUs: end.value }
}

const validateFixedInterval = (
  startUs: number,
  endUs: number,
  intervalSeconds: number | undefined,
):
  | { ok: true; intervalUs: number; sampleCount: number }
  | { ok: false; error: string } => {
  if (intervalSeconds === undefined) {
    return { ok: false, error: '固定间隔模式必须填写采样间隔。' }
  }
  const interval = secondsToSafeMicroseconds(intervalSeconds, '采样间隔')
  if (!interval.ok) return interval
  if (interval.value < MIN_OPERATION_INTERVAL_US) {
    return { ok: false, error: '固定采样间隔不能小于 0.1 秒。' }
  }
  const sampleCount =
    Math.floor((endUs - startUs - 1) / interval.value) + 1
  if (sampleCount > MAX_OPERATION_FIXED_SAMPLES) {
    return {
      ok: false,
      error: `该区间会采样 ${sampleCount} 帧，超过单次 5000 帧上限。`,
    }
  }
  return { ok: true, intervalUs: interval.value, sampleCount }
}

interface ReworkRefreshResult {
  operations?: ReworkOperation[]
  evidence?: EvidenceItem[]
  evidenceRevisionId?: string | null
  operationsError?: unknown
  evidenceError?: unknown
}

const fetchReworkState = async (
  client: Video2NotesApi,
  taskId: string,
): Promise<ReworkRefreshResult> => {
  const [operationsResult, evidenceResult] = await Promise.allSettled([
    client.listOperations(taskId),
    client.getEvidence(taskId),
  ])
  return {
    operations:
      operationsResult.status === 'fulfilled'
        ? operationsResult.value.map(mapReworkOperation)
        : undefined,
    evidence:
      evidenceResult.status === 'fulfilled'
        ? evidenceResult.value.evidence.map(mapEvidence)
        : undefined,
    evidenceRevisionId:
      evidenceResult.status === 'fulfilled'
        ? evidenceResult.value.revision_id
        : undefined,
    operationsError:
      operationsResult.status === 'rejected'
        ? operationsResult.reason
        : undefined,
    evidenceError:
      evidenceResult.status === 'rejected'
        ? evidenceResult.reason
        : undefined,
  }
}

interface BackendReworkResult extends ReworkRefreshResult {
  operation: ReworkOperation
}

const executeBackendRework = async (
  client: Video2NotesApi,
  taskId: string,
  request: ApiRunOperationRequest,
): Promise<BackendReworkResult> => {
  const created = mapReworkOperation(
    await client.createOperation(taskId, request),
  )
  if (created.status === 'failed') {
    try {
      const operations = includeReworkOperation(
        (await client.listOperations(taskId)).map(mapReworkOperation),
        created,
      )
      return { operation: created, operations }
    } catch (operationsError) {
      return { operation: created, operationsError }
    }
  }
  const refreshed = await fetchReworkState(client, taskId)
  return {
    operation: created,
    ...refreshed,
    operations:
      refreshed.operations === undefined
        ? undefined
        : includeReworkOperation(refreshed.operations, created),
  }
}

const applyBackendRework = (
  task: ProcessingTask,
  result: BackendReworkResult,
): ProcessingTask => ({
  ...task,
  operations:
    result.operations ??
    upsertReworkOperation(task.operations, result.operation),
  evidence: result.evidence ?? task.evidence,
  evidenceRevisionId:
    result.evidenceRevisionId === undefined
      ? task.evidenceRevisionId
      : result.evidenceRevisionId ?? undefined,
})

const backendReworkNotice = (result: BackendReworkResult): string => {
  if (result.operation.status === 'failed') {
    const detail =
      result.operation.detail ||
      result.operation.errorType ||
      '模型执行阶段返回失败'
    return `返工执行失败：${detail}；现有证据保持不变，报告尚未重生成。`
  }
  if (result.evidence === undefined) {
    return '返工已完成并保存 revision，但有效证据刷新失败；当前证据保持不变，报告尚未重生成。'
  }
  if (result.operations === undefined) {
    return '返工已完成，有效证据已刷新；操作列表暂以本次返回补齐，报告尚未重生成。'
  }
  return `返工已完成，已刷新 ${result.evidence.length} 条有效证据；报告尚未重生成。`
}

const materialRangeError = (startUs?: number, endUs?: number): string | undefined => {
  if ((startUs === undefined) !== (endUs === undefined)) {
    return '材料时间范围必须同时提供开始和结束时间。'
  }
  if (startUs !== undefined && endUs !== undefined) {
    if (!Number.isSafeInteger(startUs) || !Number.isSafeInteger(endUs)) {
      return '材料时间范围必须使用整数微秒。'
    }
    if (startUs < 0 || endUs < 0) return '材料时间范围不能为负数。'
    if (endUs <= startUs) return '材料结束时间必须晚于开始时间。'
  }
  return undefined
}

const hydrateTask = async (task: ProcessingTask): Promise<ProcessingTask> => {
  const client = api
  if (!client || !task.realBackend) return task
  const materialsPromise = client
    .listMaterials(task.id)
    .then(materials => materials.map(mapSupportingMaterial))
    .catch(() => task.materials)
  const operationsPromise =
    task.status === 'completed'
      ? client
          .listOperations(task.id)
          .then(operations => operations.map(mapReworkOperation))
          .catch(() => task.operations)
      : Promise.resolve(task.operations)
  const effectiveEvidencePromise =
    task.status === 'completed'
      ? client
          .getEvidence(task.id)
          .then(response => ({
            evidence: response.evidence.map(mapEvidence),
            revisionId: response.revision_id,
          }))
          .catch(() => undefined)
      : Promise.resolve(undefined)
  const reportRevisionsPromise =
    task.status === 'completed'
      ? client
          .listReportRevisions(task.id)
          .then(index =>
            newestReportFirst(index.revisions.map(mapReportRevision)),
          )
          .catch(() => task.reportRevisions ?? [])
      : Promise.resolve(task.reportRevisions ?? [])
  let note = task.note
  let evidence = task.evidence
  let source = task.source
  const notePath = task.artifactPaths?.noteDocument || 'notes/document.json'
  if (task.status === 'completed' && !note) {
    try {
      const document = await client.artifactJson<ApiNoteDocument>(task.id, notePath)
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
              (await client.artifactBlobUrl(task.id, section.screenshotPath))
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
        (await client.artifactBlobUrl(task.id, mediaPath))
      objectUrls.set(task.id, mediaUrl)
    } catch {
      // Notes remain readable when the media asset cannot be previewed.
    }
  }
  const [materials, operations, effectiveEvidence, reportRevisions] =
    await Promise.all([
    materialsPromise,
    operationsPromise,
    effectiveEvidencePromise,
    reportRevisionsPromise,
  ])
  return {
    ...task,
    source,
    note,
    evidence: effectiveEvidence?.evidence ?? evidence,
    evidenceRevisionId:
      effectiveEvidence === undefined
        ? task.evidenceRevisionId
        : effectiveEvidence.revisionId ?? undefined,
    mediaUrl,
    materials,
    operations,
    reportRevisions,
  }
}

const loadRevisionNote = async (
  client: Video2NotesApi,
  taskId: string,
  documentPath: string,
): Promise<NoteDocument> => {
  const document = await client.artifactJson<ApiNoteDocument>(
    taskId,
    documentPath,
  )
  const note = mapNote(document)
  return {
    ...note,
    sections: await Promise.all(
      note.sections.map(async section => {
        if (!section.screenshotPath) return section
        try {
          const screenshotUrl =
            (await localArtifactUrl(taskId, section.screenshotPath)) ||
            (await client.artifactBlobUrl(taskId, section.screenshotPath))
          objectUrls.set(
            `${taskId}:${documentPath}:${section.screenshotPath}`,
            screenshotUrl,
          )
          return { ...section, screenshotUrl }
        } catch {
          return section
        }
      }),
    ),
  }
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
        const [
          system,
          performancePayload,
          catalogPayload,
          runtime,
          profiles,
          loadedRegistry,
          runs,
          componentPayload,
          runtimePackagePayload,
        ] = await Promise.all([
          nextApi.system(),
          nextApi.performance(),
          nextApi.configurationCatalog(),
          nextApi.runtime(),
          nextApi.browserProfiles(),
          nextApi.providers(),
          nextApi.listRuns(),
          nextApi.components(),
          nextApi.runtimePackages().catch(() => undefined),
        ])
        api = nextApi
        registry = loadedRegistry
        const configurationCatalog = mapConfigurationCatalog(catalogPayload)
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
        const presentation = registryPresentation(
          loadedRegistry,
          configurationCatalog,
          secretStates,
        )
        const systemReport = mapSystemReport(system, performancePayload)
        const componentReport = mapComponentReport(componentPayload)
        const tasks = await Promise.all(
          runs.map(run => hydrateTask(taskFromRun(run))),
        )
        const warnings = runtime.warnings
        set({
          ...presentation,
          configurationCatalog,
          performance: systemReport.performance,
          systemReport,
          componentReport,
          componentPreparationStatus: 'idle',
          componentPreparationResults: [],
          componentPreparationActivated: false,
          componentPreparationActivatedRoles: [],
          componentPreparationBlockedRoles: [],
          componentPreparationWarnings: [],
          componentPreparationError: undefined,
          selectedComponentIds: selectableComponentIds(componentReport),
          runtimePackages: runtimePackagePayload
            ? mapRuntimePackages(runtimePackagePayload)
            : undefined,
          runtimePackagesStatus: runtimePackagePayload ? 'ready' : 'idle',
          runtimePackageError: undefined,
          jobPreflight: undefined,
          jobPreflightStatus: 'idle',
          jobPreflightDialogOpen: false,
          preflightRuntimeOperationIds: [],
          preflightAutoRetryPending: false,
          backend: {
            mode: 'real',
            version: health.version,
            capabilities: health.capabilities ?? [],
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
        if (
          runtimePackagePayload?.inventory.operations.some(operation =>
            ['queued', 'running'].includes(operation.status),
          )
        ) {
          get().refreshRuntimePackages()
        }
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
          if (!response) {
            return job
              ? {
                  ...task,
                  lastMessage: job.message ?? task.lastMessage,
                  telemetry: mergeTelemetry(task.telemetry, telemetryFromJob(job)),
                }
              : task
          }
          const next = taskFromRun(
            response.run,
            job ?? response.job,
            task.source,
            response.runtime_warnings,
          )
          return {
            ...next,
            note: task.note,
            evidence: task.evidence,
            mediaUrl: task.mediaUrl,
            materials: task.materials,
            operations: task.operations,
            evidenceRevisionId: task.evidenceRevisionId,
            reportRevisions: task.reportRevisions,
            telemetry: mergeTelemetry(
              mergeTelemetry(task.telemetry, next.telemetry),
              telemetryFromJob(response.job),
            ),
          }
        })
        for (const response of responseById.values()) {
          if (!merged.some(task => task.id === response.run.run_id)) {
            merged.unshift(
              taskFromRun(
                response.run,
                response.job,
                undefined,
                response.runtime_warnings,
              ),
            )
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
  setDraftInput: input => {
    if (input === get().draft.input) return
    sourceProbeSequence += 1
    invalidateProcessingEstimateContext()
    set(state => ({
      draft: invalidateProbedDraft(state.draft, {
        input,
        sourceKind: isUrl(input)
          ? 'url'
          : isWindowsAbsolutePath(input)
            ? 'local'
            : state.draft.sourceKind,
      }),
      processingEstimates: {},
      processingEstimateStatus: 'idle',
      processingEstimateError: undefined,
      ...clearedJobPreflight(),
    }))
  },
  setDraftMode: mode => {
    if (mode === get().draft.mode) return
    sourceProbeSequence += 1
    invalidateProcessingEstimateContext()
    set(state => ({
      draft: invalidateProbedDraft(state.draft, { mode }),
      processingEstimates: {},
      processingEstimateStatus: 'idle',
      processingEstimateError: undefined,
      ...clearedJobPreflight(),
    }))
  },
  setProcessingScope: processingScope => {
    if (processingScope === get().draft.processingScope) return
    if (get().submissionInFlight || get().draft.status === 'submitting') return
    if (processingScope === 'audio_only' && !backendSupportsAudioOnly(get().backend)) {
      set({ notice: '后端版本不支持仅音频；请升级本地后端后再试。' })
      return
    }
    processingEstimateSequence += 1
    set(state => ({
      draft: {
        ...state.draft,
        processingScope,
        error: undefined,
      },
      processingEstimates: {},
      processingEstimateStatus: 'idle',
      processingEstimateError: undefined,
      ...clearedJobPreflight(),
    }))
    get().refreshProcessingEstimates()
  },
  setDraftAuthKind: authKind => {
    if (authKind === get().draft.authKind) return
    sourceProbeSequence += 1
    invalidateProcessingEstimateContext()
    set(state => ({
      draft: invalidateProbedDraft(state.draft, { authKind }),
      processingEstimates: {},
      processingEstimateStatus: 'idle',
      processingEstimateError: undefined,
      ...clearedJobPreflight(),
    }))
  },
  setDraftBrowser: browser => {
    if (browser === get().draft.browser) return
    sourceProbeSequence += 1
    invalidateProcessingEstimateContext()
    set(state => ({
      draft: invalidateProbedDraft(state.draft, { browser, profile: '' }),
      processingEstimates: {},
      processingEstimateStatus: 'idle',
      processingEstimateError: undefined,
      ...clearedJobPreflight(),
    }))
  },
  setDraftProfile: profile => {
    if (profile === get().draft.profile) return
    sourceProbeSequence += 1
    invalidateProcessingEstimateContext()
    set(state => ({
      draft: invalidateProbedDraft(state.draft, { profile }),
      processingEstimates: {},
      processingEstimateStatus: 'idle',
      processingEstimateError: undefined,
      ...clearedJobPreflight(),
    }))
  },
  setDraftCookieFile: cookieFile => {
    if (cookieFile === get().draft.cookieFile) return
    sourceProbeSequence += 1
    invalidateProcessingEstimateContext()
    set(state => ({
      draft: invalidateProbedDraft(state.draft, { cookieFile }),
      processingEstimates: {},
      processingEstimateStatus: 'idle',
      processingEstimateError: undefined,
      ...clearedJobPreflight(),
    }))
  },
  setLanguageHints: languageHints =>
    set(state => ({ draft: { ...state.draft, languageHints }, ...clearedJobPreflight() })),
  setIncludeScreenshots: includeScreenshots =>
    set(state => ({ draft: { ...state.draft, includeScreenshots }, ...clearedJobPreflight() })),
  setGeneratePdf: generatePdf =>
    set(state => ({ draft: { ...state.draft, generatePdf }, ...clearedJobPreflight() })),
  setSamplingMode: samplingMode =>
    set(state => ({ draft: { ...state.draft, samplingMode }, ...clearedJobPreflight() })),
  setSamplingIntervalSeconds: samplingIntervalSeconds =>
    set(state => ({ draft: { ...state.draft, samplingIntervalSeconds }, ...clearedJobPreflight() })),
  addSamplingOverride: () =>
    set(state => {
      samplingOverrideSequence += 1
      const override = nextSamplingOverride(
        state.draft.samplingOverrides,
        state.draft.manifest?.durationSeconds,
        `sampling-${samplingOverrideSequence}`,
      )
      return {
        ...clearedJobPreflight(),
        draft: {
          ...state.draft,
          samplingOverrides: [...state.draft.samplingOverrides, override],
        },
      }
    }),
  updateSamplingOverride: (id, patch) =>
    set(state => ({
      ...clearedJobPreflight(),
      draft: {
        ...state.draft,
        samplingOverrides: state.draft.samplingOverrides.map(override =>
          override.id === id ? { ...override, ...patch } : override,
        ),
      },
    })),
  removeSamplingOverride: id =>
    set(state => ({
      ...clearedJobPreflight(),
      draft: {
        ...state.draft,
        samplingOverrides: state.draft.samplingOverrides.filter(override => override.id !== id),
      },
    })),
  setReportPreset: reportPreset =>
    set(state => ({ draft: { ...state.draft, reportPreset }, ...clearedJobPreflight() })),

  probeSource: () => {
    const draft = get().draft
    const input = draft.input.trim()
    if (!input) {
      set(state => ({
        draft: { ...state.draft, status: 'error', error: '请输入视频链接或选择本地视频。' },
      }))
      return
    }
    const probeId = ++sourceProbeSequence
    invalidateProcessingEstimateContext()
    set({
      processingEstimates: {},
      processingEstimateStatus: 'idle',
      processingEstimateError: undefined,
      ...clearedJobPreflight(),
    })
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
        processingEstimates: {},
        processingEstimateStatus: 'idle',
        processingEstimateError: undefined,
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
    set(state => ({
      draft: { ...state.draft, status: 'probing', error: undefined },
      processingEstimates: {},
      processingEstimateStatus: 'idle',
      processingEstimateError: undefined,
    }))
    void api
      .probeSource(sourceInput(draft), authSpec(draft), acquisitionPolicy(draft.mode))
      .then(raw => {
        if (probeId !== sourceProbeSequence) return
        const manifest = mapSource(raw, draft)
        processingEstimateContext = {
          manifestId: manifest.id,
          mediaProfile: estimateMediaProfile(raw),
        }
        set(state => ({
          draft: { ...state.draft, status: 'ready', manifest, error: undefined },
          processingEstimateStatus: 'idle',
          processingEstimateError: undefined,
          notice: raw.quality_warning || `已探测 ${manifest.quality}；下载会校验实际格式。`,
        }))
        get().refreshProcessingEstimates()
      })
      .catch(error => {
        if (probeId !== sourceProbeSequence) return
        set(state => ({
          draft: { ...state.draft, status: 'error', error: errorMessage(error) },
          processingEstimates: {},
          processingEstimateStatus: 'idle',
          processingEstimateError: undefined,
        }))
      })
  },

  refreshProcessingEstimates: () => {
    const client = api
    const draft = get().draft
    const manifest = draft.manifest
    const context = processingEstimateContext
    if (
      !client ||
      get().backend.mode !== 'real' ||
      draft.status !== 'ready' ||
      !manifest ||
      !context ||
      context.manifestId !== manifest.id
    ) {
      return
    }
    if (
      draft.processingScope === 'audio_only' &&
      !backendSupportsAudioOnly(get().backend)
    ) {
      set({
        processingEstimates: {},
        processingEstimateStatus: 'error',
        processingEstimateError: '后端版本不支持仅音频。',
      })
      return
    }
    const estimateId = ++processingEstimateSequence
    const processingScope = draft.processingScope
    set({
      processingEstimates: {},
      processingEstimateStatus: 'loading',
      processingEstimateError: undefined,
    })
    void Promise.allSettled(
      processingModes.map(mode =>
        client.estimateProcessing({
          duration_seconds: manifest.durationSeconds,
          quality_mode: mode,
          processing_scope: processingScope,
          ...context.mediaProfile,
        }),
      ),
    ).then(results => {
      if (
        estimateId !== processingEstimateSequence ||
        get().draft.status !== 'ready' ||
        get().draft.manifest?.id !== manifest.id ||
        get().draft.processingScope !== processingScope
      ) {
        return
      }
      const estimates: Partial<Record<ProcessingMode, ProcessingEstimate>> = {}
      const failures: string[] = []
      results.forEach((result, index) => {
        const mode = processingModes[index]
        if (result.status === 'fulfilled') {
          estimates[mode] = mapProcessingEstimate(result.value)
        } else {
          failures.push(`${mode}: ${errorMessage(result.reason)}`)
        }
      })
      const successCount = Object.keys(estimates).length
      set({
        processingEstimates: estimates,
        processingEstimateStatus:
          successCount === processingModes.length
            ? 'ready'
            : successCount > 0
              ? 'partial'
              : 'error',
        processingEstimateError:
          failures.length > 0 ? [...new Set(failures)].join('；') : undefined,
      })
    })
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
    sourceProbeSequence += 1
    invalidateProcessingEstimateContext()
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
        ...clearedJobPreflight(),
        draft: {
          ...state.draft,
          input: fileName,
          sourceKind: 'local',
          status: 'ready',
          error: undefined,
          manifest,
        },
        processingEstimates: {},
        processingEstimateStatus: 'idle',
        processingEstimateError: undefined,
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
      ...clearedJobPreflight(),
      draft: invalidateProbedDraft(state.draft, {
        input: fileName,
        sourceKind: 'local',
      }),
      processingEstimates: {},
      processingEstimateStatus: 'idle',
      processingEstimateError: undefined,
    }))
    get().probeSource()
  },

  createTask: () => {
    if (
      get().submissionInFlight ||
      get().draft.status === 'submitting' ||
      get().jobPreflightStatus === 'loading' ||
      get().preflightAutoRetryPending
    ) {
      set({ notice: '正在检查依赖或提交任务，请等待本次操作确认。' })
      return
    }
    const draft = get().draft
    if (
      draft.processingScope === 'audio_only' &&
      !backendSupportsAudioOnly(get().backend)
    ) {
      set(state => ({
        draft: {
          ...state.draft,
          error: '后端版本不支持仅音频；请升级本地后端后再试。',
        },
        notice: '任务未提交：后端版本不支持仅音频。',
      }))
      return
    }
    if (draft.status !== 'ready' || !draft.manifest) {
      set(state => ({
        draft: {
          ...state.draft,
          status: draft.manifest ? 'stale' : 'error',
          error:
            draft.manifest
              ? '来源或探测策略已变化，请重新探测后再开始处理。'
              : '请先探测来源，再开始处理。',
        },
      }))
      return
    }
    const samplingValidation = validateSamplingDraft(draft)
    if (samplingValidation.errors.length > 0) {
      set({
        notice: `无法开始任务：${samplingValidation.errors[0]}`,
      })
      return
    }
    if (get().backend.mode === 'demo') {
      const taskId = `task-${Date.now().toString(36)}`
      const demoEvidence = demoEvidenceForScope(draft.processingScope)
      const task: ProcessingTask = {
        id: taskId,
        source: draft.manifest,
        mode: draft.mode,
        processingScope: draft.processingScope,
        status: 'running',
        progress: 0,
        etaSeconds: draft.mode === 'fast' ? 520 : draft.mode === 'balanced' ? 780 : 1140,
        createdAt: '刚刚',
        stages: makeStages(0, draft.processingScope),
        telemetry: makeDemoTelemetry(0, taskId, draft.processingScope),
        runtimeWarnings: [...demoRuntimeWarnings],
        materials: makeDemoMaterials(taskId),
        operations: [],
        evidence: demoEvidence.slice(0, 3),
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
    const client = api
    const submission = buildPipelineSubmission(draft)
    const submissionId = ++submissionSequence
    const submittedProbeSequence = sourceProbeSequence
    const submissionFingerprint = JSON.stringify(submission)

    const submitPreparedJob = (preflight: JobPreflightDefinition) => {
      if (submissionId !== submissionSequence) return
      set(state => ({
        submissionInFlight: true,
        jobPreflight: preflight,
        jobPreflightStatus: 'ready',
        jobPreflightDialogOpen: false,
        preflightRuntimeOperationIds: [],
        preflightAutoRetryPending: false,
        draft: { ...state.draft, status: 'submitting', error: undefined },
        notice:
          preflight.state === 'degraded'
            ? preflight.detail ?? '依赖预检允许降级执行，任务将保留相关提示。'
            : '依赖检查通过，正在提交本机处理任务。',
      }))
      void client
        .submitJob(submission)
        .then(response => {
          if (submissionId !== submissionSequence) return
          sourceByRun.set(response.run.run_id, draft.manifest!)
          submissionByRun.set(response.run.run_id, submission)
          const task = taskFromRun(
            response.run,
            response.job,
            draft.manifest,
            response.runtime_warnings,
          )
          set(state => {
            const submittedDraftStillCurrent =
              sourceProbeSequence === submittedProbeSequence &&
              state.draft.status === 'submitting'
            return {
              submissionInFlight: false,
              draft: submittedDraftStillCurrent
                ? { ...state.draft, status: 'ready', error: undefined }
                : state.draft,
              tasks: [task, ...state.tasks.filter(item => item.id !== task.id)],
              activeTaskId: submittedDraftStillCurrent ? task.id : state.activeTaskId,
              view: submittedDraftStillCurrent ? 'tasks' : state.view,
              notice:
                response.runtime_warnings[0] ||
                (submittedDraftStillCurrent
                  ? `真实任务 ${task.id} 已提交到本机处理队列。`
                  : `较早的草稿已提交为任务 ${task.id}；当前草稿保持不变。`),
            }
          })
        })
        .catch(error => {
          if (submissionId !== submissionSequence) return
          const message = errorMessage(error)
          set(state => {
            const submittedDraftStillCurrent =
              sourceProbeSequence === submittedProbeSequence &&
              state.draft.status === 'submitting'
            return {
              submissionInFlight: false,
              draft: submittedDraftStillCurrent
                ? { ...state.draft, status: 'error', error: message }
                : state.draft,
              notice: submittedDraftStillCurrent
                ? state.notice
                : `较早的任务提交失败：${message}；当前草稿保持不变。`,
            }
          })
        })
    }

    set({
      jobPreflight: undefined,
      jobPreflightStatus: 'loading',
      jobPreflightDialogOpen: false,
      preflightRuntimeOperationIds: [],
      preflightAutoRetryPending: false,
      notice: '正在检查当前任务所需的本地工具与识别运行时。',
    })
    void client
      .preflightJob(submission)
      .then(payload => {
        if (submissionId !== submissionSequence) return
        const currentDraft = get().draft
        if (
          sourceProbeSequence !== submittedProbeSequence ||
          currentDraft.status !== 'ready' ||
          JSON.stringify(buildPipelineSubmission(currentDraft)) !== submissionFingerprint
        ) {
          set({ ...clearedJobPreflight() })
          return
        }
        const preflight = mapJobPreflight(payload)
        if (preflight.state === 'blocked') {
          set({
            jobPreflight: preflight,
            jobPreflightStatus: 'ready',
            jobPreflightDialogOpen: true,
            preflightRuntimeOperationIds: [],
            preflightAutoRetryPending: false,
            notice: '任务尚未提交：请先安装或绑定缺失的本地依赖。',
          })
          return
        }
        submitPreparedJob(preflight)
      })
      .catch(error => {
        if (submissionId !== submissionSequence) return
        const message = errorMessage(error)
        set({
          jobPreflightStatus: 'error',
          jobPreflightDialogOpen: false,
          preflightRuntimeOperationIds: [],
          preflightAutoRetryPending: false,
          notice: `依赖预检失败，任务未提交：${message}`,
        })
      })
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
        const demoEvidence = demoEvidenceForScope(task.processingScope)
        return {
          ...task,
          progress,
          status: completed ? 'completed' : 'running',
          etaSeconds: completed ? 0 : Math.max(0, task.etaSeconds - 1),
          stages: makeStages(progress, task.processingScope),
          telemetry: makeDemoTelemetry(progress, task.id, task.processingScope),
          evidence:
            progress > 55 && task.evidence.length < demoEvidence.length
              ? demoEvidence.slice(
                  0,
                  Math.max(3, Math.ceil((progress / 100) * demoEvidence.length)),
                )
              : task.evidence,
          note: completed ? demoNoteForScope(task.processingScope) : task.note,
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
            ? {
                ...task,
                status: 'cancelled',
                etaSeconds: 0,
                stages: task.stages.map(stage =>
                  stage.status === 'running'
                    ? { ...stage, status: 'cancelled' as const }
                    : stage,
                ),
              }
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
            task.id === taskId
              ? {
                  ...task,
                  lastMessage: snapshot.message,
                  telemetry: mergeTelemetry(task.telemetry, telemetryFromJob(snapshot)),
                }
              : task,
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
                stages: makeStages(0, task.processingScope),
                telemetry: makeDemoTelemetry(0, task.id, task.processingScope),
                runtimeWarnings: [...demoRuntimeWarnings],
                note: undefined,
              }
            : task,
        ),
        notice: '演示任务已从头重新开始；演示模式不会写入真实产物。',
      }))
      return
    }
    const previous = submissionByRun.get(taskId)
    if (!previous) {
      set({
        notice:
          '无法一键重试：该历史任务没有可安全复用的认证与采样请求，请在新建任务页重新选择来源。',
      })
      return
    }
    set({
      notice:
        '正在按原配置创建新的可追溯任务；当前后端不支持在原任务内从失败阶段续跑。',
    })
    void api
      .submitJob(previous)
      .then(response => {
        const source = get().tasks.find(task => task.id === taskId)?.source
        if (source) sourceByRun.set(response.run.run_id, source)
        submissionByRun.set(response.run.run_id, previous)
        const next = taskFromRun(
          response.run,
          response.job,
          source,
          response.runtime_warnings,
        )
        set(state => ({
          tasks: [next, ...state.tasks],
          activeTaskId: next.id,
          notice:
            '已按原配置创建新的可追溯重试任务；原任务与已完成产物保持不变。',
        }))
      })
      .catch(error => set({ notice: errorMessage(error) }))
  },

  refreshOperations: taskId => {
    const task = get().tasks.find(item => item.id === taskId)
    if (!task) {
      set({ notice: '没有找到要刷新的任务。' })
      return
    }
    if (!task.realBackend) {
      set({
        notice: '演示返工记录仅保存在当前内存中；没有向本地后端发起请求。',
      })
      return
    }
    const client = api
    if (!client || get().backend.mode !== 'real') {
      set({ notice: '本地后端尚未连接，无法刷新返工记录。' })
      return
    }
    void fetchReworkState(client, taskId).then(result => {
      let notice: string
      if (result.operations !== undefined && result.evidence !== undefined) {
        notice = `已同步 ${result.operations.length} 条返工记录与 ${result.evidence.length} 条有效证据；报告尚未重生成。`
      } else if (result.operations !== undefined) {
        notice = `已同步 ${result.operations.length} 条返工记录，但有效证据刷新失败：${errorMessage(result.evidenceError)}；当前证据保持不变。`
      } else if (result.evidence !== undefined) {
        notice = `已刷新 ${result.evidence.length} 条有效证据，但返工记录刷新失败：${errorMessage(result.operationsError)}；报告尚未重生成。`
      } else {
        notice = `返工记录与有效证据均刷新失败：${errorMessage(result.operationsError ?? result.evidenceError)}；当前数据保持不变。`
      }
      set(state => ({
        tasks: state.tasks.map(item =>
          item.id === taskId
            ? {
                ...item,
                operations: result.operations ?? item.operations,
                evidence: result.evidence ?? item.evidence,
                evidenceRevisionId:
                  result.evidenceRevisionId === undefined
                    ? item.evidenceRevisionId
                    : result.evidenceRevisionId ?? undefined,
              }
            : item,
        ),
        notice,
      }))
    }).catch(error =>
      set({
        notice: `返工状态刷新失败：${errorMessage(error)}；当前数据保持不变。`,
      }),
    )
  },

  runVisionRework: (taskId, input) => {
    const task = get().tasks.find(item => item.id === taskId)
    if (!task) {
      set({ notice: '没有找到要执行视觉返工的任务。' })
      return
    }
    if (task.processingScope === 'audio_only') {
      set({ notice: '仅音频任务没有视觉基线，不能执行画面或 OCR 返工。' })
      return
    }
    if (task.status !== 'completed') {
      set({ notice: '只有已完成的任务可以执行局部视觉返工。' })
      return
    }
    const range = validateOperationRange(
      task,
      input.startSeconds,
      input.endSeconds,
    )
    if (!range.ok) {
      set({ notice: range.error })
      return
    }
    let sampling: Extract<
      ApiRunOperationRequest,
      { kind: 'vision_rescan' }
    >['sampling']
    if (input.mode === 'fixed_interval') {
      const interval = validateFixedInterval(
        range.startUs,
        range.endUs,
        input.intervalSeconds,
      )
      if (!interval.ok) {
        set({ notice: interval.error })
        return
      }
      sampling = {
        mode: 'fixed_interval',
        interval_us: interval.intervalUs,
      }
    } else {
      if (input.intervalSeconds !== undefined) {
        set({ notice: '自适应视觉返工不接受固定采样间隔。' })
        return
      }
      sampling = { mode: 'adaptive' }
    }
    if (!task.realBackend) {
      const operation = makeDemoReworkOperation({
        taskId,
        kind: 'vision_rescan',
        startSeconds: range.startUs / MICROSECONDS_PER_SECOND,
        endSeconds: range.endUs / MICROSECONDS_PER_SECOND,
        samplingMode: input.mode,
        intervalSeconds:
          sampling.mode === 'fixed_interval'
            ? sampling.interval_us / MICROSECONDS_PER_SECOND
            : undefined,
        runOcr: input.runOcr,
      })
      set(state => ({
        tasks: state.tasks.map(item =>
          item.id === taskId
            ? { ...item, operations: [...item.operations, operation] }
            : item,
        ),
        notice:
          '已记录视觉返工演示预览；演示不执行模型、不修改证据，也未重生成报告。',
      }))
      return
    }
    const client = api
    if (!client || get().backend.mode !== 'real') {
      set({ notice: '本地后端尚未连接，无法执行视觉返工。' })
      return
    }
    const request: ApiRunOperationRequest = {
      kind: 'vision_rescan',
      range: { start_us: range.startUs, end_us: range.endUs },
      sampling,
      run_ocr: input.runOcr,
    }
    void executeBackendRework(client, taskId, request)
      .then(result =>
        set(state => ({
          tasks: state.tasks.map(item =>
            item.id === taskId ? applyBackendRework(item, result) : item,
          ),
          notice: backendReworkNotice(result),
        })),
      )
      .catch(error => set({ notice: reworkErrorMessage(error) }))
  },

  runAsrRework: (taskId, input) => {
    const task = get().tasks.find(item => item.id === taskId)
    if (!task) {
      set({ notice: '没有找到要执行语音返工的任务。' })
      return
    }
    if (task.status !== 'completed') {
      set({ notice: '只有已完成的任务可以执行局部语音返工。' })
      return
    }
    const range = validateOperationRange(
      task,
      input.range.startSeconds,
      input.range.endSeconds,
    )
    if (!range.ok) {
      set({ notice: range.error })
      return
    }
    if (!Array.isArray(input.languageHints)) {
      set({ notice: '语言提示必须是字符串列表。' })
      return
    }
    const languageHints = [
      ...new Set(input.languageHints.map(value => value.trim()).filter(Boolean)),
    ]
    if (languageHints.length > 8) {
      set({ notice: '语音返工最多接受 8 个去重后的语言提示。' })
      return
    }
    if (!task.realBackend) {
      const operation = makeDemoReworkOperation({
        taskId,
        kind: 'asr_retranscribe',
        startSeconds: range.startUs / MICROSECONDS_PER_SECOND,
        endSeconds: range.endUs / MICROSECONDS_PER_SECOND,
        languageHints,
      })
      set(state => ({
        tasks: state.tasks.map(item =>
          item.id === taskId
            ? { ...item, operations: [...item.operations, operation] }
            : item,
        ),
        notice:
          '已记录语音返工演示预览；演示不执行模型、不修改证据，也未重生成报告。',
      }))
      return
    }
    const client = api
    if (!client || get().backend.mode !== 'real') {
      set({ notice: '本地后端尚未连接，无法执行语音返工。' })
      return
    }
    const request: ApiRunOperationRequest = {
      kind: 'asr_retranscribe',
      range: { start_us: range.startUs, end_us: range.endUs },
      language_hints: languageHints,
    }
    void executeBackendRework(client, taskId, request)
      .then(result =>
        set(state => ({
          tasks: state.tasks.map(item =>
            item.id === taskId ? applyBackendRework(item, result) : item,
          ),
          notice: backendReworkNotice(result),
        })),
      )
      .catch(error => set({ notice: reworkErrorMessage(error) }))
  },

  correctEvidence: (taskId, input) => {
    const task = get().tasks.find(item => item.id === taskId)
    if (!task) {
      set({ notice: '没有找到要修正证据的任务。' })
      return
    }
    if (task.status !== 'completed') {
      set({ notice: '只有已完成的任务可以修正证据。' })
      return
    }
    const evidenceId = input.evidenceId.trim()
    const target = task.evidence.find(item => item.id === evidenceId)
    if (!target) {
      set({ notice: '没有找到要修正的有效证据。' })
      return
    }
    const newText = input.newText.trim()
    const reason = input.reason?.trim() || undefined
    if (!newText) {
      set({ notice: '人工修正文本不能为空。' })
      return
    }
    if (newText.length > 200_000) {
      set({ notice: '人工修正文本不能超过 200000 个字符。' })
      return
    }
    if (reason && reason.length > 2_000) {
      set({ notice: '修正原因不能超过 2000 个字符。' })
      return
    }
    const start = secondsToSafeMicroseconds(target.startSeconds, '证据开始时间')
    if (!start.ok) {
      set({ notice: start.error })
      return
    }
    const end = secondsToSafeMicroseconds(target.endSeconds, '证据结束时间')
    if (!end.ok) {
      set({ notice: end.error })
      return
    }
    const endUs =
      end.value > start.value
        ? end.value
        : start.value < Number.MAX_SAFE_INTEGER
          ? start.value + 1
          : undefined
    if (endUs === undefined) {
      set({ notice: '证据时间点无法安全转换为非空微秒区间。' })
      return
    }
    if (!task.realBackend) {
      const operation = makeDemoReworkOperation({
        taskId,
        kind: 'evidence_correct',
        status: 'completed',
        startSeconds: start.value / MICROSECONDS_PER_SECOND,
        endSeconds: endUs / MICROSECONDS_PER_SECOND,
        evidenceId,
        newText,
        reason,
        replacedEvidenceIds: [evidenceId],
        newEvidenceIds: [evidenceId],
      })
      set(state => ({
        tasks: state.tasks.map(item =>
          item.id === taskId
            ? {
                ...item,
                operations: [...item.operations, operation],
                evidenceRevisionId: `demo-${operation.id}`,
                evidence: item.evidence.map(evidence =>
                  evidence.id === evidenceId
                    ? {
                        ...evidence,
                        rawText: newText,
                        provider: 'user/demo-memory',
                      }
                    : evidence,
                ),
              }
            : item,
        ),
        notice:
          '人工修正已写入演示内存并标记为 user/demo-memory；原时间与置信度保持不变，报告尚未重生成。',
      }))
      return
    }
    const client = api
    if (!client || get().backend.mode !== 'real') {
      set({ notice: '本地后端尚未连接，无法修正证据。' })
      return
    }
    const request: ApiRunOperationRequest = {
      kind: 'evidence_correct',
      range: { start_us: start.value, end_us: endUs },
      evidence_id: evidenceId,
      new_text: newText,
      ...(reason ? { reason } : {}),
    }
    void executeBackendRework(client, taskId, request)
      .then(result =>
        set(state => ({
          tasks: state.tasks.map(item =>
            item.id === taskId ? applyBackendRework(item, result) : item,
          ),
          notice: backendReworkNotice(result),
        })),
      )
      .catch(error => set({ notice: reworkErrorMessage(error) }))
  },

  refreshReportRevisions: async taskId => {
    const task = get().tasks.find(item => item.id === taskId)
    if (!task) {
      const error = new Error('没有找到要刷新的任务。')
      set({ notice: error.message })
      throw error
    }
    if (!task.realBackend) {
      set({
        notice: '演示报告 revision 仅保存在当前内存中；没有请求本地后端。',
      })
      return
    }
    const client = api
    if (!client || get().backend.mode !== 'real') {
      const error = new Error('本地后端尚未连接，无法刷新报告 revision。')
      set({ notice: error.message })
      throw error
    }
    try {
      const index = await client.listReportRevisions(taskId)
      const revisions = newestReportFirst(
        index.revisions.map(mapReportRevision),
      )
      set(state => ({
        tasks: state.tasks.map(item =>
          item.id === taskId ? { ...item, reportRevisions: revisions } : item,
        ),
        notice: `已同步 ${revisions.length} 份不可变报告 revision。`,
      }))
    } catch (error) {
      set({ notice: errorMessage(error) })
      throw error
    }
  },

  generateReportRevision: async (taskId, input) => {
    const task = get().tasks.find(item => item.id === taskId)
    if (!task) {
      const error = new Error('没有找到要生成报告的任务。')
      set({ notice: error.message })
      throw error
    }
    if (task.status !== 'completed') {
      const error = new Error('只有已完成的任务可以重新生成报告。')
      set({ notice: error.message })
      throw error
    }
    if (!task.realBackend) {
      const now = new Date().toISOString()
      const formats: ReportRevisionFormat[] = input.includePdf
        ? ['markdown', 'html', 'pdf']
        : ['markdown', 'html']
      const revision: ReportRevision = {
        id: `revision-demo-${Date.now()}`,
        runId: taskId,
        preset: input.preset,
        createdAt: now,
        formats,
        fallback: false,
        warnings: ['演示 revision：未调用 LLM，也未写入本地 artifact。'],
        evidenceRevisionId: task.evidenceRevisionId ?? 'base-evidence',
        materialIds: task.materials.map(material => material.id),
        artifactPaths: {},
        source: 'demo-memory',
      }
      set(state => ({
        tasks: state.tasks.map(item =>
          item.id === taskId
            ? {
                ...item,
                reportRevisions: newestReportFirst([
                  ...(item.reportRevisions ?? []),
                  revision,
                ]),
              }
            : item,
        ),
        notice:
          '已创建明确标记的演示报告 revision；未执行模型、未写入文件。',
      }))
      return
    }
    const client = api
    if (!client || get().backend.mode !== 'real') {
      const error = new Error('本地后端尚未连接，无法生成报告 revision。')
      set({ notice: error.message })
      throw error
    }
    try {
      const created = await client.createReportRevision(taskId, {
        preset: input.preset,
        language: 'zh-CN',
        include_screenshots: input.includeScreenshots,
        output_formats: input.includePdf
          ? ['markdown', 'html', 'pdf']
          : ['markdown', 'html'],
      })
      const revision = mapReportRevision(created)
      let generatedNote: NoteDocument | undefined
      try {
        generatedNote = await loadRevisionNote(
          client,
          taskId,
          revision.artifactPaths.document ?? '',
        )
      } catch {
        generatedNote = undefined
      }
      set(state => ({
        tasks: state.tasks.map(item =>
          item.id === taskId
            ? {
                ...item,
                note: generatedNote ?? item.note,
                reportRevisions: newestReportFirst([
                  ...(item.reportRevisions ?? []).filter(
                    existing => existing.id !== revision.id,
                  ),
                  revision,
                ]),
                artifactPaths: {
                  ...item.artifactPaths,
                  markdown: revision.artifactPaths.markdown,
                  html: revision.artifactPaths.html,
                  pdf: revision.artifactPaths.pdf,
                  noteDocument: revision.artifactPaths.document,
                },
              }
            : item,
        ),
        notice: generatedNote
          ? `已生成 ${input.preset} 报告，并切换阅读区到 ${revision.id}。`
          : `报告 ${revision.id} 已生成，但阅读预览刷新失败；产物仍可下载。`,
      }))
    } catch (error) {
      set({ notice: errorMessage(error) })
      throw error
    }
  },

  downloadReportRevisionArtifact: (taskId, relativePath, format) => {
    if (!relativePath || !api) {
      set({ notice: `该报告没有可下载的 ${format.toUpperCase()} 产物。` })
      return
    }
    void api
      .artifactBlobUrl(taskId, relativePath)
      .then(url => {
        const anchor = document.createElement('a')
        anchor.href = url
        anchor.download =
          relativePath.split('/').at(-1) || `note.${format}`
        anchor.click()
        window.setTimeout(() => URL.revokeObjectURL(url), 10_000)
        set({ notice: `已导出报告 revision 的 ${anchor.download}。` })
      })
      .catch(error => set({ notice: errorMessage(error) }))
  },

  refreshMaterials: taskId => {
    const task = get().tasks.find(item => item.id === taskId)
    if (!task) {
      set({ notice: '没有找到要刷新的任务。' })
      return
    }
    if (!task.realBackend) {
      set({ notice: '演示材料仅保存在当前内存中；没有向本地后端发起请求。' })
      return
    }
    if (!api || get().backend.mode !== 'real') {
      set({ notice: '本地后端尚未连接，无法刷新补充材料。' })
      return
    }
    void api
      .listMaterials(taskId)
      .then(materials =>
        set(state => ({
          tasks: state.tasks.map(item =>
            item.id === taskId
              ? { ...item, materials: materials.map(mapSupportingMaterial) }
              : item,
          ),
          notice: `已同步 ${materials.length} 条补充材料。`,
        })),
      )
      .catch(error => set({ notice: errorMessage(error) }))
  },

  addTextMaterial: (taskId, input) => {
    const task = get().tasks.find(item => item.id === taskId)
    if (!task) {
      set({ notice: '没有找到要添加材料的任务。' })
      return
    }
    const title = input.title.trim()
    const content = input.content.trim()
    if (!title || !content) {
      set({ notice: '补充文字的标题和内容都不能为空。' })
      return
    }
    const rangeError = materialRangeError(input.startUs, input.endUs)
    if (rangeError) {
      set({ notice: rangeError })
      return
    }
    if (!task.realBackend) {
      demoMaterialSequence += 1
      const material: SupportingMaterial = {
        id: `material-demotext${demoMaterialSequence}`,
        runId: taskId,
        kind: 'text',
        title,
        originalName: null,
        mediaType: 'text/markdown; charset=utf-8',
        artifact: null,
        sha256: null,
        sizeBytes: new TextEncoder().encode(content).byteLength,
        textContent: content,
        startUs: input.startUs ?? null,
        endUs: input.endUs ?? null,
        status: 'active',
        createdAt: new Date().toISOString(),
        deletedAt: null,
        storage: 'demo-memory',
      }
      set(state => ({
        tasks: state.tasks.map(item =>
          item.id === taskId
            ? { ...item, materials: [...item.materials, material] }
            : item,
        ),
        notice: '演示文字材料已加入当前内存；没有上传或写入后端。',
      }))
      return
    }
    if (!api || get().backend.mode !== 'real') {
      set({ notice: '本地后端尚未连接，无法保存补充文字。' })
      return
    }
    void api
      .addTextMaterial(taskId, {
        title,
        content,
        ...(input.startUs !== undefined ? { start_us: input.startUs } : {}),
        ...(input.endUs !== undefined ? { end_us: input.endUs } : {}),
      })
      .then(created => {
        const material = mapSupportingMaterial(created)
        set(state => ({
          tasks: state.tasks.map(item =>
            item.id === taskId
              ? {
                  ...item,
                  materials: [
                    ...item.materials.filter(existing => existing.id !== material.id),
                    material,
                  ],
                }
              : item,
          ),
          notice: `补充文字“${material.title}”已保存到本地任务。`,
        }))
      })
      .catch(error => set({ notice: errorMessage(error) }))
  },

  addFileMaterial: (taskId, file, options = {}) => {
    const task = get().tasks.find(item => item.id === taskId)
    if (!task) {
      set({ notice: '没有找到要添加材料的任务。' })
      return
    }
    const rangeError = materialRangeError(options.startUs, options.endUs)
    if (rangeError) {
      set({ notice: rangeError })
      return
    }
    if (!task.realBackend) {
      const supportedTypes = new Set(['image/png', 'image/jpeg', 'image/webp'])
      if (!supportedTypes.has(file.type)) {
        set({ notice: '演示材料只接受 PNG、JPEG 或 WebP 图片元数据。' })
        return
      }
      demoMaterialSequence += 1
      const material: SupportingMaterial = {
        id: `material-demoimage${demoMaterialSequence}`,
        runId: taskId,
        kind: 'image',
        title: options.title?.trim() || file.name || '补充图片',
        originalName: file.name || null,
        mediaType: file.type,
        artifact: null,
        sha256: null,
        sizeBytes: file.size,
        textContent: null,
        startUs: options.startUs ?? null,
        endUs: options.endUs ?? null,
        status: 'active',
        createdAt: new Date().toISOString(),
        deletedAt: null,
        storage: 'demo-memory',
      }
      set(state => ({
        tasks: state.tasks.map(item =>
          item.id === taskId
            ? { ...item, materials: [...item.materials, material] }
            : item,
        ),
        notice: '演示图片仅保存名称与元数据；没有读取内容或伪造后端上传。',
      }))
      return
    }
    if (!api || get().backend.mode !== 'real') {
      set({ notice: '本地后端尚未连接，无法上传补充图片。' })
      return
    }
    void api
      .addFileMaterial(taskId, file, {
        ...(options.title !== undefined ? { title: options.title.trim() } : {}),
        ...(options.startUs !== undefined ? { start_us: options.startUs } : {}),
        ...(options.endUs !== undefined ? { end_us: options.endUs } : {}),
      })
      .then(created => {
        const material = mapSupportingMaterial(created)
        set(state => ({
          tasks: state.tasks.map(item =>
            item.id === taskId
              ? {
                  ...item,
                  materials: [
                    ...item.materials.filter(existing => existing.id !== material.id),
                    material,
                  ],
                }
              : item,
          ),
          notice: `补充图片“${material.title}”已保存到本地任务。`,
        }))
      })
      .catch(error => set({ notice: errorMessage(error) }))
  },

  deleteMaterial: (taskId, materialId) => {
    const task = get().tasks.find(item => item.id === taskId)
    if (!task) {
      set({ notice: '没有找到要删除材料的任务。' })
      return
    }
    if (!task.materials.some(material => material.id === materialId)) {
      set({ notice: '没有找到这条补充材料。' })
      return
    }
    if (!task.realBackend) {
      set(state => ({
        tasks: state.tasks.map(item =>
          item.id === taskId
            ? {
                ...item,
                materials: item.materials.filter(material => material.id !== materialId),
              }
            : item,
        ),
        notice: '演示材料已从当前内存移除；没有调用后端删除接口。',
      }))
      return
    }
    if (!api || get().backend.mode !== 'real') {
      set({ notice: '本地后端尚未连接，无法删除补充材料。' })
      return
    }
    void api
      .deleteMaterial(taskId, materialId)
      .then(() =>
        set(state => ({
          tasks: state.tasks.map(item =>
            item.id === taskId
              ? {
                  ...item,
                  materials: item.materials.filter(material => material.id !== materialId),
                }
              : item,
          ),
          notice: '补充材料已从当前任务中删除。',
        })),
      )
      .catch(error => set({ notice: errorMessage(error) }))
  },

  setCurrentTime: currentTimeSeconds => set({ currentTimeSeconds }),
  selectEvidence: (selectedEvidenceId, timeSeconds) =>
    set(state => ({
      selectedEvidenceId,
      currentTimeSeconds: timeSeconds ?? state.currentTimeSeconds,
    })),
  selectProvider: selectedProviderId => set({ selectedProviderId }),
  refreshComponents: () => {
    if (!api || get().backend.mode !== 'real') {
      set({ notice: '演示模式只展示组件状态样例；没有扫描或修改本机文件。' })
      return
    }
    void api
      .components()
      .then(payload => {
        const componentReport = mapComponentReport(payload)
        set({
          componentReport,
          selectedComponentIds: selectableComponentIds(componentReport),
          componentPreparationStatus: 'idle',
          componentPreparationResults: [],
          componentPreparationActivated: false,
          componentPreparationActivatedRoles: [],
          componentPreparationBlockedRoles: [],
          componentPreparationWarnings: [],
          componentPreparationError: undefined,
          notice: componentReport.inventory.ready
            ? '本地运行环境和推荐模型均已就绪。'
            : '组件状态已重新扫描。',
        })
      })
      .catch(error =>
        set({
          componentPreparationStatus: 'error',
          componentPreparationError: errorMessage(error),
          notice: errorMessage(error),
        }),
      )
  },
  setComponentSelected: (componentId, selected) => {
    const item = get().componentReport?.inventory.items.find(
      candidate => candidate.id === componentId,
    )
    if (!item || item.kind !== 'local_model') return
    set(state => ({
      selectedComponentIds: selected
        ? [...new Set([...state.selectedComponentIds, componentId])].slice(0, 8)
        : state.selectedComponentIds.filter(id => id !== componentId),
    }))
  },
  prepareLocalComponents: componentIds => {
    const report = get().componentReport
    if (!api || get().backend.mode !== 'real') {
      const message = '演示模式不会下载模型；请通过桌面应用连接本机后端后再准备。'
      set({
        componentPreparationStatus: 'error',
        componentPreparationError: message,
        notice: message,
      })
      return
    }
    const requestedIds = componentIds
      ? [...new Set(componentIds.map(value => value.trim()).filter(Boolean))]
      : []
    if (componentIds && requestedIds.length === 0) {
      const message = '请至少选择一个可准备的本地模型组件。'
      set({
        componentPreparationStatus: 'error',
        componentPreparationError: message,
        notice: message,
      })
      return
    }
    const allowedIds = new Set(
      report?.inventory.items
        .filter(item => item.kind === 'local_model')
        .map(item => item.id) ?? [],
    )
    if (
      componentIds &&
      (requestedIds.length > 8 || requestedIds.some(componentId => !allowedIds.has(componentId)))
    ) {
      const message = '组件选择无效；只能准备当前清单中的 ASR/OCR 本地模型。'
      set({
        componentPreparationStatus: 'error',
        componentPreparationError: message,
        notice: message,
      })
      return
    }

    set({
      componentPreparationStatus: 'preparing',
      componentPreparationResults: [],
      componentPreparationActivated: false,
      componentPreparationActivatedRoles: [],
      componentPreparationBlockedRoles: [],
      componentPreparationWarnings: [],
      componentPreparationError: undefined,
      notice: componentIds
        ? `正在下载并校验 ${requestedIds.length} 个所选组件…`
        : '正在下载并校验推荐的 ASR / OCR 模型…',
    })
    void api
      .prepareComponents({
        component_ids: requestedIds,
        hardware_tier: report?.hardwareTier ?? null,
        activate: true,
      })
      .then(async payload => {
        const preparation = mapComponentPreparation(payload)
        const failed = preparation.results.filter(result => result.status === 'failed')
        let presentation:
          | ReturnType<typeof registryPresentation>
          | undefined
        let refreshWarning = ''
        if (preparation.activated) {
          try {
            const refreshedRegistry = await api!.providers()
            registry = refreshedRegistry
            const secretStates = Object.fromEntries(
              get().providers.map(provider => [
                provider.id,
                provider.credentialState === 'stored-locally'
                  ? 'configured'
                  : 'not_configured',
              ]),
            )
            presentation = registryPresentation(
              refreshedRegistry,
              get().configurationCatalog,
              secretStates,
            )
          } catch (error) {
            refreshWarning = ` 模型已激活，但模型路由刷新失败：${errorMessage(error)}`
          }
        }
        const failedMessage = failed
          .map(result => result.detail || `${result.componentId} 准备失败`)
          .join('；')
        const activatedRoleSummary = preparation.activatedRoles.length > 0
          ? `已激活：${preparation.activatedRoles.map(role => roleMetadata(role).label).join('、')}。`
          : ''
        const blockedRoleSummary = preparation.blockedRoles.length > 0
          ? `仍未激活：${preparation.blockedRoles.map(role => roleMetadata(role).label).join('、')}。`
          : ''
        const warningSummary = preparation.warnings[0]
          ? `提示：${preparation.warnings[0]}`
          : ''
        const activationSummary = [
          activatedRoleSummary,
          blockedRoleSummary,
          warningSummary,
        ].filter(Boolean).join(' ')
        const currentProviderId = get().selectedProviderId
        const nextPresentation = presentation
          ? {
              ...presentation,
              selectedProviderId: presentation.providers.some(
                provider => provider.id === currentProviderId,
              )
                ? currentProviderId
                : presentation.selectedProviderId,
            }
          : {}
        set({
          ...nextPresentation,
          componentReport: preparation.report,
          componentPreparationStatus: failed.length > 0 ? 'error' : 'success',
          componentPreparationResults: preparation.results,
          componentPreparationActivated: preparation.activated,
          componentPreparationActivatedRoles: preparation.activatedRoles,
          componentPreparationBlockedRoles: preparation.blockedRoles,
          componentPreparationWarnings: preparation.warnings,
          componentPreparationError: failed.length > 0 ? failedMessage : undefined,
          selectedComponentIds: selectableComponentIds(preparation.report),
          notice:
            failed.length > 0
              ? `部分组件准备失败：${failedMessage}`
              : preparation.activated
                ? `推荐模型已校验并激活。${activationSummary} ${refreshWarning}`.trim()
                : `所选组件已准备。${activationSummary || 'ASR 与 OCR 都就绪后会自动激活。'} ${refreshWarning}`.trim(),
        })
      })
      .catch(error => {
        const message = errorMessage(error)
        set({
          componentPreparationStatus: 'error',
          componentPreparationError: message,
          notice: message,
        })
      })
  },
  refreshRuntimePackages: () => {
    if (!api || get().backend.mode !== 'real') {
      set({ notice: '演示模式只展示运行时包样例，不会扫描或修改本机目录。' })
      return
    }
    stopRuntimeInventoryPolling()
    if (!get().runtimePackages) {
      set({ runtimePackagesStatus: 'loading', runtimePackageError: undefined })
    }
    void api
      .runtimePackages()
      .then(payload => {
        const inventory = mapRuntimePackages(payload)
        const state = get()
        const previousOperations = new Map(
          (state.runtimePackages?.operations ?? []).map(operation => [
            operation.operationId,
            operation,
          ]),
        )
        const active = inventory.operations.some(operation =>
          ['queued', 'running'].includes(operation.status),
        )
        const watchedIds = state.preflightRuntimeOperationIds
        const watchingPreflight =
          state.preflightAutoRetryPending && watchedIds.length > 0
        const watchedOperations = watchedIds
          .map(operationId =>
            inventory.operations.find(
              operation => operation.operationId === operationId,
            ),
          )
          .filter(
            (operation): operation is RuntimePackageOperationDefinition =>
              operation !== undefined,
          )
        const allWatchedFound = watchedOperations.length === watchedIds.length
        const allWatchedTerminal =
          allWatchedFound &&
          watchedOperations.every(operation =>
            ['succeeded', 'failed', 'cancelled'].includes(operation.status),
          )

        if (watchingPreflight && allWatchedTerminal) {
          const failedOperation = watchedOperations.find(
            operation => operation.status !== 'succeeded',
          )
          if (failedOperation) {
            const detail = failedOperation.detail
              ? `${failedOperation.packageId}：${failedOperation.detail}`
              : `${failedOperation.packageId} 的${runtimeOperationKindLabels[failedOperation.kind]}任务${failedOperation.status === 'cancelled' ? '已取消' : '失败'}。`
            set({
              runtimePackages: inventory,
              runtimePackagesStatus: 'ready',
              runtimePackageError: detail,
              preflightRuntimeOperationIds: [],
              preflightAutoRetryPending: false,
              jobPreflightDialogOpen: true,
              notice: `依赖准备未完成：${detail}`,
            })
            return
          }

          set({
            runtimePackages: inventory,
            runtimePackagesStatus: 'ready',
            runtimePackageError: undefined,
            preflightRuntimeOperationIds: [],
            preflightAutoRetryPending: false,
            jobPreflightDialogOpen: false,
            notice: '所需依赖已全部安装并绑定，正在重新检查同一任务。',
          })
          void Promise.resolve().then(() => get().createTask())
          return
        }

        const terminalTransitions = inventory.operations.filter(operation => {
          const previous = previousOperations.get(operation.operationId)
          return Boolean(
            previous &&
              ['queued', 'running'].includes(previous.status) &&
              ['succeeded', 'failed', 'cancelled'].includes(operation.status),
          )
        })
        const failedTransition = terminalTransitions.find(
          operation => operation.status === 'failed',
        )
        const cancelledTransition = terminalTransitions.find(
          operation => operation.status === 'cancelled',
        )
        const completedTransition = terminalTransitions.find(
          operation => operation.status === 'succeeded',
        )
        const transitionNotice = failedTransition
          ? `${failedTransition.packageId} 的${runtimeOperationKindLabels[failedTransition.kind]}失败：${failedTransition.detail ?? failedTransition.errorCode ?? '请查看操作记录。'}`
          : cancelledTransition
            ? `${cancelledTransition.packageId} 的${runtimeOperationKindLabels[cancelledTransition.kind]}已取消。`
            : completedTransition
              ? `${completedTransition.packageId} 的${runtimeOperationKindLabels[completedTransition.kind]}已完成，运行时清单与绑定已刷新。`
              : undefined
        set({
          runtimePackages: inventory,
          runtimePackagesStatus: 'ready',
          runtimePackageError: failedTransition
            ? failedTransition.detail ?? failedTransition.errorCode ?? '运行时操作失败。'
            : undefined,
          ...(transitionNotice ? { notice: transitionNotice } : {}),
        })
        if (
          (active || (watchingPreflight && !allWatchedTerminal)) &&
          typeof window !== 'undefined'
        ) {
          runtimeInventoryPollTimer = window.setTimeout(
            () => get().refreshRuntimePackages(),
            800,
          )
        }
      })
      .catch(error => {
        const message = errorMessage(error)
        set({
          runtimePackagesStatus: 'error',
          runtimePackageError: message,
          notice: message,
        })
        if (get().preflightAutoRetryPending && typeof window !== 'undefined') {
          runtimeInventoryPollTimer = window.setTimeout(
            () => get().refreshRuntimePackages(),
            800,
          )
        }
      })
  },
  discoverRuntimePackages: () => {
    if (!api || get().backend.mode !== 'real') {
      set({ notice: '连接真实本机后端后才能重新发现系统运行时。' })
      return
    }
    stopRuntimeInventoryPolling()
    set({
      runtimePackagesStatus: 'loading',
      runtimePackageError: undefined,
      notice: '正在检查随包、受管和本机系统运行时。',
    })
    void api
      .discoverRuntimePackages()
      .then(payload => {
        const inventory = mapRuntimePackages(payload)
        set({
          runtimePackages: inventory,
          runtimePackagesStatus: 'ready',
          runtimePackageError: undefined,
          notice: `运行时发现完成，共找到 ${inventory.instances.length} 个可登记实例。`,
        })
      })
      .catch(error => {
        const message = errorMessage(error)
        set({
          runtimePackagesStatus: 'error',
          runtimePackageError: message,
          notice: message,
        })
      })
  },
  registerCustomRuntimeDirectory: () => {
    if (!api || get().backend.mode !== 'real') {
      set({ notice: '连接真实本机后端后才能登记自定义运行时目录。' })
      return
    }
    void pickRuntimeDirectoryWithNativeDialog()
      .then(root => {
        if (!root) return
        set({
          runtimePackagesStatus: 'loading',
          runtimePackageError: undefined,
          notice: '正在验证目录中的 runtime-package.json 与固定入口。',
        })
        return api!
          .registerCustomRuntime(root)
          .then(() => {
            set({
              runtimePackagesStatus: 'ready',
              runtimePackageError: undefined,
              notice: '自定义运行时已登记；应用不会接管或删除原目录。',
            })
            get().refreshRuntimePackages()
          })
      })
      .catch(error => {
        const message = errorMessage(error)
        set({
          runtimePackagesStatus: 'error',
          runtimePackageError: message,
          notice: message,
        })
      })
  },
  installRuntimePackage: (packageId, version, bindRequirements = []) => {
    if (!api || get().backend.mode !== 'real') {
      set({ notice: '演示模式不会下载运行时包。' })
      return
    }
    set({
      runtimePackageError: undefined,
      notice: `正在创建 ${packageId} 的受管安装任务。`,
    })
    void api
      .installRuntimePackage({
        package_id: packageId,
        version: version ?? null,
        bind_requirements: [...new Set(bindRequirements)],
      })
      .then(operation => {
        set(state => ({
          runtimePackages: mergeRuntimeOperation(state.runtimePackages, operation),
          runtimePackagesStatus: 'ready',
          notice: '安装任务已开始；可安全取消，已下载的分片会保留用于续传。',
        }))
        get().refreshRuntimePackages()
      })
      .catch(error => {
        const message = errorMessage(error)
        set({ runtimePackagesStatus: 'error', runtimePackageError: message, notice: message })
      })
  },
  upgradeRuntimePackage: instanceId => {
    const instance = get().runtimePackages?.instances.find(
      item => item.instanceId === instanceId,
    )
    if (!instance || instance.source !== 'managed') {
      set({ notice: '只有应用受管的运行时包可以升级。' })
      return
    }
    if (!api || get().backend.mode !== 'real') {
      set({ notice: '演示模式不会升级运行时包。' })
      return
    }
    set({ runtimePackageError: undefined, notice: '正在创建并排升级任务。' })
    void api
      .upgradeRuntimePackage(instanceId)
      .then(operation => {
        set(state => ({
          runtimePackages: mergeRuntimeOperation(state.runtimePackages, operation),
          runtimePackagesStatus: 'ready',
          notice: '升级任务已开始；新版本验证通过后才会切换绑定。',
        }))
        get().refreshRuntimePackages()
      })
      .catch(error => {
        const message = errorMessage(error)
        set({ runtimePackagesStatus: 'error', runtimePackageError: message, notice: message })
      })
  },
  cancelRuntimeOperation: operationId => {
    if (!api || get().backend.mode !== 'real') return
    void api
      .cancelRuntimePackageOperation(operationId)
      .then(operation => {
        set(state => ({
          runtimePackages: mergeRuntimeOperation(state.runtimePackages, operation),
          notice: '已请求取消；当前校验或文件操作到达安全点后会停止。',
        }))
        get().refreshRuntimePackages()
      })
      .catch(error => set({ notice: errorMessage(error) }))
  },
  bindRuntimeRequirement: (requirementId, instanceId, capabilityId) => {
    if (!api || get().backend.mode !== 'real') {
      set({ notice: '演示模式不会修改运行时绑定。' })
      return
    }
    void api
      .bindRuntimePackage({
        requirement_id: requirementId,
        instance_id: instanceId,
        capability_id: capabilityId ?? null,
      })
      .then(() => {
        set({
          runtimePackagesStatus: 'ready',
          runtimePackageError: undefined,
          notice: `${runtimeRequirementLabels[requirementId] ?? requirementId} 已绑定。`,
        })
        get().refreshRuntimePackages()
      })
      .catch(error => set({ notice: errorMessage(error) }))
  },
  unbindRuntimeRequirement: requirementId => {
    if (!api || get().backend.mode !== 'real') {
      set({ notice: '演示模式不会修改运行时绑定。' })
      return
    }
    void api
      .unbindRuntimeRequirement(requirementId)
      .then(() => {
        set({
          runtimePackagesStatus: 'ready',
          runtimePackageError: undefined,
          notice: `${runtimeRequirementLabels[requirementId] ?? requirementId} 已取消绑定。`,
        })
        get().refreshRuntimePackages()
      })
      .catch(error => set({ notice: errorMessage(error) }))
  },
  removeRuntimePackage: instanceId => {
    const instance = get().runtimePackages?.instances.find(
      item => item.instanceId === instanceId,
    )
    if (!instance || instance.source !== 'managed') {
      set({ notice: '只有应用受管的运行时包可以卸载。' })
      return
    }
    if (!api || get().backend.mode !== 'real') return
    void api
      .uninstallRuntimePackage(instanceId)
      .then(operation => {
        set(state => ({
          runtimePackages: mergeRuntimeOperation(state.runtimePackages, operation),
          notice: '卸载任务已开始；仍被绑定或使用中的版本不会被删除。',
        }))
        get().refreshRuntimePackages()
      })
      .catch(error => set({ notice: errorMessage(error) }))
  },
  forgetRuntimePackage: instanceId => {
    const instance = get().runtimePackages?.instances.find(
      item => item.instanceId === instanceId,
    )
    if (!instance || instance.source !== 'custom') {
      set({ notice: '只有自定义登记可以从清单中忘记。' })
      return
    }
    if (!api || get().backend.mode !== 'real') return
    void api
      .forgetCustomRuntime(instanceId)
      .then(() => {
        set({
          runtimePackagesStatus: 'ready',
          runtimePackageError: undefined,
          notice: '已忘记自定义运行时登记；原目录与文件没有被删除。',
        })
        get().refreshRuntimePackages()
      })
      .catch(error => set({ notice: errorMessage(error) }))
  },
  installPreflightRequirements: () => {
    const preflight = get().jobPreflight
    if (get().preflightAutoRetryPending) {
      set({ notice: '依赖安装正在进行，全部完成后会自动重新检查当前任务。' })
      return
    }
    if (!preflight || !api || get().backend.mode !== 'real') {
      set({ notice: '当前没有可自动安装的依赖建议。' })
      return
    }
    const installs = Array.from(
      preflight.recommendedActions
        .filter(action => action.kind === 'install' && action.packageId)
        .reduce((items, action) => {
          const key = `${action.packageId}@${action.version ?? 'latest'}`
          const existing = items.get(key)
          items.set(key, {
            packageId: action.packageId!,
            version: action.version,
            bindRequirements: [
              ...(existing?.bindRequirements ?? []),
              ...action.bindRequirements,
            ],
          })
          return items
        }, new Map<string, { packageId: string; version?: string; bindRequirements: string[] }>())
        .values(),
    )
    if (installs.length === 0) {
      set({ notice: '后端没有提供可自动执行的安装建议，请打开依赖管理。' })
      return
    }
    set({
      runtimePackageError: undefined,
      preflightRuntimeOperationIds: [],
      preflightAutoRetryPending: false,
      notice: '正在创建缺失依赖的安装与绑定任务。',
    })
    void Promise.allSettled(
      installs.map(item =>
        api!.installRuntimePackage({
          package_id: item.packageId,
          version: item.version ?? null,
          bind_requirements: [...new Set(item.bindRequirements)],
        }),
      ),
    )
      .then(results => {
        const startedOperations = results.flatMap(result =>
          result.status === 'fulfilled' ? [result.value] : [],
        )
        const startFailures = results.flatMap(result =>
          result.status === 'rejected' ? [errorMessage(result.reason)] : [],
        )
        const operationIds = startedOperations.map(
          operation => operation.operation_id,
        )
        set(state => ({
          runtimePackages: startedOperations.reduce(
            (inventory, operation) => mergeRuntimeOperation(inventory, operation),
            state.runtimePackages,
          ),
          preflightRuntimeOperationIds: operationIds,
          preflightAutoRetryPending:
            startFailures.length === 0 && operationIds.length === installs.length,
          runtimePackageError:
            startFailures.length > 0
              ? `部分安装任务无法启动：${startFailures.join('；')}`
              : undefined,
          jobPreflightDialogOpen: true,
          notice:
            startFailures.length > 0
              ? `部分安装任务无法启动：${startFailures.join('；')}`
              : '所需组件任务已开始；全部完成后会自动刷新清单并重新检查当前任务。',
        }))
        get().refreshRuntimePackages()
      })
  },
  dismissJobPreflight: () => {
    const autoRetryPending = get().preflightAutoRetryPending
    set({
      jobPreflightDialogOpen: false,
      ...(autoRetryPending
        ? {
            preflightRuntimeOperationIds: [],
            preflightAutoRetryPending: false,
            notice:
              '依赖安装会继续在后台进行；自动重检与任务提交已取消，完成后可手动重新检查。',
          }
        : {}),
    })
  },
  savePerformance: settings => {
    const normalized: PerformanceSettings = {
      ...settings,
      overrides: settings.experienceMode === 'guided' ? {} : { ...settings.overrides },
    }
    if (!api || get().backend.mode !== 'real') {
      set(state => ({
        performance: normalized,
        systemReport: state.systemReport
          ? { ...state.systemReport, performance: normalized }
          : state.systemReport,
        notice: '演示模式已更新性能偏好；没有写入本机配置文件。',
      }))
      return
    }
    set({ notice: '正在保存性能与资源配置…' })
    void api
      .savePerformance(serializePerformance(normalized))
      .then(async saved => {
        const system = await api!.system()
        const systemReport = mapSystemReport(system, saved)
        set({
          performance: systemReport.performance,
          systemReport,
          machine: mapMachine(system),
          notice: '性能配置已保存；算法计划已按当前机器重新计算。',
        })
      })
      .catch(error => set({ notice: errorMessage(error) }))
  },
  saveProvider: input => {
    if (!api || !registry || get().backend.mode !== 'real') {
      set({ notice: '只有已连接的真实本地后端会持久化 Provider。' })
      return
    }
    const id = input.id.trim().toLowerCase().replace(/[^a-z0-9._-]+/g, '-')
    if (!id || !input.name.trim()) {
      set({ notice: 'Provider ID 和显示名称不能为空。' })
      return
    }
    if (input.protocol !== 'local' && !input.baseUrl.trim()) {
      set({ notice: 'HTTP Provider 必须填写 Base URL。' })
      return
    }
    const existing = registry.providers[id]
    const next: ApiModelRegistry = {
      ...registry,
      schema_version: 2,
      providers: {
        ...registry.providers,
        [id]: {
          id,
          display_name: input.name.trim(),
          kind: input.kind,
          protocol: input.protocol,
          auth_scheme: input.authScheme,
          base_url:
            input.protocol === 'local'
              ? null
              : input.baseUrl.trim().replace(/\/+$/, ''),
          credential_ref: existing?.credential_ref ?? null,
          endpoint_style:
            input.protocol === 'openai_responses' ? 'responses' : 'chat_completions',
          protocol_options: { ...input.protocolOptions },
          request_timeout_seconds: input.timeoutSeconds,
          locality: input.locality,
          enabled: input.enabled,
        },
      },
    }
    void api
      .saveProviders(next)
      .then(async saved => {
        let persistedRegistry = saved
        const secretStates = Object.fromEntries(
          get().providers.map(provider => [
            provider.id,
            provider.credentialState === 'stored-locally' ? 'configured' : 'not_configured',
          ]),
        )
        if (input.authScheme !== 'none' && input.credential?.trim()) {
          await api!.saveProviderSecret(id, input.credential.trim())
          secretStates[id] = 'configured'
          persistedRegistry = await api!.providers()
        }
        registry = persistedRegistry
        const presentation = registryPresentation(
          persistedRegistry,
          get().configurationCatalog,
          secretStates,
        )
        set({
          ...presentation,
          selectedProviderId: id,
          discoveredModels: [],
          providerDiscoveryStatus: 'idle',
          notice: `${input.name.trim()} 已保存；能力声明仍需在添加模型时明确确认。`,
        })
      })
      .catch(error => set({ notice: errorMessage(error) }))
  },
  discoverProviderModels: providerId => {
    if (!api || get().backend.mode !== 'real') {
      set({ notice: '模型发现只会通过已连接的本机后端执行。' })
      return
    }
    set({
      discoveryProviderId: providerId,
      providerDiscoveryStatus: 'loading',
      discoveredModels: [],
      notice: '正在读取 Provider 的模型目录…',
    })
    void api
      .discoverProviderModels(providerId)
      .then(result =>
        set({
          discoveryProviderId: providerId,
          providerDiscoveryStatus: 'ready',
          discoveredModels: result.models.map(model => ({
            modelId: model.model_id,
            displayName: model.display_name,
            contextWindow: model.context_window ?? undefined,
          })),
          notice: `发现 ${result.models.length} 个模型；发现结果不包含能力声明。`,
        }),
      )
      .catch(error =>
        set({
          providerDiscoveryStatus: 'error',
          discoveredModels: [],
          notice: errorMessage(error),
        }),
      )
  },
  createModel: input => {
    if (!api || !registry || get().backend.mode !== 'real') {
      set({ notice: '只有已连接的真实本地后端会持久化模型。' })
      return
    }
    const modelId = input.modelId.trim()
    const displayName = input.displayName.trim() || modelId
    const id = `${input.providerId}-${modelId}`
      .toLowerCase()
      .replace(/[^a-z0-9._-]+/g, '-')
    if (!modelId || input.capabilities.length === 0) {
      set({ notice: '请填写模型 ID，并至少明确声明一项能力。' })
      return
    }
    if (!registry.providers[input.providerId]) {
      set({ notice: '请先保存 Provider，再添加模型。' })
      return
    }
    if (registry.models[id]) {
      set({ notice: `模型标识 ${id} 已存在。` })
      return
    }
    const next: ApiModelRegistry = {
      ...registry,
      schema_version: 2,
      models: {
        ...registry.models,
        [id]: {
          id,
          provider_id: input.providerId,
          model_id: modelId,
          display_name: displayName,
          capabilities: [...input.capabilities],
          locality: input.locality,
          context_window: input.contextWindow,
          settings: {},
          enabled: true,
        },
      },
    }
    void api
      .saveProviders(next)
      .then(saved => {
        registry = saved
        const secretStates = Object.fromEntries(
          get().providers.map(provider => [
            provider.id,
            provider.credentialState === 'stored-locally' ? 'configured' : 'not_configured',
          ]),
        )
        const presentation = registryPresentation(
          saved,
          get().configurationCatalog,
          secretStates,
        )
        set({
          ...presentation,
          selectedProviderId: input.providerId,
          notice: `${displayName} 已创建；只声明了你确认的 ${input.capabilities.length} 项能力。`,
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
      .then(async () => {
        registry = await api!.providers()
        set(state => ({
          providers: state.providers.map(provider =>
            provider.id === providerId
              ? { ...provider, credentialState: 'stored-locally' }
              : provider,
          ),
          notice: '密钥已写入 Windows 凭据库；界面不会读取或回显原值。',
        }))
      })
      .catch(error => set({ notice: errorMessage(error) }))
  },
  deleteProviderSecret: providerId => {
    if (!api || get().backend.mode !== 'real') {
      set({ notice: '演示模式没有可删除的密钥。' })
      return
    }
    void api
      .deleteProviderSecret(providerId)
      .then(async () => {
        registry = await api!.providers()
        set(state => ({
          providers: state.providers.map(provider =>
            provider.id === providerId ? { ...provider, credentialState: 'missing' } : provider,
          ),
          notice: 'Provider 密钥已从 Windows 凭据库删除。',
        }))
      })
      .catch(error => set({ notice: errorMessage(error) }))
  },
  bindRole: (roleId, modelId) => {
    const role = get().roles.find(item => item.id === roleId)
    if (!role) return
    if (!modelId) {
      if (get().backend.mode !== 'real' || !api || !registry) {
        set(state => ({
          roles: state.roles.map(item =>
            item.id === roleId ? { ...item, modelId: '' } : item,
          ),
          notice: `${role.label} 已取消绑定。`,
        }))
        return
      }
      const nextRoles = { ...registry.roles }
      delete nextRoles[roleId]
      void api
        .saveProviders({ ...registry, roles: nextRoles })
        .then(saved => {
          registry = saved
          set(state => ({
            roles: state.roles.map(item =>
              item.id === roleId ? { ...item, modelId: '' } : item,
            ),
            notice: `${role.label} 已取消绑定。`,
          }))
        })
        .catch(error => set({ notice: errorMessage(error) }))
      return
    }
    const model = get().models.find(item => item.id === modelId)
    const provider = model
      ? get().providers.find(item => item.id === model.providerId)
      : undefined
    if (!model) return
    const compatible = role.requiredCapabilities.every(capability =>
      model.capabilities.includes(capability),
    )
    if (!compatible || !model.enabled || !provider?.enabled) {
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

  downloadRunArtifact: (taskId, artifact) => {
    const task = get().tasks.find(item => item.id === taskId)
    if (!task?.realBackend) {
      set({ notice: '演示模式不会写入可下载的真实阶段产物。' })
      return
    }
    if (!api) {
      set({ notice: '本地后端当前未连接，暂时无法读取阶段产物。' })
      return
    }
    void api
      .artifactBlobUrl(taskId, artifact.relativePath)
      .then(url => {
        const anchor = document.createElement('a')
        anchor.href = url
        anchor.download =
          artifact.relativePath.split('/').at(-1) || `${artifact.kind}-artifact`
        anchor.click()
        window.setTimeout(() => URL.revokeObjectURL(url), 10_000)
        set({ notice: `已导出阶段产物 ${anchor.download}。` })
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
    stopRuntimeInventoryPolling()
    api = undefined
    registry = undefined
    demoOperationSequence = 0
    samplingOverrideSequence = 0
    sourceProbeSequence += 1
    invalidateProcessingEstimateContext()
    submissionSequence += 1
    sourceByRun.clear()
    submissionByRun.clear()
    for (const url of objectUrls.values()) URL.revokeObjectURL(url)
    objectUrls.clear()
    set(initialData(true))
  },
}))

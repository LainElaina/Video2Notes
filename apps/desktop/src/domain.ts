export type ViewId = 'create' | 'tasks' | 'reader' | 'models'
export type SourcePlatform = 'bilibili' | 'youtube' | 'x' | 'local'
export type ProcessingMode = 'fast' | 'balanced' | 'accurate'
export type SamplingMode = 'adaptive' | 'fixed_interval' | 'skip'
export type ReportPreset = 'concise' | 'detailed' | 'professional' | 'beginner' | 'executive'
export type TaskStatus = 'running' | 'paused' | 'cancelled' | 'completed' | 'failed'
export type StageStatus = 'pending' | 'running' | 'completed' | 'failed'
export type EvidenceKind = 'asr' | 'ocr' | 'visual' | 'chapter'
export type ProviderStatus = 'connected' | 'disconnected' | 'testing'
export type TelemetryValue = string | number | boolean | null

export type ModelCapability =
  | 'text'
  | 'vision'
  | 'structured_output'
  | 'long_context'
  | 'embeddings'
  | 'asr'
  | 'language_id'
  | 'segment_timestamps'
  | 'word_timestamps'
  | 'word_confidence'
  | 'ocr'
  | 'ocr_boxes'
  | 'ocr_confidence'
  | 'video_frame_metrics'

export type ProviderKind =
  | 'local'
  | 'openai'
  | 'anthropic'
  | 'google'
  | 'openai_compatible'
  | 'ollama'
  | 'custom'

export type ProviderProtocol =
  | 'local'
  | 'openai_responses'
  | 'openai_chat_completions'
  | 'openai_audio_transcriptions'
  | 'anthropic_messages'
  | 'gemini_generate_content'
  | 'gemini_interactions'
  | 'ollama_native_chat'
  | 'custom_http'

export type ProviderAuthScheme =
  | 'none'
  | 'bearer'
  | 'x_api_key'
  | 'x_goog_api_key'
  | 'custom_header'

export type ExperienceMode = 'guided' | 'professional'
export type ResourcePreference = 'responsive' | 'balanced' | 'throughput'

export interface SourceManifest {
  id: string
  platform: SourcePlatform
  title: string
  author: string
  durationSeconds: number
  quality: string
  codec: string
  audio: string
  subtitle: string
  authLabel: string
  sourceLabel: string
}

export interface TaskStage {
  id: string
  label: string
  detail: string
  status: StageStatus
  progress: number
  durationSeconds?: number
  artifactCount: number
  metrics: Record<string, TelemetryValue>
  warnings: string[]
  outputArtifacts: StageOutputArtifact[]
  metric?: string
}

export interface StageOutputArtifact {
  stage: string
  kind: string
  relativePath: string
  sha256: string
  sizeBytes: number
  mediaType?: string | null
  createdAt: string
}

export interface SupportingMaterialArtifact {
  kind: string
  relativePath: string
  sha256: string
  sizeBytes: number
  mediaType: string | null
  createdAt: string
}

export interface SupportingMaterial {
  id: string
  runId: string
  kind: 'text' | 'image'
  title: string
  originalName: string | null
  mediaType: string
  artifact: SupportingMaterialArtifact | null
  sha256: string | null
  sizeBytes: number
  textContent: string | null
  startUs: number | null
  endUs: number | null
  status: 'active' | 'deleted'
  createdAt: string
  deletedAt: string | null
  storage: 'run-artifact' | 'demo-memory'
}

export type ReworkOperationKind =
  | 'vision_rescan'
  | 'asr_retranscribe'
  | 'evidence_correct'

export interface ReworkOperation {
  id: string
  runId: string
  kind: ReworkOperationKind
  status: 'completed' | 'failed' | 'demo-preview'
  source: 'backend' | 'demo-memory'
  startSeconds: number
  endSeconds: number
  samplingMode?: 'adaptive' | 'fixed_interval'
  intervalSeconds?: number
  runOcr?: boolean
  languageHints: string[]
  evidenceId?: string
  newText?: string
  reason?: string
  createdAt: string
  finishedAt: string
  revisionId?: string
  artifactPaths: string[]
  replacedEvidenceIds: string[]
  newEvidenceIds: string[]
  visualStateCount: number
  errorType?: string
  detail?: string
}

export type ReportRevisionFormat = 'markdown' | 'html' | 'pdf'

export interface ReportRevision {
  id: string
  runId: string
  preset: ReportPreset
  createdAt: string
  formats: ReportRevisionFormat[]
  fallback: boolean
  warnings: string[]
  evidenceRevisionId: string
  materialIds: string[]
  artifactPaths: {
    document?: string
    markdown?: string
    html?: string
    pdf?: string
  }
  source: 'backend' | 'demo-memory'
}

export interface TelemetrySample {
  sequence: number
  runId: string
  state: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
  stage: string
  progress?: number
  message?: string
  metrics: Record<string, TelemetryValue>
  createdAt: string
}

export interface EvidenceItem {
  id: string
  kind: EvidenceKind
  startSeconds: number
  endSeconds: number
  label: string
  rawText: string
  confidence: number
  provider: string
}

export interface NoteClaim {
  id: string
  text: string
  timeSeconds: number
  evidenceIds: string[]
  emphasis?: boolean
}

export interface NoteSection {
  id: string
  title: string
  summary: string
  claims: NoteClaim[]
  startSeconds?: number
  endSeconds?: number
  screenshotAt?: number
  screenshotPath?: string
  screenshotUrl?: string
  screenshotCaption?: string
}

export interface NoteDocument {
  title: string
  overview: string
  sourceSummary: string
  sections: NoteSection[]
  generatedAt: string
}

export interface ProcessingTask {
  id: string
  source: SourceManifest
  mode: ProcessingMode
  status: TaskStatus
  progress: number
  etaSeconds: number
  createdAt: string
  stages: TaskStage[]
  telemetry: TelemetrySample[]
  runtimeWarnings: string[]
  materials: SupportingMaterial[]
  operations: ReworkOperation[]
  evidenceRevisionId?: string
  reportRevisions?: ReportRevision[]
  evidence: EvidenceItem[]
  note?: NoteDocument
  lastMessage?: string
  warnings?: string[]
  artifactPaths?: {
    markdown?: string
    html?: string
    pdf?: string
    noteDocument?: string
    media?: string
  }
  mediaUrl?: string
  realBackend?: boolean
}

export interface ModelDefinition {
  id: string
  providerId: string
  label: string
  modelId: string
  locality: 'local' | 'cloud'
  capabilities: ModelCapability[]
  contextWindow?: number
  enabled: boolean
}

export interface ProviderDefinition {
  id: string
  name: string
  kind: ProviderKind
  protocol: ProviderProtocol
  authScheme: ProviderAuthScheme
  endpoint: string
  locality: 'local' | 'cloud'
  enabled: boolean
  timeoutSeconds: number
  protocolOptions: Record<string, unknown>
  status: ProviderStatus
  credentialState: 'not-needed' | 'stored-locally' | 'missing'
  lastChecked?: string
}

export interface RoleBinding {
  id: string
  group: '语音' | '视觉' | '笔记'
  label: string
  description: string
  requiredCapabilities: ModelDefinition['capabilities']
  modelId: string
  fallbackModelId?: string
}

export interface ProtocolCatalogDefinition {
  protocol: ProviderProtocol
  displayName: string
  defaultAuthScheme: ProviderAuthScheme
  defaultBaseUrl?: string
  requestPath?: string
  discoveryPath?: string
  requestContentType: string
  structuredGenerationAdapter: boolean
  supportsJsonSchemaTransport: boolean
  supportsImageTransport: boolean
  supportsStreamingTransport: boolean
  streamTransport: string
}

export interface RoleCatalogDefinition {
  role: string
  requiredCapabilities: ModelCapability[]
}

export interface ConfigurationCatalogDefinition {
  protocols: ProtocolCatalogDefinition[]
  roles: RoleCatalogDefinition[]
  capabilities: ModelCapability[]
}

export interface DiscoveredModelDefinition {
  modelId: string
  displayName: string
  contextWindow?: number
}

export interface ResourceReserve {
  cpuReserveRatio: number
  memoryReserveRatio: number
  gpuReserveRatio: number
  vramReserveRatio: number
  diskReserveRatio: number
  cpuReserveCores: number
  memoryReserveBytes: number
  vramReserveBytes: number
  diskReserveBytes: number
  cpuSafetyFactor: number
  memorySafetyFactor: number
  gpuSafetyFactor: number
  vramSafetyFactor: number
  diskSafetyFactor: number
}

export interface PerformanceOverrides {
  decodeBackend?: 'software' | 'auto_hw'
  concurrentGpuStages?: number
  cpuWorkers?: number
  remoteModelConcurrency?: number
  visualDecodeThreads?: number
  maxFixedSamples?: number
  analysisWidth?: number
  cheapScanFps?: number
  expensiveScanFps?: number
  ocrModelClass?: 'mobile' | 'medium'
  ocrDevice?: 'auto' | 'cpu' | 'cuda'
  ocrBatchSize?: number
  ocrCpuThreads?: number
  asrModelClass?: string
  asrDevice?: 'auto' | 'cpu' | 'cuda'
  asrComputeType?: 'default' | 'int8' | 'int8_float16' | 'float16' | 'float32'
  asrBatchSize?: number
  asrCpuThreads?: number
  asrBeamSize?: number
  verificationPasses?: number
  screenshotBudgetPerSection?: number
  learnedSceneDetector?: boolean
}

export interface PerformanceSettings {
  schemaVersion: 1
  experienceMode: ExperienceMode
  preference: ResourcePreference
  reserve?: ResourceReserve
  overrides: PerformanceOverrides
}

export interface ResourceBudget {
  cpuAvailableEquivalent: number
  cpuBudgetEquivalent: number
  cpuWorkers: number
  memoryAvailableBytes?: number
  memoryBudgetBytes?: number
  diskAvailableBytes?: number
  diskBudgetBytes?: number
  gpuName?: string
  gpuComputeAvailableRatio?: number
  gpuComputeBudgetRatio?: number
  vramAvailableBytes?: number
  vramBudgetBytes?: number
  gpuStageSlots: number
  remoteModelConcurrency: number
}

export interface ResourceRecommendation {
  experienceMode: ExperienceMode
  preference: ResourcePreference
  reserve: ResourceReserve
  budget: ResourceBudget
  notes: string[]
}

export interface ExecutionPlan {
  hardwareTier: string
  qualityMode: ProcessingMode
  experienceMode: ExperienceMode
  resourcePreference: ResourcePreference
  decodeBackend: string
  concurrentGpuStages: number
  cpuWorkers: number
  remoteModelConcurrency: number
  visualDecodeThreads: number
  maxFixedSamples: number
  analysisWidth: number
  cheapScanFps: number
  expensiveScanFps: number
  ocrModelClass: string
  ocrDevice: string
  ocrBatchSize: number
  ocrCpuThreads: number
  asrModelClass: string
  asrDevice: string
  asrComputeType: string
  asrBatchSize: number
  asrCpuThreads: number
  asrBeamSize: number
  verificationPasses: number
  screenshotBudgetPerSection: number
  learnedSceneDetector: boolean
  notes: string[]
}

export interface PerformanceSystemReport {
  recommendedTier: string
  performance: PerformanceSettings
  recommendation: ResourceRecommendation
  plans: Partial<Record<ProcessingMode, ExecutionPlan>>
}

export type HardwareTier =
  | 'cpu_igpu'
  | 'gpu_8gb'
  | 'gpu_12gb'
  | 'gpu_24gb_plus'

export type ComponentKind = 'runtime' | 'tool' | 'local_model'
export type ComponentState = 'ready' | 'missing' | 'incomplete' | 'degraded'
export type ComponentActionKind = 'prepare' | 'resume' | 'repair_runtime'
export type ComponentPrepareStatus = 'prepared' | 'reused' | 'failed'

export interface ComponentRecommendation {
  hardwareTier: HardwareTier
  asrComponentId: string
  ocrComponentId: string
  asrDevice: string
  asrComputeType: string
  ocrDevice: string
  reason: string
}

export interface ComponentActionDefinition {
  id: string
  kind: ComponentActionKind
  componentId: string
  label: string
  automatic: boolean
}

export interface ComponentInventoryItemDefinition {
  id: string
  displayName: string
  kind: ComponentKind
  state: ComponentState
  ready: boolean
  degraded: boolean
  required: boolean
  version?: string
  path?: string
  detail?: string
  actions: ComponentActionDefinition[]
}

export interface ComponentInventoryDefinition {
  ready: boolean
  degraded: boolean
  capabilities: Record<string, boolean>
  items: ComponentInventoryItemDefinition[]
  actions: ComponentActionDefinition[]
}

export interface ComponentReportDefinition {
  hardwareTier: HardwareTier
  recommendation: ComponentRecommendation
  inventory: ComponentInventoryDefinition
}

export interface ComponentPrepareResultDefinition {
  componentId: string
  status: ComponentPrepareStatus
  path?: string
  resumed: boolean
  detail?: string
}

export interface ComponentPreparationDefinition {
  hardwareTier: HardwareTier
  results: ComponentPrepareResultDefinition[]
  activated: boolean
  report: ComponentReportDefinition
}

export interface MachineProfile {
  cpu: string
  gpu: string
  memory: string
  backend: 'ready' | 'starting' | 'offline'
}

export interface BrowserProfile {
  browser: 'chrome' | 'edge' | 'firefox'
  profileId: string
  displayName: string
  path: string
  isDefault: boolean
}

export interface BackendProfile {
  mode: 'connecting' | 'real' | 'demo' | 'offline'
  version?: string
  detail: string
  dataRoot?: string
}

export interface SamplingOverrideDraft {
  id: string
  startSeconds: number
  endSeconds: number
  mode: SamplingMode
  intervalSeconds: number
}

export interface DraftState {
  input: string
  mode: ProcessingMode
  status: 'idle' | 'probing' | 'ready' | 'submitting' | 'error'
  error?: string
  manifest?: SourceManifest
  sourceKind: 'url' | 'local'
  authKind: 'none' | 'browser_profile' | 'cookie_file'
  browser: 'chrome' | 'edge' | 'firefox'
  profile: string
  cookieFile: string
  languageHints: string
  includeScreenshots: boolean
  generatePdf: boolean
  samplingMode: SamplingMode
  samplingIntervalSeconds: number
  samplingOverrides: SamplingOverrideDraft[]
  reportPreset: ReportPreset
}

export const formatTime = (seconds: number): string => {
  const safeSeconds = Math.max(0, Math.floor(seconds))
  const hours = Math.floor(safeSeconds / 3600)
  const minutes = Math.floor((safeSeconds % 3600) / 60)
  const rest = safeSeconds % 60
  return hours > 0
    ? `${hours}:${minutes.toString().padStart(2, '0')}:${rest.toString().padStart(2, '0')}`
    : `${minutes.toString().padStart(2, '0')}:${rest.toString().padStart(2, '0')}`
}

export const platformLabel: Record<SourcePlatform, string> = {
  bilibili: 'Bilibili',
  youtube: 'YouTube',
  x: 'X',
  local: '本地文件',
}

export const statusLabel: Record<TaskStatus, string> = {
  running: '处理中',
  paused: '已暂停',
  cancelled: '已取消',
  completed: '已完成',
  failed: '失败',
}

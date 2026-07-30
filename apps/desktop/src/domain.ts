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
  capabilities: Array<'text' | 'vision' | 'asr' | 'ocr' | 'json' | 'timestamps'>
}

export interface ProviderDefinition {
  id: string
  name: string
  kind: 'local' | 'openai-compatible' | 'ollama'
  endpoint: string
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

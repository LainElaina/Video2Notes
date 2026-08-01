import { convertFileSrc, invoke } from '@tauri-apps/api/core'

export type BackendMode = 'connecting' | 'real' | 'demo' | 'offline'

export interface BackendConnection {
  baseUrl: string
  token: string
  backendStatus?: string
  dataRoot?: string
}

export interface ApiSourceInput {
  kind: 'local' | 'url'
  value: string
}

export interface ApiAuthSpec {
  kind: 'none' | 'browser_profile' | 'cookie_file'
  browser?: 'chrome' | 'edge' | 'firefox'
  profile?: string
  keyring?: string
  container?: string
  cookie_file?: string
}

export interface ApiAcquisitionPolicy {
  mode: 'fast' | 'accurate'
  max_height?: number
  include_subtitles: boolean
  include_automatic_captions: boolean
  allow_quality_change: boolean
  prefer_hardlink: boolean
}

export interface ApiSamplingSpec {
  mode: 'adaptive' | 'fixed_interval' | 'skip'
  interval_us?: number
}

export interface ApiSamplingOverride {
  range: {
    start_us: number
    end_us: number
  }
  sampling: ApiSamplingSpec
}

export interface ApiSamplingPlan {
  default: ApiSamplingSpec
  overrides: ApiSamplingOverride[]
}

export interface ApiReportSpec {
  preset: 'concise' | 'detailed' | 'professional' | 'beginner' | 'executive'
  language: string
  include_screenshots: boolean
  output_formats: Array<'markdown' | 'html' | 'pdf'>
}

export interface ApiResolvedReportSpec extends ApiReportSpec {
  audience: string
  editorial_goal: string
  max_sections: number
  max_facts_per_section: number
  max_takeaways: number
  include_glossary: boolean
  include_evidence_index: boolean
  template_version: string
  max_output_tokens: number
}

export interface ApiReportRevision {
  id: string
  run_id: string
  created_at: string
  report_spec: ApiResolvedReportSpec
  evidence_revision_id: string | null
  material_ids: string[]
  document: ApiArtifactRef
  markdown: ApiArtifactRef
  html: ApiArtifactRef
  pdf: ApiArtifactRef | null
  invocations: unknown[]
  warnings: string[]
  used_deterministic_fallback: boolean
}

export interface ApiReportRevisionIndex {
  schema_version: number
  run_id: string
  latest_revision_id: string | null
  revisions: ApiReportRevision[]
}

export interface ApiFormatInfo {
  format_id: string
  width?: number
  height?: number
  fps?: number
  vcodec?: string
  acodec?: string
  audio_sample_rate?: number
  language?: string
}

export interface ApiSourceManifest {
  platform: 'local' | 'bilibili' | 'youtube' | 'x'
  source: ApiSourceInput
  source_id: string
  canonical_url?: string
  title?: string
  author?: string
  duration_seconds?: number
  formats: ApiFormatInfo[]
  subtitles: Record<string, string[]>
  automatic_captions: Record<string, string[]>
  selected_format_ids: string[]
  auth_kind: ApiAuthSpec['kind']
  quality_warning?: string
}

export interface ApiProcessingEstimateRequest {
  duration_seconds: number
  quality_mode: 'fast' | 'balanced' | 'accurate'
  source_height?: number
  source_fps?: number
}

export interface ApiProcessingEstimate {
  hardware_tier: string
  quality_mode: 'fast' | 'balanced' | 'accurate'
  media_duration_seconds: number
  lower_seconds: number
  upper_seconds: number
  lower_realtime_factor: number
  upper_realtime_factor: number
  basis: string
  precision_intent: string
  notes: string[]
}

export interface ApiArtifactRef {
  kind: string
  relative_path: string
  sha256: string
  size_bytes: number
  media_type?: string | null
  created_at: string
}

export interface ApiRunMaterialArtifact {
  kind: string
  relative_path: string
  sha256: string
  size_bytes: number
  media_type: string | null
  created_at: string
}

export interface ApiRunMaterial {
  id: string
  run_id: string
  kind: 'text' | 'image'
  title: string
  original_name: string | null
  media_type: string
  artifact: ApiRunMaterialArtifact
  sha256: string
  size_bytes: number
  text_content: string | null
  start_us: number | null
  end_us: number | null
  status: 'active' | 'deleted'
  created_at: string
  deleted_at: string | null
}

export interface ApiTextMaterialRequest {
  title: string
  content: string
  start_us?: number
  end_us?: number
}

export interface ApiFileMaterialOptions {
  title?: string
  start_us?: number
  end_us?: number
}

export interface ApiStageRecord {
  stage_name: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
  outputs: ApiArtifactRef[]
  wall_time_seconds?: number
  warnings: string[]
  metrics: Record<string, string | number | boolean | null>
  error?: string
}

export interface ApiRunManifest {
  run_id: string
  source: {
    kind: string
    locator: string
    platform?: string
    canonical_url?: string
    source_id?: string
    title?: string
    author?: string
  }
  profile: 'fast' | 'balanced' | 'accurate'
  status: 'created' | 'running' | 'completed' | 'failed' | 'cancelled'
  created_at: string
  updated_at: string
  stages: Record<string, ApiStageRecord>
  warnings: string[]
}

export interface ApiJobEvent {
  sequence: number
  run_id: string
  state: ApiJobSnapshot['state']
  stage: string
  progress?: number
  message?: string
  metrics: Record<string, string | number | boolean | null>
  created_at: string
}

export interface ApiJobSnapshot {
  run_id: string
  state: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
  stage: string
  progress?: number
  message?: string
  error_type?: string
  queued_at: string
  started_at?: string
  finished_at?: string
  events: ApiJobEvent[]
}

export interface ApiPipelineOutcome {
  run_id: string
  markdown: ApiArtifactRef
  html: ApiArtifactRef
  pdf?: ApiArtifactRef
  note_document: ApiArtifactRef
  evidence_count: number
  visual_state_count: number
  used_deterministic_note_fallback: boolean
}

export interface ApiProcessingRun {
  run: ApiRunManifest
  job: ApiJobSnapshot
  result?: ApiPipelineOutcome
  runtime_warnings: string[]
}

export interface ApiEvidenceSpan {
  id: string
  modality: 'metadata' | 'platform_caption' | 'asr' | 'ocr' | 'visual'
  start_us: number
  end_us: number
  raw_text?: string
  normalized_text?: string
  confidence?: number
  provider?: string
  model?: string
}

export interface ApiOperationTimeRange {
  start_us: number
  end_us: number
}

export type ApiOperationSampling =
  | {
      mode: 'adaptive'
      interval_us?: null
    }
  | {
      mode: 'fixed_interval'
      interval_us: number
    }

export interface ApiVisionRescanRequest {
  kind: 'vision_rescan'
  range: ApiOperationTimeRange
  sampling: ApiOperationSampling
  run_ocr: boolean
  language_hints?: string[]
  evidence_id?: null
  new_text?: null
  reason?: string | null
}

export interface ApiAsrRetranscribeRequest {
  kind: 'asr_retranscribe'
  range: ApiOperationTimeRange
  sampling?: null
  run_ocr?: true
  language_hints: string[]
  evidence_id?: null
  new_text?: null
  reason?: string | null
}

export interface ApiEvidenceCorrectRequest {
  kind: 'evidence_correct'
  range: ApiOperationTimeRange
  sampling?: null
  run_ocr?: true
  language_hints?: []
  evidence_id: string
  new_text: string
  reason?: string | null
}

export type ApiRunOperationRequest =
  | ApiVisionRescanRequest
  | ApiAsrRetranscribeRequest
  | ApiEvidenceCorrectRequest

export interface ApiRunOperation {
  schema_version: number
  operation_id: string
  run_id: string
  request: ApiRunOperationRequest
  status: 'completed' | 'failed'
  created_at: string
  finished_at: string
  revision_id: string | null
  artifact_paths: string[]
  replaced_evidence_ids: string[]
  new_evidence_ids: string[]
  visual_state_count: number
  error_type: string | null
  detail: string | null
}

export interface ApiEvidenceRevisionResponse {
  schema_version: number
  run_id: string
  revision_id: string | null
  operation_id: string | null
  evidence: ApiEvidenceSpan[]
  superseded_evidence_ids: string[]
}

export interface ApiNoteDocument {
  metadata: {
    title: string
    run_id: string
    source_kind: string
    source_locator: string
    source_url?: string
    author?: string
    duration_us: number
    languages: string[]
    quality_mode: string
    source_resolution?: string
    quality_warnings: string[]
    created_at: string
  }
  abstract: string
  key_takeaways: string[]
  sections: Array<{
    id: string
    title: string
    start_us: number
    end_us: number
    summary: string
    body_markdown: string
    evidence_ids: string[]
    fact_ids: string[]
    screenshots: Array<{
      relative_path: string
      timestamp_us: number
      caption: string
      alt_text: string
      evidence_ids: string[]
    }>
  }>
  facts: Array<{
    id: string
    claim: string
    evidence_ids: string[]
    confidence?: number
    kind: string
    needs_review: boolean
  }>
  evidence: ApiEvidenceSpan[]
  glossary: Record<string, string>
}

export interface ApiSystemReport {
  hardware: {
    os_name?: string
    os_version?: string
    architecture?: string
    cpu_name: string
    logical_cores: number
    cpu_load_percent?: number | null
    memory_total_bytes?: number | null
    memory_available_bytes?: number | null
    memory_load_percent?: number | null
    disk_total_bytes?: number | null
    disk_available_bytes?: number | null
    gpus: Array<{
      name: string
      vendor?: string
      memory_total_bytes?: number | null
      memory_free_bytes?: number | null
      memory_used_bytes?: number | null
      utilization_percent?: number | null
      driver_version?: string | null
    }>
    ffmpeg_hwaccels?: string[]
  }
  recommended_tier: string
  performance: ApiPerformanceSettings
  recommendation: ApiResourceRecommendation
  plans: Record<string, ApiExecutionPlan>
}

export type ApiHardwareTier =
  | 'cpu_igpu'
  | 'gpu_8gb'
  | 'gpu_12gb'
  | 'gpu_24gb_plus'

export type ApiComponentKind = 'runtime' | 'tool' | 'local_model'
export type ApiComponentState = 'ready' | 'missing' | 'incomplete' | 'degraded'
export type ApiComponentActionKind = 'prepare' | 'resume' | 'repair_runtime'
export type ApiComponentPrepareStatus = 'prepared' | 'reused' | 'failed'

export interface ApiTierRecommendation {
  hardware_tier: ApiHardwareTier
  asr_component_id: string
  ocr_component_id: string
  asr_device: string
  asr_compute_type: string
  ocr_device: string
  reason: string
}

export interface ApiComponentAction {
  id: string
  kind: ApiComponentActionKind
  component_id: string
  label: string
  automatic: boolean
}

export interface ApiComponentInventoryItem {
  id: string
  display_name: string
  kind: ApiComponentKind
  state: ApiComponentState
  ready: boolean
  degraded: boolean
  required: boolean
  version?: string | null
  path?: string | null
  detail?: string | null
  actions: ApiComponentAction[]
}

export interface ApiComponentInventory {
  ready: boolean
  degraded: boolean
  capabilities: Record<string, boolean>
  items: ApiComponentInventoryItem[]
  actions: ApiComponentAction[]
}

export interface ApiComponentReport {
  hardware_tier: ApiHardwareTier
  recommendation: ApiTierRecommendation
  inventory: ApiComponentInventory
}

export interface ApiPrepareComponentsRequest {
  component_ids?: string[]
  hardware_tier?: ApiHardwareTier | null
  activate?: boolean
}

export interface ApiComponentPrepareResult {
  component_id: string
  status: ApiComponentPrepareStatus
  path?: string | null
  resumed: boolean
  detail?: string | null
}

export interface ApiComponentPreparationResponse {
  hardware_tier: ApiHardwareTier
  results: ApiComponentPrepareResult[]
  activated: boolean
  report: ApiComponentReport
}

export type ApiExperienceMode = 'guided' | 'professional'
export type ApiResourcePreference = 'responsive' | 'balanced' | 'throughput'

export interface ApiResourceReserve {
  cpu_reserve_ratio: number
  memory_reserve_ratio: number
  gpu_reserve_ratio: number
  vram_reserve_ratio: number
  disk_reserve_ratio: number
  cpu_reserve_cores: number
  memory_reserve_bytes: number
  vram_reserve_bytes: number
  disk_reserve_bytes: number
  cpu_safety_factor: number
  memory_safety_factor: number
  gpu_safety_factor: number
  vram_safety_factor: number
  disk_safety_factor: number
}

export interface ApiPerformanceOverrides {
  decode_backend?: 'software' | 'auto_hw' | null
  concurrent_gpu_stages?: number | null
  cpu_workers?: number | null
  remote_model_concurrency?: number | null
  visual_decode_threads?: number | null
  max_fixed_samples?: number | null
  analysis_width?: number | null
  cheap_scan_fps?: number | null
  expensive_scan_fps?: number | null
  ocr_model_class?: 'mobile' | 'medium' | null
  ocr_device?: 'auto' | 'cpu' | 'cuda' | null
  ocr_batch_size?: number | null
  ocr_cpu_threads?: number | null
  asr_model_class?: string | null
  asr_device?: 'auto' | 'cpu' | 'cuda' | null
  asr_compute_type?: 'default' | 'int8' | 'int8_float16' | 'float16' | 'float32' | null
  asr_batch_size?: number | null
  asr_cpu_threads?: number | null
  asr_beam_size?: number | null
  verification_passes?: number | null
  screenshot_budget_per_section?: number | null
  learned_scene_detector?: boolean | null
}

export interface ApiPerformanceSettings {
  schema_version: 1
  experience_mode: ApiExperienceMode
  preference: ApiResourcePreference
  reserve?: ApiResourceReserve | null
  overrides: ApiPerformanceOverrides
}

export interface ApiResourceBudget {
  cpu_available_equivalent: number
  cpu_budget_equivalent: number
  cpu_workers: number
  memory_available_bytes?: number | null
  memory_budget_bytes?: number | null
  disk_available_bytes?: number | null
  disk_budget_bytes?: number | null
  gpu_name?: string | null
  gpu_compute_available_ratio?: number | null
  gpu_compute_budget_ratio?: number | null
  vram_available_bytes?: number | null
  vram_budget_bytes?: number | null
  gpu_stage_slots: number
  remote_model_concurrency: number
}

export interface ApiResourceRecommendation {
  experience_mode: ApiExperienceMode
  preference: ApiResourcePreference
  reserve: ApiResourceReserve
  budget: ApiResourceBudget
  notes: string[]
}

export interface ApiExecutionPlan {
  hardware_tier: string
  quality_mode: 'fast' | 'balanced' | 'accurate'
  experience_mode: ApiExperienceMode
  resource_preference: ApiResourcePreference
  resource_budget?: ApiResourceBudget
  decode_backend: string
  concurrent_gpu_stages: number
  cpu_workers: number
  remote_model_concurrency: number
  visual_decode_threads: number
  max_fixed_samples: number
  analysis_width: number
  cheap_scan_fps: number
  expensive_scan_fps: number
  ocr_model_class: string
  ocr_device: string
  ocr_batch_size: number
  ocr_cpu_threads: number
  asr_model_class: string
  asr_device: string
  asr_compute_type: string
  asr_batch_size: number
  asr_cpu_threads: number
  asr_beam_size: number
  secondary_asr?: string
  verification_passes: number
  screenshot_budget_per_section: number
  learned_scene_detector: boolean
  notes: string[]
}

export interface ApiBrowserProfile {
  browser: 'chrome' | 'edge' | 'firefox'
  profile_id: string
  display_name: string
  path: string
  is_default: boolean
}

export interface ApiProviderSpec {
  id: string
  display_name: string
  kind: ApiProviderKind
  protocol: ApiProviderProtocol
  auth_scheme: ApiAuthScheme
  base_url?: string | null
  credential_ref?: string | null
  endpoint_style: 'responses' | 'chat_completions'
  protocol_options: Record<string, unknown>
  request_timeout_seconds: number
  locality: 'local' | 'cloud'
  enabled: boolean
}

export type ApiProviderKind =
  | 'local'
  | 'openai'
  | 'anthropic'
  | 'google'
  | 'openai_compatible'
  | 'ollama'
  | 'custom'

export type ApiProviderProtocol =
  | 'local'
  | 'openai_responses'
  | 'openai_chat_completions'
  | 'openai_audio_transcriptions'
  | 'anthropic_messages'
  | 'gemini_generate_content'
  | 'gemini_interactions'
  | 'ollama_native_chat'
  | 'custom_http'

export type ApiAuthScheme =
  | 'none'
  | 'bearer'
  | 'x_api_key'
  | 'x_goog_api_key'
  | 'custom_header'

export type ApiModelCapability =
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

export interface ApiProtocolCatalogEntry {
  protocol: ApiProviderProtocol
  display_name: string
  default_auth_scheme: ApiAuthScheme
  default_base_url?: string | null
  request_path?: string | null
  discovery_path?: string | null
  request_content_type: string
  structured_generation_adapter: boolean
  supports_json_schema_transport: boolean
  supports_image_transport: boolean
  supports_streaming_transport: boolean
  stream_transport: string
}

export interface ApiRoleCatalogEntry {
  role: string
  required_capabilities: ApiModelCapability[]
}

export interface ApiConfigurationCatalog {
  protocols: ApiProtocolCatalogEntry[]
  roles: ApiRoleCatalogEntry[]
  capabilities: ApiModelCapability[]
}

export interface ApiDiscoveredModel {
  model_id: string
  display_name: string
  context_window?: number | null
}

export interface ApiProviderDiscoveryResult {
  provider_id: string
  protocol: ApiProviderProtocol
  models: ApiDiscoveredModel[]
}

export interface ApiModelSpec {
  id: string
  provider_id: string
  model_id: string
  display_name: string
  capabilities: ApiModelCapability[]
  locality: 'local' | 'cloud'
  context_window?: number | null
  settings: Record<string, unknown>
  enabled: boolean
}

export interface ApiRoleBinding {
  role: string
  primary_model_id: string
  fallback_model_ids: string[]
  escalation_rule?: string
}

export interface ApiModelRegistry {
  schema_version: number
  providers: Record<string, ApiProviderSpec>
  models: Record<string, ApiModelSpec>
  roles: Record<string, ApiRoleBinding>
}

export interface PipelineSubmission {
  source: ApiSourceInput
  auth: ApiAuthSpec
  acquisition: ApiAcquisitionPolicy
  quality_mode: 'fast' | 'balanced' | 'accurate'
  title_override?: string
  language_hints: string[]
  sampling_plan: ApiSamplingPlan
  include_screenshots: boolean
  generate_pdf: boolean
  report_spec: ApiReportSpec
}

export class Video2NotesApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'Video2NotesApiError'
    this.status = status
  }
}

const normalizeBaseUrl = (value: string): string => value.trim().replace(/\/+$/, '')

const queryRequestsDemo = (): boolean => {
  if (typeof window === 'undefined') return false
  return new URLSearchParams(window.location.search).get('demo') === '1'
}

export const resolveBackendConnection = async (): Promise<
  { mode: 'real'; connection: BackendConnection } | { mode: 'demo' }
> => {
  if (import.meta.env.VITE_VIDEO2NOTES_DEMO === '1' || queryRequestsDemo()) {
    return { mode: 'demo' }
  }

  const configuredUrl = String(import.meta.env.VITE_VIDEO2NOTES_API_URL ?? '').trim()
  const configuredToken = String(import.meta.env.VITE_VIDEO2NOTES_API_TOKEN ?? '').trim()
  if (configuredUrl && configuredToken) {
    return {
      mode: 'real',
      connection: {
        baseUrl: normalizeBaseUrl(configuredUrl),
        token: configuredToken,
        backendStatus: 'configured',
      },
    }
  }

  if (typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window) {
    const response = await invoke<{
      base_url?: string
      baseUrl?: string
      token: string
      backend_status?: string
      backendStatus?: string
      data_root?: string
      dataRoot?: string
    }>('backend_connection')
    const baseUrl = response.baseUrl ?? response.base_url
    if (!baseUrl || !response.token) {
      throw new Error('Tauri 后端没有返回可用的本地 API 会话。')
    }
    return {
      mode: 'real',
      connection: {
        baseUrl: normalizeBaseUrl(baseUrl),
        token: response.token,
        backendStatus: response.backendStatus ?? response.backend_status,
        dataRoot: response.dataRoot ?? response.data_root,
      },
    }
  }

  throw new Error(
    '未发现本地后端。请通过 Tauri 启动，或设置 VITE_VIDEO2NOTES_API_URL 与 VITE_VIDEO2NOTES_API_TOKEN；视觉演示请显式添加 ?demo=1。',
  )
}

export const pickVideoWithNativeDialog = async (): Promise<string | null> => {
  if (typeof window === 'undefined' || !('__TAURI_INTERNALS__' in window)) return null
  return invoke<string | null>('pick_video')
}

export const bundledDemoVideoPath = async (): Promise<string | null> => {
  if (typeof window === 'undefined' || !('__TAURI_INTERNALS__' in window)) return null
  return invoke<string>('demo_video_path')
}

export const localArtifactUrl = async (
  runId: string,
  relativePath: string,
): Promise<string | null> => {
  if (typeof window === 'undefined' || !('__TAURI_INTERNALS__' in window)) return null
  const path = await invoke<string>('artifact_file_path', {
    runId,
    relativePath,
  })
  return convertFileSrc(path)
}

export class Video2NotesApi {
  readonly baseUrl: string
  readonly token: string

  constructor(connection: BackendConnection) {
    this.baseUrl = normalizeBaseUrl(connection.baseUrl)
    this.token = connection.token
  }

  private async request<T>(
    path: string,
    init: RequestInit = {},
    allowUnauthenticated = false,
  ): Promise<T> {
    const headers = new Headers(init.headers)
    if (!allowUnauthenticated) headers.set('X-Video2Notes-Token', this.token)
    const isFormData =
      typeof FormData !== 'undefined' && init.body instanceof FormData
    if (init.body !== undefined && !isFormData && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json')
    }
    let response: Response
    try {
      response = await fetch(`${this.baseUrl}${path}`, { ...init, headers })
    } catch {
      throw new Video2NotesApiError(0, '无法连接本机 Video2Notes 后端。')
    }
    if (!response.ok) {
      let detail = `本地后端返回 HTTP ${response.status}`
      try {
        const payload = (await response.json()) as { detail?: string }
        if (payload.detail) detail = payload.detail
      } catch {
        // Keep the status-only fallback; never echo an arbitrary HTML error page.
      }
      throw new Video2NotesApiError(response.status, detail)
    }
    return (await response.json()) as T
  }

  health(): Promise<{ status: string; version: string; scope: string }> {
    return this.request('/api/health', {}, true)
  }

  async waitForHealth(
    timeoutMs = 15_000,
  ): Promise<{ status: string; version: string; scope: string }> {
    const deadline = Date.now() + timeoutMs
    let lastError: unknown
    while (Date.now() < deadline) {
      try {
        return await this.health()
      } catch (error) {
        lastError = error
        await new Promise(resolve => window.setTimeout(resolve, 200))
      }
    }
    throw lastError instanceof Error
      ? lastError
      : new Video2NotesApiError(0, '本地后端启动超时。')
  }

  system(): Promise<ApiSystemReport> {
    return this.request('/api/system')
  }

  performance(): Promise<ApiPerformanceSettings> {
    return this.request('/api/performance')
  }

  components(): Promise<ApiComponentReport> {
    return this.request('/api/components')
  }

  prepareComponents(
    request: ApiPrepareComponentsRequest = {},
  ): Promise<ApiComponentPreparationResponse> {
    return this.request('/api/components/prepare', {
      method: 'POST',
      body: JSON.stringify(request),
    })
  }

  savePerformance(settings: ApiPerformanceSettings): Promise<ApiPerformanceSettings> {
    return this.request('/api/performance', {
      method: 'PUT',
      body: JSON.stringify(settings),
    })
  }

  configurationCatalog(): Promise<ApiConfigurationCatalog> {
    return this.request('/api/configuration-catalog')
  }

  runtime(): Promise<{ injected: boolean; warnings: string[] }> {
    return this.request('/api/runtime')
  }

  browserProfiles(): Promise<ApiBrowserProfile[]> {
    return this.request('/api/browser-profiles')
  }

  probeSource(
    source: ApiSourceInput,
    auth: ApiAuthSpec,
    policy: ApiAcquisitionPolicy,
  ): Promise<ApiSourceManifest> {
    return this.request('/api/sources/probe', {
      method: 'POST',
      body: JSON.stringify({ source, auth, policy }),
    })
  }

  estimateProcessing(request: ApiProcessingEstimateRequest): Promise<ApiProcessingEstimate> {
    return this.request('/api/estimate', {
      method: 'POST',
      body: JSON.stringify(request),
    })
  }

  listRuns(): Promise<ApiRunManifest[]> {
    return this.request('/api/runs')
  }

  listJobs(): Promise<ApiJobSnapshot[]> {
    return this.request('/api/jobs')
  }

  submitJob(request: PipelineSubmission): Promise<ApiProcessingRun> {
    return this.request('/api/jobs', {
      method: 'POST',
      body: JSON.stringify(request),
    })
  }

  jobResult(runId: string): Promise<ApiProcessingRun> {
    return this.request(`/api/jobs/${encodeURIComponent(runId)}/result`)
  }

  listOperations(runId: string): Promise<ApiRunOperation[]> {
    return this.request(`/api/runs/${encodeURIComponent(runId)}/operations`)
  }

  createOperation(
    runId: string,
    request: ApiRunOperationRequest,
  ): Promise<ApiRunOperation> {
    return this.request(`/api/runs/${encodeURIComponent(runId)}/operations`, {
      method: 'POST',
      body: JSON.stringify(request),
    })
  }

  getEvidence(
    runId: string,
    revision?: string,
  ): Promise<ApiEvidenceRevisionResponse> {
    const query = new URLSearchParams()
    if (revision !== undefined) query.set('revision', revision)
    const suffix = query.size > 0 ? `?${query.toString()}` : ''
    return this.request(`/api/runs/${encodeURIComponent(runId)}/evidence${suffix}`)
  }

  listReportRevisions(runId: string): Promise<ApiReportRevisionIndex> {
    return this.request(
      `/api/runs/${encodeURIComponent(runId)}/report-revisions`,
    )
  }

  createReportRevision(
    runId: string,
    request: ApiReportSpec,
  ): Promise<ApiReportRevision> {
    return this.request(
      `/api/runs/${encodeURIComponent(runId)}/report-revisions`,
      {
        method: 'POST',
        body: JSON.stringify(request),
      },
    )
  }

  listMaterials(runId: string): Promise<ApiRunMaterial[]> {
    return this.request(`/api/runs/${encodeURIComponent(runId)}/materials`)
  }

  addTextMaterial(
    runId: string,
    request: ApiTextMaterialRequest,
  ): Promise<ApiRunMaterial> {
    return this.request(`/api/runs/${encodeURIComponent(runId)}/materials/text`, {
      method: 'POST',
      body: JSON.stringify(request),
    })
  }

  addFileMaterial(
    runId: string,
    file: File,
    options: ApiFileMaterialOptions = {},
  ): Promise<ApiRunMaterial> {
    const query = new URLSearchParams()
    if (options.title !== undefined) query.set('title', options.title)
    if (options.start_us !== undefined) query.set('start_us', String(options.start_us))
    if (options.end_us !== undefined) query.set('end_us', String(options.end_us))
    const suffix = query.size > 0 ? `?${query.toString()}` : ''
    const body = new FormData()
    body.append('file', file, file.name)
    return this.request(
      `/api/runs/${encodeURIComponent(runId)}/materials/files${suffix}`,
      {
        method: 'POST',
        body,
      },
    )
  }

  deleteMaterial(runId: string, materialId: string): Promise<ApiRunMaterial> {
    return this.request(
      `/api/runs/${encodeURIComponent(runId)}/materials/${encodeURIComponent(materialId)}`,
      { method: 'DELETE' },
    )
  }

  cancelJob(runId: string): Promise<ApiJobSnapshot> {
    return this.request(`/api/jobs/${encodeURIComponent(runId)}/cancel`, {
      method: 'POST',
    })
  }

  providers(): Promise<ApiModelRegistry> {
    return this.request('/api/providers')
  }

  saveProviders(registry: ApiModelRegistry): Promise<ApiModelRegistry> {
    return this.request('/api/providers', {
      method: 'PUT',
      body: JSON.stringify(registry),
    })
  }

  providerSecretStatus(
    providerId: string,
  ): Promise<{ provider_id: string; status: string }> {
    return this.request(`/api/providers/${encodeURIComponent(providerId)}/secret`)
  }

  saveProviderSecret(
    providerId: string,
    secret: string,
  ): Promise<{ provider_id: string; status: string }> {
    return this.request(`/api/providers/${encodeURIComponent(providerId)}/secret`, {
      method: 'PUT',
      body: JSON.stringify({ secret }),
    })
  }

  deleteProviderSecret(
    providerId: string,
  ): Promise<{ provider_id: string; status: string }> {
    return this.request(`/api/providers/${encodeURIComponent(providerId)}/secret`, {
      method: 'DELETE',
    })
  }

  testProvider(
    providerId: string,
  ): Promise<{ provider_id: string; status: string; detail: string }> {
    return this.request(`/api/providers/${encodeURIComponent(providerId)}/test`, {
      method: 'POST',
    })
  }

  discoverProviderModels(providerId: string): Promise<ApiProviderDiscoveryResult> {
    return this.request(`/api/providers/${encodeURIComponent(providerId)}/discover`)
  }

  async artifactJson<T>(runId: string, relativePath: string): Promise<T> {
    const query = new URLSearchParams({ path: relativePath })
    return this.request(`/api/runs/${encodeURIComponent(runId)}/artifact?${query}`)
  }

  async artifactBlobUrl(runId: string, relativePath: string): Promise<string> {
    const query = new URLSearchParams({ path: relativePath })
    const response = await fetch(
      `${this.baseUrl}/api/runs/${encodeURIComponent(runId)}/artifact?${query}`,
      { headers: { 'X-Video2Notes-Token': this.token } },
    )
    if (!response.ok) {
      throw new Video2NotesApiError(response.status, '无法读取本地任务产物。')
    }
    return URL.createObjectURL(await response.blob())
  }
}

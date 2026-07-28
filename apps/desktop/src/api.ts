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

export interface ApiArtifactRef {
  kind: string
  relative_path: string
  sha256: string
  size_bytes: number
  media_type?: string
  created_at: string
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
    cpu_name: string
    logical_cores: number
    memory_total_bytes?: number
    gpus: Array<{
      name: string
      memory_total_bytes?: number
    }>
  }
  recommended_tier: string
  plans: Record<string, Record<string, unknown>>
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
  kind: 'local' | 'openai_compatible' | 'ollama'
  base_url?: string
  credential_ref?: string
  endpoint_style: 'responses' | 'chat_completions'
  request_timeout_seconds: number
  locality: 'local' | 'cloud'
  enabled: boolean
}

export interface ApiModelSpec {
  id: string
  provider_id: string
  model_id: string
  display_name: string
  capabilities: string[]
  locality: 'local' | 'cloud'
  context_window?: number
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
  include_screenshots: boolean
  generate_pdf: boolean
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
    if (init.body !== undefined && !headers.has('Content-Type')) {
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

import { beforeEach, describe, expect, it, vi } from 'vitest'
import type {
  ApiEvidenceSpan,
  ApiJobEvent,
  ApiJobPreflight,
  ApiJobSnapshot,
  ApiRunManifest,
  ApiRunMaterial,
  ApiRunOperation,
  ApiRunOperationRequest,
  ApiRuntimePackageOperation,
  ApiRuntimePackageReport,
} from './api'
import { useStudioStore } from './store'

const json = (value: unknown, status = 200): Response =>
  new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })

const deferred = <T,>() => {
  let resolve!: (value: T | PromiseLike<T>) => void
  const promise = new Promise<T>(next => {
    resolve = next
  })
  return { promise, resolve }
}

const createdAt = '2026-07-28T12:00:00Z'
const runtimeWarnings = ['CUDA OCR unavailable; CPU fallback selected.']
const textMaterial: ApiRunMaterial = {
  id: 'material-text001',
  run_id: 'run-real-01',
  kind: 'text',
  title: '评论区补充资料',
  original_name: null,
  media_type: 'text/markdown; charset=utf-8',
  artifact: {
    kind: 'supporting',
    relative_path: 'supporting/files/material-text001/text.md',
    sha256: 'a'.repeat(64),
    size_bytes: 24,
    media_type: null,
    created_at: createdAt,
  },
  sha256: 'a'.repeat(64),
  size_bytes: 24,
  text_content: '一条需要一起整理的资料。',
  start_us: 1_000_000,
  end_us: 2_000_000,
  status: 'active',
  created_at: createdAt,
  deleted_at: null,
}

const baseEffectiveEvidence: ApiEvidenceSpan[] = [
  {
    id: 'evidence-early',
    modality: 'asr',
    start_us: 1_000_000,
    end_us: 2_000_000,
    raw_text: 'Early section context.',
    confidence: 0.82,
    provider: 'base-asr',
    model: 'base-model',
  },
  {
    id: 'evidence-late',
    modality: 'asr',
    start_us: 9_000_000,
    end_us: 10_000_000,
    raw_text: 'Late fact evidence.',
    confidence: 0.91,
    provider: 'base-asr',
    model: 'base-model',
  },
]

const registryPayload = {
  schema_version: 2,
  providers: {
    'cloud-one': {
      id: 'cloud-one',
      display_name: 'Cloud One',
      kind: 'openai_compatible' as const,
      protocol: 'openai_chat_completions' as const,
      auth_scheme: 'bearer' as const,
      base_url: 'https://models.example.test/v1',
      endpoint_style: 'chat_completions' as const,
      protocol_options: {},
      request_timeout_seconds: 180,
      locality: 'cloud' as const,
      enabled: true,
    },
  },
  models: {
    'asr-main': {
      id: 'asr-main',
      provider_id: 'cloud-one',
      model_id: 'whisper-large-v3',
      display_name: 'Whisper large-v3',
      capabilities: ['asr', 'segment_timestamps'],
      locality: 'cloud' as const,
      settings: {},
      enabled: true,
    },
  },
  roles: {
    'asr.primary': {
      role: 'asr.primary',
      primary_model_id: 'asr-main',
      fallback_model_ids: [],
    },
  },
}

const performancePayload = {
  schema_version: 1 as const,
  experience_mode: 'guided' as const,
  preference: 'balanced' as const,
  reserve: {
    cpu_reserve_ratio: 0.25,
    memory_reserve_ratio: 0.25,
    gpu_reserve_ratio: 0.2,
    vram_reserve_ratio: 0.2,
    disk_reserve_ratio: 0.1,
    cpu_reserve_cores: 1,
    memory_reserve_bytes: 3 * 1024 ** 3,
    vram_reserve_bytes: 1024 ** 3,
    disk_reserve_bytes: 10 * 1024 ** 3,
    cpu_safety_factor: 0.9,
    memory_safety_factor: 0.9,
    gpu_safety_factor: 0.9,
    vram_safety_factor: 0.85,
    disk_safety_factor: 0.9,
  },
  overrides: {},
}

const configurationCatalogPayload = {
  protocols: [
    {
      protocol: 'openai_chat_completions',
      display_name: 'OpenAI Chat Completions',
      default_auth_scheme: 'bearer',
      default_base_url: 'https://api.openai.com/v1',
      request_path: '/chat/completions',
      discovery_path: '/models',
      request_content_type: 'application/json',
      structured_generation_adapter: true,
      supports_json_schema_transport: true,
      supports_image_transport: true,
      supports_streaming_transport: true,
      stream_transport: 'sse',
    },
  ],
  roles: [
    { role: 'asr.primary', required_capabilities: ['asr', 'segment_timestamps'] },
    { role: 'notes.drafter', required_capabilities: ['text', 'long_context'] },
  ],
  capabilities: [
    'text',
    'long_context',
    'asr',
    'segment_timestamps',
  ],
}

const componentReportPayload = (ready = false) => ({
  hardware_tier: 'gpu_12gb',
  recommendation: {
    hardware_tier: 'gpu_12gb',
    asr_component_id: 'asr-faster-whisper-large-v3',
    ocr_component_id: 'ocr-paddle-ppocrv5-server',
    asr_device: 'cuda',
    asr_compute_type: 'float16',
    ocr_device: 'cpu',
    reason: '12 GiB GPUs can run large-v3 on CUDA while OCR uses the bundled CPU runtime.',
  },
  inventory: {
    ready,
    degraded: !ready,
    capabilities: { core: true, download: true, asr: ready, ocr: ready },
    items: [
      {
        id: 'runtime-root',
        display_name: 'Portable runtime resources',
        kind: 'runtime',
        state: 'ready',
        ready: true,
        degraded: false,
        required: true,
        version: 'test',
        path: 'D:/runtime',
        detail: null,
        actions: [],
      },
      {
        id: 'ffmpeg',
        display_name: 'ffmpeg',
        kind: 'tool',
        state: 'ready',
        ready: true,
        degraded: false,
        required: true,
        version: '7.1',
        path: 'D:/runtime/ffmpeg.exe',
        detail: null,
        actions: [],
      },
      ...[
        ['asr-faster-whisper-large-v3', 'faster-whisper large-v3'],
        ['ocr-paddle-ppocrv5-server', 'PaddleOCR PP-OCRv5 server'],
      ].map(([id, displayName]) => ({
        id,
        display_name: displayName,
        kind: 'local_model',
        state: ready ? 'ready' : 'missing',
        ready,
        degraded: !ready,
        required: true,
        version: '1.0.0',
        path: `D:/data/components/${id}`,
        detail: ready ? null : 'Model payload has not been downloaded.',
        actions: ready
          ? []
          : [
              {
                id: `prepare-${id}`,
                kind: 'prepare',
                component_id: id,
                label: `Prepare ${displayName}`,
                automatic: true,
              },
            ],
      })),
    ],
    actions: ready
      ? []
      : [
          'asr-faster-whisper-large-v3',
          'ocr-paddle-ppocrv5-server',
        ].map(id => ({
          id: `prepare-${id}`,
          kind: 'prepare',
          component_id: id,
          label: `Prepare ${id}`,
          automatic: true,
        })),
  },
})

const runningJob: ApiJobSnapshot = {
  run_id: 'run-real-01',
  state: 'running' as const,
  stage: 'audio.asr',
  progress: 0.45,
  message: '正在校准语音时间戳',
  queued_at: createdAt,
  started_at: createdAt,
  events: [
    {
      sequence: 1,
      run_id: 'run-real-01',
      state: 'queued' as const,
      stage: 'queued',
      metrics: {},
      created_at: '2026-07-28T12:00:00Z',
    },
    {
      sequence: 2,
      run_id: 'run-real-01',
      state: 'running' as const,
      stage: 'source.acquire',
      progress: 0.5,
      message: '正在下载媒体',
      metrics: { downloaded_bytes: 2_048, total_bytes: 4_096, speed_bps: null },
      created_at: '2026-07-28T12:00:01Z',
    },
    {
      sequence: 3,
      run_id: 'run-real-01',
      state: 'running' as const,
      stage: 'audio.asr',
      progress: 0.45,
      message: '正在校准语音时间戳',
      metrics: { processed_seconds: 41.2, realtime_factor: 0.24 },
      created_at: '2026-07-28T12:00:02Z',
    },
  ],
}

const runningRun: ApiRunManifest = {
  run_id: 'run-real-01',
  source: {
    kind: 'url',
    locator: 'https://www.youtube.com/watch?v=real-api-test',
    platform: 'youtube',
    source_id: 'real-api-test',
    title: 'Real API lecture',
    author: 'Video2Notes test',
  },
  profile: 'balanced' as const,
  processing_scope: 'audio_visual' as const,
  status: 'running' as const,
  created_at: createdAt,
  updated_at: createdAt,
  stages: {
    'source.acquire': {
      stage_name: 'source.acquire',
      status: 'completed' as const,
      outputs: [
        {
          kind: 'media',
          relative_path: 'source/source.mp4',
          sha256: 'source-sha256',
          size_bytes: 4_096,
          media_type: 'video/mp4',
          created_at: createdAt,
        },
      ],
      wall_time_seconds: 1.25,
      warnings: ['Source selected a lower authenticated format.'],
      metrics: { downloaded_bytes: 4_096, speed_bps: null, used_browser_profile: true },
    },
    'audio.extract': {
      stage_name: 'audio.extract',
      status: 'completed' as const,
      outputs: [
        {
          kind: 'audio',
          relative_path: 'audio/mono.wav',
          sha256: 'audio-sha256',
          size_bytes: 2_048,
          media_type: 'audio/wav',
          created_at: createdAt,
        },
      ],
      wall_time_seconds: 2.5,
      warnings: [],
      metrics: { segment_count: 1, sample_rate_hz: 16_000 },
    },
    'audio.asr': {
      stage_name: 'audio.asr',
      status: 'running' as const,
      outputs: [
        {
          kind: 'evidence',
          relative_path: 'speech/asr-evidence.json',
          sha256: 'asr-sha256',
          size_bytes: 8_192,
          media_type: 'application/json',
          created_at: createdAt,
        },
      ],
      warnings: ['Primary ASR retained one low-confidence segment.'],
      metrics: { segment_count: 24, secondary_window_count: 2 },
    },
  },
  warnings: [],
}

const readyRuntimePreflight = (): ApiJobPreflight => ({
  state: 'ready',
  requirements: [],
  missing_required: [],
  missing_optional: [],
  selected_instances: {},
  binding_snapshot: {},
  recommended_actions: [],
  estimated_download_bytes: 0,
  estimated_installed_bytes: 0,
})

const blockedRuntimePreflight = (): ApiJobPreflight => ({
  state: 'blocked',
  requirements: [
    {
      requirement_id: 'asr.faster_whisper',
      capability_id: 'asr.faster_whisper',
      required: true,
      state: 'blocked',
      selected_instance_id: null,
      selected_source: null,
      detail: '当前任务需要本地语音识别运行时。',
    },
  ],
  missing_required: ['asr.faster_whisper'],
  missing_optional: [],
  selected_instances: {},
  binding_snapshot: {},
  recommended_actions: [
    {
      package_id: 'video2notes-nvidia-asr',
      version: '1.0.0',
      display_name: 'NVIDIA ASR 运行时',
      requirement_ids: ['asr.faster_whisper'],
      archive_file_name: 'video2notes-nvidia-asr-1.0.0.zip',
      source_url: 'https://downloads.example.test/video2notes-nvidia-asr-1.0.0.zip',
      download_size_bytes: 512 * 1024 ** 2,
      download_part_count: 1,
      installed_size_bytes: 1024 ** 3,
      install_root: 'D:/data/runtime-packages/video2notes-nvidia-asr/1.0.0',
      supported_devices: ['cuda'],
    },
  ],
  estimated_download_bytes: 512 * 1024 ** 2,
  estimated_installed_bytes: 1024 ** 3,
})

const runtimePackageReport = (
  operations: ApiRuntimePackageOperation[],
): ApiRuntimePackageReport => ({
  inventory: {
    instances: [],
    bindings: {},
    operations,
    available_releases: [],
  },
  managed_root: 'D:/data/runtime-packages',
  releases: [],
})

describe('studio store against the real loopback API contract', () => {
  const fetchMock = vi.fn()
  const requests: Array<{
    url: string
    method: string
    body?: string
    rawBody?: BodyInit | null
    token?: string
  }> = []
  let currentJob: ApiJobSnapshot = runningJob
  let historicalJobEvents: ApiJobEvent[] = [...runningJob.events]
  let historicalEventPageSize: number | undefined
  let historicalLogAvailable = true
  let historicalCorruptLineCount = 0
  let currentRun: ApiRunManifest = runningRun
  let currentMaterials: ApiRunMaterial[] = [textMaterial]
  let materialListFails = false
  let currentOperations: ApiRunOperation[] = []
  let effectiveEvidence: ApiEvidenceSpan[] = structuredClone(baseEffectiveEvidence)
  let operationListFails = false
  let evidenceListFails = false
  let conflictingOperationKind: ApiRunOperationRequest['kind'] | undefined
  let failedOperationKind: ApiRunOperationRequest['kind'] | undefined
  let operationSequence = 0
  let componentsReady = false
  let failedEstimateMode: 'fast' | 'balanced' | 'accurate' | undefined
  let listedRuns: ApiRunManifest[] = []
  let includeAcceleration = true
  let includeProcessingScopeCapability = true
  let probeGate: Promise<void> | undefined
  let submissionGate: Promise<void> | undefined
  let runtimePackageOperations: ApiRuntimePackageOperation[] = []
  let runtimeInstallSequence = 0
  let preflightResponses: ApiJobPreflight[] = [readyRuntimePreflight()]
  let preflightRequestCount = 0

  beforeEach(() => {
    vi.unstubAllEnvs()
    vi.stubEnv('VITE_VIDEO2NOTES_API_URL', 'http://127.0.0.1:43119')
    vi.stubEnv('VITE_VIDEO2NOTES_API_TOKEN', 'desktop-session-token')
    requests.length = 0
    currentJob = runningJob
    historicalJobEvents = [...runningJob.events]
    historicalEventPageSize = undefined
    historicalLogAvailable = true
    historicalCorruptLineCount = 0
    currentRun = runningRun
    currentMaterials = [textMaterial]
    materialListFails = false
    currentOperations = []
    effectiveEvidence = structuredClone(baseEffectiveEvidence)
    operationListFails = false
    evidenceListFails = false
    conflictingOperationKind = undefined
    failedOperationKind = undefined
    operationSequence = 0
    componentsReady = false
    failedEstimateMode = undefined
    listedRuns = []
    includeAcceleration = true
    includeProcessingScopeCapability = true
    probeGate = undefined
    submissionGate = undefined
    runtimePackageOperations = []
    runtimeInstallSequence = 0
    preflightResponses = [readyRuntimePreflight()]
    preflightRequestCount = 0
    fetchMock.mockReset()
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input))
      const method = init?.method ?? 'GET'
      const headers = new Headers(init?.headers)
      requests.push({
        url: `${url.pathname}${url.search}`,
        method,
        body: typeof init?.body === 'string' ? init.body : undefined,
        rawBody: init?.body,
        token: headers.get('X-Video2Notes-Token') ?? undefined,
      })

      if (url.pathname === '/api/health') {
        return Promise.resolve(
          json({
            status: 'ok',
            version: '0.1.0',
            scope: 'loopback',
            ...(includeProcessingScopeCapability
              ? { capabilities: ['processing_scope_audio_only'] }
              : {}),
          }),
        )
      }
      if (url.pathname === '/api/system') {
        return Promise.resolve(
          json({
            hardware: {
              cpu_name: 'Ryzen test',
              logical_cores: 16,
              memory_total_bytes: 32 * 1024 ** 3,
              gpus: [{ name: 'RTX test', memory_total_bytes: 16 * 1024 ** 3 }],
            },
            ...(includeAcceleration
              ? {
                  acceleration: {
                    schema_version: 1,
                    asr: {
                      engine: 'faster-whisper/CTranslate2',
                      cuda_available: true,
                      device_count: 1,
                      supported_compute_types: ['float16', 'int8_float16'],
                      runtime_directories: ['D:/runtime/nvidia/cublas/bin'],
                      reason: 'CUDA 运行库与 CTranslate2 已验证。',
                    },
                    ocr: {
                      engine: 'PaddleOCR/PaddlePaddle',
                      cuda_available: false,
                      device_count: 0,
                      supported_compute_types: [],
                      runtime_directories: [],
                      reason: '当前 PaddlePaddle 为 CPU 构建。',
                    },
                  },
                }
              : {}),
            recommended_tier: 'high',
            performance: performancePayload,
            recommendation: {
              experience_mode: 'guided',
              preference: 'balanced',
              reserve: performancePayload.reserve,
              budget: {
                cpu_available_equivalent: 12,
                cpu_budget_equivalent: 8,
                cpu_workers: 8,
                memory_available_bytes: 24 * 1024 ** 3,
                memory_budget_bytes: 18 * 1024 ** 3,
                disk_available_bytes: 100 * 1024 ** 3,
                disk_budget_bytes: 80 * 1024 ** 3,
                gpu_name: 'RTX test',
                gpu_compute_available_ratio: 0.8,
                gpu_compute_budget_ratio: 0.6,
                vram_available_bytes: 12 * 1024 ** 3,
                vram_budget_bytes: 10 * 1024 ** 3,
                gpu_stage_slots: 1,
                remote_model_concurrency: 2,
              },
              notes: ['test recommendation'],
            },
            plans: {},
          }),
        )
      }
      if (url.pathname === '/api/performance') {
        return Promise.resolve(
          method === 'PUT' ? json(JSON.parse(String(init?.body))) : json(performancePayload),
        )
      }
      if (url.pathname === '/api/configuration-catalog') {
        return Promise.resolve(json(configurationCatalogPayload))
      }
      if (url.pathname === '/api/runtime') {
        return Promise.resolve(json({ injected: true, warnings: [] }))
      }
      if (url.pathname === '/api/components' && method === 'GET') {
        return Promise.resolve(json(componentReportPayload(componentsReady)))
      }
      if (url.pathname === '/api/components/prepare' && method === 'POST') {
        const body = JSON.parse(String(init?.body)) as {
          component_ids?: string[]
          hardware_tier?: string | null
          activate?: boolean
        }
        const componentIds = body.component_ids?.length
          ? body.component_ids
          : [
              'asr-faster-whisper-large-v3',
              'ocr-paddle-ppocrv5-server',
            ]
        componentsReady = componentIds.length === 2
        return Promise.resolve(
          json({
            hardware_tier: body.hardware_tier ?? 'gpu_12gb',
            results: componentIds.map(componentId => ({
              component_id: componentId,
              status: 'prepared',
              path: `D:/data/components/${componentId}`,
              resumed: false,
              detail: null,
            })),
            activated: componentsReady && body.activate !== false,
            activated_roles:
              componentsReady && body.activate !== false
                ? ['asr.primary', 'ocr.primary', 'vision.text_detector']
                : [],
            blocked_roles:
              componentsReady && body.activate !== false
                ? []
                : ['asr.primary', 'ocr.primary', 'vision.text_detector'],
            warnings: [],
            report: componentReportPayload(componentsReady),
          }),
        )
      }
      if (url.pathname === '/api/runtime-packages' && method === 'GET') {
        return Promise.resolve(json(runtimePackageReport(runtimePackageOperations)))
      }
      if (url.pathname === '/api/runtime-packages/discover' && method === 'POST') {
        return Promise.resolve(json(runtimePackageReport(runtimePackageOperations)))
      }
      if (url.pathname === '/api/runtime-packages/install' && method === 'POST') {
        const body = JSON.parse(String(init?.body)) as {
          package_id: string
          version?: string | null
          bind_requirements?: string[]
        }
        runtimeInstallSequence += 1
        const operation: ApiRuntimePackageOperation = {
          operation_id: `runtime-install-${runtimeInstallSequence}`,
          kind: 'install',
          package_id: body.package_id,
          target_version: body.version ?? null,
          instance_id: null,
          source_instance_id: null,
          status: 'queued',
          phase: 'queued',
          progress: 0,
          downloaded_bytes: 0,
          total_bytes: 512 * 1024 ** 2,
          expected_installed_bytes: 1024 ** 3,
          resumable: true,
          target_root: `D:/data/runtime-packages/${body.package_id}/${body.version ?? 'latest'}`,
          requested_bindings: body.bind_requirements ?? [],
          cancel_requested: false,
          created_at_utc: `2026-07-28T12:30:0${runtimeInstallSequence}Z`,
          started_at_utc: null,
          finished_at_utc: null,
          result_instance_id: null,
          detail: null,
          error_code: null,
        }
        runtimePackageOperations = [...runtimePackageOperations, operation]
        return Promise.resolve(json(operation, 202))
      }
      if (url.pathname === '/api/browser-profiles') {
        return Promise.resolve(
          json([
            {
              browser: 'edge',
              profile_id: 'Default',
              display_name: 'Edge Default',
              path: 'C:/Users/test/AppData/Edge/Default',
              is_default: true,
            },
          ]),
        )
      }
      if (url.pathname === '/api/providers' && method === 'GET') {
        return Promise.resolve(json(registryPayload))
      }
      if (url.pathname === '/api/providers' && method === 'PUT') {
        return Promise.resolve(json(JSON.parse(String(init?.body))))
      }
      if (url.pathname === '/api/providers/cloud-one/secret' && method === 'GET') {
        return Promise.resolve(json({ provider_id: 'cloud-one', status: 'configured' }))
      }
      if (url.pathname === '/api/providers/cloud-one/secret' && method === 'PUT') {
        return Promise.resolve(json({ provider_id: 'cloud-one', status: 'configured' }))
      }
      if (url.pathname === '/api/providers/cloud-one/test' && method === 'POST') {
        return Promise.resolve(
          json({ provider_id: 'cloud-one', status: 'connected', detail: 'Provider reachable' }),
        )
      }
      if (url.pathname === '/api/providers/cloud-one/discover' && method === 'GET') {
        return Promise.resolve(
          json({
            provider_id: 'cloud-one',
            protocol: 'openai_chat_completions',
            models: [
              {
                model_id: 'notes-writer',
                display_name: 'Notes writer',
                context_window: 128_000,
              },
            ],
          }),
        )
      }
      if (url.pathname === '/api/runs') return Promise.resolve(json(listedRuns))
      if (url.pathname === '/api/sources/probe') {
        const response = () =>
          json({
            platform: 'youtube',
            source: { kind: 'url', value: 'https://www.youtube.com/watch?v=real-api-test' },
            source_id: 'real-api-test',
            canonical_url: 'https://www.youtube.com/watch?v=real-api-test',
            title: 'Real API lecture',
            author: 'Video2Notes test',
            duration_seconds: 91.5,
            formats: [
              {
                format_id: '137+140',
                width: 1920,
                height: 1080,
                fps: 30,
                vcodec: 'avc1',
                acodec: 'aac',
              },
            ],
            subtitles: { en: ['vtt'] },
            automatic_captions: {},
            selected_format_ids: ['137+140'],
            auth_kind: 'browser_profile',
          })
        return probeGate ? probeGate.then(response) : Promise.resolve(response())
      }
      if (url.pathname === '/api/estimate' && method === 'POST') {
        const body = JSON.parse(String(init?.body)) as {
          duration_seconds: number
          quality_mode: 'fast' | 'balanced' | 'accurate'
          source_height?: number
          source_fps?: number
        }
        const factors = {
          fast: [0.05, 0.15],
          balanced: [0.1, 0.33],
          accurate: [0.2, 0.67],
        } as const
        if (body.quality_mode === failedEstimateMode) {
          return Promise.resolve(json({ detail: 'estimate temporarily unavailable' }, 503))
        }
        const [lower, upper] = factors[body.quality_mode]
        return Promise.resolve(
          json({
            hardware_tier: 'gpu_12gb',
            quality_mode: body.quality_mode,
            media_duration_seconds: body.duration_seconds,
            lower_seconds: 15 + body.duration_seconds * lower,
            upper_seconds: 15 + body.duration_seconds * upper,
            lower_realtime_factor: lower,
            upper_realtime_factor: upper,
            basis: 'engineering_budget_v1',
            precision_intent: `${body.quality_mode} test intent`,
            notes: ['test estimate'],
          }),
        )
      }
      if (url.pathname === '/api/jobs/preflight' && method === 'POST') {
        const response =
          preflightResponses[
            Math.min(preflightRequestCount, preflightResponses.length - 1)
          ] ?? readyRuntimePreflight()
        preflightRequestCount += 1
        return Promise.resolve(json(response))
      }
      if (url.pathname === '/api/jobs' && method === 'POST') {
        const response = () =>
          json({ run: currentRun, job: currentJob, runtime_warnings: runtimeWarnings })
        return submissionGate ? submissionGate.then(response) : Promise.resolve(response())
      }
      if (url.pathname === '/api/jobs' && method === 'GET') return Promise.resolve(json([currentJob]))
      if (url.pathname === '/api/jobs/run-real-01/result') {
        return Promise.resolve(
          json({ run: currentRun, job: currentJob, runtime_warnings: runtimeWarnings }),
        )
      }
      if (url.pathname === '/api/runs/run-real-01/events' && method === 'GET') {
        const afterSequence = Number(url.searchParams.get('after_sequence') ?? 0)
        const requestedLimit = Number(url.searchParams.get('limit') ?? 200)
        const limit = Math.min(requestedLimit, historicalEventPageSize ?? requestedLimit)
        const remaining = historicalJobEvents.filter(
          event => event.sequence > afterSequence,
        )
        const events = remaining.slice(0, limit)
        return Promise.resolve(
          json({
            run_id: 'run-real-01',
            events,
            next_sequence: events.at(-1)?.sequence ?? afterSequence,
            has_more: remaining.length > events.length,
            log_available: historicalLogAvailable,
            corrupt_line_count: historicalCorruptLineCount,
          }),
        )
      }
      if (
        url.pathname === '/api/runs/run-real-01/operations' &&
        method === 'GET'
      ) {
        return Promise.resolve(
          operationListFails
            ? json({ detail: 'operation index temporarily unavailable' }, 500)
            : json(currentOperations),
        )
      }
      if (
        url.pathname === '/api/runs/run-real-01/operations' &&
        method === 'POST'
      ) {
        const payload = JSON.parse(
          String(init?.body),
        ) as ApiRunOperationRequest
        if (payload.kind === conflictingOperationKind) {
          return Promise.resolve(
            json(
              {
                detail:
                  payload.kind === 'asr_retranscribe'
                    ? 'ASR backend is not configured'
                    : 'OCR backend is not configured',
              },
              409,
            ),
          )
        }
        operationSequence += 1
        const executionFailed = payload.kind === failedOperationKind
        const operation: ApiRunOperation = {
          schema_version: 1,
          operation_id: `operation-${operationSequence}`,
          run_id: 'run-real-01',
          request: payload,
          status: executionFailed ? 'failed' : 'completed',
          created_at: `2026-07-28T12:20:0${operationSequence}Z`,
          finished_at: `2026-07-28T12:20:0${operationSequence}Z`,
          revision_id: executionFailed ? null : `revision-${operationSequence}`,
          artifact_paths: executionFailed ? [] : ['operations/evidence.json'],
          replaced_evidence_ids: executionFailed
            ? []
            : effectiveEvidence.map(item => item.id),
          new_evidence_ids: executionFailed
            ? []
            : [`evidence-operation-${operationSequence}`],
          visual_state_count:
            !executionFailed && payload.kind === 'vision_rescan' ? 4 : 0,
          error_type: executionFailed ? 'RuntimeError' : null,
          detail: executionFailed ? 'worker exited before producing evidence' : null,
        }
        currentOperations = [...currentOperations, operation]
        if (!executionFailed && payload.kind === 'vision_rescan') {
          effectiveEvidence = [
            {
              id: `evidence-operation-${operationSequence}`,
              modality: 'ocr',
              start_us: payload.range.start_us,
              end_us: payload.range.end_us,
              raw_text: '视觉返工刷新后的屏幕文字。',
              confidence: 0.94,
              provider: 'vision-provider',
              model: 'vision-v2',
            },
          ]
        }
        return Promise.resolve(json(operation))
      }
      if (
        url.pathname === '/api/runs/run-real-01/evidence' &&
        method === 'GET'
      ) {
        return Promise.resolve(
          evidenceListFails
            ? json({ detail: 'effective evidence temporarily unavailable' }, 500)
            : json({
                schema_version: 1,
                run_id: 'run-real-01',
                revision_id: currentOperations.at(-1)?.revision_id ?? null,
                operation_id: currentOperations.at(-1)?.operation_id ?? null,
                evidence: effectiveEvidence,
                superseded_evidence_ids: [],
              }),
        )
      }
      if (url.pathname === '/api/runs/run-real-01/materials' && method === 'GET') {
        return Promise.resolve(
          materialListFails
            ? json({ detail: 'material index temporarily unavailable' }, 500)
            : json(currentMaterials),
        )
      }
      if (url.pathname === '/api/runs/run-real-01/materials/text' && method === 'POST') {
        const payload = JSON.parse(String(init?.body)) as {
          title: string
          content: string
          start_us?: number
          end_us?: number
        }
        const created: ApiRunMaterial = {
          ...textMaterial,
          id: 'material-addedtext',
          title: payload.title,
          text_content: payload.content,
          start_us: payload.start_us ?? null,
          end_us: payload.end_us ?? null,
        }
        currentMaterials = [...currentMaterials, created]
        return Promise.resolve(json(created))
      }
      if (url.pathname === '/api/runs/run-real-01/materials/files' && method === 'POST') {
        const form = init?.body as FormData
        const file = form.get('file') as File
        const created: ApiRunMaterial = {
          ...textMaterial,
          id: 'material-addedimage',
          kind: 'image',
          title: url.searchParams.get('title') || file.name,
          original_name: file.name,
          media_type: file.type,
          artifact: {
            kind: 'supporting',
            relative_path: 'supporting/files/material-addedimage/image.png',
            sha256: 'b'.repeat(64),
            size_bytes: file.size,
            media_type: file.type,
            created_at: createdAt,
          },
          sha256: 'b'.repeat(64),
          size_bytes: file.size,
          text_content: null,
          start_us: Number(url.searchParams.get('start_us')),
          end_us: Number(url.searchParams.get('end_us')),
        }
        currentMaterials = [...currentMaterials, created]
        return Promise.resolve(json(created))
      }
      if (
        url.pathname.startsWith('/api/runs/run-real-01/materials/') &&
        method === 'DELETE'
      ) {
        const materialId = decodeURIComponent(url.pathname.split('/').at(-1) ?? '')
        const existing = currentMaterials.find(material => material.id === materialId)
        if (!existing) return Promise.resolve(json({ detail: 'material not found' }, 404))
        currentMaterials = currentMaterials.filter(material => material.id !== materialId)
        return Promise.resolve(
          json({
            ...existing,
            status: 'deleted',
            deleted_at: '2026-07-28T12:10:00Z',
          }),
        )
      }
      if (url.pathname === '/api/runs/run-real-01/artifact') {
        return Promise.resolve(
          json({
            metadata: {
              title: 'Evidence timing note',
              run_id: 'run-real-01',
              source_kind: 'url',
              source_locator: 'https://www.youtube.com/watch?v=real-api-test',
              duration_us: 12_000_000,
              languages: ['en'],
              quality_mode: 'balanced',
              quality_warnings: [],
              created_at: createdAt,
            },
            abstract: 'A note hydrated from the canonical artifact.',
            key_takeaways: [],
            sections: [
              {
                id: 'section-one',
                title: 'Timing',
                start_us: 0,
                end_us: 12_000_000,
                summary: 'The fact must point at its own evidence.',
                body_markdown: '',
                evidence_ids: ['evidence-early'],
                fact_ids: ['fact-late'],
                screenshots: [],
              },
            ],
            facts: [
              {
                id: 'fact-late',
                claim: 'The important claim begins at nine seconds.',
                evidence_ids: ['evidence-late'],
                kind: 'claim',
                needs_review: false,
              },
            ],
            evidence: [
              {
                id: 'evidence-early',
                modality: 'asr',
                start_us: 1_000_000,
                end_us: 2_000_000,
                raw_text: 'Early section context.',
              },
              {
                id: 'evidence-late',
                modality: 'asr',
                start_us: 9_000_000,
                end_us: 10_000_000,
                raw_text: 'Late fact evidence.',
              },
            ],
            glossary: {},
          }),
        )
      }
      return Promise.resolve(json({ detail: `unexpected ${method} ${url.pathname}` }, 404))
    })
    vi.stubGlobal('fetch', fetchMock)

    useStudioStore.getState().resetDemo()
    useStudioStore.setState({
      backend: { mode: 'connecting', detail: 'test boot' },
      machine: { cpu: 'pending', gpu: 'pending', memory: '—', backend: 'starting' },
      tasks: [],
      providers: [],
      models: [],
      roles: [],
      browserProfiles: [],
      activeTaskId: '',
      draft: {
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
      },
    })
  })

  it('initializes, probes, submits, polls, persists secrets, tests providers, and saves role bindings', async () => {
    useStudioStore.getState().initializeBackend()
    await vi.waitFor(() => expect(useStudioStore.getState().backend.mode).toBe('real'))

    expect(useStudioStore.getState().browserProfiles).toMatchObject([
      { browser: 'edge', profileId: 'Default' },
    ])
    expect(useStudioStore.getState().providers).toMatchObject([
      { id: 'cloud-one', credentialState: 'stored-locally' },
    ])
    expect(useStudioStore.getState().models[0]?.capabilities).toEqual([
      'asr',
      'segment_timestamps',
    ])
    expect(useStudioStore.getState().roles).toEqual([
      expect.objectContaining({
        id: 'asr.primary',
        modelId: 'asr-main',
        requiredCapabilities: ['asr', 'segment_timestamps'],
      }),
      expect.objectContaining({
        id: 'notes.drafter',
        modelId: '',
        requiredCapabilities: ['text', 'long_context'],
      }),
    ])
    expect(useStudioStore.getState().performance).toMatchObject({
      experienceMode: 'guided',
      preference: 'balanced',
    })
    expect(useStudioStore.getState().systemReport?.acceleration).toEqual({
      asr: {
        engine: 'faster-whisper/CTranslate2',
        cudaAvailable: true,
        deviceCount: 1,
        supportedComputeTypes: ['float16', 'int8_float16'],
        reason: 'CUDA 运行库与 CTranslate2 已验证。',
      },
      ocr: {
        engine: 'PaddleOCR/PaddlePaddle',
        cudaAvailable: false,
        deviceCount: 0,
        supportedComputeTypes: [],
        reason: '当前 PaddlePaddle 为 CPU 构建。',
      },
    })

    useStudioStore.getState().savePerformance({
      ...useStudioStore.getState().performance,
      experienceMode: 'professional',
      preference: 'throughput',
      overrides: {
        cpuWorkers: 10,
        remoteModelConcurrency: 3,
        ocrInferenceMaxWidth: 1536,
        cheapScanFps: 2,
        expensiveScanFps: 8,
      },
    })
    await vi.waitFor(() =>
      expect(
        requests.some(
          request => request.url === '/api/performance' && request.method === 'PUT',
        ),
      ).toBe(true),
    )
    await vi.waitFor(() =>
      expect(useStudioStore.getState().performance).toMatchObject({
        experienceMode: 'professional',
        preference: 'throughput',
        overrides: {
          cpuWorkers: 10,
          remoteModelConcurrency: 3,
          ocrInferenceMaxWidth: 1536,
        },
      }),
    )
    const performanceSave = requests.find(
      request => request.url === '/api/performance' && request.method === 'PUT',
    )
    expect(JSON.parse(performanceSave?.body ?? '{}')).toMatchObject({
      experience_mode: 'professional',
      preference: 'throughput',
      overrides: {
        cpu_workers: 10,
        remote_model_concurrency: 3,
        ocr_inference_max_width: 1536,
        cheap_scan_fps: 2,
        expensive_scan_fps: 8,
      },
    })

    const store = useStudioStore.getState()
    store.setDraftInput('https://www.youtube.com/watch?v=real-api-test')
    store.setDraftAuthKind('browser_profile')
    store.setDraftBrowser('edge')
    store.setDraftProfile('Default')
    store.setLanguageHints('zh-CN, en')
    store.addSamplingOverride()
    const overrideId = useStudioStore.getState().draft.samplingOverrides[0].id
    store.updateSamplingOverride(overrideId, {
      startSeconds: 1.25,
      endSeconds: 2.75,
      mode: 'fixed_interval',
      intervalSeconds: 0.1,
    })
    store.setReportPreset('professional')
    store.probeSource()
    await vi.waitFor(() => expect(useStudioStore.getState().draft.status).toBe('ready'))
    await vi.waitFor(() =>
      expect(useStudioStore.getState().processingEstimateStatus).toBe('ready'),
    )

    const probe = requests.find(request => request.url === '/api/sources/probe')
    expect(probe).toMatchObject({ method: 'POST', token: 'desktop-session-token' })
    expect(JSON.parse(probe?.body ?? '{}')).toEqual({
      source: { kind: 'url', value: 'https://www.youtube.com/watch?v=real-api-test' },
      auth: { kind: 'browser_profile', browser: 'edge', profile: 'Default' },
      policy: expect.objectContaining({ mode: 'accurate', max_height: 1080 }),
    })
    const estimateRequests = requests.filter(request => request.url === '/api/estimate')
    expect(estimateRequests).toHaveLength(3)
    expect(
      estimateRequests.map(request => JSON.parse(request.body ?? '{}')),
    ).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          quality_mode: 'fast',
          duration_seconds: 91.5,
          source_height: 1080,
          source_fps: 30,
        }),
        expect.objectContaining({ quality_mode: 'balanced' }),
        expect.objectContaining({ quality_mode: 'accurate' }),
      ]),
    )
    expect(
      estimateRequests
        .map(request => JSON.parse(request.body ?? '{}'))
        .every(request => !('processing_scope' in request)),
    ).toBe(true)
    expect(useStudioStore.getState().processingEstimates.accurate).toMatchObject({
      hardwareTier: 'gpu_12gb',
      lowerRealtimeFactor: 0.2,
      upperRealtimeFactor: 0.67,
    })

    useStudioStore.getState().createTask()
    await vi.waitFor(() =>
      expect(
        requests.some(
          request => request.url === '/api/jobs' && request.method === 'POST',
        ),
      ).toBe(true),
    )
    const submission = requests.find(request => request.url === '/api/jobs' && request.method === 'POST')
    expect(JSON.parse(submission?.body ?? '{}')).toMatchObject({
      quality_mode: 'balanced',
      language_hints: ['zh-CN', 'en'],
      include_screenshots: true,
      generate_pdf: true,
      sampling_plan: {
        default: { mode: 'adaptive' },
        overrides: [
          {
            range: { start_us: 1_250_000, end_us: 2_750_000 },
            sampling: { mode: 'fixed_interval', interval_us: 100_000 },
          },
        ],
      },
      report_spec: {
        preset: 'professional',
        language: 'zh-CN',
        include_screenshots: true,
        output_formats: ['markdown', 'html', 'pdf'],
      },
    })
    expect(JSON.parse(submission?.body ?? '{}')).not.toHaveProperty('processing_scope')

    useStudioStore.getState().refreshTasks()
    await vi.waitFor(() =>
      expect(requests.some(request => request.url === '/api/jobs/run-real-01/result')).toBe(true),
    )
    await vi.waitFor(() =>
      expect(useStudioStore.getState().tasks[0]?.lastMessage).toBe('正在校准语音时间戳'),
    )
    expect(useStudioStore.getState().tasks[0]).toMatchObject({
      id: 'run-real-01',
      processingScope: 'audio_visual',
      status: 'running',
      lastMessage: '正在校准语音时间戳',
      runtimeWarnings,
      realBackend: true,
    })
    const runningTask = useStudioStore.getState().tasks[0]
    expect(runningTask.telemetry.map(sample => sample.sequence)).toEqual([1, 2, 3])
    expect(runningTask.telemetry[1]).toEqual({
      sequence: 2,
      runId: 'run-real-01',
      state: 'running',
      stage: 'source.acquire',
      progress: 0.5,
      message: '正在下载媒体',
      metrics: { downloaded_bytes: 2_048, total_bytes: 4_096, speed_bps: null },
      createdAt: '2026-07-28T12:00:01Z',
    })
    expect(runningTask.stages.find(stage => stage.id === 'acquire')).toMatchObject({
      durationSeconds: 1.25,
      artifactCount: 1,
      metrics: { downloaded_bytes: 4_096, speed_bps: null, used_browser_profile: true },
      warnings: ['Source selected a lower authenticated format.'],
      outputArtifacts: [
        {
          stage: 'source.acquire',
          kind: 'media',
          relativePath: 'source/source.mp4',
          sha256: 'source-sha256',
          sizeBytes: 4_096,
          mediaType: 'video/mp4',
          createdAt,
        },
      ],
    })
    expect(runningTask.stages.find(stage => stage.id === 'speech')).toMatchObject({
      durationSeconds: 2.5,
      artifactCount: 2,
      metrics: {
        'audio.extract.segment_count': 1,
        'audio.extract.sample_rate_hz': 16_000,
        'audio.asr.segment_count': 24,
        'audio.asr.secondary_window_count': 2,
      },
      warnings: ['Primary ASR retained one low-confidence segment.'],
    })

    useStudioStore.getState().refreshMaterials('run-real-01')
    await vi.waitFor(() =>
      expect(useStudioStore.getState().tasks[0]?.materials).toHaveLength(1),
    )
    expect(useStudioStore.getState().tasks[0]?.materials[0]).toEqual({
      id: 'material-text001',
      runId: 'run-real-01',
      kind: 'text',
      title: '评论区补充资料',
      originalName: null,
      mediaType: 'text/markdown; charset=utf-8',
      artifact: {
        kind: 'supporting',
        relativePath: 'supporting/files/material-text001/text.md',
        sha256: 'a'.repeat(64),
        sizeBytes: 24,
        mediaType: null,
        createdAt,
      },
      sha256: 'a'.repeat(64),
      sizeBytes: 24,
      textContent: '一条需要一起整理的资料。',
      startUs: 1_000_000,
      endUs: 2_000_000,
      status: 'active',
      createdAt,
      deletedAt: null,
      storage: 'run-artifact',
    })

    useStudioStore.getState().addTextMaterial('run-real-01', {
      title: '手工文字',
      content: '补充事实',
      startUs: 3_000_000,
      endUs: 4_000_000,
    })
    await vi.waitFor(() =>
      expect(useStudioStore.getState().tasks[0]?.materials).toHaveLength(2),
    )
    const textRequest = requests.find(
      request =>
        request.url === '/api/runs/run-real-01/materials/text' &&
        request.method === 'POST',
    )
    expect(JSON.parse(textRequest?.body ?? '{}')).toEqual({
      title: '手工文字',
      content: '补充事实',
      start_us: 3_000_000,
      end_us: 4_000_000,
    })

    const image = new File([new Uint8Array([1, 2, 3, 4])], 'reference.png', {
      type: 'image/png',
    })
    useStudioStore.getState().addFileMaterial('run-real-01', image, {
      title: '手工图片',
      startUs: 5_000_000,
      endUs: 6_000_000,
    })
    await vi.waitFor(() =>
      expect(useStudioStore.getState().tasks[0]?.materials).toHaveLength(3),
    )
    const fileRequest = requests.find(request =>
      request.url.startsWith('/api/runs/run-real-01/materials/files?'),
    )
    const fileUrl = new URL(`http://local${fileRequest?.url}`)
    expect(Object.fromEntries(fileUrl.searchParams)).toEqual({
      title: '手工图片',
      start_us: '5000000',
      end_us: '6000000',
    })
    const fileBody = fileRequest?.rawBody as FormData
    expect((fileBody.get('file') as File).name).toBe('reference.png')
    expect(
      useStudioStore
        .getState()
        .tasks[0]?.materials.find(material => material.id === 'material-addedimage'),
    ).toMatchObject({
      kind: 'image',
      originalName: 'reference.png',
      storage: 'run-artifact',
      artifact: { mediaType: 'image/png' },
    })

    useStudioStore.getState().deleteMaterial('run-real-01', 'material-text001')
    await vi.waitFor(() =>
      expect(
        useStudioStore
          .getState()
          .tasks[0]?.materials.some(material => material.id === 'material-text001'),
      ).toBe(false),
    )
    materialListFails = true
    operationListFails = true
    evidenceListFails = true
    // refreshTasks releases its in-flight guard after applying the merged run.
    await new Promise(resolve => window.setTimeout(resolve, 0))

    currentJob = {
      ...runningJob,
      state: 'completed',
      stage: 'render.outputs',
      progress: 1,
      message: '笔记已完成',
      finished_at: createdAt,
      events: [
        runningJob.events[2],
        {
          sequence: 4,
          run_id: 'run-real-01',
          state: 'completed',
          stage: 'completed',
          progress: 1,
          message: '笔记已完成',
          metrics: { artifact_count: 4 },
          created_at: '2026-07-28T12:00:03Z',
        },
      ],
    }
    currentRun = {
      ...runningRun,
      status: 'completed',
      stages: {
        ...runningRun.stages,
        'notes.compose': {
          stage_name: 'notes.compose',
          status: 'completed',
          outputs: [
            {
              kind: 'note',
              relative_path: 'notes/document.json',
              sha256: 'test',
              size_bytes: 1,
              created_at: createdAt,
            },
          ],
          warnings: [],
          metrics: {},
        },
      },
    }
    useStudioStore.getState().refreshTasks()
    await vi.waitFor(() => expect(useStudioStore.getState().tasks[0]?.note).toBeDefined())
    expect(useStudioStore.getState().tasks[0]?.note?.sections[0]?.claims[0]).toMatchObject({
      id: 'fact-late',
      timeSeconds: 9,
      evidenceIds: ['evidence-late'],
    })
    expect(useStudioStore.getState().tasks[0]).toBeDefined()
    expect(useStudioStore.getState().tasks[0]?.materials).toHaveLength(2)
    expect(useStudioStore.getState().tasks[0]?.operations).toEqual([])
    expect(useStudioStore.getState().tasks[0]?.evidence).toHaveLength(2)
    expect(
      requests.some(
        request =>
          request.url === '/api/runs/run-real-01/materials' &&
          request.method === 'GET',
      ),
    ).toBe(true)
    expect(
      requests.some(
        request =>
          request.url === '/api/runs/run-real-01/operations' &&
          request.method === 'GET',
      ),
    ).toBe(true)
    expect(
      requests.some(
        request =>
          request.url === '/api/runs/run-real-01/evidence' &&
          request.method === 'GET',
      ),
    ).toBe(true)
    expect(
      useStudioStore.getState().tasks[0]?.telemetry.map(sample => sample.sequence),
    ).toEqual([1, 2, 3, 4])
    expect(useStudioStore.getState().tasks[0]?.telemetry.at(-1)).toMatchObject({
      sequence: 4,
      state: 'completed',
      metrics: { artifact_count: 4 },
    })
    const composedStage = useStudioStore
      .getState()
      .tasks[0]?.stages.find(stage => stage.id === 'draft')
    expect(composedStage?.metrics).toEqual({})
    expect(composedStage?.durationSeconds).toBeUndefined()

    operationListFails = false
    evidenceListFails = false
    useStudioStore.getState().runVisionRework('run-real-01', {
      startSeconds: 1.25,
      endSeconds: 2.5,
      mode: 'fixed_interval',
      intervalSeconds: 0.25,
      runOcr: true,
    })
    await vi.waitFor(() =>
      expect(useStudioStore.getState().tasks[0]?.operations).toHaveLength(1),
    )
    await vi.waitFor(() =>
      expect(useStudioStore.getState().tasks[0]?.evidence[0]?.id).toBe(
        'evidence-operation-1',
      ),
    )
    const visionReworkRequest = requests.find(
      request =>
        request.url === '/api/runs/run-real-01/operations' &&
        request.method === 'POST',
    )
    expect(JSON.parse(visionReworkRequest?.body ?? '{}')).toEqual({
      kind: 'vision_rescan',
      range: { start_us: 1_250_000, end_us: 2_500_000 },
      sampling: { mode: 'fixed_interval', interval_us: 250_000 },
      run_ocr: true,
    })
    expect(useStudioStore.getState().tasks[0]?.operations[0]).toMatchObject({
      id: 'operation-1',
      kind: 'vision_rescan',
      source: 'backend',
      status: 'completed',
      startSeconds: 1.25,
      endSeconds: 2.5,
      intervalSeconds: 0.25,
      revisionId: 'revision-1',
    })
    expect(useStudioStore.getState().tasks[0]?.evidence[0]).toEqual({
      id: 'evidence-operation-1',
      kind: 'ocr',
      startSeconds: 1.25,
      endSeconds: 2.5,
      label: '屏幕文字',
      rawText: '视觉返工刷新后的屏幕文字。',
      confidence: 0.94,
      provider: 'vision-provider · vision-v2',
    })
    expect(useStudioStore.getState().notice).toContain('报告尚未重生成')

    const evidenceBeforeFailedRecord = structuredClone(
      useStudioStore.getState().tasks[0]!.evidence,
    )
    failedOperationKind = 'vision_rescan'
    useStudioStore.getState().runVisionRework('run-real-01', {
      startSeconds: 5,
      endSeconds: 6,
      mode: 'adaptive',
      runOcr: false,
    })
    await vi.waitFor(() =>
      expect(useStudioStore.getState().tasks[0]?.operations).toHaveLength(2),
    )
    expect(useStudioStore.getState().tasks[0]?.operations.at(-1)).toMatchObject({
      id: 'operation-2',
      status: 'failed',
      errorType: 'RuntimeError',
      detail: 'worker exited before producing evidence',
    })
    expect(useStudioStore.getState().tasks[0]?.evidence).toEqual(
      evidenceBeforeFailedRecord,
    )
    expect(useStudioStore.getState().notice).toContain('返工执行失败')
    failedOperationKind = undefined

    const evidenceBeforeConflict = structuredClone(
      useStudioStore.getState().tasks[0]!.evidence,
    )
    const operationCountBeforeConflict =
      useStudioStore.getState().tasks[0]!.operations.length
    conflictingOperationKind = 'asr_retranscribe'
    useStudioStore.getState().runAsrRework('run-real-01', {
      range: { startSeconds: 3, endSeconds: 4.5 },
      languageHints: ['zh-CN', 'en'],
    })
    await vi.waitFor(() =>
      expect(useStudioStore.getState().notice).toContain(
        '缺少所需的 ASR/OCR 模型后端',
      ),
    )
    expect(useStudioStore.getState().tasks[0]?.evidence).toEqual(
      evidenceBeforeConflict,
    )
    expect(useStudioStore.getState().tasks[0]?.operations).toHaveLength(
      operationCountBeforeConflict,
    )

    useStudioStore.getState().saveProviderSecret('cloud-one', 'new-local-credential')
    await vi.waitFor(() =>
      expect(
        requests.some(
          request =>
            request.url === '/api/providers/cloud-one/secret' && request.method === 'PUT',
        ),
      ).toBe(true),
    )
    const secretWrite = requests.find(
      request => request.url === '/api/providers/cloud-one/secret' && request.method === 'PUT',
    )
    expect(JSON.parse(secretWrite?.body ?? '{}')).toEqual({ secret: 'new-local-credential' })
    await vi.waitFor(() =>
      expect(useStudioStore.getState().notice).toContain('Windows 凭据库'),
    )

    useStudioStore.getState().testProvider('cloud-one')
    await vi.waitFor(() =>
      expect(useStudioStore.getState().providers[0]?.status).toBe('connected'),
    )
    expect(useStudioStore.getState().notice).toBe('Provider reachable')

    useStudioStore.getState().discoverProviderModels('cloud-one')
    await vi.waitFor(() =>
      expect(useStudioStore.getState().providerDiscoveryStatus).toBe('ready'),
    )
    expect(useStudioStore.getState().discoveredModels).toEqual([
      {
        modelId: 'notes-writer',
        displayName: 'Notes writer',
        contextWindow: 128_000,
      },
    ])
    expect(useStudioStore.getState().notice).toContain('不包含能力声明')

    useStudioStore.getState().createModel({
      providerId: 'cloud-one',
      modelId: 'notes-writer',
      displayName: 'Notes writer',
      contextWindow: 128_000,
      capabilities: ['text', 'long_context'],
      locality: 'cloud',
    })
    await vi.waitFor(() =>
      expect(
        useStudioStore
          .getState()
          .models.some(model => model.modelId === 'notes-writer'),
      ).toBe(true),
    )
    const modelSave = requests.find(
      request =>
        request.url === '/api/providers' &&
        request.method === 'PUT' &&
        request.body?.includes('notes-writer'),
    )
    expect(
      JSON.parse(modelSave?.body ?? '{}').models['cloud-one-notes-writer'].capabilities,
    ).toEqual(['text', 'long_context'])

    useStudioStore.getState().bindRole('asr.primary', 'asr-main')
    await vi.waitFor(() =>
      expect(
        requests.some(request => request.url === '/api/providers' && request.method === 'PUT'),
      ).toBe(true),
    )
    const providerSave = requests.find(
      request => request.url === '/api/providers' && request.method === 'PUT',
    )
    expect(JSON.parse(providerSave?.body ?? '{}').roles['asr.primary']).toMatchObject({
      primary_model_id: 'asr-main',
      fallback_model_ids: [],
    })
  })

  it('merges paged persisted events with truncated realtime telemetry and advances the cursor', async () => {
    listedRuns = [runningRun]
    currentJob = {
      ...runningJob,
      events: [runningJob.events[2]],
    }
    historicalEventPageSize = 2
    historicalCorruptLineCount = 3
    historicalJobEvents = [
      ...runningJob.events,
      {
        sequence: 4,
        run_id: 'run-real-01',
        state: 'running',
        stage: 'notes.compose',
        progress: 0.8,
        message: '正在整理证据链',
        error_type: 'WorkerProtocolError',
        metrics: { evidence_count: 24 },
        created_at: '2026-07-28T12:00:03Z',
      },
    ]

    useStudioStore.getState().initializeBackend()
    await vi.waitFor(() =>
      expect(
        useStudioStore.getState().tasks[0]?.telemetry.map(sample => sample.sequence),
      ).toEqual([1, 2, 3, 4]),
    )
    expect(useStudioStore.getState().tasks[0]?.telemetry.at(-1)).toMatchObject({
      sequence: 4,
      errorType: 'WorkerProtocolError',
      metrics: { evidence_count: 24 },
    })
    expect(useStudioStore.getState().tasks[0]?.eventLog).toEqual({
      available: true,
      corruptLineCount: 3,
    })
    expect(
      requests
        .filter(request => request.url.includes('/api/runs/run-real-01/events?'))
        .map(request => request.url),
    ).toEqual([
      '/api/runs/run-real-01/events?after_sequence=0&limit=500',
      '/api/runs/run-real-01/events?after_sequence=2&limit=500',
    ])

    historicalJobEvents = [
      ...historicalJobEvents,
      {
        sequence: 5,
        run_id: 'run-real-01',
        state: 'running',
        stage: 'render.outputs',
        progress: 0.95,
        message: '正在写入 HTML',
        metrics: { artifact_count: 2 },
        created_at: '2026-07-28T12:00:04Z',
      },
    ]
    await new Promise(resolve => window.setTimeout(resolve, 0))
    useStudioStore.getState().refreshTasks()
    await vi.waitFor(() =>
      expect(
        useStudioStore.getState().tasks[0]?.telemetry.map(sample => sample.sequence),
      ).toEqual([1, 2, 3, 4, 5]),
    )
    expect(
      requests.some(
        request =>
          request.url ===
          '/api/runs/run-real-01/events?after_sequence=4&limit=500',
      ),
    ).toBe(true)
    expect(useStudioStore.getState().tasks[0]?.eventLog).toEqual({
      available: true,
      corruptLineCount: 3,
    })
  })

  it('preserves a successful empty response as an unavailable persisted log', async () => {
    listedRuns = [runningRun]
    historicalJobEvents = []
    historicalLogAvailable = false

    useStudioStore.getState().initializeBackend()
    await vi.waitFor(() =>
      expect(useStudioStore.getState().tasks[0]?.eventLog).toEqual({
        available: false,
        corruptLineCount: 0,
      }),
    )
    expect(useStudioStore.getState().tasks[0]?.telemetry).toEqual(runningJob.events.map(
      event => expect.objectContaining({ sequence: event.sequence }),
    ))
  })

  it('keeps the first source probe alive across scope changes and estimates the latest scope', async () => {
    useStudioStore.getState().initializeBackend()
    await vi.waitFor(() => expect(useStudioStore.getState().backend.mode).toBe('real'))
    const gate = deferred<void>()
    probeGate = gate.promise
    const store = useStudioStore.getState()
    store.setDraftInput('https://www.youtube.com/watch?v=scope-during-probe')
    store.probeSource()
    expect(useStudioStore.getState().draft.status).toBe('probing')

    store.setProcessingScope('audio_only')
    expect(useStudioStore.getState().draft).toMatchObject({
      status: 'probing',
      processingScope: 'audio_only',
    })
    gate.resolve()

    await vi.waitFor(() => expect(useStudioStore.getState().draft.status).toBe('ready'))
    await vi.waitFor(() =>
      expect(useStudioStore.getState().processingEstimateStatus).toBe('ready'),
    )
    expect(requests.filter(request => request.url === '/api/sources/probe')).toHaveLength(1)
    const estimates = requests
      .filter(request => request.url === '/api/estimate')
      .map(request => JSON.parse(request.body ?? '{}'))
    expect(estimates).toHaveLength(3)
    expect(estimates.every(request => request.processing_scope === 'audio_only')).toBe(true)
  })

  it('keeps submission locked and ignores stale draft completion updates', async () => {
    useStudioStore.getState().initializeBackend()
    await vi.waitFor(() => expect(useStudioStore.getState().backend.mode).toBe('real'))
    await vi.waitFor(() => expect(useStudioStore.getState().tasks).toHaveLength(1))
    const store = useStudioStore.getState()
    store.setDraftInput('https://www.youtube.com/watch?v=deferred-submit')
    store.probeSource()
    await vi.waitFor(() => expect(useStudioStore.getState().draft.status).toBe('ready'))

    const gate = deferred<void>()
    submissionGate = gate.promise
    currentRun = { ...runningRun, processing_scope: 'audio_only' }
    useStudioStore.getState().createTask()
    await vi.waitFor(() =>
      expect(useStudioStore.getState().submissionInFlight).toBe(true),
    )
    expect(useStudioStore.getState().draft.status).toBe('submitting')

    useStudioStore.getState().setProcessingScope('audio_only')
    expect(useStudioStore.getState().draft.processingScope).toBe('audio_visual')
    useStudioStore
      .getState()
      .setDraftInput('https://www.youtube.com/watch?v=newer-draft')
    useStudioStore.getState().createTask()
    expect(
      requests.filter(
        request => request.url === '/api/jobs' && request.method === 'POST',
      ),
    ).toHaveLength(1)

    gate.resolve()
    await vi.waitFor(() => expect(useStudioStore.getState().submissionInFlight).toBe(false))
    await vi.waitFor(() =>
      expect(
        useStudioStore.getState().tasks.filter(task => task.id === 'run-real-01'),
      ).toHaveLength(1),
    )
    expect(useStudioStore.getState().tasks[0].processingScope).toBe('audio_only')
    expect(useStudioStore.getState().draft).toMatchObject({
      input: 'https://www.youtube.com/watch?v=newer-draft',
      status: 'stale',
      processingScope: 'audio_visual',
    })
    expect(useStudioStore.getState().view).toBe('create')
    expect(
      requests.filter(
        request => request.url === '/api/jobs' && request.method === 'POST',
      ),
    ).toHaveLength(1)
  })

  it('treats a legacy health response without capabilities as full-scope only', async () => {
    includeProcessingScopeCapability = false
    useStudioStore.getState().initializeBackend()
    await vi.waitFor(() => expect(useStudioStore.getState().backend.mode).toBe('real'))

    expect(useStudioStore.getState().backend.capabilities).toEqual([])
    useStudioStore.getState().setProcessingScope('audio_only')
    expect(useStudioStore.getState().draft.processingScope).toBe('audio_visual')
    expect(useStudioStore.getState().notice).toContain('后端版本不支持仅音频')
  })

  it('uses an honest CPU fallback when an older backend omits acceleration capabilities', async () => {
    includeAcceleration = false

    useStudioStore.getState().initializeBackend()
    await vi.waitFor(() => expect(useStudioStore.getState().backend.mode).toBe('real'))

    expect(useStudioStore.getState().systemReport?.acceleration).toMatchObject({
      asr: {
        cudaAvailable: false,
        deviceCount: 0,
        reason: expect.stringContaining('未返回 ASR 加速能力'),
      },
      ocr: {
        cudaAvailable: false,
        deviceCount: 0,
        reason: expect.stringContaining('未返回 OCR 加速能力'),
      },
    })
  })

  it('keeps a verified source usable when one machine estimate falls back', async () => {
    failedEstimateMode = 'accurate'
    useStudioStore.getState().initializeBackend()
    await vi.waitFor(() => expect(useStudioStore.getState().backend.mode).toBe('real'))

    const store = useStudioStore.getState()
    store.setDraftInput('https://www.youtube.com/watch?v=real-api-test')
    store.probeSource()

    await vi.waitFor(() =>
      expect(useStudioStore.getState().processingEstimateStatus).toBe('partial'),
    )
    expect(useStudioStore.getState().draft.status).toBe('ready')
    expect(useStudioStore.getState().processingEstimates.fast).toBeDefined()
    expect(useStudioStore.getState().processingEstimates.balanced).toBeDefined()
    expect(useStudioStore.getState().processingEstimates.accurate).toBeUndefined()
    expect(useStudioStore.getState().processingEstimateError).toContain('accurate')
  })

  it('maps safe failure diagnostics, completed stages, real artifacts, and retry availability', async () => {
    currentJob = {
      ...runningJob,
      state: 'failed',
      stage: 'audio.asr',
      progress: 0.45,
      message: '语音模型初始化失败',
      error_type: 'ModelLoadError',
      finished_at: createdAt,
      events: [
        ...runningJob.events,
        {
          sequence: 4,
          run_id: 'run-real-01',
          state: 'failed',
          stage: 'audio.asr',
          progress: 0.45,
          message: '语音模型初始化失败',
          metrics: {},
          created_at: '2026-07-28T12:00:03Z',
        },
      ],
    }
    currentRun = {
      ...runningRun,
      status: 'failed',
      stages: {
        ...runningRun.stages,
        'audio.asr': {
          ...runningRun.stages['audio.asr'],
          status: 'failed',
          error: 'ModelLoadError',
        },
      },
    }
    listedRuns = [currentRun]

    useStudioStore.getState().initializeBackend()
    await vi.waitFor(() => expect(useStudioStore.getState().backend.mode).toBe('real'))
    await vi.waitFor(() => expect(useStudioStore.getState().tasks[0]?.status).toBe('failed'))

    const failed = useStudioStore.getState().tasks[0]
    expect(failed?.failure).toEqual({
      failedStages: [{ stage: 'audio.asr', errorType: 'ModelLoadError' }],
      completedStages: ['source.acquire', 'audio.extract'],
      errorType: 'ModelLoadError',
      message: '语音模型初始化失败',
    })
    expect(failed?.recovery).toMatchObject({
      canRetry: false,
      strategy: 'manual_recreate',
    })
    expect(failed?.stages.find(stage => stage.id === 'speech')).toMatchObject({
      status: 'failed',
      backendStages: ['audio.extract', 'audio.asr'],
      errorTypes: ['ModelLoadError'],
      artifactCount: 2,
    })
    expect(
      failed?.stages
        .flatMap(stage => stage.outputArtifacts)
        .some(artifact => artifact.relativePath === 'source/source.mp4'),
    ).toBe(true)
  })

  it('offers a new-run retry only while the original submission remains in this session', async () => {
    useStudioStore.getState().initializeBackend()
    await vi.waitFor(() => expect(useStudioStore.getState().backend.mode).toBe('real'))

    const store = useStudioStore.getState()
    store.setDraftInput('https://www.youtube.com/watch?v=real-api-test')
    store.probeSource()
    await vi.waitFor(() => expect(useStudioStore.getState().draft.status).toBe('ready'))
    store.createTask()
    await vi.waitFor(() => expect(useStudioStore.getState().tasks[0]?.id).toBe('run-real-01'))

    currentJob = {
      ...runningJob,
      state: 'failed',
      stage: 'audio.asr',
      message: '语音模型初始化失败',
      error_type: 'ModelLoadError',
      finished_at: createdAt,
    }
    currentRun = {
      ...runningRun,
      status: 'failed',
      stages: {
        ...runningRun.stages,
        'audio.asr': {
          ...runningRun.stages['audio.asr'],
          status: 'failed',
          error: 'ModelLoadError',
        },
      },
    }
    await new Promise(resolve => window.setTimeout(resolve, 0))
    useStudioStore.getState().refreshTasks()

    await vi.waitFor(() =>
      expect(useStudioStore.getState().tasks[0]?.status).toBe('failed'),
    )
    expect(useStudioStore.getState().tasks[0]?.recovery).toMatchObject({
      canRetry: true,
      strategy: 'new_run',
    })
    expect(useStudioStore.getState().tasks[0]?.recovery?.reason).toContain(
      '暂未提供同一任务从失败阶段续跑',
    )
  })

  it('waits for every recommended runtime install before rechecking and submitting', async () => {
    const blocked = blockedRuntimePreflight()
    blocked.requirements.push({
      requirement_id: 'tool.ffmpeg',
      capability_id: 'tool.ffmpeg',
      required: true,
      state: 'blocked',
      selected_instance_id: null,
      selected_source: null,
      detail: '当前任务需要 FFmpeg。',
    })
    blocked.missing_required.push('tool.ffmpeg')
    blocked.recommended_actions.push({
      package_id: 'video2notes-media-tools',
      version: '1.0.0',
      display_name: '媒体工具运行时',
      requirement_ids: ['tool.ffmpeg'],
      archive_file_name: 'video2notes-media-tools-1.0.0.zip',
      source_url: 'https://downloads.example.test/video2notes-media-tools-1.0.0.zip',
      download_size_bytes: 128 * 1024 ** 2,
      download_part_count: 1,
      installed_size_bytes: 256 * 1024 ** 2,
      install_root: 'D:/data/runtime-packages/video2notes-media-tools/1.0.0',
      supported_devices: ['cpu'],
    })
    blocked.estimated_download_bytes += 128 * 1024 ** 2
    blocked.estimated_installed_bytes += 256 * 1024 ** 2
    preflightResponses = [blocked, readyRuntimePreflight()]

    useStudioStore.getState().initializeBackend()
    await vi.waitFor(() => expect(useStudioStore.getState().backend.mode).toBe('real'))
    useStudioStore
      .getState()
      .setDraftInput('https://www.youtube.com/watch?v=runtime-preflight-success')
    useStudioStore.getState().probeSource()
    await vi.waitFor(() => expect(useStudioStore.getState().draft.status).toBe('ready'))

    useStudioStore.getState().createTask()
    await vi.waitFor(() =>
      expect(useStudioStore.getState().jobPreflightDialogOpen).toBe(true),
    )
    expect(
      requests.filter(
        request => request.url === '/api/jobs' && request.method === 'POST',
      ),
    ).toHaveLength(0)

    useStudioStore.getState().installPreflightRequirements()
    await vi.waitFor(() =>
      expect(useStudioStore.getState().preflightRuntimeOperationIds).toHaveLength(2),
    )
    expect(useStudioStore.getState().preflightAutoRetryPending).toBe(true)

    runtimePackageOperations = runtimePackageOperations.map((operation, index) =>
      index === 0
        ? {
            ...operation,
            status: 'succeeded',
            phase: 'completed',
            progress: 1,
            finished_at_utc: '2026-07-28T12:31:01Z',
          }
        : {
            ...operation,
            status: 'running',
            phase: 'extracting',
            progress: 0.7,
            started_at_utc: '2026-07-28T12:30:02Z',
          },
    )
    useStudioStore.getState().refreshRuntimePackages()
    await vi.waitFor(() =>
      expect(useStudioStore.getState().runtimePackages?.operations).toEqual(
        expect.arrayContaining([
          expect.objectContaining({ status: 'succeeded' }),
          expect.objectContaining({ status: 'running' }),
        ]),
      ),
    )
    expect(preflightRequestCount).toBe(1)
    expect(
      requests.filter(
        request => request.url === '/api/jobs' && request.method === 'POST',
      ),
    ).toHaveLength(0)

    runtimePackageOperations = runtimePackageOperations.map(operation => ({
      ...operation,
      status: 'succeeded',
      phase: 'completed',
      progress: 1,
      finished_at_utc: '2026-07-28T12:31:02Z',
    }))
    useStudioStore.getState().refreshRuntimePackages()

    await vi.waitFor(() => expect(preflightRequestCount).toBe(2))
    await vi.waitFor(() =>
      expect(
        requests.filter(
          request => request.url === '/api/jobs' && request.method === 'POST',
        ),
      ).toHaveLength(1),
    )
    expect(useStudioStore.getState()).toMatchObject({
      preflightRuntimeOperationIds: [],
      preflightAutoRetryPending: false,
      jobPreflightDialogOpen: false,
    })
    const requestOrder = requests.map(request => `${request.method} ${request.url}`)
    const finalInventoryIndex = requestOrder.lastIndexOf('GET /api/runtime-packages')
    const secondPreflightIndex = requestOrder.lastIndexOf('POST /api/jobs/preflight')
    const submitIndex = requestOrder.lastIndexOf('POST /api/jobs')
    expect(finalInventoryIndex).toBeLessThan(secondPreflightIndex)
    expect(secondPreflightIndex).toBeLessThan(submitIndex)
  })

  it('keeps the dependency dialog open when a preflight install fails', async () => {
    preflightResponses = [blockedRuntimePreflight()]
    useStudioStore.getState().initializeBackend()
    await vi.waitFor(() => expect(useStudioStore.getState().backend.mode).toBe('real'))
    useStudioStore
      .getState()
      .setDraftInput('https://www.youtube.com/watch?v=runtime-preflight-failure')
    useStudioStore.getState().probeSource()
    await vi.waitFor(() => expect(useStudioStore.getState().draft.status).toBe('ready'))
    useStudioStore.getState().createTask()
    await vi.waitFor(() =>
      expect(useStudioStore.getState().jobPreflightDialogOpen).toBe(true),
    )

    useStudioStore.getState().installPreflightRequirements()
    await vi.waitFor(() =>
      expect(useStudioStore.getState().preflightAutoRetryPending).toBe(true),
    )
    runtimePackageOperations = runtimePackageOperations.map(operation => ({
      ...operation,
      status: 'failed',
      phase: 'failed',
      progress: 0.42,
      finished_at_utc: '2026-07-28T12:32:00Z',
      detail: '归档哈希不匹配。',
      error_code: 'archive_hash_mismatch',
    }))
    useStudioStore.getState().refreshRuntimePackages()

    await vi.waitFor(() =>
      expect(useStudioStore.getState().preflightAutoRetryPending).toBe(false),
    )
    expect(useStudioStore.getState()).toMatchObject({
      jobPreflightDialogOpen: true,
      preflightRuntimeOperationIds: [],
      runtimePackageError: expect.stringContaining('归档哈希不匹配'),
      notice: expect.stringContaining('依赖准备未完成'),
    })
    expect(preflightRequestCount).toBe(1)
    expect(
      requests.filter(
        request => request.url === '/api/jobs' && request.method === 'POST',
      ),
    ).toHaveLength(0)
  })

  it('loads component inventory and prepares the recommended local models in one request', async () => {
    useStudioStore.getState().initializeBackend()
    await vi.waitFor(() => expect(useStudioStore.getState().backend.mode).toBe('real'))

    expect(useStudioStore.getState().componentReport).toMatchObject({
      hardwareTier: 'gpu_12gb',
      recommendation: {
        asrComponentId: 'asr-faster-whisper-large-v3',
        ocrComponentId: 'ocr-paddle-ppocrv5-server',
      },
      inventory: {
        ready: false,
        capabilities: { core: true, asr: false, ocr: false },
      },
    })
    expect(useStudioStore.getState().selectedComponentIds).toEqual([
      'asr-faster-whisper-large-v3',
      'ocr-paddle-ppocrv5-server',
    ])

    useStudioStore.getState().prepareLocalComponents()
    expect(useStudioStore.getState().componentPreparationStatus).toBe('preparing')
    await vi.waitFor(() =>
      expect(useStudioStore.getState().componentPreparationStatus).toBe('success'),
    )

    const prepareRequest = requests.find(
      request =>
        request.url === '/api/components/prepare' && request.method === 'POST',
    )
    expect(JSON.parse(prepareRequest?.body ?? '{}')).toEqual({
      component_ids: [],
      hardware_tier: 'gpu_12gb',
      activate: true,
    })
    expect(useStudioStore.getState()).toMatchObject({
      componentPreparationActivated: true,
      selectedComponentIds: [],
      componentReport: { inventory: { ready: true } },
    })
    expect(useStudioStore.getState().componentPreparationResults).toEqual([
      expect.objectContaining({
        componentId: 'asr-faster-whisper-large-v3',
        status: 'prepared',
      }),
      expect.objectContaining({
        componentId: 'ocr-paddle-ppocrv5-server',
        status: 'prepared',
      }),
    ])
    expect(
      requests.filter(
        request => request.url === '/api/providers' && request.method === 'GET',
      ),
    ).toHaveLength(2)
  })
})

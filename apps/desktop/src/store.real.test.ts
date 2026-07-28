import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ApiJobSnapshot, ApiRunManifest } from './api'
import { useStudioStore } from './store'

const json = (value: unknown, status = 200): Response =>
  new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })

const createdAt = '2026-07-28T12:00:00Z'

const registryPayload = {
  schema_version: 1,
  providers: {
    'cloud-one': {
      id: 'cloud-one',
      display_name: 'Cloud One',
      kind: 'openai_compatible' as const,
      base_url: 'https://models.example.test/v1',
      endpoint_style: 'chat_completions' as const,
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
      capabilities: ['asr', 'timestamped_segments'],
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

const runningJob = {
  run_id: 'run-real-01',
  state: 'running' as const,
  stage: 'audio.asr',
  progress: 0.45,
  message: '正在校准语音时间戳',
  queued_at: createdAt,
  started_at: createdAt,
  events: [],
}

const runningRun = {
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
  status: 'running' as const,
  created_at: createdAt,
  updated_at: createdAt,
  stages: {
    'source.acquire': {
      stage_name: 'source.acquire',
      status: 'completed' as const,
      outputs: [],
      warnings: [],
      metrics: {},
    },
  },
  warnings: [],
}

describe('studio store against the real loopback API contract', () => {
  const fetchMock = vi.fn()
  const requests: Array<{ url: string; method: string; body?: string; token?: string }> = []
  let currentJob: ApiJobSnapshot = runningJob
  let currentRun: ApiRunManifest = runningRun

  beforeEach(() => {
    vi.unstubAllEnvs()
    vi.stubEnv('VITE_VIDEO2NOTES_API_URL', 'http://127.0.0.1:43119')
    vi.stubEnv('VITE_VIDEO2NOTES_API_TOKEN', 'desktop-session-token')
    requests.length = 0
    currentJob = runningJob
    currentRun = runningRun
    fetchMock.mockReset()
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input))
      const method = init?.method ?? 'GET'
      const headers = new Headers(init?.headers)
      requests.push({
        url: `${url.pathname}${url.search}`,
        method,
        body: typeof init?.body === 'string' ? init.body : undefined,
        token: headers.get('X-Video2Notes-Token') ?? undefined,
      })

      if (url.pathname === '/api/health') {
        return Promise.resolve(json({ status: 'ok', version: '0.1.0', scope: 'loopback' }))
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
            recommended_tier: 'high',
            plans: {},
          }),
        )
      }
      if (url.pathname === '/api/runtime') {
        return Promise.resolve(json({ injected: true, warnings: [] }))
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
      if (url.pathname === '/api/runs') return Promise.resolve(json([]))
      if (url.pathname === '/api/sources/probe') {
        return Promise.resolve(
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
          }),
        )
      }
      if (url.pathname === '/api/jobs' && method === 'POST') {
        return Promise.resolve(json({ run: currentRun, job: currentJob, runtime_warnings: [] }))
      }
      if (url.pathname === '/api/jobs' && method === 'GET') return Promise.resolve(json([currentJob]))
      if (url.pathname === '/api/jobs/run-real-01/result') {
        return Promise.resolve(json({ run: currentRun, job: currentJob, runtime_warnings: [] }))
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
        status: 'idle',
        sourceKind: 'url',
        authKind: 'none',
        browser: 'edge',
        profile: '',
        cookieFile: '',
        languageHints: '',
        includeScreenshots: true,
        generatePdf: true,
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

    const store = useStudioStore.getState()
    store.setDraftInput('https://www.youtube.com/watch?v=real-api-test')
    store.setDraftAuthKind('browser_profile')
    store.setDraftBrowser('edge')
    store.setDraftProfile('Default')
    store.setLanguageHints('zh-CN, en')
    store.probeSource()
    await vi.waitFor(() => expect(useStudioStore.getState().draft.status).toBe('ready'))

    const probe = requests.find(request => request.url === '/api/sources/probe')
    expect(probe).toMatchObject({ method: 'POST', token: 'desktop-session-token' })
    expect(JSON.parse(probe?.body ?? '{}')).toEqual({
      source: { kind: 'url', value: 'https://www.youtube.com/watch?v=real-api-test' },
      auth: { kind: 'browser_profile', browser: 'edge', profile: 'Default' },
      policy: expect.objectContaining({ mode: 'accurate', max_height: 1080 }),
    })

    useStudioStore.getState().createTask()
    await vi.waitFor(() => expect(useStudioStore.getState().tasks).toHaveLength(1))
    const submission = requests.find(request => request.url === '/api/jobs' && request.method === 'POST')
    expect(JSON.parse(submission?.body ?? '{}')).toMatchObject({
      quality_mode: 'balanced',
      language_hints: ['zh-CN', 'en'],
      include_screenshots: true,
      generate_pdf: true,
    })

    useStudioStore.getState().refreshTasks()
    await vi.waitFor(() =>
      expect(requests.some(request => request.url === '/api/jobs/run-real-01/result')).toBe(true),
    )
    await vi.waitFor(() =>
      expect(useStudioStore.getState().tasks[0]?.lastMessage).toBe('正在校准语音时间戳'),
    )
    expect(useStudioStore.getState().tasks[0]).toMatchObject({
      id: 'run-real-01',
      status: 'running',
      lastMessage: '正在校准语音时间戳',
      realBackend: true,
    })
    // refreshTasks releases its in-flight guard after applying the merged run.
    await new Promise(resolve => window.setTimeout(resolve, 0))

    currentJob = {
      ...runningJob,
      state: 'completed',
      stage: 'render.outputs',
      progress: 1,
      message: '笔记已完成',
      finished_at: createdAt,
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

    useStudioStore.getState().testProvider('cloud-one')
    await vi.waitFor(() =>
      expect(useStudioStore.getState().providers[0]?.status).toBe('connected'),
    )
    expect(useStudioStore.getState().notice).toBe('Provider reachable')

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
})

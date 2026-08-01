import { beforeEach, describe, expect, it, vi } from 'vitest'

const { convertFileSrcMock, invokeMock } = vi.hoisted(() => ({
  convertFileSrcMock: vi.fn((path: string) => `asset://localhost/${path}`),
  invokeMock: vi.fn(),
}))

vi.mock('@tauri-apps/api/core', () => ({
  convertFileSrc: convertFileSrcMock,
  invoke: invokeMock,
}))

import {
  Video2NotesApi,
  bundledDemoVideoPath,
  localArtifactUrl,
  pickVideoWithNativeDialog,
  resolveBackendConnection,
} from './api'

const json = (value: unknown, status = 200): Response =>
  new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })

const asRequest = (call: unknown[]) => {
  const [input, init] = call as [RequestInfo | URL, RequestInit | undefined]
  return { init: init ?? {}, url: String(input) }
}

describe('real local API client', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    vi.unstubAllEnvs()
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
    invokeMock.mockReset()
    convertFileSrcMock.mockClear()
    delete (window as Window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__
  })

  it('uses explicit Vite configuration before trying Tauri IPC', async () => {
    vi.stubEnv('VITE_VIDEO2NOTES_API_URL', ' http://127.0.0.1:43119/ ')
    vi.stubEnv('VITE_VIDEO2NOTES_API_TOKEN', 'local-session-token')

    await expect(resolveBackendConnection()).resolves.toEqual({
      mode: 'real',
      connection: {
        baseUrl: 'http://127.0.0.1:43119',
        token: 'local-session-token',
        backendStatus: 'configured',
      },
    })
    expect(invokeMock).not.toHaveBeenCalled()
  })

  it('uses the Tauri session command and native file/artifact IPC when no env is set', async () => {
    Object.defineProperty(window, '__TAURI_INTERNALS__', {
      configurable: true,
      value: {},
    })
    invokeMock
      .mockResolvedValueOnce({
        base_url: 'http://127.0.0.1:43120/',
        token: 'tauri-session-token',
        backend_status: 'ready',
        data_root: 'C:/Video2Notes',
      })
      .mockResolvedValueOnce('D:/captures/lecture.mp4')
      .mockResolvedValueOnce('C:/Program Files/Video2Notes/demo/evidence-demo.mp4')
      .mockResolvedValueOnce('C:/Video2Notes/runs/run_01/media/source.mp4')

    await expect(resolveBackendConnection()).resolves.toEqual({
      mode: 'real',
      connection: {
        baseUrl: 'http://127.0.0.1:43120',
        token: 'tauri-session-token',
        backendStatus: 'ready',
        dataRoot: 'C:/Video2Notes',
      },
    })
    await expect(pickVideoWithNativeDialog()).resolves.toBe('D:/captures/lecture.mp4')
    await expect(bundledDemoVideoPath()).resolves.toBe(
      'C:/Program Files/Video2Notes/demo/evidence-demo.mp4',
    )
    await expect(localArtifactUrl('run_01', 'media/source.mp4')).resolves.toBe(
      'asset://localhost/C:/Video2Notes/runs/run_01/media/source.mp4',
    )
    expect(invokeMock).toHaveBeenNthCalledWith(1, 'backend_connection')
    expect(invokeMock).toHaveBeenNthCalledWith(2, 'pick_video')
    expect(invokeMock).toHaveBeenNthCalledWith(3, 'demo_video_path')
    expect(invokeMock).toHaveBeenNthCalledWith(4, 'artifact_file_path', {
      runId: 'run_01',
      relativePath: 'media/source.mp4',
    })
  })

  it('maps authenticated processing, provider v2, discovery, catalog, and performance requests', async () => {
    fetchMock.mockImplementation(() => Promise.resolve(json({ ok: true })))
    const client = new Video2NotesApi({
      baseUrl: 'http://127.0.0.1:43119/',
      token: 'session-secret',
    })
    const registry = {
      schema_version: 2,
      providers: {},
      models: {},
      roles: {
        'notes.drafter': {
          role: 'notes.drafter',
          primary_model_id: 'writer-v2',
          fallback_model_ids: ['writer-v1'],
        },
      },
    }

    await client.health()
    await client.probeSource(
      { kind: 'url', value: 'https://www.youtube.com/watch?v=abc' },
      { kind: 'browser_profile', browser: 'edge', profile: 'Default' },
      {
        mode: 'accurate',
        include_subtitles: true,
        include_automatic_captions: true,
        allow_quality_change: false,
        prefer_hardlink: true,
      },
    )
    await client.submitJob({
      source: { kind: 'local', value: 'D:/clips/input.mp4' },
      auth: { kind: 'none' },
      acquisition: {
        mode: 'fast',
        include_subtitles: true,
        include_automatic_captions: true,
        allow_quality_change: false,
        prefer_hardlink: true,
      },
      quality_mode: 'balanced',
      language_hints: ['zh-CN', 'en'],
      sampling_plan: {
        default: { mode: 'adaptive' },
        overrides: [],
      },
      include_screenshots: true,
      generate_pdf: false,
      report_spec: {
        preset: 'detailed',
        language: 'zh-CN',
        include_screenshots: true,
        output_formats: ['markdown', 'html'],
      },
    })
    await client.jobResult('run / 01')
    await client.saveProviderSecret('cloud/one', 'credential-value')
    await client.testProvider('cloud/one')
    await client.saveProviders(registry)
    await client.configurationCatalog()
    await client.performance()
    await client.savePerformance({
      schema_version: 1,
      experience_mode: 'professional',
      preference: 'throughput',
      reserve: null,
      overrides: {
        cpu_workers: 12,
        remote_model_concurrency: 3,
        cheap_scan_fps: 2,
        expensive_scan_fps: 8,
      },
    })
    await client.discoverProviderModels('cloud/one')

    const calls = fetchMock.mock.calls.map(asRequest)
    expect(calls[0]).toMatchObject({ url: 'http://127.0.0.1:43119/api/health' })
    expect(new Headers(calls[0].init.headers).get('X-Video2Notes-Token')).toBeNull()

    expect(calls[1]).toMatchObject({
      url: 'http://127.0.0.1:43119/api/sources/probe',
      init: { method: 'POST' },
    })
    expect(new Headers(calls[1].init.headers).get('X-Video2Notes-Token')).toBe('session-secret')
    expect(JSON.parse(String(calls[1].init.body))).toEqual({
      source: { kind: 'url', value: 'https://www.youtube.com/watch?v=abc' },
      auth: { kind: 'browser_profile', browser: 'edge', profile: 'Default' },
      policy: expect.objectContaining({ mode: 'accurate' }),
    })

    expect(calls[2]).toMatchObject({ url: 'http://127.0.0.1:43119/api/jobs', init: { method: 'POST' } })
    expect(JSON.parse(String(calls[2].init.body))).toMatchObject({
      quality_mode: 'balanced',
      language_hints: ['zh-CN', 'en'],
      sampling_plan: { default: { mode: 'adaptive' }, overrides: [] },
      report_spec: {
        preset: 'detailed',
        output_formats: ['markdown', 'html'],
      },
    })
    expect(calls[3].url).toBe('http://127.0.0.1:43119/api/jobs/run%20%2F%2001/result')
    expect(calls[4]).toMatchObject({
      url: 'http://127.0.0.1:43119/api/providers/cloud%2Fone/secret',
      init: { method: 'PUT' },
    })
    expect(JSON.parse(String(calls[4].init.body))).toEqual({ secret: 'credential-value' })
    expect(calls[5]).toMatchObject({
      url: 'http://127.0.0.1:43119/api/providers/cloud%2Fone/test',
      init: { method: 'POST' },
    })
    expect(calls[6]).toMatchObject({ url: 'http://127.0.0.1:43119/api/providers', init: { method: 'PUT' } })
    expect(JSON.parse(String(calls[6].init.body))).toEqual(registry)
    expect(JSON.parse(String(calls[6].init.body)).roles['notes.drafter']).toMatchObject({
      primary_model_id: 'writer-v2',
      fallback_model_ids: ['writer-v1'],
    })
    expect(calls[7].url).toBe(
      'http://127.0.0.1:43119/api/configuration-catalog',
    )
    expect(calls[8].url).toBe('http://127.0.0.1:43119/api/performance')
    expect(calls[9]).toMatchObject({
      url: 'http://127.0.0.1:43119/api/performance',
      init: { method: 'PUT' },
    })
    expect(JSON.parse(String(calls[9].init.body))).toMatchObject({
      experience_mode: 'professional',
      preference: 'throughput',
      overrides: {
        cpu_workers: 12,
        remote_model_concurrency: 3,
        cheap_scan_fps: 2,
        expensive_scan_fps: 8,
      },
    })
    expect(calls[10].url).toBe(
      'http://127.0.0.1:43119/api/providers/cloud%2Fone/discover',
    )
  })

  it('maps the run materials contract, including multipart files and query metadata', async () => {
    const material = {
      id: 'material-abc123',
      run_id: 'run / 01',
      kind: 'image',
      title: '补充截图',
      original_name: 'diagram.png',
      media_type: 'image/png',
      artifact: {
        kind: 'supporting',
        relative_path: 'supporting/files/material-abc123/image.png',
        sha256: 'a'.repeat(64),
        size_bytes: 4,
        media_type: 'image/png',
        created_at: '2026-07-30T12:00:00Z',
      },
      sha256: 'a'.repeat(64),
      size_bytes: 4,
      text_content: null,
      start_us: 1_000_000,
      end_us: 2_000_000,
      status: 'active',
      created_at: '2026-07-30T12:00:00Z',
      deleted_at: null,
    }
    fetchMock
      .mockResolvedValueOnce(json([material]))
      .mockResolvedValueOnce(json({ ...material, kind: 'text', text_content: '补充说明' }))
      .mockResolvedValueOnce(json(material))
      .mockResolvedValueOnce(
        json({ ...material, status: 'deleted', deleted_at: '2026-07-30T12:01:00Z' }),
      )
    const client = new Video2NotesApi({
      baseUrl: 'http://127.0.0.1:43119/',
      token: 'session-secret',
    })
    const image = new File([new Uint8Array([1, 2, 3, 4])], 'diagram #1.png', {
      type: 'image/png',
    })

    await client.listMaterials('run / 01')
    await client.addTextMaterial('run / 01', {
      title: '评论区资料',
      content: '补充说明',
      start_us: 3_000_000,
      end_us: 4_000_000,
    })
    await client.addFileMaterial('run / 01', image, {
      title: '补充截图 / A',
      start_us: 1_000_000,
      end_us: 2_000_000,
    })
    await client.deleteMaterial('run / 01', 'material / abc')

    const calls = fetchMock.mock.calls.map(asRequest)
    expect(calls[0]).toMatchObject({
      url: 'http://127.0.0.1:43119/api/runs/run%20%2F%2001/materials',
    })
    expect(calls[1]).toMatchObject({
      url: 'http://127.0.0.1:43119/api/runs/run%20%2F%2001/materials/text',
      init: { method: 'POST' },
    })
    expect(JSON.parse(String(calls[1].init.body))).toEqual({
      title: '评论区资料',
      content: '补充说明',
      start_us: 3_000_000,
      end_us: 4_000_000,
    })

    const uploadUrl = new URL(calls[2].url)
    expect(uploadUrl.pathname).toBe('/api/runs/run%20%2F%2001/materials/files')
    expect(Object.fromEntries(uploadUrl.searchParams)).toEqual({
      title: '补充截图 / A',
      start_us: '1000000',
      end_us: '2000000',
    })
    expect(calls[2].init.method).toBe('POST')
    expect(new Headers(calls[2].init.headers).get('Content-Type')).toBeNull()
    expect(new Headers(calls[2].init.headers).get('X-Video2Notes-Token')).toBe(
      'session-secret',
    )
    const form = calls[2].init.body as FormData
    const uploaded = form.get('file') as File
    expect(uploaded.name).toBe('diagram #1.png')
    expect(uploaded.type).toBe('image/png')
    expect(calls[3]).toMatchObject({
      url: 'http://127.0.0.1:43119/api/runs/run%20%2F%2001/materials/material%20%2F%20abc',
      init: { method: 'DELETE' },
    })
  })

  it('maps every operation request union and the revision-aware effective evidence route', async () => {
    fetchMock
      .mockResolvedValueOnce(json([]))
      .mockResolvedValueOnce(json({ operation_id: 'op-vision' }))
      .mockResolvedValueOnce(json({ operation_id: 'op-asr' }))
      .mockResolvedValueOnce(json({ operation_id: 'op-correction' }))
      .mockResolvedValueOnce(
        json({
          schema_version: 1,
          run_id: 'run / 01',
          revision_id: 'revision / 02',
          operation_id: 'op-correction',
          evidence: [],
          superseded_evidence_ids: [],
        }),
      )
    const client = new Video2NotesApi({
      baseUrl: 'http://127.0.0.1:43119/',
      token: 'session-secret',
    })

    await client.listOperations('run / 01')
    await client.createOperation('run / 01', {
      kind: 'vision_rescan',
      range: { start_us: 1_250_000, end_us: 2_500_000 },
      sampling: { mode: 'fixed_interval', interval_us: 250_000 },
      run_ocr: true,
    })
    await client.createOperation('run / 01', {
      kind: 'asr_retranscribe',
      range: { start_us: 3_000_000, end_us: 4_750_000 },
      language_hints: ['zh-CN', 'en'],
    })
    await client.createOperation('run / 01', {
      kind: 'evidence_correct',
      range: { start_us: 5_000_000, end_us: 5_500_000 },
      evidence_id: 'ASR / 01',
      new_text: '人工校正后的文本',
      reason: '术语更正',
    })
    await client.getEvidence('run / 01', 'revision / 02')

    const calls = fetchMock.mock.calls.map(asRequest)
    expect(calls[0].url).toBe(
      'http://127.0.0.1:43119/api/runs/run%20%2F%2001/operations',
    )
    expect(calls.slice(1, 4).map(call => call.init.method)).toEqual([
      'POST',
      'POST',
      'POST',
    ])
    expect(JSON.parse(String(calls[1].init.body))).toEqual({
      kind: 'vision_rescan',
      range: { start_us: 1_250_000, end_us: 2_500_000 },
      sampling: { mode: 'fixed_interval', interval_us: 250_000 },
      run_ocr: true,
    })
    expect(JSON.parse(String(calls[2].init.body))).toEqual({
      kind: 'asr_retranscribe',
      range: { start_us: 3_000_000, end_us: 4_750_000 },
      language_hints: ['zh-CN', 'en'],
    })
    expect(JSON.parse(String(calls[3].init.body))).toEqual({
      kind: 'evidence_correct',
      range: { start_us: 5_000_000, end_us: 5_500_000 },
      evidence_id: 'ASR / 01',
      new_text: '人工校正后的文本',
      reason: '术语更正',
    })
    const evidenceUrl = new URL(calls[4].url)
    expect(evidenceUrl.pathname).toBe(
      '/api/runs/run%20%2F%2001/evidence',
    )
    expect(evidenceUrl.searchParams.get('revision')).toBe('revision / 02')
  })

  it('maps immutable report revision list and generation contracts', async () => {
    fetchMock
      .mockResolvedValueOnce(
        json({
          schema_version: 1,
          run_id: 'run / 01',
          latest_revision_id: null,
          revisions: [],
        }),
      )
      .mockResolvedValueOnce(json({ id: 'revision-created' }))
    const client = new Video2NotesApi({
      baseUrl: 'http://127.0.0.1:43119/',
      token: 'session-secret',
    })

    await client.listReportRevisions('run / 01')
    await client.createReportRevision('run / 01', {
      preset: 'executive',
      language: 'zh-CN',
      include_screenshots: false,
      output_formats: ['markdown', 'html', 'pdf'],
    })

    const calls = fetchMock.mock.calls.map(asRequest)
    expect(calls[0].url).toBe(
      'http://127.0.0.1:43119/api/runs/run%20%2F%2001/report-revisions',
    )
    expect(calls[1]).toMatchObject({
      url: 'http://127.0.0.1:43119/api/runs/run%20%2F%2001/report-revisions',
      init: { method: 'POST' },
    })
    expect(JSON.parse(String(calls[1].init.body))).toEqual({
      preset: 'executive',
      language: 'zh-CN',
      include_screenshots: false,
      output_formats: ['markdown', 'html', 'pdf'],
    })
  })
})

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

  it('maps authenticated probe, job, result, provider secret, test, and role persistence requests', async () => {
    fetchMock.mockImplementation(() => Promise.resolve(json({ ok: true })))
    const client = new Video2NotesApi({
      baseUrl: 'http://127.0.0.1:43119/',
      token: 'session-secret',
    })
    const registry = {
      schema_version: 1,
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
      include_screenshots: true,
      generate_pdf: false,
    })
    await client.jobResult('run / 01')
    await client.saveProviderSecret('cloud/one', 'credential-value')
    await client.testProvider('cloud/one')
    await client.saveProviders(registry)

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
  })
})

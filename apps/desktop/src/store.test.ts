import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useStudioStore } from './store'

describe('studio store', () => {
  beforeEach(() => {
    useStudioStore.getState().resetDemo()
  })

  it('probes a supported URL and creates a resumable task', () => {
    const store = useStudioStore.getState()
    store.setDraftInput('https://www.youtube.com/watch?v=typed-fixture')
    store.setDraftMode('fast')
    store.probeSource()

    expect(useStudioStore.getState().draft.manifest?.platform).toBe('youtube')
    expect(useStudioStore.getState().draft.status).toBe('ready')

    store.createTask()
    const state = useStudioStore.getState()
    expect(state.view).toBe('tasks')
    expect(state.tasks[0]).toMatchObject({
      mode: 'fast',
      status: 'running',
      progress: 0,
    })

    state.pauseTask(state.tasks[0].id)
    expect(useStudioStore.getState().tasks[0].status).toBe('paused')
    state.resumeTask(state.tasks[0].id)
    expect(useStudioStore.getState().tasks[0].status).toBe('running')
  })

  it('loads the redistributable demo media without an account', () => {
    useStudioStore.getState().chooseBundledDemo()

    expect(useStudioStore.getState().draft).toMatchObject({
      input: 'evidence-demo.mp4',
      sourceKind: 'local',
      status: 'ready',
    })
  })

  it('rejects a role binding when the model lacks capabilities', () => {
    const store = useStudioStore.getState()
    const before = store.roles.find(role => role.id === 'asr-primary')?.modelId

    store.bindRole('asr-primary', 'model-text-local')

    const state = useStudioStore.getState()
    expect(state.roles.find(role => role.id === 'asr-primary')?.modelId).toBe(before)
    expect(state.notice).toContain('能力')
  })

  it('exposes a visible provider test lifecycle', () => {
    vi.useFakeTimers()
    const store = useStudioStore.getState()
    store.testProvider('provider-openai')
    expect(
      useStudioStore.getState().providers.find(provider => provider.id === 'provider-openai')
        ?.status,
    ).toBe('testing')

    vi.advanceTimersByTime(700)
    expect(
      useStudioStore.getState().providers.find(provider => provider.id === 'provider-openai')
        ?.status,
    ).toBe('connected')
    vi.useRealTimers()
  })
})

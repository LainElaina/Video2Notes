import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useStudioStore } from '../store'
import { usePendingActions } from './usePendingActions'

const flushMicrotasks = () => act(async () => {})

describe('usePendingActions', () => {
  beforeEach(() => {
    useStudioStore.getState().resetDemo()
  })

  it('blocks a re-entrant call while the same key is in flight and frees it on settle', async () => {
    const { result } = renderHook(() => usePendingActions())
    let release!: () => void
    const gate = new Promise<void>(resolve => {
      release = resolve
    })
    const first = vi.fn(() => gate)
    const second = vi.fn(() => Promise.resolve())

    act(() => result.current.track('run:pause', first))
    act(() => result.current.track('run:pause', second))

    expect(first).toHaveBeenCalledTimes(1)
    expect(second).not.toHaveBeenCalled()

    await flushMicrotasks()
    expect(result.current.isPending('run:pause')).toBe(true)

    act(() => release())
    await flushMicrotasks()
    expect(result.current.isPending('run:pause')).toBe(false)

    act(() => result.current.track('run:pause', second))
    expect(second).toHaveBeenCalledTimes(1)
  })

  it('tracks keys independently so a sibling control stays usable', async () => {
    const { result } = renderHook(() => usePendingActions())
    let release!: () => void
    const gate = new Promise<void>(resolve => {
      release = resolve
    })

    act(() => result.current.track('run:pause', () => gate))
    await flushMicrotasks()

    expect(result.current.isPending('run:pause')).toBe(true)
    expect(result.current.isPending('run:cancel')).toBe(false)

    act(() => release())
    await flushMicrotasks()
  })

  it('never flashes busy for an action that settles within the same microtask turn', async () => {
    const pendingHistory: boolean[] = []
    const { result } = renderHook(() => {
      const pending = usePendingActions()
      pendingHistory.push(pending.isPending('demo:shortcut'))
      return pending
    })

    act(() => result.current.track('demo:shortcut', () => Promise.resolve()))
    expect(result.current.isPending('demo:shortcut')).toBe(false)

    await flushMicrotasks()
    expect(result.current.isPending('demo:shortcut')).toBe(false)
    expect(pendingHistory).not.toContain(true)
  })

  it('ignores synchronous actions entirely', async () => {
    const { result } = renderHook(() => usePendingActions())

    act(() => result.current.track('sync', () => undefined))
    await flushMicrotasks()

    expect(result.current.isPending('sync')).toBe(false)
  })

  it('recovers when the action throws synchronously', async () => {
    const { result } = renderHook(() => usePendingActions())

    act(() =>
      result.current.track('throws', () => {
        throw new Error('boom')
      }),
    )
    await flushMicrotasks()

    expect(result.current.isPending('throws')).toBe(false)

    const retry = vi.fn(() => Promise.resolve())
    act(() => result.current.track('throws', retry))
    expect(retry).toHaveBeenCalledTimes(1)
  })

  it('clears pending when the action rejects', async () => {
    const { result } = renderHook(() => usePendingActions())
    let reject!: (error: Error) => void
    const gate = new Promise<void>((_resolve, rejectPromise) => {
      reject = rejectPromise
    })

    act(() => result.current.track('run:cancel', () => gate))
    await flushMicrotasks()
    expect(result.current.isPending('run:cancel')).toBe(true)

    act(() => reject(new Error('cancel failed')))
    await flushMicrotasks()
    expect(result.current.isPending('run:cancel')).toBe(false)
  })

  it('clears pending when a store-style action resolves after setting a notice', async () => {
    const { result } = renderHook(() => usePendingActions())
    let release!: () => void
    const gate = new Promise<void>(resolve => {
      release = resolve
    })

    act(() => result.current.track('demo:pause', () => gate))
    await flushMicrotasks()
    expect(result.current.isPending('demo:pause')).toBe(true)

    // Store actions report failures through the notice bar and then resolve.
    act(() => {
      useStudioStore.setState({ notice: '本地后端尚未连接。' })
      release()
    })
    await flushMicrotasks()

    expect(result.current.isPending('demo:pause')).toBe(false)
    expect(useStudioStore.getState().notice).toBe('本地后端尚未连接。')
  })
})

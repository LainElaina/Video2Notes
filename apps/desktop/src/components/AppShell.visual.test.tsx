import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { useStudioStore } from '../store'
import {
  DEFAULT_UI_PREFERENCES,
  useUiPreferences,
} from '../stores/uiPreferences'
import { AppShell } from './AppShell'

const SUCCESS_NOTICE = '性能配置已保存；算法计划已按当前机器重新计算。'
const PROGRESS_NOTICE = '正在检查依赖或提交任务，请等待本次操作确认。'
const NEUTRAL_NOTICE = '演示任务已暂停。'
const FAILURE_NOTICE = '依赖预检失败，任务未提交：缺少 FFmpeg。'

const stubAnimationFrame = () => {
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) =>
    window.setTimeout(() => callback(performance.now()), 16),
  )
  vi.stubGlobal('cancelAnimationFrame', (handle: number) => {
    window.clearTimeout(handle)
  })
}

interface NarrowMediaControl {
  setNarrow: (narrow: boolean) => void
}

const stubNarrowMatchMedia = (initial: boolean): NarrowMediaControl => {
  let narrow = initial
  const listeners = new Set<(event: unknown) => void>()
  const narrowQuery = {
    media: '(max-width: 1120px)',
    onchange: null,
    get matches() {
      return narrow
    },
    addEventListener: (_type: string, listener: (event: unknown) => void) => {
      listeners.add(listener)
    },
    removeEventListener: (_type: string, listener: (event: unknown) => void) => {
      listeners.delete(listener)
    },
    addListener: (listener: (event: unknown) => void) => {
      listeners.add(listener)
    },
    removeListener: (listener: (event: unknown) => void) => {
      listeners.delete(listener)
    },
    dispatchEvent: () => true,
  } as unknown as MediaQueryList
  const otherQuery = (media: string) =>
    ({
      media,
      onchange: null,
      matches: false,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => true,
    }) as unknown as MediaQueryList

  vi.stubGlobal('matchMedia', (query: string) =>
    query === '(max-width: 1120px)' ? narrowQuery : otherQuery(query),
  )

  return {
    setNarrow: next => {
      narrow = next
      listeners.forEach(listener =>
        listener({ matches: next } as MediaQueryListEvent),
      )
    },
  }
}

const renderShell = () =>
  render(
    <AppShell>
      <div>Workspace page</div>
    </AppShell>,
  )

describe('notice auto-dismiss', () => {
  beforeEach(() => {
    window.localStorage.clear()
    useUiPreferences.setState(DEFAULT_UI_PREFERENCES)
    useStudioStore.getState().resetDemo()
    vi.useFakeTimers()
    stubAnimationFrame()
  })

  afterEach(() => {
    vi.clearAllTimers()
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it.each([
    ['success', SUCCESS_NOTICE],
    ['progress', PROGRESS_NOTICE],
    ['neutral', NEUTRAL_NOTICE],
  ])('auto-dismisses a %s notice after about 4.5s', (_tone, notice) => {
    renderShell()
    act(() => {
      useStudioStore.setState({ notice })
    })

    const bar = screen.getByRole('status')
    expect(bar).toHaveTextContent(notice)

    act(() => {
      vi.advanceTimersByTime(4_499)
    })
    expect(screen.getByRole('status')).toBeInTheDocument()
    expect(useStudioStore.getState().notice).toBe(notice)

    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(useStudioStore.getState().notice).toBeUndefined()

    act(() => {
      vi.advanceTimersByTime(200)
    })
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(document.querySelector('.notice-bar')).toBeNull()
  })

  it('keeps a failure notice until it is dismissed manually', () => {
    renderShell()
    act(() => {
      useStudioStore.setState({ notice: FAILURE_NOTICE })
    })

    expect(screen.getByRole('status')).toHaveTextContent('依赖预检失败')
    act(() => {
      vi.advanceTimersByTime(30_000)
    })
    expect(screen.getByRole('status')).toBeInTheDocument()
    expect(useStudioStore.getState().notice).toBe(FAILURE_NOTICE)

    fireEvent.click(screen.getByRole('button', { name: '关闭提示' }))
    expect(useStudioStore.getState().notice).toBeUndefined()
    act(() => {
      vi.advanceTimersByTime(200)
    })
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('pauses the countdown while hovered and resumes after the pointer leaves', () => {
    renderShell()
    act(() => {
      useStudioStore.setState({ notice: SUCCESS_NOTICE })
    })

    const bar = screen.getByRole('status')
    fireEvent.mouseEnter(bar)
    act(() => {
      vi.advanceTimersByTime(10_000)
    })
    expect(screen.getByRole('status')).toBeInTheDocument()
    expect(useStudioStore.getState().notice).toBe(SUCCESS_NOTICE)

    fireEvent.mouseLeave(bar)
    act(() => {
      vi.advanceTimersByTime(4_499)
    })
    expect(useStudioStore.getState().notice).toBe(SUCCESS_NOTICE)

    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(useStudioStore.getState().notice).toBeUndefined()
  })
})

describe('narrow slide-over context panel', () => {
  beforeEach(() => {
    window.localStorage.clear()
    useUiPreferences.setState(DEFAULT_UI_PREFERENCES)
    useStudioStore.getState().resetDemo()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('auto-collapses the panel into a dismissed slide-over below 1120px', async () => {
    stubNarrowMatchMedia(true)
    renderShell()

    expect(useStudioStore.getState().contextCollapsed).toBe(true)
    expect(screen.getByRole('button', { name: '展开侧栏' })).toBeInTheDocument()
    expect(document.querySelector('.context-scrim')).toBeNull()
    await waitFor(() =>
      expect(document.querySelector('.context-panel')).toBeNull(),
    )
  })

  it('opens the slide-over with a scrim from the expand button and closes it with Escape', async () => {
    stubNarrowMatchMedia(true)
    renderShell()

    fireEvent.click(screen.getByRole('button', { name: '展开侧栏' }))
    expect(useStudioStore.getState().contextCollapsed).toBe(false)
    expect(document.querySelector('.context-scrim')).not.toBeNull()
    expect(document.querySelector('.context-panel')).not.toBeNull()

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(useStudioStore.getState().contextCollapsed).toBe(true)
    expect(screen.getByRole('button', { name: '展开侧栏' })).toBeInTheDocument()
    await waitFor(() =>
      expect(document.querySelector('.context-scrim')).toBeNull(),
    )
    await waitFor(() =>
      expect(document.querySelector('.context-panel')).toBeNull(),
    )
  })

  it('restores the wide-layout expanded state when leaving narrow mode', async () => {
    const media = stubNarrowMatchMedia(true)
    renderShell()
    expect(useStudioStore.getState().contextCollapsed).toBe(true)

    act(() => media.setNarrow(false))

    expect(useStudioStore.getState().contextCollapsed).toBe(false)
    expect(
      screen.queryByRole('button', { name: '展开侧栏' }),
    ).not.toBeInTheDocument()
    await waitFor(() =>
      expect(document.querySelector('.context-panel')).not.toBeNull(),
    )
    expect(document.querySelector('.context-scrim')).toBeNull()
  })

  it('keeps a user-chosen collapse when leaving narrow mode', () => {
    const media = stubNarrowMatchMedia(false)
    renderShell()

    fireEvent.click(screen.getByRole('button', { name: '收起上下文栏' }))
    expect(useStudioStore.getState().contextCollapsed).toBe(true)

    act(() => media.setNarrow(true))
    expect(useStudioStore.getState().contextCollapsed).toBe(true)

    // The collapse was not caused by the narrow auto-collapse, so leaving
    // narrow mode must not force the panel back open.
    act(() => media.setNarrow(false))
    expect(useStudioStore.getState().contextCollapsed).toBe(true)
  })

  it('re-applies the narrow collapse when backend reinitialization resets the panel', async () => {
    stubNarrowMatchMedia(true)
    renderShell()
    expect(useStudioStore.getState().contextCollapsed).toBe(true)

    // Backend (re)initialization replaces the store with initialData, which
    // resets contextCollapsed to false while the backend mode flips.
    act(() => {
      useStudioStore.setState({
        contextCollapsed: false,
        backend: { mode: 'connecting', detail: '正在重新连接本机后端…' },
      })
    })

    expect(useStudioStore.getState().contextCollapsed).toBe(true)
    expect(screen.getByRole('button', { name: '展开侧栏' })).toBeInTheDocument()
    // The slide-over plays its exit transition before the scrim leaves the DOM.
    await waitFor(() =>
      expect(document.querySelector('.context-scrim')).toBeNull(),
    )
  })
})

describe('workspace page surface', () => {
  beforeEach(() => {
    window.localStorage.clear()
    useUiPreferences.setState(DEFAULT_UI_PREFERENCES)
    useStudioStore.getState().resetDemo()
    vi.useFakeTimers()
    stubAnimationFrame()
  })

  afterEach(() => {
    vi.clearAllTimers()
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('keeps the same page surface element across navigation and re-triggers its enter transition', () => {
    render(<App />)

    const surface = document.querySelector('.workspace-page-surface')
    expect(surface).not.toBeNull()
    expect(surface?.querySelector('.create-page')).not.toBeNull()

    act(() => {
      useStudioStore.getState().navigate('tasks')
    })

    const after = document.querySelector('.workspace-page-surface')
    expect(after).toBe(surface)
    expect(after?.querySelector('.run-page')).not.toBeNull()
    expect(after).toHaveAttribute('data-entering', '')

    act(() => {
      vi.advanceTimersByTime(32)
    })
    expect(after).not.toHaveAttribute('data-entering')

    act(() => {
      useStudioStore.getState().navigate('create')
    })
    expect(document.querySelector('.workspace-page-surface')).toBe(surface)
    expect(surface).toHaveAttribute('data-entering', '')
    expect(surface?.querySelector('.create-page')).not.toBeNull()
  })
})

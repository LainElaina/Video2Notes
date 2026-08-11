import { act, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { useStudioStore } from '../store'
import {
  DEFAULT_UI_PREFERENCES,
  useUiPreferences,
} from '../stores/uiPreferences'
import { CreatePage } from './CreatePage'
import { RunPage } from './RunPage'

describe('run progress accessibility', () => {
  beforeEach(() => {
    window.localStorage.clear()
    useUiPreferences.setState(DEFAULT_UI_PREFERENCES)
    useStudioStore.getState().resetDemo()
  })

  it('exposes the overall progress track as a live progressbar', () => {
    render(<RunPage />)

    const track = screen.getByRole('progressbar')
    expect(track).toHaveAttribute('aria-label', '总体进度 36%')
    expect(track).toHaveAttribute('aria-valuenow', '36')
    expect(track).toHaveAttribute('aria-valuemin', '0')
    expect(track).toHaveAttribute('aria-valuemax', '100')

    act(() => {
      useStudioStore.setState(state => ({
        tasks: state.tasks.map(task =>
          task.id === 'task-running' ? { ...task, progress: 72.4 } : task,
        ),
      }))
    })

    expect(screen.getByRole('progressbar')).toHaveAttribute(
      'aria-valuenow',
      '72',
    )
  })

  it('reflects the completed task progress', () => {
    useStudioStore.setState({ activeTaskId: 'task-complete' })
    render(<RunPage />)

    expect(screen.getByRole('progressbar')).toHaveAttribute(
      'aria-valuenow',
      '100',
    )
  })
})

describe('create page probe feedback', () => {
  beforeEach(() => {
    window.localStorage.clear()
    useUiPreferences.setState(DEFAULT_UI_PREFERENCES)
    useStudioStore.getState().resetDemo()
  })

  it('disables the probe button and marks it busy while probing', () => {
    render(<CreatePage />)

    const idleButton = screen.getByRole('button', { name: '探测来源' })
    expect(idleButton).toBeEnabled()
    expect(idleButton).not.toHaveAttribute('aria-busy')

    act(() => {
      useStudioStore.setState(state => ({
        draft: { ...state.draft, status: 'probing' },
      }))
    })

    const busyButton = screen.getByRole('button', { name: '正在探测…' })
    expect(busyButton).toBeDisabled()
    expect(busyButton).toHaveAttribute('aria-busy', 'true')

    act(() => {
      useStudioStore.setState(state => ({
        draft: { ...state.draft, status: 'idle' },
      }))
    })

    const settledButton = screen.getByRole('button', { name: '探测来源' })
    expect(settledButton).toBeEnabled()
    expect(settledButton).not.toHaveAttribute('aria-busy')
  })
})

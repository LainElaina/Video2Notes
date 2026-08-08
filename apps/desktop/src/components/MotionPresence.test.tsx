import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MotionPresence } from './MotionPresence'

const renderPresence = (show: boolean, exitMs = 160) =>
  render(
    <MotionPresence show={show} exitMs={exitMs}>
      <span>Motion content</span>
    </MotionPresence>,
  )

describe('MotionPresence', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.stubGlobal(
      'requestAnimationFrame',
      (callback: FrameRequestCallback) =>
        window.setTimeout(() => callback(performance.now()), 16),
    )
    vi.stubGlobal('cancelAnimationFrame', (handle: number) => {
      window.clearTimeout(handle)
    })
  })

  afterEach(() => {
    cleanup()
    vi.clearAllTimers()
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('renders an initially visible child in the entering state', () => {
    renderPresence(true)

    expect(screen.getByText('Motion content').parentElement).toHaveAttribute(
      'data-motion-state',
      'entering',
    )
  })

  it('keeps the child mounted until the exit duration has elapsed', () => {
    const { rerender } = renderPresence(true, 200)

    rerender(
      <MotionPresence show={false} exitMs={200}>
        <span>Motion content</span>
      </MotionPresence>,
    )

    act(() => {
      vi.advanceTimersByTime(199)
    })
    expect(screen.getByText('Motion content')).toBeInTheDocument()

    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(screen.queryByText('Motion content')).not.toBeInTheDocument()
  })

  it('hides the retained child from assistive technology while exiting', () => {
    const { rerender } = renderPresence(true)

    rerender(
      <MotionPresence show={false}>
        <span>Motion content</span>
      </MotionPresence>,
    )

    expect(screen.getByText('Motion content').parentElement).toHaveAttribute(
      'aria-hidden',
      'true',
    )
  })

  it('cancels the pending unmount when reopened during exit', () => {
    const { rerender } = renderPresence(true, 200)

    rerender(
      <MotionPresence show={false} exitMs={200}>
        <span>Motion content</span>
      </MotionPresence>,
    )
    act(() => {
      vi.advanceTimersByTime(100)
    })

    rerender(
      <MotionPresence show exitMs={200}>
        <span>Motion content</span>
      </MotionPresence>,
    )
    act(() => {
      vi.advanceTimersByTime(200)
    })

    const wrapper = screen.getByText('Motion content').parentElement
    expect(wrapper).toHaveAttribute('data-motion-state', 'entered')
    expect(wrapper).not.toHaveAttribute('aria-hidden')
  })
})

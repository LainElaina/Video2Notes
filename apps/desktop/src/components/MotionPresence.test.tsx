import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
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

  it('moves focus into a modal, traps Tab, and restores the opener on close', () => {
    const opener = document.createElement('button')
    opener.textContent = 'Open modal'
    document.body.append(opener)
    opener.focus()

    const { rerender } = render(
      <MotionPresence show={false} focusMode="modal" exitMs={200}>
        {null}
      </MotionPresence>,
    )

    rerender(
      <MotionPresence show focusMode="modal" exitMs={200}>
        <button type="button">Backdrop</button>
        <section role="dialog" aria-label="Motion dialog" tabIndex={-1}>
          <button type="button">First action</button>
          <button type="button">Last action</button>
        </section>
      </MotionPresence>,
    )
    act(() => {
      vi.advanceTimersByTime(16)
    })

    const dialog = screen.getByRole('dialog', { name: 'Motion dialog' })
    expect(dialog).toHaveFocus()

    const first = screen.getByRole('button', { name: 'First action' })
    const last = screen.getByRole('button', { name: 'Last action' })
    fireEvent.keyDown(document, { key: 'Tab' })
    expect(first).toHaveFocus()
    dialog.focus()
    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true })
    expect(last).toHaveFocus()
    last.focus()
    fireEvent.keyDown(document, { key: 'Tab' })
    expect(first).toHaveFocus()
    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true })
    expect(last).toHaveFocus()

    rerender(
      <MotionPresence show={false} focusMode="modal" exitMs={200}>
        <button type="button">Backdrop</button>
        <section role="dialog" aria-label="Motion dialog" tabIndex={-1}>
          <button type="button">First action</button>
          <button type="button">Last action</button>
        </section>
      </MotionPresence>,
    )
    expect(opener).toHaveFocus()

    opener.remove()
  })

  it('prefers an explicit restore target for asynchronously opened modals', () => {
    const capturedTarget = document.createElement('button')
    capturedTarget.textContent = 'Captured target'
    const explicitTarget = document.createElement('button')
    explicitTarget.textContent = 'Explicit target'
    document.body.append(capturedTarget, explicitTarget)
    capturedTarget.focus()
    const restoreFocusRef = { current: explicitTarget }

    const { rerender } = render(
      <MotionPresence
        show={false}
        focusMode="modal"
        restoreFocusRef={restoreFocusRef}
      >
        {null}
      </MotionPresence>,
    )
    rerender(
      <MotionPresence show focusMode="modal" restoreFocusRef={restoreFocusRef}>
        <section role="dialog" aria-label="Async dialog" tabIndex={-1} />
      </MotionPresence>,
    )
    act(() => {
      vi.advanceTimersByTime(16)
    })

    rerender(
      <MotionPresence
        show={false}
        focusMode="modal"
        restoreFocusRef={restoreFocusRef}
      >
        <section role="dialog" aria-label="Async dialog" tabIndex={-1} />
      </MotionPresence>,
    )

    expect(explicitTarget).toHaveFocus()
    capturedTarget.remove()
    explicitTarget.remove()
  })

  it('falls back to the captured opener when the explicit target becomes invalid', () => {
    const capturedTarget = document.createElement('button')
    capturedTarget.textContent = 'Captured target'
    const disabledTarget = document.createElement('button')
    disabledTarget.textContent = 'Disabled target'
    disabledTarget.disabled = true
    document.body.append(capturedTarget, disabledTarget)
    capturedTarget.focus()
    const restoreFocusRef = { current: disabledTarget }

    const { rerender } = render(
      <MotionPresence
        show={false}
        focusMode="modal"
        restoreFocusRef={restoreFocusRef}
      >
        {null}
      </MotionPresence>,
    )
    rerender(
      <MotionPresence show focusMode="modal" restoreFocusRef={restoreFocusRef}>
        <section role="dialog" aria-label="Fallback dialog" tabIndex={-1} />
      </MotionPresence>,
    )
    act(() => {
      vi.advanceTimersByTime(16)
    })

    rerender(
      <MotionPresence
        show={false}
        focusMode="modal"
        restoreFocusRef={restoreFocusRef}
      >
        <section role="dialog" aria-label="Fallback dialog" tabIndex={-1} />
      </MotionPresence>,
    )

    expect(capturedTarget).toHaveFocus()
    capturedTarget.remove()
    disabledTarget.remove()
  })

  it('reacquires modal focus when reopened during its exit window', () => {
    const opener = document.createElement('button')
    opener.textContent = 'Rapid opener'
    document.body.append(opener)
    opener.focus()

    const dialog = (
      <section role="dialog" aria-label="Rapid dialog" tabIndex={-1}>
        <button type="button">Rapid action</button>
      </section>
    )
    const { rerender } = render(
      <MotionPresence show={false} focusMode="modal" exitMs={200}>
        {null}
      </MotionPresence>,
    )
    rerender(
      <MotionPresence show focusMode="modal" exitMs={200}>
        {dialog}
      </MotionPresence>,
    )
    act(() => {
      vi.advanceTimersByTime(16)
    })
    expect(screen.getByRole('dialog', { name: 'Rapid dialog' })).toHaveFocus()

    rerender(
      <MotionPresence show={false} focusMode="modal" exitMs={200}>
        {dialog}
      </MotionPresence>,
    )
    expect(opener).toHaveFocus()

    rerender(
      <MotionPresence show focusMode="modal" exitMs={200}>
        {dialog}
      </MotionPresence>,
    )
    act(() => {
      vi.advanceTimersByTime(16)
    })

    const reopened = screen.getByRole('dialog', { name: 'Rapid dialog' })
    expect(reopened).toHaveFocus()
    expect(reopened.parentElement).toHaveAttribute(
      'data-motion-state',
      'entered',
    )

    act(() => {
      vi.advanceTimersByTime(200)
    })
    expect(screen.getByRole('dialog', { name: 'Rapid dialog' })).toBeInTheDocument()
    opener.remove()
  })
})

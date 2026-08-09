import { act, fireEvent, render, screen } from '@testing-library/react'
import { useEffect, useRef, useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  usePopoverFocus,
  type PopoverDismissReason,
} from './usePopoverFocus'

interface PopoverFixtureProps {
  open: boolean
  onDismiss: (reason: PopoverDismissReason) => void
}

function PopoverFixture({ open, onDismiss }: PopoverFixtureProps) {
  const triggerRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const dismiss = usePopoverFocus({
    open,
    triggerRef,
    panelRef,
    onDismiss,
  })

  return (
    <>
      <button ref={triggerRef} type="button">
        Export
      </button>
      <div ref={panelRef} role="menu" aria-label="Export format">
        <button
          type="button"
          role="menuitem"
          onClick={() => dismiss('action')}
        >
          Markdown
        </button>
        <button type="button" role="menuitem">
          HTML
        </button>
        <button type="button" role="menuitem">
          PDF
        </button>
      </div>
      <button type="button">Outside control</button>
    </>
  )
}

function DelayedPanelFixture({ open, onDismiss }: PopoverFixtureProps) {
  const triggerRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const [mounted, setMounted] = useState(false)
  usePopoverFocus({ open, triggerRef, panelRef, onDismiss })

  useEffect(() => {
    if (!open) {
      setMounted(false)
      return
    }
    const frame = window.requestAnimationFrame(() => setMounted(true))
    return () => window.cancelAnimationFrame(frame)
  }, [open])

  return (
    <>
      <button ref={triggerRef} type="button">
        Export
      </button>
      {mounted && (
        <div ref={panelRef} role="menu" aria-label="Delayed export format">
          <button type="button" role="menuitem">
            Delayed Markdown
          </button>
        </div>
      )}
    </>
  )
}

describe('usePopoverFocus', () => {
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
    vi.clearAllTimers()
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('focuses the first enabled menu item when opened', () => {
    render(<PopoverFixture open onDismiss={vi.fn()} />)

    act(() => {
      vi.advanceTimersByTime(16)
    })

    expect(screen.getByRole('menuitem', { name: 'Markdown' })).toHaveFocus()
  })

  it('focuses when the presence layer mounts after the first frame', async () => {
    render(<DelayedPanelFixture open onDismiss={vi.fn()} />)

    await act(async () => {
      vi.advanceTimersByTime(16)
      await Promise.resolve()
    })
    const item = screen.getByRole('menuitem', { name: 'Delayed Markdown' })
    expect(item).toHaveFocus()
  })

  it('supports wrapped arrow navigation plus Home and End', () => {
    render(<PopoverFixture open onDismiss={vi.fn()} />)
    act(() => {
      vi.advanceTimersByTime(16)
    })

    const markdown = screen.getByRole('menuitem', { name: 'Markdown' })
    const html = screen.getByRole('menuitem', { name: 'HTML' })
    const pdf = screen.getByRole('menuitem', { name: 'PDF' })

    fireEvent.keyDown(document, { key: 'ArrowDown' })
    expect(html).toHaveFocus()
    fireEvent.keyDown(document, { key: 'End' })
    expect(pdf).toHaveFocus()
    fireEvent.keyDown(document, { key: 'ArrowDown' })
    expect(markdown).toHaveFocus()
    fireEvent.keyDown(document, { key: 'ArrowUp' })
    expect(pdf).toHaveFocus()
    fireEvent.keyDown(document, { key: 'Home' })
    expect(markdown).toHaveFocus()
  })

  it.each([
    ['Escape', 'escape'],
    ['menu action', 'action'],
  ] as const)('restores the trigger after %s dismissal', (route, reason) => {
    const onDismiss = vi.fn()
    render(<PopoverFixture open onDismiss={onDismiss} />)
    act(() => {
      vi.advanceTimersByTime(16)
    })

    if (route === 'Escape') {
      fireEvent.keyDown(document, { key: 'Escape' })
    } else {
      fireEvent.click(screen.getByRole('menuitem', { name: 'Markdown' }))
    }

    expect(onDismiss).toHaveBeenCalledWith(reason)
    expect(screen.getByRole('button', { name: 'Export' })).toHaveFocus()
  })

  it.each([
    ['outside pointer', 'outside'],
    ['Tab', 'tab'],
  ] as const)('does not restore the trigger after %s dismissal', (route, reason) => {
    const onDismiss = vi.fn()
    render(<PopoverFixture open onDismiss={onDismiss} />)
    act(() => {
      vi.advanceTimersByTime(16)
    })

    const outside = screen.getByRole('button', { name: 'Outside control' })
    if (route === 'outside pointer') {
      outside.focus()
      fireEvent.pointerDown(outside)
    } else {
      fireEvent.keyDown(document, { key: 'Tab' })
      outside.focus()
    }

    expect(onDismiss).toHaveBeenCalledWith(reason)
    expect(outside).toHaveFocus()
    expect(screen.getByRole('button', { name: 'Export' })).not.toHaveFocus()
  })

  it('cancels the pending initial-focus frame when closed rapidly', () => {
    const onDismiss = vi.fn()
    const { rerender } = render(
      <PopoverFixture open onDismiss={onDismiss} />,
    )
    const outside = screen.getByRole('button', { name: 'Outside control' })
    outside.focus()

    rerender(<PopoverFixture open={false} onDismiss={onDismiss} />)
    act(() => {
      vi.advanceTimersByTime(16)
    })

    expect(outside).toHaveFocus()
    expect(screen.getByRole('menuitem', { name: 'Markdown' })).not.toHaveFocus()
  })
})

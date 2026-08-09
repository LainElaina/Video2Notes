import { useCallback, useEffect, type RefObject } from 'react'

export type PopoverDismissReason =
  | 'action'
  | 'escape'
  | 'outside'
  | 'tab'
  | 'transfer'

interface UsePopoverFocusOptions {
  open: boolean
  triggerRef: RefObject<HTMLElement | null>
  panelRef: RefObject<HTMLElement | null>
  onDismiss: (reason: PopoverDismissReason) => void
}

const menuItems = (panel: HTMLElement) =>
  Array.from(
    panel.querySelectorAll<HTMLElement>(
      '[role="menuitem"]:not([aria-disabled="true"]):not([disabled])',
    ),
  )

export function usePopoverFocus({
  open,
  triggerRef,
  panelRef,
  onDismiss,
}: UsePopoverFocusOptions) {
  const dismiss = useCallback(
    (reason: PopoverDismissReason) => {
      if (reason === 'action' || reason === 'escape') {
        triggerRef.current?.focus({ preventScroll: true })
      }
      onDismiss(reason)
    },
    [onDismiss, triggerRef],
  )

  useEffect(() => {
    if (!open) return

    const focusFirstItem = () => {
      const panel = panelRef.current
      if (!panel || panel.closest('[inert]')) return false
      const item = menuItems(panel)[0]
      if (!item) return false
      item.focus({ preventScroll: true })
      return document.activeElement === item
    }

    const focusObserver = new MutationObserver(() => {
      if (focusFirstItem()) focusObserver.disconnect()
    })
    focusObserver.observe(document.body, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ['inert'],
    })
    const focusFrame = window.requestAnimationFrame(() => {
      if (focusFirstItem()) focusObserver.disconnect()
    })
    const focusTimeout = window.setTimeout(
      () => focusObserver.disconnect(),
      1_000,
    )

    const dismissOutside = (event: PointerEvent) => {
      const target = event.target as Node
      if (
        triggerRef.current?.contains(target) ||
        panelRef.current?.contains(target)
      ) {
        return
      }
      dismiss('outside')
    }

    const handleKeyboard = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        dismiss('escape')
        return
      }

      const panel = panelRef.current
      if (!panel || !panel.contains(document.activeElement)) return
      if (event.key === 'Tab') {
        dismiss('tab')
        return
      }

      const items = menuItems(panel)
      if (items.length === 0) return
      const activeIndex = items.indexOf(document.activeElement as HTMLElement)
      let nextIndex: number | undefined
      if (event.key === 'ArrowDown') {
        nextIndex = activeIndex < 0 ? 0 : (activeIndex + 1) % items.length
      } else if (event.key === 'ArrowUp') {
        nextIndex = activeIndex < 0 ? items.length - 1 : (activeIndex - 1 + items.length) % items.length
      } else if (event.key === 'Home') {
        nextIndex = 0
      } else if (event.key === 'End') {
        nextIndex = items.length - 1
      }

      if (nextIndex === undefined) return
      event.preventDefault()
      items[nextIndex]?.focus({ preventScroll: true })
    }

    document.addEventListener('pointerdown', dismissOutside)
    document.addEventListener('keydown', handleKeyboard)
    return () => {
      window.cancelAnimationFrame(focusFrame)
      window.clearTimeout(focusTimeout)
      focusObserver.disconnect()
      document.removeEventListener('pointerdown', dismissOutside)
      document.removeEventListener('keydown', handleKeyboard)
    }
  }, [dismiss, open, panelRef, triggerRef])

  return dismiss
}

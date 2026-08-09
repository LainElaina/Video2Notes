import {
  useEffect,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from 'react'

type MotionPresenceState = 'entering' | 'entered' | 'exiting'

interface MotionPresenceProps {
  show: boolean
  children: ReactNode
  className?: string
  exitMs?: number
  animateInitial?: boolean
  focusMode?: 'modal'
  initialFocusRef?: RefObject<HTMLElement | null>
  restoreFocusRef?: RefObject<HTMLElement | null>
}

const focusableSelector = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

const focusableDescendants = (root: HTMLElement) =>
  Array.from(root.querySelectorAll<HTMLElement>(focusableSelector)).filter(
    element =>
      element.getAttribute('aria-hidden') !== 'true' &&
      !element.closest('[inert]'),
  )

const focusScope = (root: HTMLElement) =>
  root.querySelector<HTMLElement>('[role="dialog"]') ?? root

const canRestoreFocus = (element: HTMLElement | null): element is HTMLElement =>
  Boolean(
    element?.isConnected &&
      !element.hasAttribute('disabled') &&
      element.getAttribute('aria-hidden') !== 'true' &&
      !element.closest('[inert]'),
  )

export function MotionPresence({
  show,
  children,
  className,
  exitMs = 160,
  animateInitial = true,
  focusMode,
  initialFocusRef,
  restoreFocusRef,
}: MotionPresenceProps) {
  const [mounted, setMounted] = useState(show)
  const [state, setState] = useState<MotionPresenceState>(
    show ? (animateInitial ? 'entering' : 'entered') : 'exiting',
  )
  const cachedChildren = useRef(children)
  const wrapperRef = useRef<HTMLDivElement>(null)
  const restoreTargetRef = useRef<HTMLElement | null>(null)
  const wasShownRef = useRef(false)
  const initialVisibleWithoutMotion = useRef(show && !animateInitial)

  if (show) cachedChildren.current = children

  useEffect(() => {
    if (show) {
      if (!mounted) setMounted(true)
      if (initialVisibleWithoutMotion.current) {
        setState('entered')
        return
      }
      setState('entering')
      const enterFrame = window.requestAnimationFrame(() => setState('entered'))
      return () => window.cancelAnimationFrame(enterFrame)
    }

    if (!mounted) return

    initialVisibleWithoutMotion.current = false
    setState('exiting')
    const exitTimer = window.setTimeout(() => setMounted(false), exitMs)
    return () => window.clearTimeout(exitTimer)
  }, [animateInitial, exitMs, mounted, show])

  useEffect(() => {
    if (focusMode !== 'modal') {
      wasShownRef.current = show
      return
    }

    if (show && !wasShownRef.current) {
      restoreTargetRef.current =
        document.activeElement instanceof HTMLElement
          ? document.activeElement
          : null
    } else if (!show && wasShownRef.current) {
      const preferredTarget = restoreFocusRef?.current ?? null
      const restoreTarget = canRestoreFocus(preferredTarget)
        ? preferredTarget
        : restoreTargetRef.current
      if (canRestoreFocus(restoreTarget)) {
        restoreTarget.focus({ preventScroll: true })
      }
    }

    wasShownRef.current = show
  }, [focusMode, restoreFocusRef, show])

  useEffect(() => {
    if (focusMode !== 'modal' || !show || !mounted) return

    const focusModal = () => {
      const root = wrapperRef.current
      if (!root || root.hasAttribute('inert')) return false
      const scope = focusScope(root)
      const target =
        initialFocusRef?.current ??
        scope.querySelector<HTMLElement>('[data-motion-autofocus]') ??
        (scope.matches('[tabindex]') ? scope : null) ??
        focusableDescendants(scope)[0] ??
        scope
      target.focus({ preventScroll: true })
      return scope.contains(document.activeElement)
    }

    const root = wrapperRef.current
    const focusObserver = new MutationObserver(() => {
      if (focusModal()) focusObserver.disconnect()
    })
    if (root) {
      focusObserver.observe(root, {
        subtree: true,
        childList: true,
        attributes: true,
        attributeFilter: ['inert'],
      })
    }
    const focusFrame = window.requestAnimationFrame(() => {
      if (focusModal()) focusObserver.disconnect()
    })
    const focusTimeout = window.setTimeout(
      () => focusObserver.disconnect(),
      1_000,
    )

    const trapFocus = (event: KeyboardEvent) => {
      if (event.key !== 'Tab') return
      const root = wrapperRef.current
      if (!root) return
      const scope = focusScope(root)
      const focusable = focusableDescendants(scope)
      if (focusable.length === 0) {
        event.preventDefault()
        scope.focus({ preventScroll: true })
        return
      }

      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      const active = document.activeElement
      const activeIndex = focusable.indexOf(active as HTMLElement)
      if (event.shiftKey && (activeIndex <= 0 || !scope.contains(active))) {
        event.preventDefault()
        last.focus({ preventScroll: true })
      } else if (
        !event.shiftKey &&
        (activeIndex < 0 ||
          activeIndex === focusable.length - 1 ||
          !scope.contains(active))
      ) {
        event.preventDefault()
        first.focus({ preventScroll: true })
      }
    }

    document.addEventListener('keydown', trapFocus, true)
    return () => {
      window.cancelAnimationFrame(focusFrame)
      window.clearTimeout(focusTimeout)
      focusObserver.disconnect()
      document.removeEventListener('keydown', trapFocus, true)
    }
  }, [focusMode, initialFocusRef, mounted, show])

  if (!mounted) return null

  const classes = ['motion-presence', className].filter(Boolean).join(' ')

  return (
    <div
      ref={wrapperRef}
      className={classes}
      data-motion-state={state}
      aria-hidden={state === 'exiting' ? 'true' : undefined}
      inert={state === 'exiting' ? true : undefined}
      tabIndex={focusMode === 'modal' ? -1 : undefined}
    >
      {cachedChildren.current}
    </div>
  )
}

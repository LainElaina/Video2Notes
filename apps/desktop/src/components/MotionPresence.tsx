import {
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react'

type MotionPresenceState = 'entering' | 'entered' | 'exiting'

interface MotionPresenceProps {
  show: boolean
  children: ReactNode
  className?: string
  exitMs?: number
}

export function MotionPresence({
  show,
  children,
  className,
  exitMs = 160,
}: MotionPresenceProps) {
  const [mounted, setMounted] = useState(show)
  const [state, setState] = useState<MotionPresenceState>(
    show ? 'entering' : 'exiting',
  )
  const cachedChildren = useRef(children)

  if (show) cachedChildren.current = children

  useEffect(() => {
    if (show) {
      if (!mounted) setMounted(true)
      setState('entering')
      const enterFrame = window.requestAnimationFrame(() => setState('entered'))
      return () => window.cancelAnimationFrame(enterFrame)
    }

    if (!mounted) return

    setState('exiting')
    const exitTimer = window.setTimeout(() => setMounted(false), exitMs)
    return () => window.clearTimeout(exitTimer)
  }, [exitMs, mounted, show])

  if (!mounted) return null

  const classes = ['motion-presence', className].filter(Boolean).join(' ')

  return (
    <div
      className={classes}
      data-motion-state={state}
      aria-hidden={state === 'exiting' ? 'true' : undefined}
      inert={state === 'exiting' ? true : undefined}
    >
      {cachedChildren.current}
    </div>
  )
}

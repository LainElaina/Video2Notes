import { useCallback, useRef, useState } from 'react'

/**
 * Tracks in-flight store actions per key so the triggering control (and its
 * siblings) can disable and expose aria-busy until the real promise settles.
 * Store actions report failures through the notice bar and never reject, so
 * settling always clears the pending flag.
 *
 * The re-entrancy guard engages synchronously, but the visible busy state is
 * deferred one microtask: actions that settle immediately (demo shortcuts,
 * mocked actions) never fabricate a busy flash, while genuinely async work
 * still surfaces honest pending feedback before the next paint.
 */
export function usePendingActions() {
  const [pendingKeys, setPendingKeys] = useState<readonly string[]>([])
  const pendingRef = useRef(new Set<string>())

  const track = useCallback((key: string, action: () => Promise<void> | void) => {
    if (pendingRef.current.has(key)) return
    let result: Promise<void> | void
    try {
      result = action()
    } catch {
      return
    }
    if (!result || typeof result.then !== 'function') return
    pendingRef.current.add(key)
    const settle = () => {
      pendingRef.current.delete(key)
      setPendingKeys(current =>
        current.includes(key) ? current.filter(item => item !== key) : current,
      )
    }
    void result.then(settle, settle)
    queueMicrotask(() => {
      if (!pendingRef.current.has(key)) return
      setPendingKeys(current =>
        current.includes(key) ? current : [...current, key],
      )
    })
  }, [])

  const isPending = useCallback(
    (key: string) => pendingKeys.includes(key),
    [pendingKeys],
  )

  return { isPending, track }
}

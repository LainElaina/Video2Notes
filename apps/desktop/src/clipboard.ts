export async function copyText(text: string): Promise<void> {
  if (!text) throw new Error('Nothing to copy')

  if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return
    } catch {
      // WebView2 can deny the modern API when clipboard permissions are
      // restricted. Fall through to the selection-based local fallback.
    }
  }

  if (typeof document === 'undefined') throw new Error('Clipboard is unavailable')
  const input = document.createElement('textarea')
  input.value = text
  input.setAttribute('readonly', '')
  input.style.position = 'fixed'
  input.style.opacity = '0'
  input.style.pointerEvents = 'none'
  document.body.appendChild(input)
  input.select()
  const copied = document.execCommand('copy')
  input.remove()
  if (!copied) throw new Error('Clipboard permission was denied')
}

import { render } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  DEFAULT_UI_PREFERENCES,
  useUiPreferences,
} from '../stores/uiPreferences'
import { SynchronizedVideo } from './SynchronizedVideo'

const baseProps = {
  title: '演示视频',
  durationSeconds: 120,
  currentTimeSeconds: 12,
  onSeek: vi.fn(),
}

describe('SynchronizedVideo OCR corner', () => {
  beforeEach(() => {
    window.localStorage.clear()
    useUiPreferences.setState(DEFAULT_UI_PREFERENCES)
  })

  it('shows the honest fallback instead of a fabricated confidence', () => {
    const { container } = render(<SynchronizedVideo {...baseProps} />)

    const corner = container.querySelector('.ocr-corner')
    expect(corner).not.toBeNull()
    expect(corner).toHaveTextContent('OCR —')
    expect(corner).not.toHaveTextContent('0.93')
  })

  it('renders the real confidence value when data exists', () => {
    const { container } = render(
      <SynchronizedVideo {...baseProps} ocrConfidence={0.87} />,
    )

    expect(container.querySelector('.ocr-corner')).toHaveTextContent('OCR 0.87')
  })

  it('omits the corner for local media without OCR data', () => {
    const { container } = render(
      <SynchronizedVideo {...baseProps} src="file:///D:/demo.mp4" />,
    )

    expect(container.querySelector('.ocr-corner')).toBeNull()
  })
})

import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { EvidenceItem, EvidenceKind } from '../domain'
import { evidenceFixture } from '../fixtures'
import { EvidenceRail } from './EvidenceRail'

const denseEvidence = (count: number, durationSeconds: number): EvidenceItem[] => {
  const kinds: EvidenceKind[] = ['asr', 'ocr', 'visual', 'chapter']
  return Array.from({ length: count }, (_, index) => {
    const startSeconds = (index / count) * durationSeconds
    return {
      id: `DENSE-${index}`,
      kind: kinds[index % kinds.length],
      startSeconds,
      endSeconds: Math.min(durationSeconds, startSeconds + 0.2),
      label: `密集证据 ${index}`,
      rawText: `dense evidence ${index}`,
      confidence: 0.95,
      provider: 'test-provider',
    }
  })
}

describe('EvidenceRail', () => {
  it('seeks to the exact evidence timestamp when an event is selected', () => {
    const onSelect = vi.fn()
    render(
      <EvidenceRail
        evidence={evidenceFixture}
        durationSeconds={1518}
        currentTimeSeconds={92}
        selectedEvidenceId="FRAME-011"
        onSeek={vi.fn()}
        onSelect={onSelect}
      />,
    )

    fireEvent.click(
      screen.getByRole('button', {
        name: /画面证据 FRAME-011，01:32，关键帧：统一物理时间轴示意/,
      }),
    )

    expect(onSelect).toHaveBeenCalledWith('FRAME-011', 92)
  })

  it('keeps dense timelines bounded while preserving the selected evidence', () => {
    const evidence = denseEvidence(4_524, 1_518)
    const selected = evidence[4_511]
    const onSelect = vi.fn()
    const { container, rerender } = render(
      <EvidenceRail
        evidence={evidence}
        durationSeconds={1_518}
        currentTimeSeconds={92}
        selectedEvidenceId={selected.id}
        onSeek={vi.fn()}
        onSelect={onSelect}
      />,
    )

    const eventButtons = container.querySelectorAll<HTMLButtonElement>('.rail-event')
    expect(eventButtons.length).toBeLessThanOrEqual(4 * 144)

    const selectedButton = screen.getByRole('button', { name: new RegExp(selected.id) })
    expect(selectedButton).toHaveClass('is-selected')
    fireEvent.click(selectedButton)
    expect(onSelect).toHaveBeenCalledWith(selected.id, selected.startSeconds)

    rerender(
      <EvidenceRail
        evidence={evidence}
        durationSeconds={1_518}
        currentTimeSeconds={93}
        selectedEvidenceId={selected.id}
        onSeek={vi.fn()}
        onSelect={onSelect}
      />,
    )
    expect(container.querySelectorAll('.rail-event')).toHaveLength(eventButtons.length)
  })

  it('maps a click inside an aggregated pixel bucket to the nearest original evidence', () => {
    const evidence: EvidenceItem[] = [
      {
        id: 'FIRST',
        kind: 'asr',
        startSeconds: 10,
        endSeconds: 10.05,
        label: '第一条',
        rawText: 'first',
        confidence: 0.9,
        provider: 'test-provider',
      },
      {
        id: 'SECOND',
        kind: 'asr',
        startSeconds: 10.2,
        endSeconds: 10.25,
        label: '第二条',
        rawText: 'second',
        confidence: 0.9,
        provider: 'test-provider',
      },
    ]
    const onSelect = vi.fn()
    const { container } = render(
      <EvidenceRail
        evidence={evidence}
        durationSeconds={100}
        currentTimeSeconds={0}
        onSeek={vi.fn()}
        onSelect={onSelect}
      />,
    )
    const button = container.querySelector<HTMLButtonElement>('.track-asr .rail-event')
    const trackLine = button?.parentElement
    expect(button).not.toBeNull()
    expect(trackLine).not.toBeNull()
    vi.spyOn(trackLine!, 'getBoundingClientRect').mockReturnValue({
      left: 0,
      right: 1_000,
      top: 0,
      bottom: 8,
      width: 1_000,
      height: 8,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    })

    fireEvent.click(button!, { clientX: 102, detail: 1 })

    expect(onSelect).toHaveBeenCalledWith('SECOND', 10.2)
  })
})

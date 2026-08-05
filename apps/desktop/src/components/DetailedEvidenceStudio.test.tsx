import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { EvidenceItem, EvidenceKind } from '../domain'
import { makeTaskFixtures, noteFixture } from '../fixtures'
import { DetailedEvidenceStudio } from './DetailedEvidenceStudio'

const denseEvidence = (count: number, durationSeconds: number): EvidenceItem[] => {
  const kinds: EvidenceKind[] = ['asr', 'ocr', 'visual']
  return Array.from({ length: count }, (_, index) => {
    const startSeconds = (index / count) * durationSeconds
    return {
      id: `STUDIO-DENSE-${index}`,
      kind: kinds[index % kinds.length],
      startSeconds,
      endSeconds: Math.min(durationSeconds, startSeconds + 0.2),
      label: `工作室密集证据 ${index}`,
      rawText: `studio dense evidence ${index}`,
      confidence: 0.95,
      provider: 'test-provider',
    }
  })
}

describe('DetailedEvidenceStudio', () => {
  it('bounds dense timeline DOM and keeps exact selected-evidence interaction', () => {
    const baseTask = makeTaskFixtures().find(task => task.id === 'task-complete')!
    const evidence = denseEvidence(4_524, baseTask.source.durationSeconds)
    const selected = evidence[4_510]
    const task = { ...baseTask, evidence }
    const onSelectEvidence = vi.fn()
    const onSeek = vi.fn()
    const { container, rerender } = render(
      <DetailedEvidenceStudio
        task={task}
        note={noteFixture}
        currentTimeSeconds={92}
        selectedEvidenceId={selected.id}
        onSeek={onSeek}
        onSelectEvidence={onSelectEvidence}
        onRequestRework={vi.fn()}
      />,
    )

    const timelineButtons = container.querySelectorAll<HTMLButtonElement>(
      '.timeline-track-canvas > button',
    )
    expect(timelineButtons.length).toBeLessThanOrEqual(3 * 168)
    expect(screen.getByText('4524 个可见证据区间')).toBeInTheDocument()

    const selectedButton = screen.getByRole('button', { name: new RegExp(selected.id) })
    expect(selectedButton).toHaveClass('is-selected')
    fireEvent.click(selectedButton)
    expect(onSelectEvidence).toHaveBeenCalledWith(selected.id, selected.startSeconds)

    rerender(
      <DetailedEvidenceStudio
        task={task}
        note={noteFixture}
        currentTimeSeconds={93}
        selectedEvidenceId={selected.id}
        onSeek={onSeek}
        onSelectEvidence={onSelectEvidence}
        onRequestRework={vi.fn()}
      />,
    )
    expect(container.querySelectorAll('.timeline-track-canvas > button')).toHaveLength(
      timelineButtons.length,
    )
  })
})

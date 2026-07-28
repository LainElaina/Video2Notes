import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { evidenceFixture } from '../fixtures'
import { EvidenceRail } from './EvidenceRail'

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
})

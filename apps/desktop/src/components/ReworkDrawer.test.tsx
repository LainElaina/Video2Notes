import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { makeTaskFixtures } from '../fixtures'
import { useStudioStore } from '../store'
import { useUiPreferences } from '../stores/uiPreferences'
import { ReworkDrawer } from './ReworkDrawer'

describe('ReworkDrawer processing scope safeguards', () => {
  beforeEach(() => {
    useStudioStore.getState().resetDemo()
    useUiPreferences.getState().resetPreferences()
  })

  it('opens on speech controls and disables visual rework for audio-only tasks', () => {
    const completed = makeTaskFixtures().find(task => task.id === 'task-complete')!
    const task = { ...completed, processingScope: 'audio_only' as const }

    render(<ReworkDrawer task={task} onClose={vi.fn()} />)

    expect(screen.getByRole('tab', { name: '画面' })).toBeDisabled()
    expect(screen.getByRole('tab', { name: '语音' })).toHaveAttribute(
      'aria-selected',
      'true',
    )
    expect(screen.getByText(/不能执行画面重新识别或 OCR 返工/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /执行画面返工/ })).not.toBeInTheDocument()
  })

  it('localizes the audio-only safeguards and actions in English', () => {
    useUiPreferences.getState().setLocale('en-US')
    const completed = makeTaskFixtures().find(task => task.id === 'task-complete')!
    const task = { ...completed, processingScope: 'audio_only' as const }

    render(<ReworkDrawer task={task} onClose={vi.fn()} />)

    expect(screen.getByRole('heading', { name: 'Range rework' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Visual' })).toBeDisabled()
    expect(screen.getByRole('tab', { name: 'Speech' })).toHaveAttribute(
      'aria-selected',
      'true',
    )
    expect(screen.getByText(/backend has no visual baseline/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run speech rework' })).toBeInTheDocument()
  })
})

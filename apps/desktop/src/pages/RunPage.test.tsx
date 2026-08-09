import { fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { makeTaskFixtures } from '../fixtures'
import { useStudioStore } from '../store'
import { RunPage } from './RunPage'

describe('run processing workspace', () => {
  beforeEach(() => {
    useStudioStore.getState().resetDemo()
  })

  it('keeps the stage rail and processing-log stage filter synchronized', () => {
    const task = makeTaskFixtures()[0]
    task.telemetry = [
      {
        sequence: 1,
        runId: task.id,
        state: 'running',
        stage: 'audio.asr',
        progress: 0.4,
        message: '正在校准语音时间戳。',
        metrics: {},
        createdAt: '2026-08-10T09:00:00Z',
      },
      {
        sequence: 2,
        runId: task.id,
        state: 'running',
        stage: 'ocr.extract',
        progress: 0.2,
        message: '正在识别变化画面中的文字。',
        metrics: {},
        createdAt: '2026-08-10T09:00:01Z',
      },
    ]
    useStudioStore.setState({
      tasks: [task],
      activeTaskId: task.id,
    })

    render(<RunPage />)

    const stageRail = screen.getByRole('region', { name: '处理阶段' })
    const speechStage = within(stageRail).getByRole('button', { name: /语音，/ })
    const visionStage = within(stageRail).getByRole('button', { name: /视觉，/ })
    const logStage = screen.getByRole('combobox', { name: '阶段' })

    fireEvent.click(visionStage)
    expect(logStage).toHaveValue('vision')
    expect(screen.getByText('正在识别变化画面中的文字。')).toBeInTheDocument()
    expect(screen.queryByText('正在校准语音时间戳。')).not.toBeInTheDocument()
    expect(visionStage).toHaveClass('is-selected')

    fireEvent.change(logStage, { target: { value: 'speech' } })
    expect(screen.getByText('正在校准语音时间戳。')).toBeInTheDocument()
    expect(screen.queryByText('正在识别变化画面中的文字。')).not.toBeInTheDocument()
    expect(speechStage).toHaveClass('is-selected')
  })
})

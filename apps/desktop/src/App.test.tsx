import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import App from './App'
import { useStudioStore } from './store'

describe('desktop workflow', () => {
  beforeEach(() => {
    useStudioStore.getState().resetDemo()
  })

  it('probes a source and starts an Accurate task from the workbench', () => {
    render(<App />)

    expect(screen.getByRole('heading', { name: '将视频变成可核查的笔记' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '探测来源' }))
    expect(screen.getByText('SOURCE VERIFIED')).toBeInTheDocument()
    expect(screen.getByText('1080p · 60 fps')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /开始处理/ }))
    expect(screen.getByText(/EVIDENCE BUILD-UP/)).toBeInTheDocument()
    expect(useStudioStore.getState().tasks[0].mode).toBe('accurate')
  })

  it('pauses and resumes a running task with visible controls', () => {
    useStudioStore.getState().navigate('tasks')
    render(<App />)

    fireEvent.click(screen.getByRole('button', { name: '暂停' }))
    expect(screen.getByRole('button', { name: '继续' })).toBeInTheDocument()
    expect(useStudioStore.getState().tasks[0].status).toBe('paused')

    fireEvent.click(screen.getByRole('button', { name: '继续' }))
    expect(screen.getByRole('button', { name: '暂停' })).toBeInTheDocument()
  })

  it('changes a compatible role binding from the model settings page', () => {
    useStudioStore.getState().navigate('models')
    render(<App />)

    const roleSelect = screen.getByLabelText('为主要语音识别选择模型')
    fireEvent.change(roleSelect, { target: { value: 'model-funasr' } })

    expect(useStudioStore.getState().roles.find(role => role.id === 'asr-primary')?.modelId).toBe(
      'model-funasr',
    )
    expect(screen.getByRole('status')).toHaveTextContent('主要语音识别 已绑定到 FunASR Paraformer')
  })
})

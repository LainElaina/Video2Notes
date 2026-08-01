import { fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { ModelsPage } from './ModelsPage'
import { useStudioStore } from '../store'

describe('models and performance workspace', () => {
  beforeEach(() => {
    useStudioStore.getState().resetDemo()
  })

  it('renders every catalog role and only lists fully compatible enabled models', () => {
    render(<ModelsPage />)

    expect(screen.getByLabelText('为画面变化检测选择模型')).toBeInTheDocument()
    expect(screen.getByLabelText('为翻译与术语统一选择模型')).toBeInTheDocument()

    const asrSelect = screen.getByLabelText('为主要语音识别选择模型')
    expect(within(asrSelect).getByRole('option', { name: /Whisper large-v3/ })).toBeInTheDocument()
    expect(within(asrSelect).getByRole('option', { name: /FunASR Paraformer/ })).toBeInTheDocument()
    expect(within(asrSelect).queryByRole('option', { name: /Qwen 3 14B/ })).not.toBeInTheDocument()
    expect(within(asrSelect).queryByText(/能力不匹配/)).not.toBeInTheDocument()
  })

  it('keeps guided controls concise and exposes validated professional overrides on demand', () => {
    render(<ModelsPage />)

    expect(screen.getByRole('button', { name: /小白模式/ })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(screen.queryByLabelText('CPU worker')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /专业模式/ }))
    fireEvent.change(screen.getByLabelText('CPU worker'), { target: { value: '10' } })
    fireEvent.click(screen.getByRole('button', { name: '保存性能设置' }))

    expect(useStudioStore.getState().performance).toMatchObject({
      experienceMode: 'professional',
      overrides: { cpuWorkers: 10 },
    })
  })

  it('does not infer capabilities when a discovered model is selected', () => {
    useStudioStore.setState({
      selectedProviderId: 'provider-openai',
      discoveryProviderId: 'provider-openai',
      providerDiscoveryStatus: 'ready',
      discoveredModels: [
        {
          modelId: 'model-from-directory',
          displayName: 'Directory model',
          contextWindow: 128_000,
        },
      ],
    })
    render(<ModelsPage />)

    fireEvent.click(screen.getByRole('button', { name: /Directory model/ }))

    expect(screen.getByLabelText('模型 ID')).toHaveValue('model-from-directory')
    expect(screen.getByLabelText('文本理解')).not.toBeChecked()
    expect(screen.getByRole('button', { name: '创建已确认模型' })).toBeDisabled()
  })
})

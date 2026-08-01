import { act, fireEvent, render, screen, within } from '@testing-library/react'
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

  it('separates packaged runtime readiness from on-demand model weights', () => {
    render(<ModelsPage />)

    expect(screen.getByText('工具与识别模型准备')).toBeInTheDocument()
    expect(screen.getByText('24 GB+ 显存')).toBeInTheDocument()
    expect(screen.getByText(/模型权重不会随 EXE 预装/)).toBeInTheDocument()
    expect(screen.getByText('FFmpeg')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '一键准备推荐模型' })).toBeDisabled()
    expect(
      screen.queryByLabelText('选择组件 faster-whisper large-v3'),
    ).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /专业模式/ }))
    const asrSelection = screen.getByLabelText(
      '选择组件 faster-whisper large-v3',
    )
    const ocrSelection = screen.getByLabelText(
      '选择组件 PaddleOCR PP-OCRv5 server',
    )
    expect(asrSelection).toBeChecked()
    expect(ocrSelection).toBeChecked()

    fireEvent.click(asrSelection)
    expect(useStudioStore.getState().selectedComponentIds).toEqual([
      'ocr-paddle-ppocrv5-server',
    ])
    expect(screen.getByRole('button', { name: '准备所选组件' })).toBeDisabled()
  })

  it('announces component preparation progress, activation, and errors', () => {
    useStudioStore.setState({
      backend: { mode: 'real', version: 'test', detail: 'ready' },
      componentPreparationStatus: 'preparing',
      componentPreparationResults: [],
      componentPreparationActivated: false,
      componentPreparationError: undefined,
    })
    render(<ModelsPage />)

    expect(screen.getByRole('button', { name: '正在下载并校验…' })).toHaveAttribute(
      'aria-busy',
      'true',
    )

    act(() => {
      useStudioStore.setState({
        componentPreparationStatus: 'success',
        componentPreparationResults: [
          {
            componentId: 'asr-faster-whisper-large-v3',
            status: 'prepared',
            resumed: false,
          },
          {
            componentId: 'ocr-paddle-ppocrv5-server',
            status: 'reused',
            resumed: false,
          },
        ],
        componentPreparationActivated: true,
      })
    })
    expect(screen.getByText('推荐模型已激活')).toBeInTheDocument()
    expect(screen.getByText(/ocr-paddle-ppocrv5-server：已复用/)).toBeInTheDocument()

    act(() => {
      useStudioStore.setState({
        componentPreparationStatus: 'error',
        componentPreparationError: '网络中断，可稍后续传。',
        componentPreparationActivated: false,
      })
    })
    expect(screen.getByRole('alert')).toHaveTextContent('网络中断，可稍后续传。')
  })
})

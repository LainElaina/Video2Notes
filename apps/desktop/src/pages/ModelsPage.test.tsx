import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { ModelsPage } from './ModelsPage'
import { useStudioStore } from '../store'
import { useUiPreferences } from '../stores/uiPreferences'

describe('models and performance workspace', () => {
  beforeEach(() => {
    useStudioStore.getState().resetDemo()
    useUiPreferences.setState({ locale: 'zh-CN' })
  })

  it('switches the settings workspace and professional controls to English', () => {
    render(<ModelsPage />)

    fireEvent.click(screen.getByRole('radio', { name: 'English' }))

    expect(useUiPreferences.getState().locale).toBe('en-US')
    expect(screen.getByRole('heading', { name: 'Settings center' })).toBeInTheDocument()

    const navigation = screen.getByRole('navigation', { name: 'Settings sections' })
    expect(within(navigation).getByRole('link', { name: /Display & type/ })).toBeInTheDocument()
    expect(within(navigation).getByRole('link', { name: /Performance & resources/ })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'How this computer should work' })).toBeInTheDocument()
    expect(
      screen.getByRole('heading', {
        name: 'Protocols, authentication, and model registry',
      }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('heading', {
        name: 'Choose a compatible model for every stage',
      }),
    ).toBeInTheDocument()
    expect(screen.getByLabelText('Text understanding')).toBeInTheDocument()
    expect(
      screen.getByLabelText('Choose a model for Frame-change detection'),
    ).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Custom performance' }))
    expect(screen.getByLabelText('CPU workers')).toBeInTheDocument()
    expect(screen.getByLabelText('OCR inference width')).toBeInTheDocument()
  })

  it('provides an accessible settings directory that focuses the selected section', () => {
    render(<ModelsPage />)

    const navigation = screen.getByRole('navigation', { name: '设置分区' })
    const appearanceLink = within(navigation).getByRole('link', {
      name: /显示与字体/,
    })
    const runtimeLink = within(navigation).getByRole('link', {
      name: /依赖与运行时/,
    })
    const runtimeSection = screen
      .getByRole('heading', { name: '依赖与运行时' })
      .closest('section')

    expect(appearanceLink).toHaveAttribute('aria-current', 'location')
    expect(runtimeSection).toHaveAttribute('id', 'runtime-packages')
    expect(runtimeSection).toHaveAttribute('tabindex', '-1')

    fireEvent.click(runtimeLink)

    expect(runtimeLink).toHaveAttribute('aria-current', 'location')
    expect(runtimeSection).toHaveFocus()
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

    expect(screen.getByRole('button', { name: /自动配置/ })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(screen.queryByLabelText('CPU worker')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('OCR 推理宽度')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /自定义性能/ }))
    fireEvent.change(screen.getByLabelText('CPU worker'), { target: { value: '10' } })
    fireEvent.change(screen.getByLabelText('OCR 推理宽度'), {
      target: { value: '1536' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存性能设置' }))

    expect(useStudioStore.getState().performance).toMatchObject({
      experienceMode: 'professional',
      overrides: { cpuWorkers: 10, ocrInferenceMaxWidth: 1536 },
    })
  })

  it('dismisses the provider editor with Escape and restores focus after its exit', async () => {
    render(<ModelsPage />)

    const trigger = screen.getByRole('button', { name: '新增供应商' })
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    const editorId = trigger.getAttribute('aria-controls')
    expect(editorId).toBeTruthy()

    fireEvent.click(trigger)

    expect(trigger).toHaveAttribute('aria-expanded', 'true')
    const editor = screen.getByRole('region', { name: '新增供应商' })
    expect(editor).toHaveAttribute('id', editorId)

    const providerId = screen.getByLabelText('供应商 ID')
    providerId.focus()
    expect(providerId).toHaveFocus()
    fireEvent.keyDown(providerId, { key: 'Escape' })

    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(
      screen.getByRole('region', { name: '新增供应商', hidden: true }),
    ).toBeInTheDocument()
    await waitFor(() => expect(trigger).toHaveFocus())
    await waitFor(() =>
      expect(
        screen.queryByRole('region', { name: '新增供应商', hidden: true }),
      ).not.toBeInTheDocument(),
    )
  })

  it('offers CUDA only for engines whose runtime capability is available', () => {
    render(<ModelsPage />)

    fireEvent.click(screen.getByRole('button', { name: /自定义性能/ }))
    const asrDevice = screen.getByLabelText('ASR 设备')
    const ocrDevice = screen.getByLabelText('OCR 设备')

    expect(within(asrDevice).getByRole('option', { name: 'CUDA' })).toBeInTheDocument()
    expect(within(ocrDevice).queryByRole('option', { name: 'CUDA' })).not.toBeInTheDocument()
  })

  it('removes unavailable CUDA choices and normalizes stale device overrides', () => {
    const state = useStudioStore.getState()
    const systemReport = state.systemReport!
    useStudioStore.setState({
      performance: {
        ...state.performance,
        experienceMode: 'professional',
        overrides: {
          ...state.performance.overrides,
          asrDevice: 'cuda',
          ocrDevice: 'cuda',
        },
      },
      systemReport: {
        ...systemReport,
        acceleration: {
          asr: {
            ...systemReport.acceleration.asr,
            cudaAvailable: false,
            deviceCount: 0,
          },
          ocr: {
            ...systemReport.acceleration.ocr,
            cudaAvailable: false,
            deviceCount: 0,
          },
        },
      },
    })

    render(<ModelsPage />)

    const asrDevice = screen.getByLabelText('ASR 设备')
    const ocrDevice = screen.getByLabelText('OCR 设备')
    expect(within(asrDevice).queryByRole('option', { name: 'CUDA' })).not.toBeInTheDocument()
    expect(within(ocrDevice).queryByRole('option', { name: 'CUDA' })).not.toBeInTheDocument()
    expect(asrDevice).toHaveValue('cpu')
    expect(ocrDevice).toHaveValue('cpu')

    fireEvent.click(screen.getByRole('button', { name: '保存性能设置' }))
    expect(useStudioStore.getState().performance.overrides).toMatchObject({
      asrDevice: 'cpu',
      ocrDevice: 'cpu',
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

    expect(
      screen.getByRole('heading', { name: '依赖与运行时' }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('heading', { name: '本地识别模型权重' }),
    ).toBeInTheDocument()
    expect(screen.getByText('24 GB+ 显存')).toBeInTheDocument()
    expect(screen.getByText(/cuda · float16 · CUDA 运行时就绪/i)).toBeInTheDocument()
    expect(screen.getByText(/cpu · CPU 运行时/i)).toBeInTheDocument()
    expect(screen.getByText(/模型权重不会随 EXE 预装/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '一键准备推荐模型' })).toBeDisabled()
    expect(
      screen.queryByLabelText('选择组件 faster-whisper large-v3'),
    ).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /自定义性能/ }))
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

  it('shows every runtime ownership class and limits destructive actions by ownership', () => {
    render(<ModelsPage />)

    expect(screen.getByText('随应用提供')).toBeInTheDocument()
    expect(screen.getByText('应用受管')).toBeInTheDocument()
    expect(screen.getByText('复用本机环境')).toBeInTheDocument()
    expect(screen.getByText('自定义目录')).toBeInTheDocument()

    const bundled = screen
      .getByRole('heading', { name: 'Core media tools' })
      .closest('article')
    const managed = screen
      .getByRole('heading', { name: 'NVIDIA ASR worker' })
      .closest('article')
    const system = screen
      .getByRole('heading', { name: 'System FFmpeg' })
      .closest('article')
    const custom = screen
      .getByRole('heading', { name: 'Custom PaddleOCR worker' })
      .closest('article')

    expect(bundled).not.toBeNull()
    expect(managed).not.toBeNull()
    expect(system).not.toBeNull()
    expect(custom).not.toBeNull()
    expect(within(managed!).getByRole('button', { name: '卸载' })).toBeDisabled()
    expect(
      within(managed!).queryByRole('button', { name: '忘记登记' }),
    ).not.toBeInTheDocument()
    expect(
      within(custom!).getByRole('button', { name: '忘记登记' }),
    ).toBeDisabled()
    expect(
      within(custom!).queryByRole('button', { name: '卸载' }),
    ).not.toBeInTheDocument()
    expect(
      within(bundled!).queryByRole('button', { name: /卸载|忘记登记/ }),
    ).not.toBeInTheDocument()
    expect(
      within(system!).queryByRole('button', { name: /卸载|忘记登记/ }),
    ).not.toBeInTheDocument()
  })

  it('exposes explicit compatible runtime bindings in professional mode', () => {
    render(<ModelsPage />)

    expect(screen.queryByRole('heading', { name: '功能绑定' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /自定义性能/ }))

    expect(screen.getByRole('heading', { name: '功能绑定' })).toBeInTheDocument()
    const videoRuntime = screen.getByLabelText('为视频处理选择运行时')
    const asrRuntime = screen.getByLabelText('为本地语音识别选择运行时')
    const ocrRuntime = screen.getByLabelText('为画面文字识别选择运行时')

    expect(
      within(videoRuntime).getByRole('option', {
        name: 'Core media tools · 随应用提供',
      }),
    ).toBeInTheDocument()
    expect(
      within(videoRuntime).getByRole('option', {
        name: 'System FFmpeg · 复用本机环境',
      }),
    ).toBeInTheDocument()
    expect(asrRuntime).toHaveValue('managed:local-inference-nvidia-asr:0.1.0')
    expect(ocrRuntime).toHaveValue('custom:paddleocr:3.0.0')
  })

  it('renders completed runtime operation progress and success state', () => {
    render(<ModelsPage />)

    const operationTitle = screen.getByText(
      '安装 · local-inference-nvidia-asr-cu129-win-x64',
    )
    const operation = operationTitle.closest('article')
    const progress = screen.getByRole('progressbar', {
      name: 'local-inference-nvidia-asr-cu129-win-x64 操作进度',
    })

    expect(operation).not.toBeNull()
    expect(operation).toHaveClass('status-succeeded')
    expect(progress).toHaveAttribute('value', '1')
    expect(within(operation!).getByText('已完成')).toBeInTheDocument()
    expect(within(operation!).getByText('100%')).toBeInTheDocument()
    expect(
      within(operation!).getByText(/组件已安装并通过 worker 探测/),
    ).toBeInTheDocument()
  })

  it('keeps acceleration status pending until the backend system report arrives', () => {
    useStudioStore.setState({ systemReport: undefined })

    render(<ModelsPage />)

    expect(screen.getAllByText(/加速能力检测中/)).toHaveLength(2)
    expect(screen.queryByText(/CPU 回退/)).not.toBeInTheDocument()
    expect(screen.queryByText(/CPU 运行时/)).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /自定义性能/ }))
    expect(
      within(screen.getByLabelText('ASR 设备')).queryByRole('option', {
        name: 'CUDA',
      }),
    ).not.toBeInTheDocument()
    expect(
      within(screen.getByLabelText('OCR 设备')).queryByRole('option', {
        name: 'CUDA',
      }),
    ).not.toBeInTheDocument()
  })

  it('announces component preparation progress, activation, and errors', () => {
    useStudioStore.setState({
      backend: { mode: 'real', version: 'test', detail: 'ready' },
      componentPreparationStatus: 'preparing',
      componentPreparationResults: [],
      componentPreparationActivated: false,
      componentPreparationActivatedRoles: [],
      componentPreparationBlockedRoles: [],
      componentPreparationWarnings: [],
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
        componentPreparationActivatedRoles: ['asr.primary'],
        componentPreparationBlockedRoles: ['ocr.primary'],
        componentPreparationWarnings: ['OCR 权重仍需校验。'],
      })
    })
    expect(screen.getByText('组件已准备，部分角色仍不可用')).toBeInTheDocument()
    expect(screen.getByText('已激活：主要语音识别')).toBeInTheDocument()
    expect(screen.getByText('未激活：主要 OCR')).toBeInTheDocument()
    expect(screen.getByText('提示：OCR 权重仍需校验。')).toBeInTheDocument()
    expect(screen.getByText(/ocr-paddle-ppocrv5-server：已复用/)).toBeInTheDocument()

    act(() => {
      useStudioStore.setState({
        componentPreparationStatus: 'error',
        componentPreparationError: '网络中断，可稍后续传。',
        componentPreparationActivated: false,
        componentPreparationActivatedRoles: [],
        componentPreparationBlockedRoles: [],
        componentPreparationWarnings: [],
      })
    })
    expect(screen.getByRole('alert')).toHaveTextContent('网络中断，可稍后续传。')
  })
})

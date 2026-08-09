import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import App from './App'
import { useStudioStore } from './store'
import {
  DEFAULT_UI_PREFERENCES,
  UI_PREFERENCES_STORAGE_KEY,
  useUiPreferences,
} from './stores/uiPreferences'

describe('desktop workflow', () => {
  beforeEach(() => {
    window.localStorage.clear()
    useUiPreferences.setState(DEFAULT_UI_PREFERENCES)
    useStudioStore.getState().resetDemo()
  })

  it('defaults to the precision-light guided shell and persists UI choices', () => {
    render(<App />)

    const shell = screen.getByRole('main').closest('.app-shell')
    expect(shell).toHaveAttribute('data-theme', 'precision-light')
    expect(shell).toHaveAttribute('data-workspace-mode', 'guided')
    expect(shell).toHaveAttribute('data-font-size', 'comfortable')
    expect(screen.getByRole('button', { name: '简约视图' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )

    fireEvent.click(screen.getByRole('button', { name: '数据工作室' }))
    fireEvent.change(screen.getByLabelText('主题预置'), {
      target: { value: 'studio-graphite' },
    })
    fireEvent.click(screen.getByRole('button', { name: '设置' }))
    fireEvent.click(screen.getByRole('radio', { name: /大字/ }))

    expect(shell).toHaveAttribute('data-theme', 'studio-graphite')
    expect(shell).toHaveAttribute('data-workspace-mode', 'professional')
    expect(shell).toHaveAttribute('data-font-size', 'large')
    expect(JSON.parse(window.localStorage.getItem(UI_PREFERENCES_STORAGE_KEY) ?? '{}')).toEqual({
      workspaceMode: 'professional',
      themePreset: 'studio-graphite',
      fontSizePreset: 'large',
    })
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

  it('marks a verified source stale when its processing policy changes', () => {
    render(<App />)

    fireEvent.click(screen.getByRole('button', { name: '探测来源' }))
    expect(screen.getByText('SOURCE VERIFIED')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('radio', { name: /Fast/ }))

    expect(screen.getByText('SOURCE NEEDS REFRESH')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '需重新探测' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '重新探测' })).toBeInTheDocument()
    expect(useStudioStore.getState().draft.status).toBe('stale')
  })

  it('shows a returned machine estimate and keeps generic copy for missing modes', () => {
    const store = useStudioStore.getState()
    store.probeSource()
    useStudioStore.setState({
      processingEstimates: {
        fast: {
          hardwareTier: 'gpu_12gb',
          qualityMode: 'fast',
          mediaDurationSeconds: 1518,
          lowerSeconds: 31,
          upperSeconds: 62,
          lowerRealtimeFactor: 0.02,
          upperRealtimeFactor: 0.04,
          basis: 'engineering_budget_v1',
          precisionIntent: '快速证据初稿',
          notes: [],
        },
      },
      processingEstimateStatus: 'partial',
      processingEstimateError: 'balanced: temporarily unavailable',
    })

    render(<App />)

    expect(screen.getByText('本机工程预算 00:31–01:02 · 0.02–0.04×')).toBeInTheDocument()
    expect(screen.getByText('基准实测 3.68× · 探测后显示本机预算')).toBeInTheDocument()
    expect(screen.getByText(/未返回的模式继续显示通用区间/)).toBeInTheDocument()
  })

  it('reveals the measured tier reference inline and exposes honest performance scopes', async () => {
    render(<App />)

    const guide = screen.getByRole('button', { name: /实测档位与性能说明/ })
    expect(guide).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(guide)

    expect(guide).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('table', { name: '完整视频三档处理指标' })).toBeVisible()
    expect(screen.getByText('3013.919 秒')).toBeInTheDocument()
    expect(screen.getByText(/墙钟时间占比，不是 CPU\/GPU 利用率/)).toBeInTheDocument()
    expect(screen.getByText(/这些预算是允许上限/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('radio', { name: /仅识别音频/ }))
    expect(useStudioStore.getState().draft.processingScope).toBe('audio_only')
    expect(screen.getAllByText('仅音频 · 等待本机估算')).toHaveLength(3)
    expect(screen.getByText(/专有名词仍应人工复核/)).toBeInTheDocument()

    const advancedOptions = screen.getByRole('button', { name: /高级选项/ })
    expect(advancedOptions).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(advancedOptions)

    expect(advancedOptions).toHaveAttribute('aria-expanded', 'true')
    const controlledPanelId = advancedOptions.getAttribute('aria-controls')
    expect(controlledPanelId).toBeTruthy()
    expect(document.getElementById(controlledPanelId!)).toBeInTheDocument()
    expect(screen.getByText('本任务已跳过画面处理')).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: '嵌入关键帧截图' })).toBeDisabled()

    fireEvent.click(advancedOptions)
    expect(advancedOptions).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByText('本任务已跳过画面处理')).toBeInTheDocument()
    await waitFor(() =>
      expect(screen.queryByText('本任务已跳过画面处理')).not.toBeInTheDocument(),
    )
  })

  it('disables audio-only when the connected backend does not declare support', () => {
    useStudioStore.setState({
      backend: { mode: 'real', version: 'legacy', detail: 'legacy backend' },
    })

    render(<App />)

    expect(screen.getByRole('radio', { name: /仅识别音频/ })).toBeDisabled()
    expect(screen.getByText(/后端版本不支持仅音频/)).toBeInTheDocument()
  })

  it('locks both scope controls while a task submission is in flight', () => {
    useStudioStore.setState(state => ({
      submissionInFlight: true,
      draft: { ...state.draft, status: 'submitting' },
    }))

    render(<App />)

    expect(screen.getByRole('radio', { name: /完整音画/ })).toBeDisabled()
    expect(screen.getByRole('radio', { name: /仅识别音频/ })).toBeDisabled()
  })

  it('shows blocked dependency details and opens the runtime manager', () => {
    useStudioStore.setState({
      backend: {
        mode: 'real',
        version: 'test',
        capabilities: ['processing_scope_audio_only'],
        detail: 'ready',
      },
      jobPreflightStatus: 'ready',
      jobPreflightDialogOpen: true,
      jobPreflight: {
        state: 'blocked',
        requirements: ['asr.faster_whisper'],
        missingRequired: [
          {
            requirementId: 'asr.faster_whisper',
            capabilityId: 'asr.faster_whisper',
            label: '本地语音识别',
            detail: '当前任务需要本地 ASR 与时间戳能力。',
            packageId: 'video2notes-nvidia-asr',
            version: '1.0.0',
            officialUrl: 'https://downloads.example.test/video2notes-nvidia-asr.zip',
            downloadSizeBytes: 512 * 1024 ** 2,
            installedSizeBytes: 1024 ** 3,
          },
        ],
        missingOptional: [],
        selectedInstances: {},
        bindingSnapshot: {},
        recommendedActions: [
          {
            kind: 'install',
            label: '安装 NVIDIA ASR 运行时',
            packageId: 'video2notes-nvidia-asr',
            version: '1.0.0',
            bindRequirements: ['asr.faster_whisper'],
            downloadSizeBytes: 512 * 1024 ** 2,
            installedSizeBytes: 1024 ** 3,
            targetRoot: 'D:/data/runtime-packages/video2notes-nvidia-asr/1.0.0',
            officialUrl: 'https://downloads.example.test/video2notes-nvidia-asr.zip',
            supportedDevices: ['cuda'],
          },
        ],
        estimatedDownloadBytes: 512 * 1024 ** 2,
        estimatedInstalledBytes: 1024 ** 3,
        detail: '当前配置缺少必需运行能力，任务不会提交。',
      },
    })

    render(<App />)

    const dialog = screen.getByRole('dialog', { name: '开始前还需要本地组件' })
    expect(within(dialog).getByText('本地语音识别')).toBeInTheDocument()
    expect(within(dialog).getAllByText('512 MB').length).toBeGreaterThan(0)
    expect(within(dialog).getAllByText('1.00 GB').length).toBeGreaterThan(0)

    fireEvent.click(within(dialog).getByRole('button', { name: '打开依赖管理' }))
    expect(screen.getByRole('heading', { name: '设置中心' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: '开始前还需要本地组件' })).not.toBeInTheDocument()
  })

  it('adds and edits a fixed sampling override in advanced options', () => {
    render(<App />)

    fireEvent.click(screen.getByRole('button', { name: '探测来源' }))
    const advancedOptions = screen.getByRole('button', { name: /高级选项/ })
    expect(advancedOptions).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(advancedOptions)
    expect(advancedOptions).toHaveAttribute('aria-expanded', 'true')
    fireEvent.click(screen.getByRole('button', { name: '添加时间段' }))

    fireEvent.change(screen.getByLabelText('时间段 1 开始时间（秒）'), {
      target: { value: '12.5' },
    })
    fireEvent.change(screen.getByLabelText('时间段 1 结束时间（秒）'), {
      target: { value: '22.5' },
    })
    fireEvent.change(screen.getByLabelText('时间段 1 采样方式'), {
      target: { value: 'fixed_interval' },
    })
    fireEvent.click(
      screen.getByRole('button', {
        name: '时间段 1 固定采样间隔（秒）设为 0.1 秒',
      }),
    )

    expect(useStudioStore.getState().draft.samplingOverrides).toEqual([
      expect.objectContaining({
        startSeconds: 12.5,
        endSeconds: 22.5,
        mode: 'fixed_interval',
        intervalSeconds: 0.1,
      }),
    ])
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

  it('keeps a failed active task visible in Notes instead of silently opening another note', () => {
    const state = useStudioStore.getState()
    const failed = {
      ...state.tasks[0],
      id: 'task-reader-failed',
      status: 'failed' as const,
      note: undefined,
      realBackend: true,
      failure: {
        failedStages: [{ stage: 'notes.compose', errorType: 'ProviderUnavailable' }],
        completedStages: ['source.acquire', 'media.probe', 'audio.asr'],
        errorType: 'ProviderUnavailable',
        message: '笔记模型暂时不可用',
      },
      recovery: {
        canRetry: false,
        strategy: 'manual_recreate' as const,
        reason: '当前会话没有可重放请求。',
      },
    }
    useStudioStore.setState({
      view: 'reader',
      activeTaskId: failed.id,
      tasks: [failed, ...state.tasks.slice(1)],
    })

    render(<App />)

    expect(
      screen.getByRole('heading', { name: '处理在 notes.compose 停止' }),
    ).toBeInTheDocument()
    expect(screen.getByText('ProviderUnavailable')).toBeInTheDocument()
    expect(screen.queryByText('事实支持度检查已通过')).not.toBeInTheDocument()
  })

  it('changes a compatible role binding from the model settings page', () => {
    useStudioStore.getState().navigate('models')
    render(<App />)

    const roleSelect = screen.getByLabelText('为主要语音识别选择模型')
    fireEvent.change(roleSelect, { target: { value: 'model-funasr' } })

    expect(useStudioStore.getState().roles.find(role => role.id === 'asr.primary')?.modelId).toBe(
      'model-funasr',
    )
    expect(screen.getByText('主要语音识别 已绑定到 FunASR Paraformer。')).toBeInTheDocument()
  })

  it('opens the detailed evidence, local rework, materials, and report workbenches', async () => {
    useStudioStore.setState({
      view: 'reader',
      activeTaskId: 'task-complete',
    })
    useUiPreferences.setState({ workspaceMode: 'professional' })
    render(<App />)

    expect(screen.getByText('EVIDENCE TIMELINE')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '局部返工' }))
    expect(screen.getByRole('heading', { name: '局部返工' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: '画面' })).toHaveAttribute(
      'aria-selected',
      'true',
    )
    fireEvent.click(
      within(screen.getByRole('dialog')).getByRole('button', {
        name: '关闭局部返工面板',
      }),
    )

    fireEvent.click(screen.getByRole('button', { name: /补充资料/ }))
    expect(screen.getByRole('heading', { name: '补充资料' })).toBeInTheDocument()
    fireEvent.click(
      within(screen.getByRole('dialog')).getByRole('button', {
        name: '关闭补充资料面板',
      }),
    )

    fireEvent.click(screen.getByRole('button', { name: '生成报告' }))
    expect(
      screen.getByRole('heading', { name: '重新生成报告' }),
    ).toBeInTheDocument()
    fireEvent.click(screen.getByText('领导', { selector: 'strong' }))
    fireEvent.click(screen.getByRole('button', { name: '生成新 revision' }))

    await waitFor(() =>
      expect(
        useStudioStore
          .getState()
          .tasks.find(task => task.id === 'task-complete')?.reportRevisions,
      ).toHaveLength(1),
    )
    expect(screen.getByText('历史报告 · 1')).toBeInTheDocument()
    expect(screen.queryByText('安全回退版本')).not.toBeInTheDocument()
  })
})

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
    expect(screen.getByRole('button', { name: '简约视图' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )

    fireEvent.click(screen.getByRole('button', { name: '数据工作室' }))
    fireEvent.change(screen.getByLabelText('主题预置'), {
      target: { value: 'studio-graphite' },
    })

    expect(shell).toHaveAttribute('data-theme', 'studio-graphite')
    expect(shell).toHaveAttribute('data-workspace-mode', 'professional')
    expect(JSON.parse(window.localStorage.getItem(UI_PREFERENCES_STORAGE_KEY) ?? '{}')).toEqual({
      workspaceMode: 'professional',
      themePreset: 'studio-graphite',
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

    expect(screen.getByText('本机预计 00:31–01:02 · 0.02–0.04×')).toBeInTheDocument()
    expect(screen.getByText('预计耗时约视频时长的 0.55–1.0×')).toBeInTheDocument()
    expect(screen.getByText(/未返回的模式继续显示通用区间/)).toBeInTheDocument()
  })

  it('adds and edits a fixed sampling override in advanced options', () => {
    render(<App />)

    fireEvent.click(screen.getByRole('button', { name: '探测来源' }))
    const details = document.querySelector<HTMLDetailsElement>('.advanced-options')
    const summary = details?.querySelector('summary')
    expect(summary).not.toBeNull()
    fireEvent.click(summary!)
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

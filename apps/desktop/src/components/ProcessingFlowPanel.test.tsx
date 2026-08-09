import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ProcessingTask } from '../domain'
import { makeTaskFixtures } from '../fixtures'
import {
  ProcessingFlowPanel,
  type RunEventLogEntry,
} from './ProcessingFlowPanel'

const makeTask = (): ProcessingTask => {
  const fixture = makeTaskFixtures()[0]
  return {
    ...fixture,
    id: 'run-flow-test',
    status: 'running',
    realBackend: true,
    telemetry: [],
  }
}

const logEvents: RunEventLogEntry[] = [
  {
    id: 'event-source',
    sequence: 1,
    stage: 'source.acquire',
    stageLabel: '获取视频',
    level: 'info',
    state: 'completed',
    progress: 1,
    message: '视频源与元数据已写入任务目录。',
    metrics: { bytes_downloaded: 2_048 },
    createdAt: '2026-08-10T08:00:01Z',
  },
  {
    id: 'event-asr-warning',
    sequence: 2,
    stage: 'audio.asr',
    stageLabel: '语音识别',
    level: 'warning',
    state: 'running',
    progress: 0.42,
    message: 'CUDA 显存余量不足，当前片段切换到 CPU。',
    metrics: { device: 'cpu', realtime_factor: 0.83 },
    createdAt: '2026-08-10T08:01:12Z',
  },
  {
    id: 'event-ocr-error',
    sequence: 3,
    stage: 'ocr.extract',
    stageLabel: 'OCR 提取',
    level: 'error',
    state: 'failed',
    progress: 0.64,
    message: 'OCR worker 在重试后仍未返回结果。',
    errorType: 'OcrWorkerError',
    metrics: { retry_count: 2, cuda_available: false, confidence: null },
    createdAt: '2026-08-10T08:02:45Z',
  },
]

describe('ProcessingFlowPanel', () => {
  beforeEach(() => {
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
    })
    vi.mocked(HTMLElement.prototype.scrollTo).mockClear()
  })

  it('combines level, stage, and text filters and can clear an empty result', () => {
    render(<ProcessingFlowPanel task={makeTask()} events={logEvents} />)

    expect(screen.getByText('视频源与元数据已写入任务目录。')).toBeInTheDocument()
    expect(screen.getByText('CUDA 显存余量不足，当前片段切换到 CPU。')).toBeInTheDocument()
    expect(screen.getByText('OCR worker 在重试后仍未返回结果。')).toBeInTheDocument()
    expect(screen.getByText('OcrWorkerError')).toHaveAttribute(
      'title',
      '后端返回的安全异常类型',
    )

    fireEvent.click(screen.getByRole('button', { name: /警告/ }))
    expect(screen.getByText('CUDA 显存余量不足，当前片段切换到 CPU。')).toBeInTheDocument()
    expect(screen.queryByText('视频源与元数据已写入任务目录。')).not.toBeInTheDocument()
    expect(screen.queryByText('OCR worker 在重试后仍未返回结果。')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /全部/ }))
    fireEvent.change(screen.getByRole('combobox', { name: '阶段' }), {
      target: { value: 'vision' },
    })
    expect(screen.getByText('OCR worker 在重试后仍未返回结果。')).toBeInTheDocument()
    expect(screen.queryByText('CUDA 显存余量不足，当前片段切换到 CPU。')).not.toBeInTheDocument()

    fireEvent.change(screen.getByRole('combobox', { name: '阶段' }), {
      target: { value: 'all' },
    })
    fireEvent.change(screen.getByRole('searchbox', { name: '搜索日志' }), {
      target: { value: '显存' },
    })
    expect(screen.getByText('CUDA 显存余量不足，当前片段切换到 CPU。')).toBeInTheDocument()
    expect(screen.queryByText('OCR worker 在重试后仍未返回结果。')).not.toBeInTheDocument()

    fireEvent.change(screen.getByRole('searchbox', { name: '搜索日志' }), {
      target: { value: '不存在的日志' },
    })
    expect(screen.getByText('没有匹配的日志')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '清除筛选' }))
    expect(screen.getByText('视频源与元数据已写入任务目录。')).toBeInTheDocument()
    expect(screen.getByText('OCR worker 在重试后仍未返回结果。')).toBeInTheDocument()
  })

  it('expands metrics and stops following updates when the toggle is off', async () => {
    const { rerender } = render(
      <ProcessingFlowPanel task={makeTask()} events={logEvents} />,
    )

    await waitFor(() => {
      expect(HTMLElement.prototype.scrollTo).toHaveBeenCalled()
    })

    fireEvent.click(
      screen.getByRole('button', { name: '展开事件 #03 指标' }),
    )
    expect(screen.getByText('retry count')).toBeInTheDocument()
    expect(screen.getByText('2')).toBeInTheDocument()
    expect(screen.getByText('否')).toBeInTheDocument()
    expect(screen.getByText('—')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('checkbox', { name: '自动跟随' }))
    expect(screen.getByRole('checkbox', { name: '自动跟随' })).not.toBeChecked()
    vi.mocked(HTMLElement.prototype.scrollTo).mockClear()

    rerender(
      <ProcessingFlowPanel
        task={makeTask()}
        events={[
          ...logEvents,
          {
            id: 'event-render',
            sequence: 4,
            stage: 'render.note',
            stageLabel: '生成笔记',
            level: 'info',
            state: 'running',
            message: '开始生成规范化笔记。',
            metrics: {},
            createdAt: '2026-08-10T08:03:10Z',
          },
        ]}
      />,
    )
    await act(async () => Promise.resolve())
    expect(HTMLElement.prototype.scrollTo).not.toHaveBeenCalled()
  })

  it('copies and downloads only the currently filtered events', async () => {
    const onCopy = vi.fn().mockResolvedValue(undefined)
    const onDownload = vi.fn()
    render(
      <ProcessingFlowPanel
        task={makeTask()}
        events={logEvents}
        onCopy={onCopy}
        onDownload={onDownload}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /错误/ }))
    fireEvent.click(screen.getByRole('button', { name: '复制当前日志' }))
    await waitFor(() => expect(onCopy).toHaveBeenCalledOnce())

    const copied = String(onCopy.mock.calls[0][0])
    expect(copied).toContain('[ERROR] [ocr.extract] #3 progress=64%')
    expect(copied).toContain('error_type=OcrWorkerError')
    expect(copied).toContain('retry_count=2')
    expect(copied).not.toContain('source.acquire')
    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent('日志已复制')
    })

    fireEvent.click(screen.getByRole('button', { name: '下载当前日志' }))
    expect(onDownload).toHaveBeenCalledOnce()
    const [fileName, downloaded] = onDownload.mock.calls[0]
    expect(fileName).toBe('video2notes-run-flow-test-processing-log.jsonl')
    expect(downloaded).not.toBe(copied)
    const records = String(downloaded)
      .trim()
      .split('\n')
      .map(line => JSON.parse(line) as Record<string, unknown>)
    expect(records).toEqual([
      expect.objectContaining({
        run_id: 'run-flow-test',
        sequence: 3,
        level: 'error',
        stage: 'ocr.extract',
        progress: 0.64,
        error_type: 'OcrWorkerError',
        metrics: expect.objectContaining({ retry_count: 2 }),
      }),
    ])
  })

  it('reports unavailable and partially corrupt persisted logs without hiding valid events', () => {
    const task = makeTask()
    task.eventLog = { available: false, corruptLineCount: 0 }
    const { rerender } = render(
      <ProcessingFlowPanel task={task} events={logEvents} />,
    )

    expect(
      screen.getByText(/未找到持久化事件日志；当前仅显示本次会话/),
    ).toBeInTheDocument()
    expect(screen.getByText('视频源与元数据已写入任务目录。')).toBeInTheDocument()

    rerender(
      <ProcessingFlowPanel
        task={{
          ...task,
          eventLog: { available: true, corruptLineCount: 3 },
        }}
        events={logEvents}
      />,
    )
    expect(
      screen.getByText(/跳过了 3 条损坏记录；其余事件仍可筛选和导出/),
    ).toBeInTheDocument()
  })

  it('focuses the first failure and reports its logical stage to the parent view', async () => {
    const task = makeTask()
    task.status = 'failed'
    task.stages = task.stages.map(stage =>
      stage.id === 'vision'
        ? {
            ...stage,
            status: 'failed',
            backendStages: ['vision.scan', 'ocr.extract'],
          }
        : stage,
    )
    const onStageFilterChange = vi.fn()

    render(
      <ProcessingFlowPanel
        task={task}
        events={logEvents}
        onStageFilterChange={onStageFilterChange}
      />,
    )

    await waitFor(() => {
      expect(screen.getByRole('combobox', { name: '阶段' })).toHaveValue('vision')
    })
    expect(onStageFilterChange).toHaveBeenCalledWith('vision')
    expect(screen.getByRole('button', { name: /错误/ })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(screen.getByRole('checkbox', { name: '自动跟随' })).not.toBeChecked()
    expect(screen.getByText('OcrWorkerError')).toBeInTheDocument()
    await waitFor(() => {
      expect(HTMLElement.prototype.scrollIntoView).toHaveBeenCalled()
    })
  })

  it('shows a truthful empty state for older tasks without telemetry', () => {
    render(<ProcessingFlowPanel task={makeTask()} />)

    expect(screen.getByText('这个任务没有流程日志')).toBeInTheDocument()
    expect(
      screen.getByText(/早期版本创建的任务可能只保留阶段状态和产物/),
    ).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: '阶段' })).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: '自动跟随' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '复制当前日志' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '下载当前日志' })).toBeDisabled()
  })

  it('normalizes existing telemetry states when no enhanced events are supplied', () => {
    const task = makeTask()
    task.telemetry = [
      {
        sequence: 11,
        runId: task.id,
        state: 'cancelled',
        stage: 'vision.scan',
        progress: 0.31,
        message: '视觉扫描已由用户取消。',
        metrics: {},
        createdAt: '2026-08-10T08:05:00Z',
      },
      {
        sequence: 12,
        runId: task.id,
        state: 'failed',
        stage: 'audio.asr',
        message: '语音 worker 启动失败。',
        metrics: { attempts: 3 },
        createdAt: '2026-08-10T08:05:02Z',
      },
    ]

    render(<ProcessingFlowPanel task={task} />)

    fireEvent.click(screen.getByRole('button', { name: /警告/ }))
    expect(screen.getByText('视觉扫描已由用户取消。')).toBeInTheDocument()
    expect(screen.queryByText('语音 worker 启动失败。')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /错误/ }))
    expect(screen.getByText('语音 worker 启动失败。')).toBeInTheDocument()
    expect(screen.queryByText('视觉扫描已由用户取消。')).not.toBeInTheDocument()
  })
})

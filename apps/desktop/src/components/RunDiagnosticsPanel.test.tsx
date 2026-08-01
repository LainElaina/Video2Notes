import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { ProcessingTask } from '../domain'
import { makeTaskFixtures } from '../fixtures'
import { RunDiagnosticsPanel } from './RunDiagnosticsPanel'

const failedTask = (): ProcessingTask => {
  const base = makeTaskFixtures()[0]
  return {
    ...base,
    id: 'run-failed-real',
    realBackend: true,
    status: 'failed',
    runtimeWarnings: ['OCR CUDA 不可用，已切换到 CPU。'],
    warnings: [],
    failure: {
      failedStages: [{ stage: 'audio.asr', errorType: 'ModelLoadError' }],
      completedStages: ['source.acquire', 'media.probe', 'audio.extract'],
      errorType: 'ModelLoadError',
      message: '语音模型初始化失败',
    },
    recovery: {
      canRetry: false,
      strategy: 'manual_recreate',
      reason: '当前会话没有可安全重放的认证与采样请求。',
    },
    stages: [
      {
        ...base.stages[0],
        status: 'completed',
        backendStages: ['source.acquire'],
        outputArtifacts: [
          {
            stage: 'source.acquire',
            kind: 'media',
            relativePath: 'source/source.mp4',
            sha256: 'source-sha256',
            sizeBytes: 2_048,
            mediaType: 'video/mp4',
            createdAt: '2026-08-01T12:00:00Z',
          },
        ],
        artifactCount: 1,
      },
      {
        ...base.stages[2],
        status: 'failed',
        backendStages: ['audio.asr'],
        errorTypes: ['ModelLoadError'],
        outputArtifacts: [],
        artifactCount: 0,
      },
    ],
  }
}

describe('RunDiagnosticsPanel', () => {
  it('shows only manifest-backed failure facts and routes an unreplayable task to creation', () => {
    const onRetry = vi.fn()
    const onCreateNew = vi.fn()
    const onDownloadArtifact = vi.fn()
    const task = failedTask()

    render(
      <RunDiagnosticsPanel
        task={task}
        onRetry={onRetry}
        onCreateNew={onCreateNew}
        onDownloadArtifact={onDownloadArtifact}
      />,
    )

    expect(screen.getByRole('heading', { name: '处理在 audio.asr 停止' })).toBeInTheDocument()
    expect(screen.getByText('ModelLoadError')).toBeInTheDocument()
    expect(screen.getByText('source.acquire · media.probe · audio.extract')).toBeInTheDocument()
    expect(screen.getByText('OCR CUDA 不可用，已切换到 CPU。')).toBeInTheDocument()
    expect(screen.queryByText('stage-manifest.json')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '按原配置新建重试' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '下载产物 source/source.mp4' }))
    expect(onDownloadArtifact).toHaveBeenCalledWith(
      expect.objectContaining({ relativePath: 'source/source.mp4' }),
    )

    fireEvent.click(screen.getByRole('button', { name: '返回新建任务' }))
    expect(onCreateNew).toHaveBeenCalledOnce()
    expect(onRetry).not.toHaveBeenCalled()
  })

  it('offers a truthful new-run retry when the original submission is still available', () => {
    const onRetry = vi.fn()
    const task = failedTask()
    task.recovery = {
      canRetry: true,
      strategy: 'new_run',
      reason: '会创建一个新的可追溯任务，原任务与产物保持不变。',
    }

    render(
      <RunDiagnosticsPanel
        task={task}
        compact
        onRetry={onRetry}
        onCreateNew={vi.fn()}
        onDownloadArtifact={vi.fn()}
      />,
    )

    expect(screen.getByText('从失败阶段原地续跑：当前本地 API 未提供该能力')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '按原配置新建重试' }))
    expect(onRetry).toHaveBeenCalledOnce()
  })
})

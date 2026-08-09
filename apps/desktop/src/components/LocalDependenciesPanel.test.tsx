import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { copyText } from '../clipboard'
import type { LocalToolInventoryDefinition } from '../domain'
import { useStudioStore } from '../store'
import {
  DEFAULT_UI_PREFERENCES,
  useUiPreferences,
} from '../stores/uiPreferences'
import { LocalDependenciesPanel } from './LocalDependenciesPanel'

vi.mock('../clipboard', () => ({
  copyText: vi.fn(),
}))

const inventory: LocalToolInventoryDefinition = {
  scannedAtUtc: '2026-08-10T01:02:03Z',
  platform: 'Windows',
  architecture: 'x86_64',
  bindings: {
    'ocr.paddleocr': {
      dependencyId: 'ocr.paddleocr',
      path: 'D:\\AI\\paddleocr',
      kind: 'python_module',
      boundAtUtc: '2026-08-10T01:00:00Z',
      lastVersion: '3.1.0',
    },
  },
  tools: [
    {
      dependencyId: 'tool.ffmpeg',
      displayName: 'FFmpeg multimedia tools',
      displayNameZh: 'FFmpeg 音视频工具',
      kind: 'executable',
      status: 'ready',
      compatible: true,
      path: 'C:\\Tools\\ffmpeg\\bin\\ffmpeg.exe',
      version: '7.1',
      source: 'path',
      capabilities: ['tool.ffmpeg'],
      cpuSupported: true,
      cudaSupported: false,
      bound: false,
      detail: 'Detected from the system PATH.',
      detailZh: '已从系统 PATH 识别。',
      suggestion: 'Pin this path to make selection deterministic.',
      suggestionZh: '可固定此路径，保证每次使用同一个程序。',
      candidates: [
        {
          path: 'C:\\Tools\\ffmpeg\\bin\\ffmpeg.exe',
          source: 'path',
          version: '7.1',
          compatible: true,
        },
      ],
      checkedAtUtc: '2026-08-10T01:02:03Z',
    },
    {
      dependencyId: 'ocr.paddleocr',
      displayName: 'PaddleOCR Python environment',
      displayNameZh: 'PaddleOCR Python 环境',
      kind: 'python_module',
      status: 'ready',
      compatible: true,
      path: 'D:\\AI\\paddleocr',
      version: '3.1.0',
      source: 'binding',
      capabilities: ['ocr.paddleocr'],
      cpuSupported: true,
      cudaSupported: true,
      bound: true,
      detail: 'A manually bound Python environment is ready.',
      detailZh: '手动绑定的 Python 环境已就绪。',
      candidates: [
        {
          path: 'D:\\AI\\paddleocr',
          source: 'binding',
          version: '3.1.0',
          compatible: true,
        },
      ],
      checkedAtUtc: '2026-08-10T01:02:03Z',
    },
  ],
}

const originalActions = {
  bindLocalToolPath: useStudioStore.getState().bindLocalToolPath,
  unbindLocalToolPath: useStudioStore.getState().unbindLocalToolPath,
  discoverRuntimePackages: useStudioStore.getState().discoverRuntimePackages,
}

let bindLocalToolPath: ReturnType<typeof vi.fn>
let unbindLocalToolPath: ReturnType<typeof vi.fn>
let discoverRuntimePackages: ReturnType<typeof vi.fn>

function renderPanel() {
  render(<LocalDependenciesPanel canMutate busy={false} />)
}

function cardNamed(name: string): HTMLElement {
  const card = screen.getByRole('heading', { name }).closest('article')
  if (!card) throw new Error(`Dependency card not found: ${name}`)
  return card
}

describe('LocalDependenciesPanel', () => {
  beforeEach(() => {
    useStudioStore.getState().resetDemo()
    useUiPreferences.setState(DEFAULT_UI_PREFERENCES)
    bindLocalToolPath = vi.fn()
    unbindLocalToolPath = vi.fn()
    discoverRuntimePackages = vi.fn()
    useStudioStore.setState(state => ({
      runtimePackages: {
        ...state.runtimePackages!,
        localTools: inventory,
      },
      bindLocalToolPath,
      unbindLocalToolPath,
      discoverRuntimePackages,
    }))
    vi.mocked(copyText).mockReset()
  })

  afterEach(() => {
    useStudioStore.setState(originalActions)
    useUiPreferences.setState(DEFAULT_UI_PREFERENCES)
  })

  it('renders localized dependency names and scan summaries in both languages', () => {
    renderPanel()

    expect(screen.getByRole('heading', { name: '本机依赖识别结果' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'FFmpeg 音视频工具' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'PaddleOCR Python 环境' })).toBeInTheDocument()
    const chineseSummary = screen.getByLabelText('本机依赖摘要')
    expect(within(chineseSummary).getByText('2 / 2')).toBeInTheDocument()
    expect(within(chineseSummary).getByText('可用')).toBeInTheDocument()
    expect(within(chineseSummary).getByText('手动绑定')).toBeInTheDocument()
    expect(within(chineseSummary).getByText('支持 CUDA')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '重新检测' }))
    expect(discoverRuntimePackages).toHaveBeenCalledOnce()

    act(() => useUiPreferences.getState().setLocale('en-US'))

    expect(screen.getByRole('heading', { name: 'Local dependency discovery' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'FFmpeg multimedia tools' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'PaddleOCR Python environment' })).toBeInTheDocument()
    const englishSummary = screen.getByLabelText('Local dependency summary')
    expect(within(englishSummary).getByText('2 / 2')).toBeInTheDocument()
    expect(within(englishSummary).getByText('ready')).toBeInTheDocument()
    expect(within(englishSummary).getByText('manual bindings')).toBeInTheDocument()
    expect(within(englishSummary).getByText('CUDA capable')).toBeInTheDocument()
  })

  it('pins an automatically discovered path and offers both manual selectors', () => {
    renderPanel()
    const ffmpegCard = cardNamed('FFmpeg 音视频工具')

    fireEvent.click(within(ffmpegCard).getByRole('button', { name: '固定使用此路径' }))
    expect(bindLocalToolPath).toHaveBeenLastCalledWith(
      'tool.ffmpeg',
      'file',
      'C:\\Tools\\ffmpeg\\bin\\ffmpeg.exe',
    )

    fireEvent.click(within(ffmpegCard).getByRole('button', { name: '选择程序' }))
    expect(bindLocalToolPath).toHaveBeenLastCalledWith('tool.ffmpeg', 'file')

    fireEvent.click(within(ffmpegCard).getByRole('button', { name: '选择目录' }))
    expect(bindLocalToolPath).toHaveBeenLastCalledWith('tool.ffmpeg', 'directory')
  })

  it('modifies and removes an existing manual binding', () => {
    renderPanel()
    const paddleCard = cardNamed('PaddleOCR Python 环境')

    fireEvent.click(within(paddleCard).getByRole('button', { name: '修改路径' }))
    expect(bindLocalToolPath).toHaveBeenLastCalledWith('ocr.paddleocr', 'file')

    fireEvent.click(within(paddleCard).getByRole('button', { name: '选择目录' }))
    expect(bindLocalToolPath).toHaveBeenLastCalledWith('ocr.paddleocr', 'directory')

    fireEvent.click(within(paddleCard).getByRole('button', { name: '解绑' }))
    expect(unbindLocalToolPath).toHaveBeenCalledOnce()
    expect(unbindLocalToolPath).toHaveBeenCalledWith('ocr.paddleocr')
  })

  it('copies the current path and exposes success feedback', async () => {
    vi.mocked(copyText).mockResolvedValueOnce()
    renderPanel()
    const ffmpegCard = cardNamed('FFmpeg 音视频工具')
    const copyButton = within(ffmpegCard).getByRole('button', { name: '复制路径' })

    fireEvent.click(copyButton)

    expect(copyText).toHaveBeenCalledWith('C:\\Tools\\ffmpeg\\bin\\ffmpeg.exe')
    await waitFor(() => expect(copyButton).toHaveAttribute('title', '已复制'))
  })

  it('shows a localized error when copying the current path fails', async () => {
    vi.mocked(copyText).mockRejectedValueOnce(new Error('denied'))
    renderPanel()
    const ffmpegCard = cardNamed('FFmpeg 音视频工具')

    fireEvent.click(within(ffmpegCard).getByRole('button', { name: '复制路径' }))

    expect(
      await within(ffmpegCard).findByText('无法写入剪贴板，请检查 Windows 权限。'),
    ).toBeInTheDocument()

    act(() => useUiPreferences.getState().setLocale('en-US'))
    expect(
      within(ffmpegCard).getByText(
        'Could not write to the clipboard. Check Windows permissions.',
      ),
    ).toBeInTheDocument()
  })
})

import { describe, expect, it } from 'vitest'
import { localizeUserMessage } from './userMessages'

describe('user-facing message localization', () => {
  it('translates dependency binding progress and results into English', () => {
    expect(
      localizeUserMessage('正在验证所选路径、版本与兼容性。', 'en-US'),
    ).toBe('Validating the selected path, version, and compatibility.')
    expect(
      localizeUserMessage('视频处理（FFmpeg） 已绑定到指定路径。', 'en-US'),
    ).toBe('FFmpeg was bound to the selected path.')
  })

  it('provides an understandable fallback without leaking Chinese UI copy', () => {
    expect(localizeUserMessage('未知动作失败：测试详情', 'en-US')).toBe(
      'The operation could not be completed. Open the relevant panel for details.',
    )
  })

  it('adds a Chinese summary to raw English failures', () => {
    expect(localizeUserMessage('Provider connection failed', 'zh-CN')).toBe(
      '操作未完成。技术详情：Provider connection failed',
    )
    expect(localizeUserMessage('Provider reachable', 'zh-CN')).toBe('操作已完成。')
  })
})

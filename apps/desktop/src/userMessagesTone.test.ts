import { describe, expect, it } from 'vitest'
import { userMessageTone } from './userMessages'

describe('userMessageTone', () => {
  it('treats an empty message as neutral', () => {
    expect(userMessageTone('')).toBe('neutral')
  })

  it('classifies Chinese failure wording as error', () => {
    expect(userMessageTone('依赖预检失败，任务未提交：缺少 FFmpeg。')).toBe('error')
    expect(userMessageTone('本地后端尚未连接。')).toBe('error')
    expect(userMessageTone('无法开始任务：后端离线。')).toBe('error')
    expect(userMessageTone('人工修正文本不能为空。')).toBe('error')
    expect(userMessageTone('后端版本不支持仅音频；请升级本地后端后再试。')).toBe('error')
  })

  it('classifies English failure wording as error regardless of case', () => {
    expect(userMessageTone('Provider connection failed')).toBe('error')
    expect(userMessageTone('FFprobe exited with ERROR 1')).toBe('error')
    expect(userMessageTone('Path is invalid')).toBe('error')
    expect(userMessageTone('Backend unavailable')).toBe('error')
    expect(userMessageTone('Request timeout after 30s')).toBe('error')
    expect(userMessageTone('Model not found in registry')).toBe('error')
    expect(userMessageTone('Could not bind the selected path')).toBe('error')
    expect(userMessageTone('Cannot cancel a completed run')).toBe('error')
    expect(userMessageTone('Unable to read the artifact')).toBe('error')
    expect(userMessageTone('Access Denied by credential store')).toBe('error')
  })

  it('classifies Chinese completion wording as success', () => {
    expect(userMessageTone('性能配置已保存；算法计划已按当前机器重新计算。')).toBe('success')
    expect(userMessageTone('任务已取消；已产生的 artifact 仍保留在本地。')).toBe('success')
    expect(userMessageTone('自定义运行时已登记；应用不会接管或删除原目录。')).toBe('success')
  })

  it('classifies Chinese in-progress wording as progress', () => {
    expect(userMessageTone('正在检查依赖或提交任务，请等待本次操作确认。')).toBe('progress')
    expect(userMessageTone('正在验证所选路径、版本与兼容性。')).toBe('progress')
    expect(userMessageTone('安装任务已开始；可安全取消，已下载的分片会保留用于续传。')).toBe(
      'progress',
    )
  })

  it('lets failure wording win over success and progress wording', () => {
    expect(userMessageTone('正在保存但写入失败。')).toBe('error')
    expect(userMessageTone('已导出阶段产物失败：磁盘不可用。')).toBe('error')
  })

  it('falls back to neutral for unclassified messages', () => {
    expect(userMessageTone('演示任务已暂停。')).toBe('neutral')
    expect(userMessageTone('Saved successfully')).toBe('neutral')
    expect(userMessageTone('Demo probe finished')).toBe('neutral')
  })
})

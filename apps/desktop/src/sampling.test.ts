import { describe, expect, it } from 'vitest'
import type { DraftState, SamplingOverrideDraft, SourceManifest } from './domain'
import {
  MAX_FIXED_SAMPLES,
  secondsToMicroseconds,
  serializeSamplingPlan,
  validateSamplingDraft,
} from './sampling'
import { buildPipelineSubmission } from './store'

const manifest = (durationSeconds = 100): SourceManifest => ({
  id: 'source-sampling-test',
  platform: 'local',
  title: 'Sampling test',
  author: 'Video2Notes',
  durationSeconds,
  quality: '1920 × 1080',
  codec: 'h264',
  audio: 'aac',
  subtitle: 'none',
  authLabel: '无需登录',
  sourceLabel: 'D:/clips/sampling-test.mp4',
})

const draft = (overrides: Partial<DraftState> = {}): DraftState => ({
  input: 'D:/clips/sampling-test.mp4',
  mode: 'balanced',
  status: 'ready',
  manifest: manifest(),
  sourceKind: 'local',
  authKind: 'none',
  browser: 'edge',
  profile: '',
  cookieFile: '',
  languageHints: '',
  includeScreenshots: true,
  generatePdf: true,
  samplingMode: 'adaptive',
  samplingIntervalSeconds: 0.5,
  samplingOverrides: [],
  reportPreset: 'detailed',
  ...overrides,
})

const segment = (
  id: string,
  startSeconds: number,
  endSeconds: number,
  overrides: Partial<SamplingOverrideDraft> = {},
): SamplingOverrideDraft => ({
  id,
  startSeconds,
  endSeconds,
  mode: 'adaptive',
  intervalSeconds: 0.5,
  ...overrides,
})

describe('desktop sampling plan contract', () => {
  it('converts seconds to integer microseconds and emits the backend snake_case plan', () => {
    const value = draft({
      samplingMode: 'fixed_interval',
      samplingIntervalSeconds: 0.5,
      samplingOverrides: [
        segment('fixed', 1.25, 2.75, {
          mode: 'fixed_interval',
          intervalSeconds: 0.1,
        }),
        segment('skip', 8, 10, { mode: 'skip' }),
      ],
    })

    expect(secondsToMicroseconds(1.234567)).toBe(1_234_567)
    expect(serializeSamplingPlan(value)).toEqual({
      default: { mode: 'fixed_interval', interval_us: 500_000 },
      overrides: [
        {
          range: { start_us: 1_250_000, end_us: 2_750_000 },
          sampling: { mode: 'fixed_interval', interval_us: 100_000 },
        },
        {
          range: { start_us: 8_000_000, end_us: 10_000_000 },
          sampling: { mode: 'skip' },
        },
      ],
    })
  })

  it('rejects overlapping and out-of-duration override ranges', () => {
    const result = validateSamplingDraft(
      draft({
        samplingOverrides: [
          segment('first', 5, 15),
          segment('overlap', 14.5, 20),
          segment('outside', 90, 101),
        ],
      }),
    )

    expect(result.errors).toEqual([
      expect.stringContaining('超出视频时长'),
      expect.stringContaining('发生重叠'),
    ])
    expect(result.fixedSampleCount).toBeNull()
  })

  it('allows exactly 5,000 fixed samples and rejects the next theoretical frame', () => {
    const atLimit = validateSamplingDraft(
      draft({
        manifest: manifest(500),
        samplingMode: 'fixed_interval',
        samplingIntervalSeconds: 0.1,
      }),
    )
    expect(atLimit).toEqual({ errors: [], fixedSampleCount: MAX_FIXED_SAMPLES })

    const overLimit = validateSamplingDraft(
      draft({
        manifest: manifest(500.01),
        samplingMode: 'fixed_interval',
        samplingIntervalSeconds: 0.1,
      }),
    )
    expect(overLimit.fixedSampleCount).toBe(MAX_FIXED_SAMPLES + 1)
    expect(overLimit.errors[0]).toContain('超过上限')
  })

  it('rejects fixed intervals below one tenth of a second', () => {
    const result = validateSamplingDraft(
      draft({
        samplingOverrides: [
          segment('too-dense', 1, 2, {
            mode: 'fixed_interval',
            intervalSeconds: 0.05,
          }),
        ],
      }),
    )

    expect(result.errors).toEqual([expect.stringContaining('不能小于 0.1s')])
    expect(result.fixedSampleCount).toBeNull()
  })

  it('serializes report presets, language, fixed formats, and legacy booleans together', () => {
    const withoutPdf = buildPipelineSubmission(
      draft({
        languageHints: 'en, zh-CN',
        reportPreset: 'executive',
        includeScreenshots: false,
        generatePdf: false,
      }),
    )
    expect(withoutPdf).toMatchObject({
      include_screenshots: false,
      generate_pdf: false,
      report_spec: {
        preset: 'executive',
        language: 'en',
        include_screenshots: false,
        output_formats: ['markdown', 'html'],
      },
    })

    const withPdf = buildPipelineSubmission(
      draft({ reportPreset: 'beginner', generatePdf: true }),
    )
    expect(withPdf.report_spec).toEqual({
      preset: 'beginner',
      language: 'zh-CN',
      include_screenshots: true,
      output_formats: ['markdown', 'html', 'pdf'],
    })
  })
})

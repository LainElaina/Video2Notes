import type { ApiSamplingPlan, ApiSamplingSpec } from './api'
import type { DraftState, SamplingMode, SamplingOverrideDraft } from './domain'

export const MIN_FIXED_INTERVAL_SECONDS = 0.1
export const MAX_FIXED_SAMPLES = 5_000

export interface SamplingValidationResult {
  errors: string[]
  fixedSampleCount: number | null
}

type SamplingDraft = Pick<
  DraftState,
  | 'manifest'
  | 'processingScope'
  | 'samplingMode'
  | 'samplingIntervalSeconds'
  | 'samplingOverrides'
>

interface NormalizedOverride {
  index: number
  startUs: number
  endUs: number
  mode: SamplingMode
  intervalUs: number | null
}

export const secondsToMicroseconds = (seconds: number): number =>
  Math.round(seconds * 1_000_000)

const fixedSampleCount = (startUs: number, endUs: number, intervalUs: number): number =>
  Math.floor((endUs - startUs - 1) / intervalUs) + 1

const intervalMicroseconds = (mode: SamplingMode, intervalSeconds: number): number | null =>
  mode === 'fixed_interval' ? secondsToMicroseconds(intervalSeconds) : null

const apiSamplingSpec = (mode: SamplingMode, intervalSeconds: number): ApiSamplingSpec =>
  mode === 'fixed_interval'
    ? { mode, interval_us: secondsToMicroseconds(intervalSeconds) }
    : { mode }

export function serializeSamplingPlan(draft: SamplingDraft): ApiSamplingPlan {
  if (draft.processingScope === 'audio_only') {
    return { default: { mode: 'skip' }, overrides: [] }
  }
  return {
    default: apiSamplingSpec(draft.samplingMode, draft.samplingIntervalSeconds),
    overrides: draft.samplingOverrides.map(override => ({
      range: {
        start_us: secondsToMicroseconds(override.startSeconds),
        end_us: secondsToMicroseconds(override.endSeconds),
      },
      sampling: apiSamplingSpec(override.mode, override.intervalSeconds),
    })),
  }
}

export function validateSamplingDraft(draft: SamplingDraft): SamplingValidationResult {
  if (draft.processingScope === 'audio_only') {
    return { errors: [], fixedSampleCount: 0 }
  }
  const errors: string[] = []
  const durationSeconds = draft.manifest?.durationSeconds
  const durationUs =
    durationSeconds !== undefined && Number.isFinite(durationSeconds) && durationSeconds > 0
      ? secondsToMicroseconds(durationSeconds)
      : null

  validateInterval(
    draft.samplingMode,
    draft.samplingIntervalSeconds,
    '全局固定采样间隔',
    errors,
  )

  const normalized: NormalizedOverride[] = []
  draft.samplingOverrides.forEach((override, index) => {
    const label = `时间段 ${index + 1}`
    const startValid = Number.isFinite(override.startSeconds)
    const endValid = Number.isFinite(override.endSeconds)

    if (!startValid || !endValid) {
      errors.push(`${label}的开始和结束时间必须是数字。`)
      return
    }

    const startUs = secondsToMicroseconds(override.startSeconds)
    const endUs = secondsToMicroseconds(override.endSeconds)
    if (
      !Number.isSafeInteger(startUs) ||
      !Number.isSafeInteger(endUs) ||
      startUs < 0
    ) {
      errors.push(`${label}的时间必须为非负且在可处理范围内。`)
      return
    }
    if (endUs <= startUs) {
      errors.push(`${label}的结束时间必须晚于开始时间。`)
      return
    }
    if (durationUs !== null && endUs > durationUs) {
      errors.push(
        `${label}结束于 ${override.endSeconds}s，超出视频时长 ${durationSeconds}s。`,
      )
    }

    validateInterval(override.mode, override.intervalSeconds, `${label}固定采样间隔`, errors)
    normalized.push({
      index,
      startUs,
      endUs,
      mode: override.mode,
      intervalUs: intervalMicroseconds(override.mode, override.intervalSeconds),
    })
  })

  const ordered = [...normalized].sort(
    (left, right) => left.startUs - right.startUs || left.endUs - right.endUs,
  )
  for (let index = 1; index < ordered.length; index += 1) {
    const previous = ordered[index - 1]
    const current = ordered[index]
    if (current.startUs < previous.endUs) {
      errors.push(`时间段 ${previous.index + 1} 与时间段 ${current.index + 1} 发生重叠。`)
    }
  }

  if (durationUs === null || errors.length > 0) {
    return { errors, fixedSampleCount: null }
  }

  let cursorUs = 0
  let sampleCount = 0
  const defaultIntervalUs = intervalMicroseconds(
    draft.samplingMode,
    draft.samplingIntervalSeconds,
  )

  for (const override of ordered) {
    if (cursorUs < override.startUs && defaultIntervalUs !== null) {
      sampleCount += fixedSampleCount(cursorUs, override.startUs, defaultIntervalUs)
    }
    if (override.mode === 'fixed_interval' && override.intervalUs !== null) {
      sampleCount += fixedSampleCount(override.startUs, override.endUs, override.intervalUs)
    }
    cursorUs = override.endUs
  }
  if (cursorUs < durationUs && defaultIntervalUs !== null) {
    sampleCount += fixedSampleCount(cursorUs, durationUs, defaultIntervalUs)
  }

  if (sampleCount > MAX_FIXED_SAMPLES) {
    errors.push(
      `固定采样预计产生 ${sampleCount.toLocaleString('zh-CN')} 帧，超过上限 ${MAX_FIXED_SAMPLES.toLocaleString('zh-CN')} 帧。请增大间隔或缩短固定采样时间段。`,
    )
  }
  return { errors, fixedSampleCount: sampleCount }
}

function validateInterval(
  mode: SamplingMode,
  intervalSeconds: number,
  label: string,
  errors: string[],
) {
  if (mode !== 'fixed_interval') return
  if (!Number.isFinite(intervalSeconds) || intervalSeconds < MIN_FIXED_INTERVAL_SECONDS) {
    errors.push(`${label}不能小于 ${MIN_FIXED_INTERVAL_SECONDS}s。`)
    return
  }
  const intervalUs = secondsToMicroseconds(intervalSeconds)
  if (!Number.isSafeInteger(intervalUs) || intervalUs < 100_000) {
    errors.push(`${label}必须是可处理的秒数。`)
  }
}

export function nextSamplingOverride(
  overrides: SamplingOverrideDraft[],
  durationSeconds?: number,
  id = `sampling-${Date.now().toString(36)}`,
): SamplingOverrideDraft {
  const safeDuration =
    durationSeconds !== undefined && Number.isFinite(durationSeconds) && durationSeconds > 0
      ? durationSeconds
      : 60
  const lastEnd = overrides.reduce(
    (maximum, override) =>
      Number.isFinite(override.endSeconds) ? Math.max(maximum, override.endSeconds) : maximum,
    0,
  )
  const startSeconds = lastEnd < safeDuration ? lastEnd : Math.max(0, safeDuration - 60)
  const endSeconds = Math.min(safeDuration, startSeconds + 60)
  return {
    id,
    startSeconds,
    endSeconds,
    mode: 'adaptive',
    intervalSeconds: 0.5,
  }
}

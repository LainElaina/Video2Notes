import type { EvidenceItem } from '../domain'

export interface EvidencePixelBucket {
  index: number
  items: readonly EvidenceItem[]
  displayItem: EvidenceItem
  selectedItem?: EvidenceItem
  evidenceStartSeconds: number
  evidenceEndSeconds: number
  windowStartSeconds: number
  windowEndSeconds: number
}

const clamp = (value: number, lower: number, upper: number) =>
  Math.min(upper, Math.max(lower, value))

const distanceFromInterval = (seconds: number, item: EvidenceItem) => {
  if (seconds < item.startSeconds) return item.startSeconds - seconds
  if (seconds > item.endSeconds) return seconds - item.endSeconds
  return 0
}

const midpointDistance = (seconds: number, item: EvidenceItem) =>
  Math.abs(seconds - (item.startSeconds + item.endSeconds) / 2)

export const pickEvidenceAtTime = (
  items: readonly EvidenceItem[],
  seconds: number,
): EvidenceItem => {
  let best = items[0]
  if (!best) throw new Error('Cannot select evidence from an empty pixel bucket.')

  for (let index = 1; index < items.length; index += 1) {
    const item = items[index]
    const itemDistance = distanceFromInterval(seconds, item)
    const bestDistance = distanceFromInterval(seconds, best)
    if (itemDistance !== bestDistance) {
      if (itemDistance < bestDistance) best = item
      continue
    }

    const itemMidpointDistance = midpointDistance(seconds, item)
    const bestMidpointDistance = midpointDistance(seconds, best)
    if (itemMidpointDistance !== bestMidpointDistance) {
      if (itemMidpointDistance < bestMidpointDistance) best = item
      continue
    }
    if (item.confidence > best.confidence) best = item
  }
  return best
}

export const bucketEvidenceByPixel = (
  evidence: readonly EvidenceItem[],
  durationSeconds: number,
  bucketCount: number,
  selectedEvidenceId?: string,
): EvidencePixelBucket[] => {
  const safeDuration = Math.max(1, durationSeconds)
  const safeBucketCount = Math.max(1, Math.floor(bucketCount))
  const grouped = new Map<number, EvidenceItem[]>()

  evidence.forEach(item => {
    const index = clamp(
      Math.floor((item.startSeconds / safeDuration) * safeBucketCount),
      0,
      safeBucketCount - 1,
    )
    const bucket = grouped.get(index)
    if (bucket) bucket.push(item)
    else grouped.set(index, [item])
  })

  return [...grouped.entries()]
    .sort(([left], [right]) => left - right)
    .map(([index, items]) => {
      const windowStartSeconds = (index / safeBucketCount) * safeDuration
      const windowEndSeconds = ((index + 1) / safeBucketCount) * safeDuration
      const selectedItem = selectedEvidenceId
        ? items.find(item => item.id === selectedEvidenceId)
        : undefined
      const displayItem = selectedItem ?? pickEvidenceAtTime(items, (windowStartSeconds + windowEndSeconds) / 2)
      let evidenceStartSeconds = items[0].startSeconds
      let evidenceEndSeconds = items[0].endSeconds
      for (let itemIndex = 1; itemIndex < items.length; itemIndex += 1) {
        evidenceStartSeconds = Math.min(evidenceStartSeconds, items[itemIndex].startSeconds)
        evidenceEndSeconds = Math.max(evidenceEndSeconds, items[itemIndex].endSeconds)
      }

      return {
        index,
        items,
        displayItem,
        selectedItem,
        evidenceStartSeconds,
        evidenceEndSeconds,
        windowStartSeconds,
        windowEndSeconds,
      }
    })
}

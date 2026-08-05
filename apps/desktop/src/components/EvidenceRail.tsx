import { memo, useEffect, useMemo, useRef, useState, type CSSProperties, type MouseEvent } from 'react'
import type { EvidenceItem, EvidenceKind } from '../domain'
import { formatTime } from '../domain'
import {
  bucketEvidenceByPixel,
  pickEvidenceAtTime,
  type EvidencePixelBucket,
} from './evidenceTimeline'

interface EvidenceRailProps {
  evidence: EvidenceItem[]
  durationSeconds: number
  currentTimeSeconds: number
  selectedEvidenceId?: string
  onSeek: (seconds: number) => void
  onSelect: (evidenceId: string, seconds: number) => void
  compact?: boolean
}

const tracks: Array<{ kind: EvidenceKind; short: string; label: string }> = [
  { kind: 'asr', short: 'A', label: '语音' },
  { kind: 'ocr', short: 'T', label: '文字' },
  { kind: 'visual', short: 'F', label: '画面' },
  { kind: 'chapter', short: 'C', label: '章节' },
]

const percent = (value: number, duration: number) =>
  Math.min(100, Math.max(0, (value / Math.max(1, duration)) * 100))

const bucketCountForWidth = (width: number, compact: boolean) => {
  const availableWidth = Math.max(0, width - 31)
  const pixelsPerBucket = compact ? 7 : 6
  return Math.min(220, Math.max(40, Math.floor(availableWidth / pixelsPerBucket)))
}

interface RailEventLayerProps {
  buckets: EvidencePixelBucket[]
  durationSeconds: number
  track: (typeof tracks)[number]
  onSelect: EvidenceRailProps['onSelect']
}

const RailEventLayer = memo(function RailEventLayer({
  buckets,
  durationSeconds,
  track,
  onSelect,
}: RailEventLayerProps) {
  const selectBucketEvidence = (
    event: MouseEvent<HTMLButtonElement>,
    bucket: EvidencePixelBucket,
  ) => {
    const bounds = event.currentTarget.parentElement?.getBoundingClientRect()
    const hasPointerPosition = Boolean(bounds && bounds.width > 0 && event.detail > 0)
    const pointerSeconds =
      hasPointerPosition && bounds
        ? ((event.clientX - bounds.left) / bounds.width) * Math.max(1, durationSeconds)
        : bucket.displayItem.startSeconds
    const item = hasPointerPosition
      ? pickEvidenceAtTime(bucket.items, pointerSeconds)
      : (bucket.selectedItem ?? bucket.displayItem)
    onSelect(item.id, item.startSeconds)
  }

  return (
    <>
      {buckets.map(bucket => {
        const item = bucket.displayItem
        const grouped = bucket.items.length > 1
        const leftSeconds = grouped && !bucket.selectedItem ? bucket.windowStartSeconds : item.startSeconds
        const widthSeconds =
          grouped && !bucket.selectedItem
            ? bucket.windowEndSeconds - bucket.windowStartSeconds
            : item.endSeconds - item.startSeconds
        const width =
          item.kind === 'visual' ? 0 : Math.max(1.2, percent(widthSeconds, durationSeconds))
        const style = {
          '--event-left': `${percent(leftSeconds, durationSeconds)}%`,
          '--event-width': `${width}%`,
        } as CSSProperties
        const groupDescription = grouped
          ? `，同一时间像素桶内共 ${bucket.items.length} 条证据，范围 ${formatTime(bucket.evidenceStartSeconds)} 至 ${formatTime(bucket.evidenceEndSeconds)}`
          : ''

        return (
          <button
            type="button"
            className={`rail-event ${bucket.selectedItem ? 'is-selected' : ''}`}
            style={style}
            key={`${track.kind}-${bucket.index}`}
            title={`${item.id} · ${item.label}${grouped ? ` · 聚合 ${bucket.items.length} 条` : ''}`}
            aria-label={`${track.label}证据 ${item.id}，${formatTime(item.startSeconds)}，${item.label}${groupDescription}`}
            onClick={event => selectBucketEvidence(event, bucket)}
          >
            <span className="sr-only">{item.label}</span>
          </button>
        )
      })}
    </>
  )
})

export function EvidenceRail({
  evidence,
  durationSeconds,
  currentTimeSeconds,
  selectedEvidenceId,
  onSeek,
  onSelect,
  compact = false,
}: EvidenceRailProps) {
  const tracksRef = useRef<HTMLDivElement>(null)
  const [bucketCount, setBucketCount] = useState(compact ? 96 : 144)

  useEffect(() => {
    const element = tracksRef.current
    if (!element) return

    const updateBucketCount = (width: number) => {
      if (width <= 0) return
      const next = bucketCountForWidth(width, compact)
      setBucketCount(current => (current === next ? current : next))
    }

    updateBucketCount(element.getBoundingClientRect().width)
    if (typeof ResizeObserver === 'undefined') return

    const observer = new ResizeObserver(entries => {
      const entry = entries[0]
      if (entry) updateBucketCount(entry.contentRect.width)
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [compact])

  const bucketsByKind = useMemo(() => {
    const grouped = Object.fromEntries(
      tracks.map(track => [
        track.kind,
        bucketEvidenceByPixel(
          evidence.filter(item => item.kind === track.kind),
          durationSeconds,
          bucketCount,
          selectedEvidenceId,
        ),
      ]),
    )
    return grouped as Record<EvidenceKind, EvidencePixelBucket[]>
  }, [bucketCount, durationSeconds, evidence, selectedEvidenceId])

  return (
    <section className={`evidence-rail ${compact ? 'is-compact' : ''}`} aria-label="证据时间轨">
      <div className="rail-ticks" aria-hidden="true">
        {[0, 0.25, 0.5, 0.75, 1].map(tick => (
          <span key={tick} style={{ left: `${tick * 100}%` }}>
            {formatTime(durationSeconds * tick)}
          </span>
        ))}
      </div>
      <div className="rail-tracks" ref={tracksRef}>
        {tracks.map(track => (
          <div className={`rail-track track-${track.kind}`} key={track.kind}>
            <span className="rail-track-label" title={track.label}>
              {track.short}
            </span>
            <div className="rail-track-line">
              <RailEventLayer
                buckets={bucketsByKind[track.kind]}
                durationSeconds={durationSeconds}
                track={track}
                onSelect={onSelect}
              />
            </div>
          </div>
        ))}
        <button
          type="button"
          className="rail-playhead"
          style={{ '--playhead-left': `${percent(currentTimeSeconds, durationSeconds)}%` } as CSSProperties}
          onClick={() => onSeek(currentTimeSeconds)}
          title={`当前时间 ${formatTime(currentTimeSeconds)}`}
          aria-label={`当前播放位置 ${formatTime(currentTimeSeconds)}`}
        >
          <span>{formatTime(currentTimeSeconds)}</span>
        </button>
      </div>
    </section>
  )
}

import type { CSSProperties } from 'react'
import type { EvidenceItem, EvidenceKind } from '../domain'
import { formatTime } from '../domain'

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

export function EvidenceRail({
  evidence,
  durationSeconds,
  currentTimeSeconds,
  selectedEvidenceId,
  onSeek,
  onSelect,
  compact = false,
}: EvidenceRailProps) {
  return (
    <section className={`evidence-rail ${compact ? 'is-compact' : ''}`} aria-label="证据时间轨">
      <div className="rail-ticks" aria-hidden="true">
        {[0, 0.25, 0.5, 0.75, 1].map(tick => (
          <span key={tick} style={{ left: `${tick * 100}%` }}>
            {formatTime(durationSeconds * tick)}
          </span>
        ))}
      </div>
      <div className="rail-tracks">
        {tracks.map(track => (
          <div className={`rail-track track-${track.kind}`} key={track.kind}>
            <span className="rail-track-label" title={track.label}>
              {track.short}
            </span>
            <div className="rail-track-line">
              {evidence
                .filter(item => item.kind === track.kind)
                .map(item => {
                  const left = percent(item.startSeconds, durationSeconds)
                  const width =
                    item.kind === 'visual'
                      ? 0
                      : Math.max(1.2, percent(item.endSeconds - item.startSeconds, durationSeconds))
                  const style = {
                    '--event-left': `${left}%`,
                    '--event-width': `${width}%`,
                  } as CSSProperties
                  return (
                    <button
                      type="button"
                      className={`rail-event ${selectedEvidenceId === item.id ? 'is-selected' : ''}`}
                      style={style}
                      key={item.id}
                      title={`${item.id} · ${item.label}`}
                      aria-label={`${track.label}证据 ${item.id}，${formatTime(item.startSeconds)}，${item.label}`}
                      onClick={() => onSelect(item.id, item.startSeconds)}
                    >
                      <span className="sr-only">{item.label}</span>
                    </button>
                  )
                })}
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

import { useEffect, useRef, useState } from 'react'
import { Maximize2, Pause, Play, ScanText } from 'lucide-react'
import { formatTime } from '../domain'
import { useI18n } from '../i18n'
import { VisualAsset } from './VisualAsset'

interface SynchronizedVideoProps {
  title: string
  durationSeconds: number
  currentTimeSeconds: number
  onSeek: (seconds: number) => void
  src?: string
  ocrConfidence?: number
}

export function SynchronizedVideo({
  title,
  durationSeconds,
  currentTimeSeconds,
  onSeek,
  src,
  ocrConfidence,
}: SynchronizedVideoProps) {
  const [playing, setPlaying] = useState(false)
  const videoRef = useRef<HTMLVideoElement>(null)
  const { text } = useI18n()

  useEffect(() => {
    if (src || !playing) return
    const timer = window.setInterval(() => {
      const next = Math.min(durationSeconds, currentTimeSeconds + 0.25)
      onSeek(next)
      if (next >= durationSeconds) setPlaying(false)
    }, 250)
    return () => window.clearInterval(timer)
  }, [currentTimeSeconds, durationSeconds, onSeek, playing, src])

  useEffect(() => {
    const video = videoRef.current
    if (!video || Math.abs(video.currentTime - currentTimeSeconds) < 0.35) return
    video.currentTime = Math.min(currentTimeSeconds, video.duration || durationSeconds)
  }, [currentTimeSeconds, durationSeconds])

  const togglePlayback = () => {
    const video = videoRef.current
    if (!video) {
      setPlaying(value => !value)
      return
    }
    if (video.paused) void video.play()
    else video.pause()
  }

  const sceneIndex = Math.floor(currentTimeSeconds / 180) % 4
  const sceneTitle = [
    text('统一物理时间轴', 'Unified physical timeline'),
    text('选择性二次识别', 'Selective reprocessing'),
    'EvidenceSpan',
    'Canonical NoteDocument',
  ][sceneIndex]
  const sceneAssets = [
    'demoTimeline',
    'demoRework',
    'demoEvidenceSpan',
    'demoNoteDocument',
  ] as const
  const sceneAsset = sceneAssets[sceneIndex]

  return (
    <section className="video-console" aria-label={text('同步视频', 'Synchronized video')}>
      <div className="video-stage">
        <div className="video-fixture-badge">
          {src ? 'LOCAL MEDIA / PTS SYNC' : 'LOCAL PREVIEW / EXPLICIT DEMO'}
        </div>
        {src ? (
          <video
            ref={videoRef}
            className="local-video"
            src={src}
            preload="metadata"
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onTimeUpdate={event => onSeek(event.currentTarget.currentTime)}
            onEnded={() => setPlaying(false)}
            aria-label={text(`本地视频：${title}`, `Local video: ${title}`)}
          />
        ) : (
          <div className="fixture-slide" aria-label={text(`当前演示画面：${sceneTitle}`, `Current demo frame: ${sceneTitle}`)}>
            <VisualAsset
              className="video-fixture-visual"
              asset={sceneAsset}
              width={960}
              height={640}
            />
            <div className="fixture-slide-overlay">
              <small>VIDEO2NOTES / FRAME {Math.floor(currentTimeSeconds * 30)}</small>
              <strong>{sceneTitle}</strong>
              <span>{text('语音 · 屏幕文字 · 画面状态 · 已核查论点', 'speech · screen text · visual state · verified claim')}</span>
            </div>
          </div>
        )}
        {(!src || ocrConfidence !== undefined) && (
          <div className="ocr-corner" title={text('屏幕文字区域', 'On-screen text region')}>
            <ScanText size={16} aria-hidden="true" />
            OCR {ocrConfidence !== undefined ? ocrConfidence.toFixed(2) : '—'}
          </div>
        )}
      </div>
      <div className="video-controls">
        <button
          type="button"
          className="video-play"
          onClick={togglePlayback}
          aria-label={playing ? text('暂停视频', 'Pause video') : text('播放视频', 'Play video')}
        >
          {playing ? <Pause size={16} aria-hidden="true" /> : <Play size={16} aria-hidden="true" />}
        </button>
        <span className="timecode">{formatTime(currentTimeSeconds)}</span>
        <label className="video-scrubber">
          <span className="sr-only">{text('视频播放位置', 'Video playback position')}</span>
          <input
            type="range"
            min={0}
            max={durationSeconds}
            step={0.1}
            value={Math.min(currentTimeSeconds, durationSeconds)}
            onChange={event => onSeek(Number(event.target.value))}
          />
        </label>
        <span className="timecode is-muted">{formatTime(durationSeconds)}</span>
        <button
          type="button"
          className="video-utility"
          aria-label={text('适合窗口', 'Fit window')}
          onClick={() => {
            const video = videoRef.current
            if (video?.requestFullscreen) void video.requestFullscreen()
            else setPlaying(false)
          }}
        >
          <Maximize2 size={15} aria-hidden="true" />
          <span>{text('适合窗口', 'Fit window')}</span>
        </button>
      </div>
      <p className="video-title" title={title}>
        {title}
      </p>
    </section>
  )
}

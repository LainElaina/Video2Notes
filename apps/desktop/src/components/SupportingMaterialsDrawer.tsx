import { useMemo, useRef, useState } from 'react'
import {
  FileImage,
  FileText,
  Link2,
  Plus,
  RefreshCcw,
  Trash2,
  Upload,
  X,
} from 'lucide-react'
import type { ProcessingTask } from '../domain'
import { formatTime } from '../domain'
import { useI18n } from '../i18n'
import { useStudioStore } from '../store'
import { VisualAsset } from './VisualAsset'

interface SupportingMaterialsDrawerProps {
  task: ProcessingTask
  initialStartSeconds?: number
  initialEndSeconds?: number
  onClose: () => void
}

const bytesLabel = (bytes: number) => {
  if (bytes < 1_024) return `${bytes} B`
  if (bytes < 1_048_576) return `${(bytes / 1_024).toFixed(1)} KB`
  return `${(bytes / 1_048_576).toFixed(1)} MB`
}

export function SupportingMaterialsDrawer({
  task,
  initialStartSeconds,
  initialEndSeconds,
  onClose,
}: SupportingMaterialsDrawerProps) {
  const { text } = useI18n()
  const addTextMaterial = useStudioStore(state => state.addTextMaterial)
  const addFileMaterial = useStudioStore(state => state.addFileMaterial)
  const deleteMaterial = useStudioStore(state => state.deleteMaterial)
  const refreshMaterials = useStudioStore(state => state.refreshMaterials)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [bindRange, setBindRange] = useState(
    initialStartSeconds !== undefined && initialEndSeconds !== undefined,
  )
  const [startSeconds, setStartSeconds] = useState(initialStartSeconds ?? 0)
  const [endSeconds, setEndSeconds] = useState(
    initialEndSeconds ?? Math.min(task.source.durationSeconds, 60),
  )

  const validRange =
    !bindRange ||
    (Number.isFinite(startSeconds) &&
      Number.isFinite(endSeconds) &&
      startSeconds >= 0 &&
      endSeconds > startSeconds &&
      endSeconds <= task.source.durationSeconds)
  const canAddText = Boolean(title.trim() && content.trim() && validRange)
  const rangeOptions = useMemo(
    () =>
      bindRange
        ? {
            startUs: Math.round(startSeconds * 1_000_000),
            endUs: Math.round(endSeconds * 1_000_000),
          }
        : {},
    [bindRange, endSeconds, startSeconds],
  )

  const submitText = () => {
    if (!canAddText) return
    addTextMaterial(task.id, {
      title: title.trim(),
      content: content.trim(),
      ...rangeOptions,
    })
    setTitle('')
    setContent('')
  }

  const submitFiles = (files: FileList | null) => {
    if (!files || !validRange) return
    Array.from(files).forEach(file =>
      addFileMaterial(task.id, file, {
        title: file.name,
        ...rangeOptions,
      }),
    )
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  return (
    <div className="workbench-overlay">
      <button
        type="button"
        className="workbench-scrim"
        aria-label={text('关闭补充资料面板', 'Close supporting materials panel')}
        aria-hidden="true"
        tabIndex={-1}
        onClick={onClose}
      />
      <aside
        className="workbench-drawer materials-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="materials-title"
        tabIndex={-1}
      >
        <header className="workbench-drawer-header">
          <div>
            <span className="section-kicker">{text('补充资料', 'SUPPORTING MATERIALS')}</span>
            <h2 id="materials-title">{text('补充资料', 'Supporting materials')}</h2>
            <p>{text('把评论、图片和自行找到的依据绑定到这个视频任务。', 'Attach comments, images, and evidence you found to this video task.')}</p>
          </div>
          <button type="button" onClick={onClose} aria-label={text('关闭补充资料面板', 'Close supporting materials panel')}>
            <X size={18} aria-hidden="true" />
          </button>
        </header>

        <div className="workbench-drawer-body">
          <section className="material-composer">
            <div className="drawer-section-heading">
              <div>
                <span>{text('添加文字', 'ADD TEXT')}</span>
                <h3>{text('加入文字资料', 'Add text material')}</h3>
              </div>
              <Link2 size={16} aria-hidden="true" />
            </div>
            <label>
              {text('资料标题', 'Material title')}
              <input
                value={title}
                maxLength={240}
                onChange={event => setTitle(event.target.value)}
                placeholder={text('例如：评论区中的参考链接与更正', 'For example: references and corrections from the comments')}
              />
            </label>
            <label>
              {text('资料内容', 'Material content')}
              <textarea
                value={content}
                maxLength={1_000_000}
                onChange={event => setContent(event.target.value)}
                placeholder={text('粘贴文字、链接、引用或你自己的观察。生成报告时可作为补充依据。', 'Paste text, links, quotations, or your own observations. They can be used as supporting evidence when generating the report.')}
              />
            </label>
            <label className="material-range-toggle">
              <input
                type="checkbox"
                checked={bindRange}
                onChange={event => setBindRange(event.target.checked)}
              />
              {text('只绑定到视频中的一个时间范围', 'Attach only to a time range in the video')}
            </label>
            {bindRange && (
              <div className="material-range-fields">
                <label>
                  {text('开始（秒）', 'Start (seconds)')}
                  <input
                    type="number"
                    min={0}
                    max={task.source.durationSeconds}
                    step={0.1}
                    value={startSeconds}
                    onChange={event => setStartSeconds(Number(event.target.value))}
                  />
                </label>
                <label>
                  {text('结束（秒）', 'End (seconds)')}
                  <input
                    type="number"
                    min={0}
                    max={task.source.durationSeconds}
                    step={0.1}
                    value={endSeconds}
                    onChange={event => setEndSeconds(Number(event.target.value))}
                  />
                </label>
                <span className={validRange ? '' : 'is-invalid'}>
                  {validRange
                    ? `${formatTime(startSeconds)} — ${formatTime(endSeconds)}`
                    : text('结束时间必须晚于开始时间，并位于视频时长内', 'The end time must be after the start time and within the video duration')}
                </span>
              </div>
            )}
            <button
              type="button"
              className="button button-primary"
              disabled={!canAddText}
              onClick={submitText}
            >
              <Plus size={15} aria-hidden="true" />
              {text('加入此任务', 'Add to this task')}
            </button>
          </section>

          <section className="material-upload">
            <div className="drawer-section-heading">
              <div>
                <span>{text('添加图片', 'ADD IMAGE')}</span>
                <h3>{text('加入图片资料', 'Add image material')}</h3>
              </div>
              <FileImage size={16} aria-hidden="true" />
            </div>
            <input
              ref={fileInputRef}
              className="sr-only"
              type="file"
              accept="image/png,image/jpeg,image/webp"
              multiple
              onChange={event => submitFiles(event.target.files)}
            />
            <button
              type="button"
              className="material-drop-button"
              disabled={!validRange}
              onClick={() => fileInputRef.current?.click()}
            >
              <Upload size={18} aria-hidden="true" />
              <span>
                <strong>{text('选择 PNG、JPEG 或 WebP', 'Choose PNG, JPEG, or WebP files')}</strong>
                <small>{text('单个文件最大 25 MB；本地保存并计算内容哈希', 'Up to 25 MB per file; saved locally with a content hash')}</small>
              </span>
            </button>
          </section>

          <section className="material-library">
            <div className="drawer-section-heading">
              <div>
                <span>{text('已绑定到此任务', 'BOUND TO THIS RUN')}</span>
                <h3>{text('已绑定资料', 'Attached materials')} · {task.materials.length}</h3>
              </div>
              <button
                type="button"
                onClick={() => refreshMaterials(task.id)}
                aria-label={text('刷新补充资料', 'Refresh supporting materials')}
              >
                <RefreshCcw size={14} aria-hidden="true" />
              </button>
            </div>
            {task.materials.length ? (
              <div className="material-list">
                {task.materials.map(material => {
                  const Icon = material.kind === 'image' ? FileImage : FileText
                  return (
                    <article key={material.id}>
                      <span className={`material-kind kind-${material.kind}`}>
                        <Icon size={16} aria-hidden="true" />
                      </span>
                      <div>
                        <strong>{material.title}</strong>
                        <p>
                          {material.textContent ||
                            material.originalName ||
                            text('本地补充资料', 'Local supporting material')}
                        </p>
                        <span>
                          {material.startUs !== null && material.endUs !== null
                            ? `${formatTime(material.startUs / 1_000_000)} — ${formatTime(
                                material.endUs / 1_000_000,
                              )}`
                            : text('全局资料', 'Global material')}
                          {' · '}
                          {bytesLabel(material.sizeBytes)}
                          {' · '}
                          {material.storage === 'run-artifact'
                            ? `${text('本地 artifact', 'Local artifact')} · ${material.sha256?.slice(0, 8)}`
                            : text('演示内存条目', 'In-memory demo item')}
                        </span>
                      </div>
                      <button
                        type="button"
                        onClick={() => deleteMaterial(task.id, material.id)}
                        aria-label={text(`删除资料：${material.title}`, `Delete material: ${material.title}`)}
                      >
                        <Trash2 size={14} aria-hidden="true" />
                      </button>
                    </article>
                  )
                })}
              </div>
            ) : (
              <div className="material-empty">
                <VisualAsset className="inline-empty-visual" asset="emptyMaterials" width={192} height={124} />
                <div>
                  <Link2 size={18} aria-hidden="true" />
                  <p>{text('还没有补充资料。它们会和视频证据一起保存在此任务下。', 'No supporting materials yet. They will be saved with the video evidence under this task.')}</p>
                </div>
              </div>
            )}
          </section>
        </div>

        <footer className="workbench-drawer-footer">
          <span>
            {task.realBackend
              ? text('资料写入本机 run artifact，不会上传到 Video2Notes 云服务。', 'Materials are written to the local run artifact and are not uploaded to a Video2Notes cloud service.')
              : text('当前是明确标记的演示模式；新增内容只保存在本次内存会话。', 'This is an explicitly marked demo mode; new content is kept only in this in-memory session.')}
          </span>
          <button className="button button-secondary" type="button" onClick={onClose}>
            {text('完成', 'Done')}
          </button>
        </footer>
      </aside>
    </div>
  )
}

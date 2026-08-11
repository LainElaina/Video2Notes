import { useMemo, useState } from 'react'
import {
  Check,
  CircleDot,
  Copy,
  Cpu,
  FileCog,
  FolderOpen,
  Link2,
  Pencil,
  RefreshCw,
  ScanSearch,
  ShieldCheck,
  TriangleAlert,
  Unlink,
  Zap,
} from 'lucide-react'
import { copyText } from '../clipboard'
import type {
  LocalToolDefinition,
  LocalToolSource,
  LocalToolStatus,
} from '../domain'
import { useI18n } from '../i18n'
import { useStudioStore } from '../store'
import { usePendingActions } from './usePendingActions'

const sourceLabels: Record<LocalToolSource, { zh: string; en: string }> = {
  binding: { zh: '手动绑定', en: 'Manual binding' },
  path: { zh: '系统 PATH', en: 'System PATH' },
  common: { zh: '常见安装目录', en: 'Common install location' },
  python: { zh: 'Python 环境', en: 'Python environment' },
  system: { zh: '系统运行时', en: 'System runtime' },
  none: { zh: '尚未发现', en: 'Not found' },
}

const statusLabels: Record<LocalToolStatus, { zh: string; en: string }> = {
  ready: { zh: '可用', en: 'Ready' },
  missing: { zh: '未发现', en: 'Not found' },
  incompatible: { zh: '不兼容', en: 'Incompatible' },
  error: { zh: '绑定失效', en: 'Binding error' },
}

const kindLabels = {
  executable: { zh: '程序', en: 'Executable' },
  python_module: { zh: 'Python 模块', en: 'Python module' },
  cuda_runtime: { zh: 'GPU 运行时', en: 'GPU runtime' },
} as const

function LocalToolCard({ tool, canMutate }: { tool: LocalToolDefinition; canMutate: boolean }) {
  const bindPath = useStudioStore(state => state.bindLocalToolPath)
  const unbindPath = useStudioStore(state => state.unbindLocalToolPath)
  const { locale, t, text } = useI18n()
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'error'>('idle')
  const { isPending, track } = usePendingActions()
  const mutationPending = isPending(`local-tool:${tool.dependencyId}`)
  const localized = <T extends { zh: string; en: string }>(copy: T) =>
    locale === 'zh-CN' ? copy.zh : copy.en
  const displayName = locale === 'zh-CN' ? tool.displayNameZh : tool.displayName
  const detail = locale === 'zh-CN' ? tool.detailZh : tool.detail
  const suggestion = locale === 'zh-CN' ? tool.suggestionZh : tool.suggestion
  const choosePrimary = tool.kind === 'executable' ? 'file' : 'directory'

  const copyPath = () => {
    if (!tool.path) return
    void copyText(tool.path)
      .then(() => {
        setCopyState('copied')
        window.setTimeout(() => setCopyState('idle'), 1_800)
      })
      .catch(() => setCopyState('error'))
  }

  return (
    <article
      className={`local-tool-card status-${tool.status} ${tool.bound ? 'is-bound' : ''}`}
      aria-busy={mutationPending || undefined}
    >
      <header>
        <span className="local-tool-icon">
          {tool.compatible ? <Check size={16} aria-hidden="true" /> : <TriangleAlert size={16} aria-hidden="true" />}
        </span>
        <div>
          <h4>{displayName}</h4>
          <small>{tool.dependencyId}</small>
        </div>
        <span className={`local-tool-status status-${tool.status}`}>
          {tool.compatible ? <Check size={11} aria-hidden="true" /> : <CircleDot size={11} aria-hidden="true" />}
          {localized(statusLabels[tool.status])}
        </span>
      </header>

      <div className="local-tool-tags" aria-label={text('依赖能力', 'Dependency capabilities')}>
        <span>{localized(kindLabels[tool.kind])}</span>
        <span><Cpu size={11} aria-hidden="true" />CPU</span>
        {tool.cudaSupported && <span className="is-cuda"><Zap size={11} aria-hidden="true" />CUDA</span>}
        {tool.bound && <span className="is-bound"><Link2 size={11} aria-hidden="true" />{text('已固定路径', 'Path pinned')}</span>}
      </div>

      <dl className="local-tool-meta">
        <div><dt>{t('runtime.version')}</dt><dd>{tool.version ?? t('common.unknown')}</dd></div>
        <div><dt>{text('来源', 'Source')}</dt><dd>{localized(sourceLabels[tool.source])}</dd></div>
      </dl>

      {tool.path ? (
        <div className="local-tool-path">
          <span>{t('runtime.path')}</span>
          <code className="truncate-start" title={tool.path}>{tool.path}</code>
          <button
            className="icon-button"
            type="button"
            aria-label={t('runtime.copyPath')}
            title={copyState === 'copied' ? t('common.copied') : t('runtime.copyPath')}
            onClick={copyPath}
          >
            {copyState === 'copied' ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
          </button>
        </div>
      ) : (
        <div className="local-tool-path is-missing">
          <span>{t('runtime.path')}</span>
          <em>{t('runtime.notFound')}</em>
        </div>
      )}

      <div className="local-tool-description">
        <p>{detail ?? text('尚未返回探测详情。', 'No probe details were returned.')}</p>
        {suggestion && <small>{suggestion}</small>}
        {copyState === 'error' && <small className="is-error">{text('无法写入剪贴板，请检查 Windows 权限。', 'Could not write to the clipboard. Check Windows permissions.')}</small>}
      </div>

      {tool.candidates.length > 1 && (
        <details className="local-tool-candidates">
          <summary>{text(`${tool.candidates.length} 个候选路径`, `${tool.candidates.length} candidate paths`)}</summary>
          <div>
            {tool.candidates.map(candidate => (
              <button
                type="button"
                key={`${candidate.source}:${candidate.path}`}
                disabled={!canMutate || !candidate.compatible || mutationPending}
                onClick={() =>
                  track(`local-tool:${tool.dependencyId}`, () =>
                    bindPath(tool.dependencyId, choosePrimary, candidate.path),
                  )
                }
                title={candidate.path}
              >
                <code className="truncate-start">{candidate.path}</code>
                <span>{candidate.version ?? localized(sourceLabels[candidate.source])}</span>
              </button>
            ))}
          </div>
        </details>
      )}

      <footer>
        {tool.path && !tool.bound && tool.compatible && (
          <button
            className="button button-secondary"
            type="button"
            disabled={!canMutate || mutationPending}
            onClick={() =>
              track(`local-tool:${tool.dependencyId}`, () =>
                bindPath(tool.dependencyId, choosePrimary, tool.path),
              )
            }
          >
            <Link2 size={13} aria-hidden="true" />
            {text('固定使用此路径', 'Use this path')}
          </button>
        )}
        <button
          className="button button-secondary"
          type="button"
          disabled={!canMutate || mutationPending}
          onClick={() =>
            track(`local-tool:${tool.dependencyId}`, () =>
              bindPath(tool.dependencyId, 'file'),
            )
          }
        >
          {tool.bound ? <Pencil size={13} aria-hidden="true" /> : <FileCog size={13} aria-hidden="true" />}
          {tool.bound ? t('runtime.changePath') : text('选择程序', 'Choose program')}
        </button>
        <button
          className="button button-secondary"
          type="button"
          disabled={!canMutate || mutationPending}
          onClick={() =>
            track(`local-tool:${tool.dependencyId}`, () =>
              bindPath(tool.dependencyId, 'directory'),
            )
          }
        >
          <FolderOpen size={13} aria-hidden="true" />
          {text('选择目录', 'Choose folder')}
        </button>
        {tool.bound && (
          <button
            className="button button-quiet"
            type="button"
            disabled={!canMutate || mutationPending}
            onClick={() =>
              track(`local-tool:${tool.dependencyId}`, () =>
                unbindPath(tool.dependencyId),
              )
            }
            title={text('只解除登记，不删除原程序。', 'Remove only the binding; never delete the program.')}
          >
            <Unlink size={13} aria-hidden="true" />
            {t('common.unbind')}
          </button>
        )}
      </footer>
    </article>
  )
}

export function LocalDependenciesPanel({ canMutate, busy }: { canMutate: boolean; busy: boolean }) {
  const inventory = useStudioStore(state => state.runtimePackages?.localTools)
  const discover = useStudioStore(state => state.discoverRuntimePackages)
  const { locale, text } = useI18n()
  const readyCount = inventory?.tools.filter(tool => tool.compatible).length ?? 0
  const boundCount = inventory ? Object.keys(inventory.bindings).length : 0
  const cudaCount = inventory?.tools.filter(tool => tool.cudaSupported).length ?? 0
  const scannedAt = useMemo(() => {
    if (!inventory?.scannedAtUtc) return text('尚未扫描', 'Not scanned yet')
    const value = new Date(inventory.scannedAtUtc)
    if (Number.isNaN(value.getTime())) return inventory.scannedAtUtc
    return new Intl.DateTimeFormat(locale, { dateStyle: 'short', timeStyle: 'medium' }).format(value)
  }, [inventory?.scannedAtUtc, locale, text])

  return (
    <section className="local-dependencies-section" aria-labelledby="local-dependencies-title">
      <header className="runtime-subsection-heading local-dependencies-heading">
        <div>
          <h3 id="local-dependencies-title">{text('本机依赖识别结果', 'Local dependency discovery')}</h3>
          <p>{text('扫描 PATH、常见 Windows 安装目录、当前 Python 环境与 NVIDIA 运行时。无法识别时，每一项都可以手动绑定。', 'Scans PATH, common Windows locations, the current Python environment, and NVIDIA runtimes. Every dependency can be bound manually when discovery fails.')}</p>
        </div>
        <button className="button button-secondary" type="button" disabled={!canMutate} onClick={discover}>
          <RefreshCw className={busy ? 'spin' : ''} size={14} aria-hidden="true" />
          {text('重新检测', 'Rescan')}
        </button>
      </header>

      {inventory ? (
        <>
          <div className="local-dependency-summary" aria-label={text('本机依赖摘要', 'Local dependency summary')}>
            <span><ScanSearch size={14} aria-hidden="true" /><strong>{readyCount} / {inventory.tools.length}</strong>{text('可用', 'ready')}</span>
            <span><Link2 size={14} aria-hidden="true" /><strong>{boundCount}</strong>{text('手动绑定', 'manual bindings')}</span>
            <span><Zap size={14} aria-hidden="true" /><strong>{cudaCount}</strong>{text('支持 CUDA', 'CUDA capable')}</span>
            <span><ShieldCheck size={14} aria-hidden="true" /><strong>{inventory.platform} · {inventory.architecture}</strong>{scannedAt}</span>
          </div>
          <div className="local-tool-grid">
            {inventory.tools.map(tool => <LocalToolCard key={tool.dependencyId} tool={tool} canMutate={canMutate} />)}
          </div>
        </>
      ) : (
        <div className="runtime-empty-state local-tools-empty">
          <ScanSearch size={20} aria-hidden="true" />
          <div>
            <strong>{text('尚未取得本机依赖清单', 'Local dependency inventory is unavailable')}</strong>
            <span>{text('连接本机后端后点击检测；扫描不会修改任何系统文件。', 'Connect the local backend and scan. Discovery never changes system files.')}</span>
          </div>
        </div>
      )}
    </section>
  )
}

import { useMemo, useState } from 'react'
import {
  Archive,
  Check,
  CircleDot,
  Cpu,
  Copy,
  Download,
  ExternalLink,
  FolderCog,
  Gauge,
  HardDrive,
  Link2,
  LoaderCircle,
  PackageCheck,
  RefreshCw,
  RotateCcw,
  ServerCog,
  ShieldCheck,
  Trash2,
  TriangleAlert,
  Unlink,
  X,
  Zap,
} from 'lucide-react'
import type {
  ExperienceMode,
  RuntimeCapabilityDefinition,
  RuntimePackageInstanceDefinition,
  RuntimePackageOperationDefinition,
  RuntimePackageReleaseDefinition,
  RuntimePackageSource,
} from '../domain'
import { useStudioStore } from '../store'
import { copyText } from '../clipboard'
import { useI18n } from '../i18n'
import { LocalDependenciesPanel } from './LocalDependenciesPanel'
import { MotionPresence } from './MotionPresence'
import { VisualAsset } from './VisualAsset'

interface RuntimePackagesPanelProps {
  experienceMode: ExperienceMode
}

function CopyPathButton({ path }: { path: string }) {
  const [copied, setCopied] = useState(false)
  const [failed, setFailed] = useState(false)
  const { t, text } = useI18n()
  const copy = () => {
    void copyText(path)
      .then(() => {
        setFailed(false)
        setCopied(true)
        window.setTimeout(() => setCopied(false), 1_800)
      })
      .catch(() => setFailed(true))
  }
  return (
    <button
      className="icon-button runtime-copy-path"
      type="button"
      onClick={copy}
      aria-label={t('runtime.copyPath')}
      title={failed ? text('复制失败，请检查剪贴板权限。', 'Copy failed. Check clipboard permissions.') : copied ? t('common.copied') : t('runtime.copyPath')}
    >
      {copied ? <Check size={13} aria-hidden="true" /> : <Copy size={13} aria-hidden="true" />}
    </button>
  )
}

const requirements = [
  {
    id: 'tool.ffmpeg',
    capabilityId: 'tool.ffmpeg',
    label: '视频处理',
    labelEn: 'Video processing',
    engine: 'FFmpeg',
    detail: '解码、转封装、音轨提取和截图。',
    detailEn: 'Decode, remux, extract audio tracks, and capture frames.',
    essential: true,
  },
  {
    id: 'tool.ffprobe',
    capabilityId: 'tool.ffprobe',
    label: '媒体探测',
    labelEn: 'Media probing',
    engine: 'FFprobe',
    detail: '读取时长、轨道、编码和帧率。',
    detailEn: 'Read duration, tracks, codecs, and frame rate.',
    essential: true,
  },
  {
    id: 'download.ytdlp',
    capabilityId: 'download.ytdlp',
    label: '链接下载',
    labelEn: 'Link download',
    engine: 'yt-dlp',
    detail: '处理 Bilibili、YouTube 与 X 链接。',
    detailEn: 'Handle Bilibili, YouTube, and X links.',
    essential: true,
  },
  {
    id: 'asr.faster_whisper',
    capabilityId: 'asr.faster_whisper',
    label: '本地语音识别',
    labelEn: 'Local speech recognition',
    engine: 'faster-whisper',
    detail: '提供本地 ASR、时间戳与 CUDA 加速。',
    detailEn: 'Provide local ASR, timestamps, and CUDA acceleration.',
    essential: true,
  },
  {
    id: 'ocr.paddleocr',
    capabilityId: 'ocr.paddleocr',
    label: '画面文字识别',
    labelEn: 'On-screen text recognition',
    engine: 'PaddleOCR',
    detail: '识别关键帧中的屏幕文字与坐标。',
    detailEn: 'Recognize on-screen text and coordinates in keyframes.',
    essential: false,
  },
  {
    id: 'render.chromium_pdf',
    capabilityId: 'render.chromium_pdf',
    label: 'PDF 输出',
    labelEn: 'PDF output',
    engine: 'Chromium',
    detail: '将排版后的 HTML 稳定导出为 PDF。',
    detailEn: 'Reliably export the formatted HTML as PDF.',
    essential: false,
  },
] as const

const sourceCopy: Record<
  RuntimePackageSource,
  { label: string; labelEn: string; detail: string; detailEn: string; icon: typeof Archive }
> = {
  bundled: {
    label: '随应用提供',
    labelEn: 'Bundled with app',
    detail: '只读组件，只能绑定或解绑，不能从应用内删除。',
    detailEn: 'Read-only component. It can be bound or unbound, but not deleted by the app.',
    icon: PackageCheck,
  },
  managed: {
    label: '应用受管',
    labelEn: 'App managed',
    detail: '可安装、升级、回滚和卸载，文件由 Video2Notes 管理。',
    detailEn: 'Can be installed, upgraded, rolled back, and uninstalled by Video2Notes.',
    icon: Archive,
  },
  system: {
    label: '复用本机环境',
    labelEn: 'Reuse this computer',
    detail: '只登记路径和能力，解绑时不会删除系统文件。',
    detailEn: 'Registers only path and capabilities. Unbinding never deletes system files.',
    icon: Cpu,
  },
  custom: {
    label: '自定义目录',
    labelEn: 'Custom folder',
    detail: '只注册或忘记目录，应用不会接管其中的文件。',
    detailEn: 'Registers or forgets the folder without taking ownership of its files.',
    icon: FolderCog,
  },
}

const capabilityLabels: Record<string, string> = {
  'tool.ffmpeg': 'FFmpeg',
  'tool.ffprobe': 'FFprobe',
  'download.ytdlp': 'yt-dlp',
  'asr.faster_whisper': 'ASR',
  'ocr.paddleocr': 'OCR',
  'render.chromium_pdf': 'PDF',
}

const phaseLabels: Record<string, string> = {
  queued: '等待开始',
  downloading: '下载归档',
  verifying_archive: '校验归档',
  extracting: '安全解压',
  probing: '启动探测',
  publishing: '发布版本',
  removing: '移除文件',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

const phaseLabelsEn: Record<string, string> = {
  queued: 'Queued',
  downloading: 'Downloading archive',
  verifying_archive: 'Verifying archive',
  extracting: 'Extracting safely',
  probing: 'Probing runtime',
  publishing: 'Publishing version',
  removing: 'Removing files',
  completed: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
}

const operationKindLabels: Record<RuntimePackageOperationDefinition['kind'], string> = {
  install: '安装',
  upgrade: '升级',
  uninstall: '卸载',
  verify: '验证',
}

const operationKindLabelsEn: typeof operationKindLabels = {
  install: 'Install',
  upgrade: 'Upgrade',
  uninstall: 'Uninstall',
  verify: 'Verify',
}

const formatBytes = (bytes?: number, unknown = '未知'): string => {
  if (bytes === undefined) return unknown
  if (bytes === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const exponent = Math.min(
    units.length - 1,
    Math.floor(Math.log(bytes) / Math.log(1024)),
  )
  const value = bytes / 1024 ** exponent
  return `${value.toFixed(value >= 100 || exponent === 0 ? 0 : value >= 10 ? 1 : 2)} ${units[exponent]}`
}

const formatEta = (seconds: number | undefined, locale: 'zh-CN' | 'en-US'): string => {
  if (seconds === undefined) return locale === 'zh-CN' ? 'ETA 计算中' : 'Calculating ETA'
  if (seconds < 60) return locale === 'zh-CN' ? `约 ${Math.max(1, Math.ceil(seconds))} 秒` : `About ${Math.max(1, Math.ceil(seconds))} sec`
  const minutes = Math.ceil(seconds / 60)
  if (locale === 'zh-CN') return minutes < 60 ? `约 ${minutes} 分钟` : `约 ${(minutes / 60).toFixed(1)} 小时`
  return minutes < 60 ? `About ${minutes} min` : `About ${(minutes / 60).toFixed(1)} hr`
}

const sortVersions = <T extends { version: string }>(items: T[]): T[] =>
  [...items].sort((left, right) =>
    right.version.localeCompare(left.version, undefined, { numeric: true }),
  )

const devicesForCapabilities = (
  capabilities: RuntimeCapabilityDefinition[],
): Array<'cpu' | 'cuda'> =>
  [...new Set(capabilities.flatMap(capability => capability.supportedDevices))]

const operationForInstance = (
  operations: RuntimePackageOperationDefinition[],
  instance: RuntimePackageInstanceDefinition,
): RuntimePackageOperationDefinition | undefined =>
  operations.find(
    operation =>
      ['queued', 'running'].includes(operation.status) &&
      (operation.instanceId === instance.instanceId ||
        operation.sourceInstanceId === instance.instanceId ||
        operation.packageId === instance.packageId),
  )

export function RuntimePackagesPanel({ experienceMode }: RuntimePackagesPanelProps) {
  const { locale, t, text } = useI18n()
  const inventory = useStudioStore(state => state.runtimePackages)
  const status = useStudioStore(state => state.runtimePackagesStatus)
  const error = useStudioStore(state => state.runtimePackageError)
  const backend = useStudioStore(state => state.backend)
  const systemReport = useStudioStore(state => state.systemReport)
  const refresh = useStudioStore(state => state.refreshRuntimePackages)
  const discover = useStudioStore(state => state.discoverRuntimePackages)
  const registerCustom = useStudioStore(state => state.registerCustomRuntimeDirectory)
  const install = useStudioStore(state => state.installRuntimePackage)
  const upgrade = useStudioStore(state => state.upgradeRuntimePackage)
  const cancel = useStudioStore(state => state.cancelRuntimeOperation)
  const bind = useStudioStore(state => state.bindRuntimeRequirement)
  const unbind = useStudioStore(state => state.unbindRuntimeRequirement)
  const remove = useStudioStore(state => state.removeRuntimePackage)
  const forget = useStudioStore(state => state.forgetRuntimePackage)
  const sourceLabel = (source: RuntimePackageSource) =>
    locale === 'zh-CN' ? sourceCopy[source].label : sourceCopy[source].labelEn
  const sourceDetail = (source: RuntimePackageSource) =>
    locale === 'zh-CN' ? sourceCopy[source].detail : sourceCopy[source].detailEn
  const requirementLabel = (requirement: (typeof requirements)[number]) =>
    locale === 'zh-CN' ? requirement.label : requirement.labelEn
  const requirementDetail = (requirement: (typeof requirements)[number]) =>
    locale === 'zh-CN' ? requirement.detail : requirement.detailEn

  const releasesByPackage = useMemo(() => {
    const grouped = new Map<string, RuntimePackageReleaseDefinition[]>()
    for (const release of inventory?.availableReleases ?? []) {
      grouped.set(release.packageId, [
        ...(grouped.get(release.packageId) ?? []),
        release,
      ])
    }
    return new Map(
      [...grouped.entries()].map(([packageId, releases]) => [
        packageId,
        sortVersions(releases),
      ]),
    )
  }, [inventory?.availableReleases])

  const latestReleases = useMemo(
    () => [...releasesByPackage.values()].map(releases => releases[0]),
    [releasesByPackage],
  )

  const boundCount = requirements.filter(requirement =>
    Boolean(inventory?.bindings[requirement.id]),
  ).length
  const activeOperations =
    inventory?.operations.filter(operation =>
      ['queued', 'running'].includes(operation.status),
    ) ?? []
  const managedInstalledBytes =
    inventory?.instances
      .filter(instance => instance.source === 'managed')
      .reduce((total, instance) => {
        const release = releasesByPackage
          .get(instance.packageId)
          ?.find(item => item.version === instance.version)
        return total + (release?.installedSizeBytes ?? 0)
      }, 0) ?? 0
  const missingRequirements = requirements.filter(
    requirement => !inventory?.bindings[requirement.id],
  )
  const nvidiaAvailable = systemReport?.acceleration.asr.cudaAvailable === true
  const recommendedRelease = latestReleases.find(release => {
    const id = release.packageId.toLowerCase()
    return nvidiaAvailable
      ? id.includes('nvidia') && (id.includes('full') || id.includes('asr'))
      : id.includes('cpu')
  })
  const busy = status === 'loading'
  const canMutate = backend.mode === 'real' && !busy

  const managedDestination = (release: RuntimePackageReleaseDefinition): string =>
    release.installRoot ??
    `${inventory?.managedRoot ?? backend.dataRoot ?? text('应用数据目录', 'App data folder')}\\${release.packageId}\\${release.version}`

  const installRelease = (release: RuntimePackageReleaseDefinition) => {
    const bindRequirements = requirements
      .filter(requirement =>
        release.capabilities.some(
          capability => capability.capabilityId === requirement.capabilityId,
        ),
      )
      .map(requirement => requirement.id)
    install(release.packageId, release.version, bindRequirements)
  }

  return (
    <section
      className="runtime-packages-panel models-surface"
      id="runtime-packages"
      aria-labelledby="runtime-packages-title"
      tabIndex={-1}
    >
      <div className="models-section-heading runtime-packages-heading">
        <div className="section-heading-icon">
          <ServerCog size={19} aria-hidden="true" />
        </div>
        <div>
          <span className="section-kicker">{text('可选本地依赖', 'Optional local dependencies')}</span>
          <h2 id="runtime-packages-title">{t('settings.runtime')}</h2>
          <p>{text('按功能安装独立 worker，优先复用兼容环境，并明确区分应用文件与用户目录。', 'Install isolated workers by feature, reuse compatible local environments first, and keep app-owned files separate from user-owned folders.')}</p>
        </div>
        <div className="runtime-heading-actions">
          {backend.mode === 'demo' && (
            <span className="component-demo-badge">{text('演示状态样例', 'Demo status sample')}</span>
          )}
          <button
            className="button button-secondary"
            type="button"
            onClick={discover}
            disabled={!canMutate}
          >
            <Gauge size={14} aria-hidden="true" />
            {t('common.discover')}
          </button>
          <button
            className="button button-secondary"
            type="button"
            onClick={registerCustom}
            disabled={!canMutate}
          >
            <FolderCog size={14} aria-hidden="true" />
            {text('绑定运行时目录', 'Bind runtime folder')}
          </button>
          <button
            className="component-refresh-button"
            type="button"
            aria-label={text('刷新运行时包清单', 'Refresh runtime inventory')}
            title={text('刷新运行时包清单', 'Refresh runtime inventory')}
            onClick={refresh}
            disabled={!canMutate}
          >
            <RefreshCw className={busy ? 'spin' : ''} size={15} aria-hidden="true" />
          </button>
        </div>
      </div>

      <div className="runtime-package-summary" aria-label={text('运行时状态摘要', 'Runtime status summary')}>
        <div>
          <span className="runtime-summary-icon"><Link2 size={16} aria-hidden="true" /></span>
          <span><strong>{boundCount} / {requirements.length}</strong><small>{text('能力已绑定', 'capabilities bound')}</small></span>
        </div>
        <div>
          <span className="runtime-summary-icon"><HardDrive size={16} aria-hidden="true" /></span>
          <span><strong>{formatBytes(managedInstalledBytes, t('common.unknown'))}</strong><small>{text('受管组件体积', 'managed component size')}</small></span>
        </div>
        <div>
          <span className="runtime-summary-icon"><Zap size={16} aria-hidden="true" /></span>
          <span><strong>{nvidiaAvailable ? 'NVIDIA CUDA' : 'CPU'}</strong><small>{text('当前推荐设备', 'recommended device')}</small></span>
        </div>
        <div>
          <span className="runtime-summary-icon"><LoaderCircle size={16} aria-hidden="true" /></span>
          <span><strong>{activeOperations.length}</strong><small>{text('进行中的操作', 'active operations')}</small></span>
        </div>
      </div>

      {experienceMode === 'guided' && (
        <div className="motion-swap-stack motion-swap-surface runtime-guidance-stack">
          <MotionPresence
            show={missingRequirements.length === 0}
            className="motion-presence-swap"
            exitMs={140}
            animateInitial={false}
          >
            {missingRequirements.length === 0 && (
              <div className="runtime-guidance is-ready">
                <span className="runtime-guidance-mark">
                  <ShieldCheck size={18} aria-hidden="true" />
                </span>
                <div>
                  <strong>{text('常用本地能力已经绑定', 'Common local capabilities are bound')}</strong>
                  <span>
                    {text('创建任务时仍会按处理范围再次预检，不需要手工选择 DLL 或 Python 环境。', 'Each task is checked again for its exact scope; you do not need to select individual DLL files or a Python environment.')}
                  </span>
                </div>
              </div>
            )}
          </MotionPresence>
          <MotionPresence
            show={missingRequirements.length > 0}
            className="motion-presence-swap"
            exitMs={140}
            animateInitial={false}
          >
            {missingRequirements.length > 0 && (
              <div className="runtime-guidance">
                <span className="runtime-guidance-mark">
                  <PackageCheck size={18} aria-hidden="true" />
                </span>
                <div>
                  <strong>{text(`还有 ${missingRequirements.length} 项能力未绑定`, `${missingRequirements.length} capabilities are not bound`)}</strong>
                  <span>
                    {recommendedRelease
                      ? text(`${recommendedRelease.displayName} 与当前硬件匹配，安装前会再次显示下载来源、体积和目录。`, `${recommendedRelease.displayName} matches this hardware. Source, size, and destination are shown again before installation.`)
                      : text('当前可信目录没有可自动下载的匹配归档，可检测本机环境或登记自描述运行时目录。', 'No matching archive is available in the trusted catalog. Scan this computer or register a self-describing runtime folder.')}
                  </span>
                </div>
                {recommendedRelease && (
                  <button
                    className="button button-primary"
                    type="button"
                    disabled={!canMutate || activeOperations.length > 0}
                    onClick={() => installRelease(recommendedRelease)}
                  >
                    <Download size={14} aria-hidden="true" />
                    {text('安装推荐运行时', 'Install recommended runtime')}
                  </button>
                )}
              </div>
            )}
          </MotionPresence>
        </div>
      )}

      {error && (
        <div className="runtime-package-error motion-inline-feedback" role="alert">
          <TriangleAlert size={15} aria-hidden="true" />
          <div><strong>{text('运行时操作未完成', 'Runtime operation did not complete')}</strong><span>{text('底层详情：', 'Technical details: ')}{error}</span></div>
        </div>
      )}

      {inventory ? (
        <div className="runtime-package-content motion-surface-enter">
          <LocalDependenciesPanel canMutate={canMutate} busy={busy} />
          <section className="runtime-instance-section" aria-labelledby="runtime-instance-title">
            <header className="runtime-subsection-heading">
              <div>
                <h3 id="runtime-instance-title">{text('已发现的运行时包', 'Discovered runtime packages')}</h3>
                <p>{text('运行时包用于隔离执行 worker；上方的本机依赖清单用于直接程序和环境路径。来源决定应用是否拥有删除权限。', 'Runtime packages isolate worker execution; the local dependency list above manages direct program and environment paths. Source determines whether the app may delete files.')}</p>
              </div>
              <span>{text(`${inventory.instances.length} 个实例`, `${inventory.instances.length} instances`)}</span>
            </header>

            <div className="runtime-instance-list">
              {inventory.instances.map(instance => {
                const source = sourceCopy[instance.source]
                const SourceIcon = source.icon
                const release = releasesByPackage.get(instance.packageId)?.[0]
                const devices = release ? devicesForCapabilities(release.capabilities) : []
                const operation = operationForInstance(inventory.operations, instance)
                const bound = instance.boundRequirements.length > 0
                const canRemove =
                  instance.source === 'managed' &&
                  instance.removable &&
                  !instance.leased &&
                  !bound &&
                  !operation
                const canForget =
                  instance.source === 'custom' && !instance.leased && !bound && !operation
                const officialSource = release?.officialUrl ?? release?.upstreamSources[0]

                return (
                  <article className={`runtime-instance source-${instance.source}`} key={instance.instanceId}>
                    <header>
                      <span className="runtime-source-icon"><SourceIcon size={17} aria-hidden="true" /></span>
                      <div>
                        <span className={`runtime-source-badge source-${instance.source}`}>
                          {sourceLabel(instance.source)}
                        </span>
                        <h4>{instance.displayName}</h4>
                        <small>{instance.packageId}</small>
                      </div>
                      <span className={`runtime-state state-${instance.state}`}>
                        {instance.ready ? <Check size={12} aria-hidden="true" /> : <CircleDot size={12} aria-hidden="true" />}
                        {instance.ready ? text('可用', 'Ready') : instance.state === 'missing' ? t('status.missing') : instance.state === 'invalid' ? t('status.invalid') : t('status.degraded')}
                      </span>
                    </header>

                    <p className="runtime-ownership-note">{sourceDetail(instance.source)}</p>

                    <div className="runtime-capabilities" aria-label={text('提供的能力', 'Provided capabilities')}>
                      {instance.capabilities.map(capability => (
                        <span key={capability}>{capabilityLabels[capability] ?? capability}</span>
                      ))}
                      {devices.map(device => (
                        <span className={`device-${device}`} key={device}>
                          {device === 'cuda' ? 'CUDA' : 'CPU'}
                        </span>
                      ))}
                    </div>

                    <dl className="runtime-instance-meta">
                      <div><dt>{text('当前版本', 'Current version')}</dt><dd>{instance.version}</dd></div>
                      <div><dt>{text('可用版本', 'Available version')}</dt><dd>{instance.availableVersion ?? release?.version ?? text('当前已是最新', 'Up to date')}</dd></div>
                      <div><dt>{text('下载大小', 'Download size')}</dt><dd>{formatBytes(release?.downloadSizeBytes, t('common.unknown'))}</dd></div>
                      <div><dt>{text('安装体积', 'Installed size')}</dt><dd>{formatBytes(release?.installedSizeBytes, t('common.unknown'))}</dd></div>
                      <div><dt>{text('硬件目标', 'Hardware target')}</dt><dd>{instance.targetTriple ?? release?.targetTriple ?? text('由自检确认', 'Confirmed by probe')}</dd></div>
                      <div><dt>{text('活动绑定', 'Active bindings')}</dt><dd>{instance.boundRequirements.length || text('无', 'None')}</dd></div>
                    </dl>

                    <div className="runtime-path-block">
                      <span>{text('安装或绑定目录', 'Install or bound folder')}</span>
                      <code title={instance.root}>{instance.root}</code>
                      <CopyPathButton path={instance.root} />
                    </div>

                    <div className="runtime-instance-links">
                      {officialSource ? (
                        <a href={officialSource} target="_blank" rel="noreferrer">
                          <ExternalLink size={12} aria-hidden="true" />
                          {text('查看下载或上游来源', 'View download or upstream source')}
                        </a>
                      ) : (
                        <span>{text('离线归档或本机目录，没有远程下载地址', 'Offline archive or local folder; no remote download URL')}</span>
                      )}
                      {instance.detail && <span title={instance.detail}>{instance.detail}</span>}
                    </div>

                    {bound && (
                      <div className="runtime-binding-chips" aria-label={text('当前绑定', 'Current bindings')}>
                        {instance.boundRequirements.map(requirementId => (
                          <span key={requirementId}>
                            <Link2 size={11} aria-hidden="true" />
                            {(() => {
                              const requirement = requirements.find(item => item.id === requirementId)
                              return requirement ? requirementLabel(requirement) : requirementId
                            })()}
                          </span>
                        ))}
                      </div>
                    )}

                    <footer>
                      {operation ? (
                        <button
                          className="button button-secondary"
                          type="button"
                          disabled={!canMutate || operation.cancelRequested}
                          onClick={() => cancel(operation.operationId)}
                        >
                          <X size={14} aria-hidden="true" />
                          {operation.cancelRequested ? text('正在取消', 'Cancelling') : text('取消操作', 'Cancel operation')}
                        </button>
                      ) : (
                        <>
                          {instance.source === 'managed' && instance.availableVersion && (
                            <button
                              className="button button-secondary"
                              type="button"
                              disabled={!canMutate || instance.leased}
                              onClick={() => upgrade(instance.instanceId)}
                            >
                              <RotateCcw size={14} aria-hidden="true" />
                              {text('升级到', 'Upgrade to')} {instance.availableVersion}
                            </button>
                          )}
                          {instance.source === 'managed' && (
                            <button
                              className="button button-quiet runtime-danger-action"
                              type="button"
                              disabled={!canMutate || !canRemove}
                              title={bound ? text('先解除所有绑定', 'Remove all bindings first') : instance.leased ? text('任务正在使用此版本', 'A task is using this version') : text('卸载受管文件', 'Uninstall managed files')}
                              onClick={() => {
                                if (window.confirm(text('卸载这个受管运行时包？模型权重和用户文件不会被删除。', 'Uninstall this managed runtime package? Model weights and user files will not be deleted.'))) {
                                  remove(instance.instanceId)
                                }
                              }}
                            >
                              <Trash2 size={13} aria-hidden="true" />
                              {text('卸载', 'Uninstall')}
                            </button>
                          )}
                          {instance.source === 'custom' && (
                            <button
                              className="button button-quiet"
                              type="button"
                              disabled={!canMutate || !canForget}
                              title={bound ? text('先解除所有绑定', 'Remove all bindings first') : text('只忘记登记，不删除原目录', 'Forget only the registration; keep the original folder')}
                              onClick={() => {
                                if (window.confirm(text('忘记这个自定义运行时登记？原目录与文件会保留。', 'Forget this custom runtime registration? The original folder and files will remain.'))) {
                                  forget(instance.instanceId)
                                }
                              }}
                            >
                              <Unlink size={13} aria-hidden="true" />
                              {text('忘记登记', 'Forget registration')}
                            </button>
                          )}
                        </>
                      )}
                    </footer>
                  </article>
                )
              })}
              {inventory.instances.length === 0 && (
                <div className="runtime-empty-state">
                  <VisualAsset className="inline-empty-visual" asset="emptyRuntime" width={192} height={124} />
                  <div>
                    <Archive size={20} aria-hidden="true" />
                    <strong>{text('还没有发现可用运行时包', 'No usable runtime packages found')}</strong>
                    <span>{text('检测本机环境、绑定自描述目录，或从可信目录安装组件。', 'Scan this computer, bind a self-describing folder, or install from the trusted catalog.')}</span>
                  </div>
                </div>
              )}
            </div>
          </section>

          {experienceMode === 'professional' && (
            <section className="runtime-binding-section motion-swap-surface" aria-labelledby="runtime-binding-title">
              <header className="runtime-subsection-heading">
                <div>
                  <h3 id="runtime-binding-title">{text('功能绑定', 'Capability bindings')}</h3>
                  <p>{text('只有完整提供对应 capability 且探测通过的实例会出现在下拉框中。', 'Only probed instances that fully provide the capability appear in each list.')}</p>
                </div>
                <span>{boundCount} / {requirements.length}</span>
              </header>
              <div className="runtime-binding-list">
                {requirements.map(requirement => {
                  const binding = inventory.bindings[requirement.id]
                  const compatible = inventory.instances.filter(
                    instance =>
                      instance.ready &&
                      instance.capabilities.includes(requirement.capabilityId),
                  )
                  return (
                    <div className="runtime-binding-row" key={requirement.id}>
                      <span className="runtime-binding-kind">
                        {requirement.essential ? <ShieldCheck size={15} aria-hidden="true" /> : <CircleDot size={15} aria-hidden="true" />}
                      </span>
                      <div><strong>{requirementLabel(requirement)}</strong><small>{requirement.engine} · {requirementDetail(requirement)}</small></div>
                      <label>
                        <span className="sr-only">{text(`为${requirement.label}选择运行时`, `Choose a runtime for ${requirement.labelEn}`)}</span>
                        <select
                          value={binding?.instanceId ?? ''}
                          disabled={!canMutate}
                          onChange={event => {
                            const instanceId = event.target.value
                            if (instanceId) {
                              bind(requirement.id, instanceId, requirement.capabilityId)
                            } else {
                              unbind(requirement.id)
                            }
                          }}
                        >
                          <option value="">{text('未绑定', 'Not bound')}</option>
                          {compatible.map(instance => (
                            <option value={instance.instanceId} key={instance.instanceId}>
                              {instance.displayName} · {sourceLabel(instance.source)}
                            </option>
                          ))}
                        </select>
                      </label>
                      <span className={`runtime-binding-state ${binding ? 'is-ready' : ''}`}>
                        {binding ? <Check size={12} aria-hidden="true" /> : <TriangleAlert size={12} aria-hidden="true" />}
                        {binding ? binding.packageVersion : requirement.essential ? text('处理前必须解决', 'Required before processing') : text('按功能需要', 'Needed by feature')}
                      </span>
                    </div>
                  )
                })}
              </div>
            </section>
          )}

          <section className="runtime-release-section" aria-labelledby="runtime-release-title">
            <header className="runtime-subsection-heading">
              <div>
                <h3 id="runtime-release-title">{text('可信目录中的可安装包', 'Installable packages in the trusted catalog')}</h3>
                <p>{text('安装前展示远程来源、归档大小、展开体积与受管目标目录。', 'Remote source, archive size, expanded size, and managed destination are shown before installation.')}</p>
              </div>
              <span>{text(`${latestReleases.length} 个包`, `${latestReleases.length} packages`)}</span>
            </header>
            {latestReleases.length > 0 ? (
              <div className="runtime-release-grid">
                {latestReleases.map(release => {
                  const operation = inventory.operations.find(
                    item =>
                      item.packageId === release.packageId &&
                      ['queued', 'running'].includes(item.status),
                  )
                  const installed = inventory.instances.some(
                    instance =>
                      instance.source === 'managed' &&
                      instance.packageId === release.packageId &&
                      instance.version === release.version &&
                      instance.ready,
                  )
                  const devices = devicesForCapabilities(release.capabilities)
                  const officialSource = release.officialUrl ?? release.upstreamSources[0]
                  return (
                    <article key={`${release.packageId}:${release.version}`}>
                      <header><Archive size={17} aria-hidden="true" /><div><strong>{release.displayName}</strong><small>{release.packageId}</small></div></header>
                      <div className="runtime-release-capabilities">
                        {release.capabilities.map(capability => <span key={capability.capabilityId}>{capabilityLabels[capability.capabilityId] ?? capability.capabilityId}</span>)}
                        {devices.map(device => <span key={device}>{device === 'cuda' ? 'CUDA' : 'CPU'}</span>)}
                      </div>
                      <dl>
                        <div><dt>{t('runtime.version')}</dt><dd>{release.version}</dd></div>
                        <div><dt>{text('下载', 'Download')}</dt><dd>{formatBytes(release.downloadSizeBytes, t('common.unknown'))}</dd></div>
                        <div><dt>{text('安装后', 'Installed')}</dt><dd>{formatBytes(release.installedSizeBytes, t('common.unknown'))}</dd></div>
                        <div><dt>{text('目标', 'Target')}</dt><dd>{release.targetTriple ?? 'Windows x64'}</dd></div>
                      </dl>
                      <div className="runtime-release-destination"><span>{text('最终目录', 'Destination')}</span><code title={managedDestination(release)}>{managedDestination(release)}</code><CopyPathButton path={managedDestination(release)} /></div>
                      <div className="runtime-release-source">
                        {officialSource ? <a href={officialSource} target="_blank" rel="noreferrer"><ExternalLink size={11} aria-hidden="true" />{release.officialUrl ? text('官方归档地址', 'Official archive URL') : text('上游项目来源', 'Upstream project')}</a> : <span>{text('仅可从随包离线目录安装', 'Installable only from the bundled offline catalog')}</span>}
                        <span>{release.downloadPartCount > 1 ? text(`分 ${release.downloadPartCount} 个固定哈希文件下载`, `${release.downloadPartCount} fixed-hash download parts`) : release.offlineOnly ? text('离线归档', 'Offline archive') : 'HTTPS + SHA-256'}</span>
                      </div>
                      <button
                        className="button button-primary"
                        type="button"
                        disabled={!canMutate || installed || Boolean(operation)}
                        onClick={() => installRelease(release)}
                      >
                        {operation ? <LoaderCircle className="spin" size={14} aria-hidden="true" /> : installed ? <Check size={14} aria-hidden="true" /> : <Download size={14} aria-hidden="true" />}
                        {operation ? text('正在处理', 'Processing') : installed ? text('已安装', 'Installed') : text('安装并绑定能力', 'Install and bind capabilities')}
                      </button>
                    </article>
                  )
                })}
              </div>
            ) : (
              <div className="runtime-catalog-empty">
                <ShieldCheck size={18} aria-hidden="true" />
                <div><strong>{text('当前没有可在线安装的可信归档', 'No trusted online archive is currently available')}</strong><span>{text('目录不会展示未经固定大小与 SHA-256 的下载地址；仍可复用本机环境或登记离线运行时。', 'The catalog never offers downloads without a fixed size and SHA-256. You can still reuse this computer or register an offline runtime.')}</span></div>
              </div>
            )}
          </section>

          {inventory.operations.length > 0 && (
            <section className="runtime-operation-section motion-surface-enter" aria-labelledby="runtime-operation-title">
              <header className="runtime-subsection-heading">
                <div><h3 id="runtime-operation-title">{text('安装与维护记录', 'Installation and maintenance history')}</h3><p>{text('运行中的任务显示阶段、传输速度、预计剩余时间和可续传状态。', 'Active operations show phase, transfer speed, estimated time remaining, and resume support.')}</p></div>
                <span>{text(`${inventory.operations.length} 条`, `${inventory.operations.length} records`)}</span>
              </header>
              <div className="runtime-operation-list" aria-live="polite">
                {[...inventory.operations]
                  .sort((left, right) => right.createdAtUtc.localeCompare(left.createdAtUtc))
                  .slice(0, 8)
                  .map(operation => {
                    const active = ['queued', 'running'].includes(operation.status)
                    const succeeded = operation.status === 'succeeded'
                    const failed = operation.status === 'failed'
                    return (
                      <article className={`runtime-operation motion-list-item status-${operation.status}`} key={operation.operationId}>
                        <span className="runtime-operation-icon">
                          {active ? <LoaderCircle className="spin" size={16} aria-hidden="true" /> : succeeded ? <Check size={16} aria-hidden="true" /> : failed ? <TriangleAlert size={16} aria-hidden="true" /> : <CircleDot size={16} aria-hidden="true" />}
                        </span>
                        <div className="runtime-operation-main">
                          <header><strong>{(locale === 'zh-CN' ? operationKindLabels : operationKindLabelsEn)[operation.kind]} · {operation.packageId}</strong><span>{(locale === 'zh-CN' ? phaseLabels : phaseLabelsEn)[operation.phase ?? operation.status] ?? operation.status}</span></header>
                          <progress max="1" value={operation.progress} aria-label={`${operation.packageId} ${text('操作进度', 'operation progress')}`} />
                          <div>
                            <span>{Math.round(operation.progress * 100)}%</span>
                            <span>{formatBytes(operation.downloadedBytes)} / {formatBytes(operation.totalBytes)}</span>
                            <span>{operation.transferSpeedBytesPerSecond ? `${formatBytes(operation.transferSpeedBytesPerSecond, t('common.unknown'))}/s` : text('速度计算中', 'Calculating speed')}</span>
                            <span>{formatEta(operation.etaSeconds, locale)}</span>
                            <span>{operation.resumable ? text('可续传', 'Resumable') : text('不可续传', 'Not resumable')}</span>
                          </div>
                          {(operation.detail || operation.errorCode) && <p>{operation.errorCode ? `${operation.errorCode}: ` : ''}{operation.detail === '演示记录：组件已安装并通过 worker 探测。' ? text('演示记录：组件已安装并通过 worker 探测。', 'Demo record: the component was installed and passed its worker probe.') : operation.detail}</p>}
                        </div>
                        {active && (
                          <button className="button button-quiet" type="button" disabled={!canMutate || operation.cancelRequested} onClick={() => cancel(operation.operationId)}>
                            <X size={13} aria-hidden="true" />
                            {operation.cancelRequested ? text('取消中', 'Cancelling') : t('common.cancel')}
                          </button>
                        )}
                      </article>
                    )
                  })}
              </div>
            </section>
          )}
        </div>
      ) : (
        <div className="runtime-loading-state motion-surface-enter">
          {status === 'loading' ? <LoaderCircle className="spin" size={19} aria-hidden="true" /> : <ServerCog size={19} aria-hidden="true" />}
          <div><strong>{status === 'loading' ? text('正在读取运行时包清单', 'Loading runtime inventory') : text('尚未取得运行时包清单', 'Runtime inventory is unavailable')}</strong><span>{backend.mode === 'offline' ? text('本机后端未连接。', 'The local backend is not connected.') : text('刷新后会显示可复用环境、受管安装包与能力绑定。', 'Refresh to show reusable environments, managed packages, and capability bindings.')}</span></div>
        </div>
      )}
    </section>
  )
}

import { useMemo } from 'react'
import {
  Archive,
  Check,
  CircleDot,
  Cpu,
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
import { MotionPresence } from './MotionPresence'
import { VisualAsset } from './VisualAsset'

interface RuntimePackagesPanelProps {
  experienceMode: ExperienceMode
}

const requirements = [
  {
    id: 'tool.ffmpeg',
    capabilityId: 'tool.ffmpeg',
    label: '视频处理',
    engine: 'FFmpeg',
    detail: '解码、转封装、音轨提取和截图。',
    essential: true,
  },
  {
    id: 'tool.ffprobe',
    capabilityId: 'tool.ffprobe',
    label: '媒体探测',
    engine: 'FFprobe',
    detail: '读取时长、轨道、编码和帧率。',
    essential: true,
  },
  {
    id: 'download.ytdlp',
    capabilityId: 'download.ytdlp',
    label: '链接下载',
    engine: 'yt-dlp',
    detail: '处理 Bilibili、YouTube 与 X 链接。',
    essential: true,
  },
  {
    id: 'asr.faster_whisper',
    capabilityId: 'asr.faster_whisper',
    label: '本地语音识别',
    engine: 'faster-whisper',
    detail: '提供本地 ASR、时间戳与 CUDA 加速。',
    essential: true,
  },
  {
    id: 'ocr.paddleocr',
    capabilityId: 'ocr.paddleocr',
    label: '画面文字识别',
    engine: 'PaddleOCR',
    detail: '识别关键帧中的屏幕文字与坐标。',
    essential: false,
  },
  {
    id: 'render.chromium_pdf',
    capabilityId: 'render.chromium_pdf',
    label: 'PDF 输出',
    engine: 'Chromium',
    detail: '将排版后的 HTML 稳定导出为 PDF。',
    essential: false,
  },
] as const

const sourceCopy: Record<
  RuntimePackageSource,
  { label: string; detail: string; icon: typeof Archive }
> = {
  bundled: {
    label: '随应用提供',
    detail: '只读组件，只能绑定或解绑，不能从应用内删除。',
    icon: PackageCheck,
  },
  managed: {
    label: '应用受管',
    detail: '可安装、升级、回滚和卸载，文件由 Video2Notes 管理。',
    icon: Archive,
  },
  system: {
    label: '复用本机环境',
    detail: '只登记路径和能力，解绑时不会删除系统文件。',
    icon: Cpu,
  },
  custom: {
    label: '自定义目录',
    detail: '只注册或忘记目录，应用不会接管其中的文件。',
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

const operationKindLabels: Record<RuntimePackageOperationDefinition['kind'], string> = {
  install: '安装',
  upgrade: '升级',
  uninstall: '卸载',
  verify: '验证',
}

const formatBytes = (bytes?: number): string => {
  if (bytes === undefined) return '未知'
  if (bytes === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const exponent = Math.min(
    units.length - 1,
    Math.floor(Math.log(bytes) / Math.log(1024)),
  )
  const value = bytes / 1024 ** exponent
  return `${value.toFixed(value >= 100 || exponent === 0 ? 0 : value >= 10 ? 1 : 2)} ${units[exponent]}`
}

const formatEta = (seconds?: number): string => {
  if (seconds === undefined) return 'ETA 计算中'
  if (seconds < 60) return `约 ${Math.max(1, Math.ceil(seconds))} 秒`
  const minutes = Math.ceil(seconds / 60)
  return minutes < 60 ? `约 ${minutes} 分钟` : `约 ${(minutes / 60).toFixed(1)} 小时`
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
    `${inventory?.managedRoot ?? backend.dataRoot ?? '应用数据目录'}\\${release.packageId}\\${release.version}`

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
          <span className="section-kicker">可选本地依赖</span>
          <h2 id="runtime-packages-title">依赖与运行时</h2>
          <p>按功能安装独立 worker，优先复用兼容环境，并明确区分应用文件与用户目录。</p>
        </div>
        <div className="runtime-heading-actions">
          {backend.mode === 'demo' && (
            <span className="component-demo-badge">演示状态样例</span>
          )}
          <button
            className="button button-secondary"
            type="button"
            onClick={discover}
            disabled={!canMutate}
          >
            <Gauge size={14} aria-hidden="true" />
            检测本机环境
          </button>
          <button
            className="button button-secondary"
            type="button"
            onClick={registerCustom}
            disabled={!canMutate}
          >
            <FolderCog size={14} aria-hidden="true" />
            绑定目录
          </button>
          <button
            className="component-refresh-button"
            type="button"
            aria-label="刷新运行时包清单"
            title="刷新运行时包清单"
            onClick={refresh}
            disabled={!canMutate}
          >
            <RefreshCw className={busy ? 'spin' : ''} size={15} aria-hidden="true" />
          </button>
        </div>
      </div>

      <div className="runtime-package-summary" aria-label="运行时状态摘要">
        <div>
          <span className="runtime-summary-icon"><Link2 size={16} aria-hidden="true" /></span>
          <span><strong>{boundCount} / {requirements.length}</strong><small>能力已绑定</small></span>
        </div>
        <div>
          <span className="runtime-summary-icon"><HardDrive size={16} aria-hidden="true" /></span>
          <span><strong>{formatBytes(managedInstalledBytes)}</strong><small>受管组件体积</small></span>
        </div>
        <div>
          <span className="runtime-summary-icon"><Zap size={16} aria-hidden="true" /></span>
          <span><strong>{nvidiaAvailable ? 'NVIDIA CUDA' : 'CPU'}</strong><small>当前推荐设备</small></span>
        </div>
        <div>
          <span className="runtime-summary-icon"><LoaderCircle size={16} aria-hidden="true" /></span>
          <span><strong>{activeOperations.length}</strong><small>进行中的操作</small></span>
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
                  <strong>常用本地能力已经绑定</strong>
                  <span>
                    创建任务时仍会按处理范围再次预检，不需要手工选择 DLL 或 Python 环境。
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
                  <strong>还有 {missingRequirements.length} 项能力未绑定</strong>
                  <span>
                    {recommendedRelease
                      ? `${recommendedRelease.displayName} 与当前硬件匹配，安装前会再次显示下载来源、体积和目录。`
                      : '当前可信目录没有可自动下载的匹配归档，可检测本机环境或登记自描述运行时目录。'}
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
                    安装推荐运行时
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
          <div><strong>运行时操作未完成</strong><span>{error}</span></div>
        </div>
      )}

      {inventory ? (
        <div className="runtime-package-content motion-surface-enter">
          <section className="runtime-instance-section" aria-labelledby="runtime-instance-title">
            <header className="runtime-subsection-heading">
              <div>
                <h3 id="runtime-instance-title">已发现的运行时</h3>
                <p>绑定决定某项功能实际调用哪个实例，来源决定应用是否拥有删除权限。</p>
              </div>
              <span>{inventory.instances.length} 个实例</span>
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
                          {source.label}
                        </span>
                        <h4>{instance.displayName}</h4>
                        <small>{instance.packageId}</small>
                      </div>
                      <span className={`runtime-state state-${instance.state}`}>
                        {instance.ready ? <Check size={12} aria-hidden="true" /> : <CircleDot size={12} aria-hidden="true" />}
                        {instance.ready ? '可用' : instance.state === 'missing' ? '路径缺失' : instance.state === 'invalid' ? '校验失败' : '受限可用'}
                      </span>
                    </header>

                    <p className="runtime-ownership-note">{source.detail}</p>

                    <div className="runtime-capabilities" aria-label="提供的能力">
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
                      <div><dt>当前版本</dt><dd>{instance.version}</dd></div>
                      <div><dt>可用版本</dt><dd>{instance.availableVersion ?? release?.version ?? '当前已是最新'}</dd></div>
                      <div><dt>下载大小</dt><dd>{formatBytes(release?.downloadSizeBytes)}</dd></div>
                      <div><dt>安装体积</dt><dd>{formatBytes(release?.installedSizeBytes)}</dd></div>
                      <div><dt>硬件目标</dt><dd>{instance.targetTriple ?? release?.targetTriple ?? '由自检确认'}</dd></div>
                      <div><dt>活动绑定</dt><dd>{instance.boundRequirements.length || '无'}</dd></div>
                    </dl>

                    <div className="runtime-path-block">
                      <span>安装或绑定目录</span>
                      <code title={instance.root}>{instance.root}</code>
                    </div>

                    <div className="runtime-instance-links">
                      {officialSource ? (
                        <a href={officialSource} target="_blank" rel="noreferrer">
                          <ExternalLink size={12} aria-hidden="true" />
                          查看下载或上游来源
                        </a>
                      ) : (
                        <span>离线归档或本机目录，没有远程下载地址</span>
                      )}
                      {instance.detail && <span title={instance.detail}>{instance.detail}</span>}
                    </div>

                    {bound && (
                      <div className="runtime-binding-chips" aria-label="当前绑定">
                        {instance.boundRequirements.map(requirementId => (
                          <span key={requirementId}>
                            <Link2 size={11} aria-hidden="true" />
                            {requirements.find(item => item.id === requirementId)?.label ?? requirementId}
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
                          {operation.cancelRequested ? '正在取消' : '取消操作'}
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
                              升级到 {instance.availableVersion}
                            </button>
                          )}
                          {instance.source === 'managed' && (
                            <button
                              className="button button-quiet runtime-danger-action"
                              type="button"
                              disabled={!canMutate || !canRemove}
                              title={bound ? '先解除所有绑定' : instance.leased ? '任务正在使用此版本' : '卸载受管文件'}
                              onClick={() => {
                                if (window.confirm('卸载这个受管运行时包？模型权重和用户文件不会被删除。')) {
                                  remove(instance.instanceId)
                                }
                              }}
                            >
                              <Trash2 size={13} aria-hidden="true" />
                              卸载
                            </button>
                          )}
                          {instance.source === 'custom' && (
                            <button
                              className="button button-quiet"
                              type="button"
                              disabled={!canMutate || !canForget}
                              title={bound ? '先解除所有绑定' : '只忘记登记，不删除原目录'}
                              onClick={() => {
                                if (window.confirm('忘记这个自定义运行时登记？原目录与文件会保留。')) {
                                  forget(instance.instanceId)
                                }
                              }}
                            >
                              <Unlink size={13} aria-hidden="true" />
                              忘记登记
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
                    <strong>还没有发现可用运行时</strong>
                    <span>检测本机环境、绑定自描述目录，或从可信目录安装组件。</span>
                  </div>
                </div>
              )}
            </div>
          </section>

          {experienceMode === 'professional' && (
            <section className="runtime-binding-section motion-swap-surface" aria-labelledby="runtime-binding-title">
              <header className="runtime-subsection-heading">
                <div>
                  <h3 id="runtime-binding-title">功能绑定</h3>
                  <p>只有完整提供对应 capability 且探测通过的实例会出现在下拉框中。</p>
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
                      <div><strong>{requirement.label}</strong><small>{requirement.engine} · {requirement.detail}</small></div>
                      <label>
                        <span className="sr-only">为{requirement.label}选择运行时</span>
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
                          <option value="">未绑定</option>
                          {compatible.map(instance => (
                            <option value={instance.instanceId} key={instance.instanceId}>
                              {instance.displayName} · {sourceCopy[instance.source].label}
                            </option>
                          ))}
                        </select>
                      </label>
                      <span className={`runtime-binding-state ${binding ? 'is-ready' : ''}`}>
                        {binding ? <Check size={12} aria-hidden="true" /> : <TriangleAlert size={12} aria-hidden="true" />}
                        {binding ? binding.packageVersion : requirement.essential ? '处理前必须解决' : '按功能需要'}
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
                <h3 id="runtime-release-title">可信目录中的可安装包</h3>
                <p>安装前展示远程来源、归档大小、展开体积与受管目标目录。</p>
              </div>
              <span>{latestReleases.length} 个包</span>
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
                        <div><dt>版本</dt><dd>{release.version}</dd></div>
                        <div><dt>下载</dt><dd>{formatBytes(release.downloadSizeBytes)}</dd></div>
                        <div><dt>安装后</dt><dd>{formatBytes(release.installedSizeBytes)}</dd></div>
                        <div><dt>目标</dt><dd>{release.targetTriple ?? 'Windows x64'}</dd></div>
                      </dl>
                      <div className="runtime-release-destination"><span>最终目录</span><code title={managedDestination(release)}>{managedDestination(release)}</code></div>
                      <div className="runtime-release-source">
                        {officialSource ? <a href={officialSource} target="_blank" rel="noreferrer"><ExternalLink size={11} aria-hidden="true" />{release.officialUrl ? '官方归档地址' : '上游项目来源'}</a> : <span>仅可从随包离线目录安装</span>}
                        <span>{release.downloadPartCount > 1 ? `分 ${release.downloadPartCount} 个固定哈希文件下载` : release.offlineOnly ? '离线归档' : 'HTTPS + SHA-256 固定'}</span>
                      </div>
                      <button
                        className="button button-primary"
                        type="button"
                        disabled={!canMutate || installed || Boolean(operation)}
                        onClick={() => installRelease(release)}
                      >
                        {operation ? <LoaderCircle className="spin" size={14} aria-hidden="true" /> : installed ? <Check size={14} aria-hidden="true" /> : <Download size={14} aria-hidden="true" />}
                        {operation ? '正在处理' : installed ? '已安装' : '安装并绑定能力'}
                      </button>
                    </article>
                  )
                })}
              </div>
            ) : (
              <div className="runtime-catalog-empty">
                <ShieldCheck size={18} aria-hidden="true" />
                <div><strong>当前没有可在线安装的可信归档</strong><span>目录不会展示未经固定大小与 SHA-256 的下载地址；仍可复用本机环境或登记离线运行时。</span></div>
              </div>
            )}
          </section>

          {inventory.operations.length > 0 && (
            <section className="runtime-operation-section motion-surface-enter" aria-labelledby="runtime-operation-title">
              <header className="runtime-subsection-heading">
                <div><h3 id="runtime-operation-title">安装与维护记录</h3><p>运行中的任务显示阶段、传输速度、预计剩余时间和可续传状态。</p></div>
                <span>{inventory.operations.length} 条</span>
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
                          <header><strong>{operationKindLabels[operation.kind]} · {operation.packageId}</strong><span>{phaseLabels[operation.phase ?? operation.status] ?? operation.status}</span></header>
                          <progress max="1" value={operation.progress} aria-label={`${operation.packageId} 操作进度`} />
                          <div>
                            <span>{Math.round(operation.progress * 100)}%</span>
                            <span>{formatBytes(operation.downloadedBytes)} / {formatBytes(operation.totalBytes)}</span>
                            <span>{operation.transferSpeedBytesPerSecond ? `${formatBytes(operation.transferSpeedBytesPerSecond)}/s` : '速度计算中'}</span>
                            <span>{formatEta(operation.etaSeconds)}</span>
                            <span>{operation.resumable ? '可续传' : '不可续传'}</span>
                          </div>
                          {(operation.detail || operation.errorCode) && <p>{operation.errorCode ? `${operation.errorCode}: ` : ''}{operation.detail}</p>}
                        </div>
                        {active && (
                          <button className="button button-quiet" type="button" disabled={!canMutate || operation.cancelRequested} onClick={() => cancel(operation.operationId)}>
                            <X size={13} aria-hidden="true" />
                            {operation.cancelRequested ? '取消中' : '取消'}
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
          <div><strong>{status === 'loading' ? '正在读取运行时包清单' : '尚未取得运行时包清单'}</strong><span>{backend.mode === 'offline' ? '本机后端未连接。' : '刷新后会显示可复用环境、受管安装包与能力绑定。'}</span></div>
        </div>
      )}
    </section>
  )
}

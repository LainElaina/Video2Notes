import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import {
  Bot,
  Check,
  ChevronRight,
  CircleDot,
  Cloud,
  Cpu,
  Database,
  Download,
  Gauge,
  HardDrive,
  KeyRound,
  Link2,
  LoaderCircle,
  LockKeyhole,
  Plus,
  Radar,
  RefreshCw,
  Save,
  ScanText,
  Search,
  ServerCog,
  Settings2,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
  WandSparkles,
  X,
  Zap,
} from 'lucide-react'
import type {
  ComponentInventoryItemDefinition,
  HardwareTier,
  ModelCapability,
  PerformanceOverrides,
  PerformanceSettings,
  ProviderAuthScheme,
  ProviderKind,
  ProviderProtocol,
  ResourceReserve,
} from '../domain'
import { MotionPresence } from '../components/MotionPresence'
import { RuntimePackagesPanel } from '../components/RuntimePackagesPanel'
import { useStudioStore } from '../store'
import {
  useUiPreferences,
  type FontSizePreset,
} from '../stores/uiPreferences'

const capabilityLabels: Record<ModelCapability, string> = {
  text: '文本理解',
  vision: '图像理解',
  structured_output: '结构化输出',
  long_context: '长上下文',
  embeddings: '向量嵌入',
  asr: '语音识别',
  language_id: '语言识别',
  segment_timestamps: '分段时间戳',
  word_timestamps: '词级时间戳',
  word_confidence: '词级置信度',
  ocr: '文字识别',
  ocr_boxes: '文字坐标框',
  ocr_confidence: 'OCR 置信度',
  video_frame_metrics: '画面变化指标',
}

const providerKindLabels: Record<ProviderKind, string> = {
  local: '本机引擎',
  openai: 'OpenAI',
  anthropic: 'Anthropic',
  google: 'Google',
  openai_compatible: 'OpenAI 兼容服务',
  ollama: 'Ollama',
  custom: '自定义服务',
}

const authLabels: Record<ProviderAuthScheme, string> = {
  none: '无需认证',
  bearer: 'Bearer Token',
  x_api_key: 'x-api-key',
  x_goog_api_key: 'x-goog-api-key',
  custom_header: '自定义请求头',
}

const preferenceCopy = {
  responsive: {
    title: '保持电脑流畅',
    detail: '为浏览器、播放器和其他前台应用保留更多资源。',
    icon: Zap,
  },
  balanced: {
    title: '平衡处理',
    detail: '在处理速度、温度和前台响应之间取得平衡。',
    icon: Gauge,
  },
  throughput: {
    title: '尽快完成',
    detail: '允许任务使用更多空闲资源，适合离开电脑时批处理。',
    icon: Radar,
  },
} as const

const fontSizeOptions: Array<{
  value: FontSizePreset
  label: string
  detail: string
}> = [
  { value: 'compact', label: '紧凑', detail: '保留更多同屏信息，正文仍保持可读下限。' },
  { value: 'comfortable', label: '舒适', detail: '默认档位，适合长时间阅读和日常操作。' },
  { value: 'large', label: '大字', detail: '放大正文、标签与控件文字，适合高分屏。' },
]

const defaultReserve: ResourceReserve = {
  cpuReserveRatio: 0.25,
  memoryReserveRatio: 0.25,
  gpuReserveRatio: 0.2,
  vramReserveRatio: 0.2,
  diskReserveRatio: 0.1,
  cpuReserveCores: 1,
  memoryReserveBytes: 3 * 1024 ** 3,
  vramReserveBytes: 1024 ** 3,
  diskReserveBytes: 10 * 1024 ** 3,
  cpuSafetyFactor: 0.9,
  memorySafetyFactor: 0.9,
  gpuSafetyFactor: 0.9,
  vramSafetyFactor: 0.85,
  diskSafetyFactor: 0.9,
}

const protocolKind = (protocol: ProviderProtocol): ProviderKind => {
  if (protocol === 'local') return 'local'
  if (protocol.startsWith('openai_')) return 'openai'
  if (protocol === 'anthropic_messages') return 'anthropic'
  if (protocol.startsWith('gemini_')) return 'google'
  if (protocol === 'ollama_native_chat') return 'ollama'
  return 'custom'
}

const formatBytes = (bytes?: number): string =>
  bytes === undefined ? '—' : `${(bytes / 1024 ** 3).toFixed(1)} GB`

const hardwareTierCopy: Record<HardwareTier, { label: string; reason: string }> = {
  cpu_igpu: {
    label: 'CPU / 核显',
    reason: '采用轻量 ASR 与移动版 OCR，并以串行推理保留前台响应。',
  },
  gpu_8gb: {
    label: '8 GB 显存',
    reason: '优先大模型语音识别与轻量 OCR，在有限显存内控制峰值占用。',
  },
  gpu_12gb: {
    label: '12 GB 显存',
    reason: '可串行运行 large-v3 ASR 与高容量 OCR，兼顾细节和显存安全。',
  },
  gpu_24gb_plus: {
    label: '24 GB+ 显存',
    reason: '使用本地目录中精度最高的 ASR 与 OCR 组合。',
  },
}

const componentStateCopy: Record<
  ComponentInventoryItemDefinition['state'],
  string
> = {
  ready: '已就绪',
  missing: '待下载',
  incomplete: '可续传',
  degraded: '需修复',
}

const clampNumber = (value: string, min: number, max: number): number =>
  Math.min(max, Math.max(min, Number(value) || min))

type ProviderDraft = {
  id: string
  name: string
  kind: ProviderKind
  protocol: ProviderProtocol
  authScheme: ProviderAuthScheme
  baseUrl: string
  locality: 'local' | 'cloud'
  enabled: boolean
  timeoutSeconds: number
  protocolOptionsText: string
  credential: string
}

const emptyProviderDraft = (): ProviderDraft => ({
  id: '',
  name: '',
  kind: 'openai_compatible',
  protocol: 'openai_chat_completions',
  authScheme: 'bearer',
  baseUrl: '',
  locality: 'cloud',
  enabled: true,
  timeoutSeconds: 180,
  protocolOptionsText: '{}',
  credential: '',
})

type ModelDraft = {
  modelId: string
  displayName: string
  contextWindow: string
  locality: 'local' | 'cloud'
  capabilities: ModelCapability[]
}

const numericControls: Array<{
  key: keyof PerformanceOverrides
  label: string
  hint: string
  min: number
  max: number
  step?: number
  group: '调度' | '视觉扫描' | 'OCR' | 'ASR' | '验证'
}> = [
  { key: 'cpuWorkers', label: 'CPU worker', hint: '并行 CPU 工作线程', min: 1, max: 256, group: '调度' },
  { key: 'remoteModelConcurrency', label: '远程模型并发', hint: '同时进行的 API 请求', min: 1, max: 16, group: '调度' },
  { key: 'concurrentGpuStages', label: 'GPU 引擎槽位', hint: '0 强制 CPU；1 允许串行 GPU 推理', min: 0, max: 1, group: '调度' },
  { key: 'visualDecodeThreads', label: '视频解码线程', hint: '视觉解码线程数量', min: 1, max: 64, group: '视觉扫描' },
  { key: 'analysisWidth', label: '场景检测宽度', hint: '仅用于画面变化检测（px）', min: 320, max: 1920, step: 32, group: '视觉扫描' },
  { key: 'cheapScanFps', label: '粗扫 FPS', hint: '低成本变化检测频率', min: 0.25, max: 30, step: 0.25, group: '视觉扫描' },
  { key: 'expensiveScanFps', label: '精扫 FPS', hint: '候选区间精细扫描频率', min: 0.25, max: 60, step: 0.25, group: '视觉扫描' },
  { key: 'maxFixedSamples', label: '固定采样上限', hint: '单段最多允许的固定帧数', min: 1, max: 20000, group: '视觉扫描' },
  { key: 'ocrInferenceMaxWidth', label: 'OCR 推理宽度', hint: '关键帧文字识别的独立缩放上限（px）', min: 320, max: 4096, step: 32, group: 'OCR' },
  { key: 'ocrCpuThreads', label: 'OCR CPU 线程', hint: 'OCR 在 CPU 上的线程数', min: 1, max: 64, group: 'OCR' },
  { key: 'asrCpuThreads', label: 'ASR CPU 线程', hint: 'ASR 在 CPU 上的线程数', min: 1, max: 64, group: 'ASR' },
  { key: 'asrBeamSize', label: 'ASR beam', hint: '语音解码搜索宽度', min: 1, max: 10, group: 'ASR' },
  { key: 'verificationPasses', label: '验证轮次', hint: '最终事实校验次数', min: 0, max: 4, group: '验证' },
  { key: 'screenshotBudgetPerSection', label: '每节截图上限', hint: '每个笔记章节最多截图数', min: 0, max: 16, group: '验证' },
]

type DeviceSelection = NonNullable<PerformanceOverrides['asrDevice']>

const availableDeviceSelection = (
  value: string | undefined,
  cudaAvailable: boolean,
): DeviceSelection => {
  if (value === 'cpu') return 'cpu'
  if (value === 'cuda') return cudaAvailable ? 'cuda' : 'cpu'
  return 'auto'
}

const normalizeUnavailableCudaOverrides = (
  settings: PerformanceSettings,
  asrCudaAvailable: boolean,
  ocrCudaAvailable: boolean,
): PerformanceSettings => {
  const asrDevice = settings.overrides.asrDevice
  const ocrDevice = settings.overrides.ocrDevice
  const normalizedAsrDevice =
    asrDevice === 'cuda' && !asrCudaAvailable ? 'cpu' : asrDevice
  const normalizedOcrDevice =
    ocrDevice === 'cuda' && !ocrCudaAvailable ? 'cpu' : ocrDevice

  if (normalizedAsrDevice === asrDevice && normalizedOcrDevice === ocrDevice) {
    return settings
  }
  return {
    ...settings,
    overrides: {
      ...settings.overrides,
      asrDevice: normalizedAsrDevice,
      ocrDevice: normalizedOcrDevice,
    },
  }
}

export function ModelsPage() {
  const fontSizePreset = useUiPreferences(state => state.fontSizePreset)
  const setFontSizePreset = useUiPreferences(state => state.setFontSizePreset)
  const providers = useStudioStore(state => state.providers)
  const models = useStudioStore(state => state.models)
  const roles = useStudioStore(state => state.roles)
  const catalog = useStudioStore(state => state.configurationCatalog)
  const performance = useStudioStore(state => state.performance)
  const systemReport = useStudioStore(state => state.systemReport)
  const componentReport = useStudioStore(state => state.componentReport)
  const componentPreparationStatus = useStudioStore(
    state => state.componentPreparationStatus,
  )
  const componentPreparationResults = useStudioStore(
    state => state.componentPreparationResults,
  )
  const componentPreparationActivated = useStudioStore(
    state => state.componentPreparationActivated,
  )
  const componentPreparationActivatedRoles = useStudioStore(
    state => state.componentPreparationActivatedRoles,
  )
  const componentPreparationBlockedRoles = useStudioStore(
    state => state.componentPreparationBlockedRoles,
  )
  const componentPreparationWarnings = useStudioStore(
    state => state.componentPreparationWarnings,
  )
  const componentPreparationError = useStudioStore(
    state => state.componentPreparationError,
  )
  const selectedComponentIds = useStudioStore(state => state.selectedComponentIds)
  const discoveredModels = useStudioStore(state => state.discoveredModels)
  const discoveryProviderId = useStudioStore(state => state.discoveryProviderId)
  const discoveryStatus = useStudioStore(state => state.providerDiscoveryStatus)
  const selectedProviderId = useStudioStore(state => state.selectedProviderId)
  const backend = useStudioStore(state => state.backend)
  const savePerformance = useStudioStore(state => state.savePerformance)
  const refreshComponents = useStudioStore(state => state.refreshComponents)
  const setComponentSelected = useStudioStore(state => state.setComponentSelected)
  const prepareLocalComponents = useStudioStore(state => state.prepareLocalComponents)
  const saveProvider = useStudioStore(state => state.saveProvider)
  const testProvider = useStudioStore(state => state.testProvider)
  const discoverProviderModels = useStudioStore(state => state.discoverProviderModels)
  const createModel = useStudioStore(state => state.createModel)
  const saveProviderSecret = useStudioStore(state => state.saveProviderSecret)
  const deleteProviderSecret = useStudioStore(state => state.deleteProviderSecret)
  const bindRole = useStudioStore(state => state.bindRole)
  const [performanceDraft, setPerformanceDraft] = useState<PerformanceSettings>(performance)
  const [modelQuery, setModelQuery] = useState('')
  const [secret, setSecret] = useState('')
  const [providerEditorOpen, setProviderEditorOpen] = useState(false)
  const [providerEditorKind, setProviderEditorKind] = useState<'new' | 'edit'>('edit')
  const [providerDraft, setProviderDraft] = useState<ProviderDraft>(emptyProviderDraft)
  const [providerFormError, setProviderFormError] = useState('')
  const providerEditorId = useId()
  const providerEditorTitleId = `${providerEditorId}-title`
  const providerEditorRef = useRef<HTMLDivElement>(null)
  const providerEditorTriggerRef = useRef<HTMLButtonElement | null>(null)
  const providerFocusReturnFrameRef = useRef<number | null>(null)
  const [modelDraft, setModelDraft] = useState<ModelDraft>({
    modelId: '',
    displayName: '',
    contextWindow: '',
    locality: 'cloud',
    capabilities: [],
  })

  const provider = providers.find(item => item.id === selectedProviderId) ?? providers[0]
  const protocol = catalog.protocols.find(item => item.protocol === providerDraft.protocol)
  const selectedProtocol = provider
    ? catalog.protocols.find(item => item.protocol === provider.protocol)
    : undefined
  const recommendation = systemReport?.recommendation
  const recommendedPlan = systemReport?.plans.balanced
  const localModelComponents = componentReport?.inventory.items.filter(
    item => item.kind === 'local_model',
  ) ?? []
  const componentTier = componentReport
    ? hardwareTierCopy[componentReport.hardwareTier]
    : undefined
  const componentPreparing = componentPreparationStatus === 'preparing'
  const preparationRoleLabel = (roleId: string): string =>
    roles.find(role => role.id === roleId)?.label ?? roleId
  const asrCudaAvailable = systemReport?.acceleration.asr.cudaAvailable === true
  const ocrCudaAvailable = systemReport?.acceleration.ocr.cudaAvailable === true

  useEffect(
    () =>
      setPerformanceDraft(
        normalizeUnavailableCudaOverrides(
          performance,
          asrCudaAvailable,
          ocrCudaAvailable,
        ),
      ),
    [asrCudaAvailable, ocrCudaAvailable, performance],
  )

  useEffect(() => {
    if (!provider) return
    setProviderDraft({
      id: provider.id,
      name: provider.name,
      kind: provider.kind,
      protocol: provider.protocol,
      authScheme: provider.authScheme,
      baseUrl: provider.protocol === 'local' ? '' : provider.endpoint,
      locality: provider.locality,
      enabled: provider.enabled,
      timeoutSeconds: provider.timeoutSeconds,
      protocolOptionsText: JSON.stringify(provider.protocolOptions, null, 2),
      credential: '',
    })
    setModelDraft(value => ({ ...value, locality: provider.locality }))
    setProviderFormError('')
  }, [provider])

  useEffect(() => {
    if (!providerEditorOpen) return
    const focusFrame = window.requestAnimationFrame(() => {
      providerEditorRef.current
        ?.querySelector<HTMLElement>('input:not(:disabled), select:not(:disabled), textarea:not(:disabled)')
        ?.focus()
    })
    return () => window.cancelAnimationFrame(focusFrame)
  }, [providerEditorOpen])

  const activeReserve =
    performanceDraft.reserve ?? recommendation?.reserve ?? defaultReserve
  const headroomPercent = Math.round(activeReserve.cpuReserveRatio * 20) * 5

  const providerModels = useMemo(
    () =>
      models.filter(
        model =>
          model.providerId === provider?.id &&
          `${model.label} ${model.modelId}`
            .toLowerCase()
            .includes(modelQuery.toLowerCase()),
      ),
    [modelQuery, models, provider?.id],
  )

  const effectiveNumber = (key: keyof PerformanceOverrides, fallback: number): number => {
    const override = performanceDraft.overrides[key]
    if (typeof override === 'number') return override
    const planValue = recommendedPlan?.[key as keyof typeof recommendedPlan]
    return typeof planValue === 'number' ? planValue : fallback
  }

  const updateOverride = (
    key: keyof PerformanceOverrides,
    value: PerformanceOverrides[keyof PerformanceOverrides],
  ) => {
    setPerformanceDraft(current => ({
      ...current,
      overrides: { ...current.overrides, [key]: value },
    }))
  }

  const applyRecommendedPlan = () => {
    if (!recommendedPlan) return
    setPerformanceDraft(current => ({
      ...current,
      experienceMode: 'professional',
      overrides: {
        concurrentGpuStages: recommendedPlan.concurrentGpuStages,
        cpuWorkers: recommendedPlan.cpuWorkers,
        remoteModelConcurrency: recommendedPlan.remoteModelConcurrency,
        visualDecodeThreads: recommendedPlan.visualDecodeThreads,
        maxFixedSamples: recommendedPlan.maxFixedSamples,
        analysisWidth: recommendedPlan.analysisWidth,
        ocrInferenceMaxWidth: recommendedPlan.ocrInferenceMaxWidth,
        cheapScanFps: recommendedPlan.cheapScanFps,
        expensiveScanFps: recommendedPlan.expensiveScanFps,
        ocrDevice: availableDeviceSelection(
          recommendedPlan.ocrDevice,
          ocrCudaAvailable,
        ),
        ocrCpuThreads: recommendedPlan.ocrCpuThreads,
        asrDevice: availableDeviceSelection(
          recommendedPlan.asrDevice,
          asrCudaAvailable,
        ),
        asrComputeType: [
          'default',
          'int8',
          'int8_float16',
          'float16',
          'float32',
        ].includes(recommendedPlan.asrComputeType)
          ? (recommendedPlan.asrComputeType as NonNullable<
              PerformanceOverrides['asrComputeType']
            >)
          : 'default',
        asrCpuThreads: recommendedPlan.asrCpuThreads,
        asrBeamSize: recommendedPlan.asrBeamSize,
        verificationPasses: recommendedPlan.verificationPasses,
        screenshotBudgetPerSection: recommendedPlan.screenshotBudgetPerSection,
      },
    }))
  }

  const rememberProviderEditorTrigger = (trigger: HTMLButtonElement) => {
    if (providerFocusReturnFrameRef.current !== null) {
      window.cancelAnimationFrame(providerFocusReturnFrameRef.current)
      providerFocusReturnFrameRef.current = null
    }
    providerEditorTriggerRef.current = trigger
  }

  const closeProviderEditor = useCallback(() => {
    setProviderEditorOpen(false)
    const trigger = providerEditorTriggerRef.current
    if (providerFocusReturnFrameRef.current !== null) {
      window.cancelAnimationFrame(providerFocusReturnFrameRef.current)
    }
    providerFocusReturnFrameRef.current = window.requestAnimationFrame(() => {
      trigger?.focus()
      providerFocusReturnFrameRef.current = null
    })
  }, [])

  useEffect(() => {
    if (!providerEditorOpen) return
    const dismissOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      closeProviderEditor()
    }
    document.addEventListener('keydown', dismissOnEscape)
    return () => document.removeEventListener('keydown', dismissOnEscape)
  }, [closeProviderEditor, providerEditorOpen])

  useEffect(
    () => () => {
      if (providerFocusReturnFrameRef.current !== null) {
        window.cancelAnimationFrame(providerFocusReturnFrameRef.current)
      }
    },
    [],
  )

  const openNewProvider = (trigger: HTMLButtonElement) => {
    rememberProviderEditorTrigger(trigger)
    setProviderEditorKind('new')
    const first = catalog.protocols.find(item => item.protocol === 'openai_chat_completions')
    setProviderDraft({
      ...emptyProviderDraft(),
      authScheme: first?.defaultAuthScheme ?? 'bearer',
      baseUrl: first?.defaultBaseUrl ?? '',
    })
    setProviderEditorOpen(true)
    setProviderFormError('')
  }

  const openExistingProvider = (trigger: HTMLButtonElement) => {
    rememberProviderEditorTrigger(trigger)
    setProviderEditorKind('edit')
    setProviderEditorOpen(true)
    setProviderFormError('')
  }

  const changeProtocol = (nextProtocol: ProviderProtocol) => {
    const definition = catalog.protocols.find(item => item.protocol === nextProtocol)
    const nextKind = protocolKind(nextProtocol)
    setProviderDraft(current => ({
      ...current,
      protocol: nextProtocol,
      kind: nextKind,
      authScheme: definition?.defaultAuthScheme ?? 'none',
      baseUrl: definition?.defaultBaseUrl ?? '',
      locality: nextProtocol === 'local' || nextProtocol === 'ollama_native_chat' ? 'local' : 'cloud',
    }))
  }

  const submitProvider = () => {
    try {
      const protocolOptions = JSON.parse(providerDraft.protocolOptionsText || '{}') as unknown
      if (!protocolOptions || Array.isArray(protocolOptions) || typeof protocolOptions !== 'object') {
        throw new Error('协议选项必须是 JSON 对象。')
      }
      setProviderFormError('')
      saveProvider({
        id: providerDraft.id,
        name: providerDraft.name,
        kind: providerDraft.kind,
        protocol: providerDraft.protocol,
        authScheme: providerDraft.authScheme,
        baseUrl: providerDraft.baseUrl,
        locality: providerDraft.locality,
        enabled: providerDraft.enabled,
        timeoutSeconds: providerDraft.timeoutSeconds,
        protocolOptions: protocolOptions as Record<string, unknown>,
        credential: providerDraft.credential,
      })
      closeProviderEditor()
    } catch (error) {
      setProviderFormError(error instanceof Error ? error.message : '协议选项格式不正确。')
    }
  }

  const selectDiscoveredModel = (modelId: string) => {
    const discovered = discoveredModels.find(item => item.modelId === modelId)
    if (!discovered) return
    setModelDraft(current => ({
      ...current,
      modelId: discovered.modelId,
      displayName: discovered.displayName,
      contextWindow: discovered.contextWindow ? String(discovered.contextWindow) : '',
      capabilities: [],
    }))
  }

  const toggleCapability = (capability: ModelCapability) => {
    setModelDraft(current => ({
      ...current,
      capabilities: current.capabilities.includes(capability)
        ? current.capabilities.filter(item => item !== capability)
        : [...current.capabilities, capability],
    }))
  }

  return (
    <div className="models-page models-workspace">
      <header className="models-hero">
        <div>
          <span className="section-kicker">Video2Notes 偏好设置</span>
          <h1>设置中心</h1>
          <p>统一管理界面可读性、电脑性能余量、本地依赖和各处理角色的模型路由。</p>
        </div>
        <div className="experience-switch" aria-label="性能配置模式">
          <button
            type="button"
            aria-pressed={performanceDraft.experienceMode === 'guided'}
            onClick={() =>
              setPerformanceDraft(current => ({
                ...current,
                experienceMode: 'guided',
                overrides: {},
              }))
            }
          >
            <WandSparkles size={15} aria-hidden="true" />
            自动配置
          </button>
          <button
            type="button"
            aria-pressed={performanceDraft.experienceMode === 'professional'}
            onClick={() =>
              setPerformanceDraft(current => ({
                ...current,
                experienceMode: 'professional',
              }))
            }
          >
            <Settings2 size={15} aria-hidden="true" />
            自定义性能
          </button>
        </div>
      </header>

      <section className="appearance-settings-panel models-surface" aria-labelledby="appearance-settings-title">
        <div className="models-section-heading">
          <div className="section-heading-icon">
            <ScanText size={19} aria-hidden="true" />
          </div>
          <div>
            <span className="section-kicker">显示与可读性</span>
            <h2 id="appearance-settings-title">全局字体大小</h2>
            <p>字号档位会统一作用于所有页面，同时保留标题、正文、说明和数据标签的层级。</p>
          </div>
        </div>
        <div className="font-size-settings-layout">
          <div className="font-size-options" role="radiogroup" aria-label="全局字体大小">
            {fontSizeOptions.map(option => (
              <button
                key={option.value}
                type="button"
                role="radio"
                aria-checked={fontSizePreset === option.value}
                onClick={() => setFontSizePreset(option.value)}
              >
                <span className="font-size-option-mark" aria-hidden="true">Aa</span>
                <span>
                  <strong>{option.label}</strong>
                  <small>{option.detail}</small>
                </span>
                {fontSizePreset === option.value && <Check size={16} aria-hidden="true" />}
              </button>
            ))}
          </div>
          <div className="typography-preview" aria-live="polite">
            <span>当前预览 · {fontSizeOptions.find(option => option.value === fontSizePreset)?.label}</span>
            <strong>画面证据与语音时间线已经对齐</strong>
            <p>正文用于阅读笔记内容，辅助文字补充置信度、时间码和模型来源，不再使用难以辨认的微小字号。</p>
          </div>
        </div>
      </section>

      <RuntimePackagesPanel experienceMode={performanceDraft.experienceMode} />

      <section className="component-setup-panel models-surface" aria-labelledby="component-setup-title">
        <div className="models-section-heading">
          <div className="section-heading-icon">
            <HardDrive size={19} aria-hidden="true" />
          </div>
          <div>
            <span className="section-kicker">本地运行环境</span>
            <h2 id="component-setup-title">本地识别模型权重</h2>
            <p>运行时就绪后，再按当前硬件下载 ASR / OCR 模型权重；权重与执行环境独立管理。</p>
          </div>
          <div className="component-heading-actions">
            {backend.mode === 'demo' && <span className="component-demo-badge">演示状态样例</span>}
            {componentReport && (
              <span
                className={`component-overall-state ${componentReport.inventory.ready ? 'is-ready' : ''}`}
              >
                {componentReport.inventory.ready ? <Check size={13} aria-hidden="true" /> : <CircleDot size={13} aria-hidden="true" />}
                {componentReport.inventory.ready ? '全部就绪' : '需要准备'}
              </span>
            )}
            <button
              className="component-refresh-button"
              type="button"
              aria-label="重新扫描本地组件"
              title="重新扫描本地组件"
              onClick={refreshComponents}
              disabled={backend.mode !== 'real' || componentPreparing}
            >
              <RefreshCw className={componentPreparing ? 'spin' : ''} size={15} aria-hidden="true" />
            </button>
          </div>
        </div>

        {componentReport ? (
          <div className="component-setup-body">
            <div
              className="component-tier-card"
              title={componentReport.recommendation.reason}
            >
              <span className="component-tier-mark"><Cpu size={17} aria-hidden="true" /></span>
              <div>
                <span>已识别硬件档位</span>
                <strong>{componentTier?.label ?? componentReport.hardwareTier}</strong>
                <p>{componentTier?.reason ?? componentReport.recommendation.reason}</p>
              </div>
              <dl>
                <div>
                  <dt>ASR</dt>
                  <dd title={systemReport?.acceleration.asr.reason}>
                    {recommendedPlan?.asrDevice ?? componentReport.recommendation.asrDevice}
                    {' · '}
                    {recommendedPlan?.asrComputeType ?? componentReport.recommendation.asrComputeType}
                    {systemReport
                      ? systemReport.acceleration.asr.cudaAvailable
                        ? ' · CUDA 运行时就绪'
                        : ' · CPU 回退'
                      : ' · 加速能力检测中'}
                  </dd>
                </div>
                <div>
                  <dt>OCR</dt>
                  <dd title={systemReport?.acceleration.ocr.reason}>
                    {recommendedPlan?.ocrDevice ?? componentReport.recommendation.ocrDevice}
                    {systemReport
                      ? systemReport.acceleration.ocr.cudaAvailable
                        ? ' · CUDA 运行时就绪'
                        : ' · CPU 运行时'
                      : ' · 加速能力检测中'}
                  </dd>
                </div>
              </dl>
            </div>

            <div className="component-model-only">
              <section className="managed-models" aria-labelledby="managed-models-title">
                <header>
                  <div>
                    <span className="section-kicker">推荐权重</span>
                    <h3 id="managed-models-title">ASR / OCR 本地模型</h3>
                  </div>
                  <span>{localModelComponents.filter(item => item.ready).length} / {localModelComponents.length} 就绪</span>
                </header>

                <div className="component-download-note">
                  <Download size={16} aria-hidden="true" />
                  <p><strong>模型权重不会随 EXE 预装。</strong>首次使用时按需下载到本机应用数据目录，已完成的文件会复用，未完成的文件可续传。</p>
                </div>

                <fieldset className="managed-model-list">
                  <legend className="sr-only">
                    {performanceDraft.experienceMode === 'professional' ? '选择要准备的本地模型组件' : '推荐的本地模型组件'}
                  </legend>
                  {localModelComponents.map(item => {
                    const isAsr = item.id === componentReport.recommendation.asrComponentId
                    const selected = selectedComponentIds.includes(item.id)
                    return (
                      <label
                        className={`managed-model-card state-${item.state} ${selected ? 'is-selected' : ''}`}
                        key={item.id}
                      >
                        {performanceDraft.experienceMode === 'professional' && (
                          <input
                            type="checkbox"
                            aria-label={`选择组件 ${item.displayName}`}
                            checked={selected}
                            disabled={componentPreparing}
                            onChange={event => setComponentSelected(item.id, event.target.checked)}
                          />
                        )}
                        <span className="managed-model-icon">
                          {isAsr ? <Bot size={17} aria-hidden="true" /> : <ScanText size={17} aria-hidden="true" />}
                        </span>
                        <span className="managed-model-copy">
                          <span>{isAsr ? '语音识别 · ASR' : '画面文字 · OCR'}</span>
                          <strong>{item.displayName}</strong>
                          <small>{item.version ? `版本 ${item.version}` : '受管本地权重'} · {isAsr ? componentReport.recommendation.asrDevice : componentReport.recommendation.ocrDevice}</small>
                        </span>
                        <span className={`component-state-badge state-${item.state}`}>
                          {item.ready && <Check size={12} aria-hidden="true" />}
                          {componentStateCopy[item.state]}
                        </span>
                      </label>
                    )
                  })}
                </fieldset>

                <div className="component-prepare-actions">
                  <div className="component-action-copy">
                    {backend.mode === 'real' ? (
                      performanceDraft.experienceMode === 'professional'
                        ? <span>只下载所选权重；全部推荐项就绪后自动写入模型路由。</span>
                        : <span>自动选择当前硬件档位的推荐组合并在完成后激活。</span>
                    ) : (
                      <span>连接真实本机后端后可下载；演示模式不会创建模型文件。</span>
                    )}
                  </div>
                  <button
                    className="button button-primary component-prepare-button"
                    type="button"
                    aria-busy={componentPreparing}
                    disabled={
                      backend.mode !== 'real' ||
                      componentPreparing ||
                      (performanceDraft.experienceMode === 'professional' && selectedComponentIds.length === 0)
                    }
                    onClick={() =>
                      prepareLocalComponents(
                        performanceDraft.experienceMode === 'professional'
                          ? selectedComponentIds
                          : undefined,
                      )
                    }
                  >
                    {componentPreparing ? <LoaderCircle className="spin" size={15} aria-hidden="true" /> : <Download size={15} aria-hidden="true" />}
                    {componentPreparing
                      ? '正在下载并校验…'
                      : performanceDraft.experienceMode === 'professional'
                        ? '准备所选组件'
                        : componentReport.inventory.ready
                          ? '校验并激活推荐模型'
                          : '一键准备推荐模型'}
                  </button>
                </div>

                <div className="component-preparation-feedback" aria-live="polite">
                  {componentPreparationStatus === 'success' && (
                    <div className="component-feedback is-success motion-inline-feedback">
                      <Check size={15} aria-hidden="true" />
                      <div>
                        <strong>
                          {componentPreparationBlockedRoles.length > 0
                            ? '组件已准备，部分角色仍不可用'
                            : componentPreparationActivatedRoles.length > 0 || componentPreparationActivated
                              ? '本地模型角色已激活'
                              : '所选组件已准备'}
                        </strong>
                        <span>
                          {componentPreparationResults.map(result =>
                            `${result.componentId}：${result.status === 'reused' ? '已复用' : result.resumed ? '已续传完成' : '已准备'}`,
                          ).join(' · ')}
                        </span>
                        {componentPreparationActivatedRoles.length > 0 && (
                          <span>
                            已激活：{componentPreparationActivatedRoles.map(preparationRoleLabel).join('、')}
                          </span>
                        )}
                        {componentPreparationBlockedRoles.length > 0 && (
                          <span>
                            未激活：{componentPreparationBlockedRoles.map(preparationRoleLabel).join('、')}
                          </span>
                        )}
                        {componentPreparationWarnings.map(warning => (
                          <span key={warning}>提示：{warning}</span>
                        ))}
                      </div>
                    </div>
                  )}
                  {componentPreparationStatus === 'error' && componentPreparationError && (
                    <div className="component-feedback is-error motion-inline-feedback" role="alert">
                      <TriangleAlert size={15} aria-hidden="true" />
                      <div><strong>组件准备未完成</strong><span>{componentPreparationError}</span></div>
                    </div>
                  )}
                </div>
              </section>
            </div>
          </div>
        ) : (
          <div className="component-empty-state">
            <LoaderCircle className={backend.mode === 'connecting' ? 'spin' : ''} size={18} aria-hidden="true" />
            <div>
              <strong>{backend.mode === 'connecting' ? '正在扫描本机组件' : '尚未取得组件清单'}</strong>
              <span>{backend.mode === 'offline' ? '请重启桌面应用以重新连接本机后端。' : '连接后会显示工具、运行时和推荐模型状态。'}</span>
            </div>
          </div>
        )}
      </section>

      <section className="performance-panel models-surface">
        <div className="models-section-heading">
          <div className="section-heading-icon">
            <Gauge size={19} aria-hidden="true" />
          </div>
          <div>
            <span className="section-kicker">性能策略</span>
            <h2>这台电脑应该如何工作</h2>
            <p>笔记质量模式与资源占用相互独立；这里不会降低你选择的识别精度。</p>
          </div>
          <div className="hardware-summary" aria-label="硬件建议摘要">
            <span><Cpu size={14} aria-hidden="true" />{recommendation?.budget.cpuWorkers ?? '—'} CPU worker</span>
            <span><Database size={14} aria-hidden="true" />{formatBytes(recommendation?.budget.memoryBudgetBytes)} 可用内存</span>
            <span><Zap size={14} aria-hidden="true" />{recommendedPlan?.concurrentGpuStages ?? 0} 可用 GPU 阶段</span>
          </div>
        </div>

        {performanceDraft.experienceMode === 'guided' ? (
          <div className="guided-performance motion-swap-surface" key="guided-performance">
            <div className="preference-grid" role="radiogroup" aria-label="性能偏好">
              {(Object.keys(preferenceCopy) as Array<keyof typeof preferenceCopy>).map(value => {
                const item = preferenceCopy[value]
                const Icon = item.icon
                const selected = performanceDraft.preference === value
                return (
                  <button
                    className={selected ? 'is-selected' : ''}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    key={value}
                    onClick={() =>
                      setPerformanceDraft(current => ({ ...current, preference: value }))
                    }
                  >
                    <span className="preference-icon"><Icon size={18} aria-hidden="true" /></span>
                    <strong>{item.title}</strong>
                    <small>{item.detail}</small>
                    <span className="preference-check"><Check size={13} aria-hidden="true" /></span>
                  </button>
                )
              })}
            </div>

            <div className="headroom-card">
              <div>
                <strong>给系统保留余量</strong>
                <span>预留 {headroomPercent}% 的 CPU、内存与显存空间</span>
              </div>
              <label>
                <span className="sr-only">系统资源保留百分比</span>
                <input
                  type="range"
                  min="10"
                  max="50"
                  step="5"
                  value={headroomPercent}
                  onChange={event => {
                    const ratio = Number(event.target.value) / 100
                    setPerformanceDraft(current => ({
                      ...current,
                      reserve: {
                        ...(current.reserve ?? recommendation?.reserve ?? defaultReserve),
                        cpuReserveRatio: ratio,
                        memoryReserveRatio: ratio,
                        gpuReserveRatio: ratio,
                        vramReserveRatio: ratio,
                      },
                    }))
                  }}
                />
              </label>
              <output>{headroomPercent}%</output>
            </div>

            <div className="recommendation-card">
              <span className="recommendation-mark"><Sparkles size={18} aria-hidden="true" /></span>
              <div>
                <strong>算法建议 · {systemReport?.recommendedTier ?? '正在识别硬件'}</strong>
                <p>
                  {recommendation?.notes[0] ??
                    '系统会根据实时 CPU、内存、GPU 与显存余量，为每种质量模式生成安全计划。'}
                </p>
              </div>
              <dl>
                <div><dt>远程并发</dt><dd>{recommendation?.budget.remoteModelConcurrency ?? '—'}</dd></div>
                <div><dt>显存预算</dt><dd>{formatBytes(recommendation?.budget.vramBudgetBytes)}</dd></div>
                <div><dt>CPU 预算</dt><dd>{recommendation?.budget.cpuBudgetEquivalent.toFixed(1) ?? '—'}</dd></div>
              </dl>
            </div>
          </div>
        ) : (
          <div className="professional-performance motion-swap-surface" key="professional-performance">
            <div className="professional-toolbar">
              <div>
                <strong>专业参数覆盖</strong>
                <span>空白参数继续跟随硬件建议；保存时后端会校验安全边界。</span>
              </div>
              <button
                className="button button-secondary"
                type="button"
                onClick={applyRecommendedPlan}
                disabled={!recommendedPlan}
              >
                <RefreshCw size={14} aria-hidden="true" />
                填入当前建议
              </button>
            </div>

            <div className="professional-selects">
              <label>OCR 设备
                <select
                  value={availableDeviceSelection(
                    performanceDraft.overrides.ocrDevice ?? recommendedPlan?.ocrDevice,
                    ocrCudaAvailable,
                  )}
                  onChange={event => updateOverride('ocrDevice', event.target.value as 'auto' | 'cpu' | 'cuda')}
                >
                  <option value="auto">自动</option>
                  <option value="cpu">CPU</option>
                  {ocrCudaAvailable && <option value="cuda">CUDA</option>}
                </select>
              </label>
              <label>ASR 设备
                <select
                  value={availableDeviceSelection(
                    performanceDraft.overrides.asrDevice ?? recommendedPlan?.asrDevice,
                    asrCudaAvailable,
                  )}
                  onChange={event => updateOverride('asrDevice', event.target.value as 'auto' | 'cpu' | 'cuda')}
                >
                  <option value="auto">自动</option>
                  <option value="cpu">CPU</option>
                  {asrCudaAvailable && <option value="cuda">CUDA</option>}
                </select>
              </label>
              <label>ASR 计算精度
                <select
                  value={performanceDraft.overrides.asrComputeType ?? recommendedPlan?.asrComputeType ?? 'default'}
                  onChange={event => updateOverride('asrComputeType', event.target.value as NonNullable<PerformanceOverrides['asrComputeType']>)}
                >
                  <option value="default">自动</option><option value="int8">INT8</option><option value="int8_float16">INT8 / FP16</option><option value="float16">FP16</option><option value="float32">FP32</option>
                </select>
              </label>
            </div>

            <div className="parameter-groups">
              {(['调度', '视觉扫描', 'OCR', 'ASR', '验证'] as const).map(group => (
                <section key={group}>
                  <header><strong>{group}</strong><span>{numericControls.filter(item => item.group === group).length} 项</span></header>
                  <div>
                    {numericControls.filter(item => item.group === group).map(control => (
                      <label key={control.key}>
                        <span><strong>{control.label}</strong><small>{control.hint}</small></span>
                        <input
                          aria-label={control.label}
                          type="number"
                          min={control.min}
                          max={control.max}
                          step={control.step ?? 1}
                          value={effectiveNumber(control.key, control.min)}
                          onChange={event =>
                            updateOverride(
                              control.key,
                              clampNumber(event.target.value, control.min, control.max),
                            )
                          }
                        />
                      </label>
                    ))}
                  </div>
                </section>
              ))}
            </div>
          </div>
        )}

        <footer className="performance-actions">
          <span><ShieldCheck size={15} aria-hidden="true" />设置只保存在本机，并由后端再次校验。</span>
          <button
            className="button button-primary"
            type="button"
            onClick={() =>
              savePerformance(
                normalizeUnavailableCudaOverrides(
                  performanceDraft,
                  asrCudaAvailable,
                  ocrCudaAvailable,
                ),
              )
            }
          >
            <Save size={15} aria-hidden="true" />
            保存性能设置
          </button>
        </footer>
      </section>

      <section className="provider-workbench models-surface">
        <div className="models-section-heading">
          <div className="section-heading-icon"><ServerCog size={19} aria-hidden="true" /></div>
          <div>
            <span className="section-kicker">模型供应商</span>
            <h2>协议、认证与模型目录</h2>
            <p>协议决定请求格式；模型能力必须由你明确确认，发现结果不会自动推断。</p>
          </div>
          <button
            className="button button-secondary"
            type="button"
            aria-expanded={providerEditorOpen && providerEditorKind === 'new'}
            aria-controls={providerEditorId}
            onClick={event => openNewProvider(event.currentTarget)}
          >
            <Plus size={15} aria-hidden="true" />新增供应商
          </button>
        </div>

        {provider ? (
          <div className="provider-overview">
            <div className="provider-identity">
              <span className="provider-logo">
                {provider.locality === 'local' ? <Database size={21} aria-hidden="true" /> : <Cloud size={21} aria-hidden="true" />}
              </span>
              <div><strong>{provider.name}</strong><small>{provider.endpoint}</small></div>
              <span className="protocol-badge">{selectedProtocol?.displayName ?? provider.protocol}</span>
            </div>
            <div className="provider-actions">
              <span className={`provider-connection connection-${provider.status}`}>
                {provider.status === 'testing' ? <LoaderCircle className="spin" size={14} aria-hidden="true" /> : <CircleDot size={14} aria-hidden="true" />}
                {provider.status === 'testing' ? '测试中' : provider.status === 'connected' ? '连接正常' : '未测试'}
              </span>
              <button className="button button-secondary" type="button" onClick={() => testProvider(provider.id)} disabled={provider.status === 'testing'}>
                <Link2 size={14} aria-hidden="true" />测试连接
              </button>
              <button
                className="button button-secondary"
                type="button"
                aria-expanded={providerEditorOpen && providerEditorKind === 'edit'}
                aria-controls={providerEditorId}
                onClick={event => openExistingProvider(event.currentTarget)}
              >
                <Settings2 size={14} aria-hidden="true" />编辑
              </button>
            </div>

            {provider.authScheme !== 'none' && (
              <div className="credential-strip motion-surface-enter">
                <LockKeyhole size={16} aria-hidden="true" />
                <div>
                  <strong>{provider.credentialState === 'stored-locally' ? '凭据已保存在 Windows 凭据库' : '还没有保存凭据'}</strong>
                  <small>应用只保存 credential reference，不读取或回显原值。</small>
                </div>
                <label><span className="sr-only">供应商凭据</span><input type="password" value={secret} onChange={event => setSecret(event.target.value)} placeholder="输入新值以保存或替换" autoComplete="new-password" /></label>
                <button className="button button-primary" type="button" onClick={() => { saveProviderSecret(provider.id, secret); setSecret('') }} disabled={!secret.trim() || backend.mode !== 'real'}><KeyRound size={14} aria-hidden="true" />保存凭据</button>
                {provider.credentialState === 'stored-locally' && <button className="button button-quiet" type="button" onClick={() => deleteProviderSecret(provider.id)}>删除</button>}
              </div>
            )}
          </div>
        ) : (
          <div className="provider-empty"><Cloud size={24} aria-hidden="true" /><strong>还没有供应商</strong><span>先添加一个协议端点，再发现或手工登记模型。</span></div>
        )}

        <MotionPresence
          show={providerEditorOpen}
          className="motion-presence-provider-editor"
          exitMs={140}
        >
          {providerEditorOpen && (
            <div
              className="provider-editor"
              id={providerEditorId}
              ref={providerEditorRef}
              role="region"
              aria-labelledby={providerEditorTitleId}
            >
            <header>
              <div><strong id={providerEditorTitleId}>{providerDraft.id ? '编辑供应商' : '新增供应商'}</strong><span>连接信息和密钥仅写入本机。</span></div>
              <button type="button" aria-label="关闭供应商编辑器" onClick={closeProviderEditor}><X size={17} aria-hidden="true" /></button>
            </header>
            <div className="provider-editor-grid">
              <label>供应商 ID<input value={providerDraft.id} disabled={Boolean(provider && providerDraft.id === provider.id)} onChange={event => setProviderDraft(current => ({ ...current, id: event.target.value }))} placeholder="my-provider" /></label>
              <label>显示名称<input value={providerDraft.name} onChange={event => setProviderDraft(current => ({ ...current, name: event.target.value }))} placeholder="我的模型服务" /></label>
              <label>服务类型<select value={providerDraft.kind} onChange={event => setProviderDraft(current => ({ ...current, kind: event.target.value as ProviderKind }))}>{(Object.keys(providerKindLabels) as ProviderKind[]).map(kind => <option value={kind} key={kind}>{providerKindLabels[kind]}</option>)}</select></label>
              <label>请求协议<select aria-label="请求协议" value={providerDraft.protocol} onChange={event => changeProtocol(event.target.value as ProviderProtocol)}>{catalog.protocols.map(item => <option value={item.protocol} key={item.protocol}>{item.displayName}</option>)}</select></label>
              <label>认证方式<select value={providerDraft.authScheme} onChange={event => setProviderDraft(current => ({ ...current, authScheme: event.target.value as ProviderAuthScheme }))}>{(Object.keys(authLabels) as ProviderAuthScheme[]).map(auth => <option value={auth} key={auth}>{authLabels[auth]}</option>)}</select></label>
              <label className="provider-editor-wide">Base URL<input value={providerDraft.baseUrl} disabled={providerDraft.protocol === 'local'} onChange={event => setProviderDraft(current => ({ ...current, baseUrl: event.target.value }))} placeholder={protocol?.defaultBaseUrl ?? 'https://api.example.com/v1'} /></label>
              <label>模型位置<select value={providerDraft.locality} onChange={event => setProviderDraft(current => ({ ...current, locality: event.target.value as 'local' | 'cloud' }))}><option value="local">本机</option><option value="cloud">云端</option></select></label>
              <label>请求超时（秒）<input type="number" min="1" value={providerDraft.timeoutSeconds} onChange={event => setProviderDraft(current => ({ ...current, timeoutSeconds: clampNumber(event.target.value, 1, 3600) }))} /></label>
              {providerDraft.authScheme !== 'none' && <label className="provider-editor-wide motion-field-enter">保存凭据（可选）<input type="password" value={providerDraft.credential} onChange={event => setProviderDraft(current => ({ ...current, credential: event.target.value }))} placeholder="留空则保留现有凭据" autoComplete="new-password" /></label>}
              <label className="provider-editor-wide">协议选项（JSON）<textarea value={providerDraft.protocolOptionsText} onChange={event => setProviderDraft(current => ({ ...current, protocolOptionsText: event.target.value }))} rows={4} spellCheck={false} /></label>
              <label className="provider-enable"><input type="checkbox" checked={providerDraft.enabled} onChange={event => setProviderDraft(current => ({ ...current, enabled: event.target.checked }))} />启用这个供应商</label>
            </div>
            {providerFormError && <p className="provider-form-error motion-inline-feedback"><TriangleAlert size={14} aria-hidden="true" />{providerFormError}</p>}
            <footer>
              <span>{protocol?.discoveryPath ? `支持从 ${protocol.discoveryPath} 发现模型` : '此协议没有标准模型发现接口'}</span>
              <button className="button button-primary" type="button" onClick={submitProvider} disabled={backend.mode !== 'real'}><Save size={14} aria-hidden="true" />保存供应商</button>
            </footer>
            </div>
          )}
        </MotionPresence>
      </section>

      {provider && (
        <section className="model-registry-layout">
          <div className="model-discovery models-surface">
            <div className="models-section-heading compact">
              <div>
                <span className="section-kicker">发现模型</span>
                <h2>从目录选择，或手工填写</h2>
                <p>目录只返回 ID 与上下文长度，不代表模型一定支持某项能力。</p>
              </div>
              <button className="button button-secondary" type="button" onClick={() => discoverProviderModels(provider.id)} disabled={backend.mode !== 'real' || discoveryStatus === 'loading' || !selectedProtocol?.discoveryPath}>
                {discoveryStatus === 'loading' ? <LoaderCircle className="spin" size={14} aria-hidden="true" /> : <Radar size={14} aria-hidden="true" />}
                发现模型
              </button>
            </div>

            {discoveryProviderId === provider.id && discoveredModels.length > 0 && (
              <div className="discovery-results motion-surface-enter" aria-label="发现的模型">
                {discoveredModels.slice(0, 12).map(model => (
                  <button type="button" key={model.modelId} onClick={() => selectDiscoveredModel(model.modelId)}>
                    <span><strong>{model.displayName}</strong><small>{model.modelId}</small></span>
                    <span>{model.contextWindow ? `${model.contextWindow.toLocaleString()} tokens` : '上下文未知'}</span>
                    <ChevronRight size={15} aria-hidden="true" />
                  </button>
                ))}
              </div>
            )}

            <div className="model-create-form">
              <label>模型 ID<input aria-label="模型 ID" value={modelDraft.modelId} onChange={event => setModelDraft(current => ({ ...current, modelId: event.target.value }))} placeholder="模型目录中的真实 ID" /></label>
              <label>显示名称<input value={modelDraft.displayName} onChange={event => setModelDraft(current => ({ ...current, displayName: event.target.value }))} placeholder="便于自己识别的名称" /></label>
              <label>上下文长度<input type="number" min="1" value={modelDraft.contextWindow} onChange={event => setModelDraft(current => ({ ...current, contextWindow: event.target.value }))} placeholder="可选" /></label>
              <label>运行位置<select value={modelDraft.locality} onChange={event => setModelDraft(current => ({ ...current, locality: event.target.value as 'local' | 'cloud' }))}><option value="local">本机</option><option value="cloud">云端</option></select></label>
              <fieldset>
                <legend>明确声明能力 <span>（必须人工确认）</span></legend>
                <div className="capability-picker">
                  {catalog.capabilities.map(capability => (
                    <label className={modelDraft.capabilities.includes(capability) ? 'is-selected' : ''} key={capability}>
                      <input type="checkbox" checked={modelDraft.capabilities.includes(capability)} onChange={() => toggleCapability(capability)} />
                      {capabilityLabels[capability]}
                    </label>
                  ))}
                </div>
              </fieldset>
              <div className="capability-warning"><TriangleAlert size={15} aria-hidden="true" />请根据供应商文档或实际测试确认能力；发现模型不会自动勾选任何项。</div>
              <button className="button button-primary" type="button" disabled={!modelDraft.modelId.trim() || modelDraft.capabilities.length === 0 || backend.mode !== 'real'} onClick={() => { createModel({ providerId: provider.id, modelId: modelDraft.modelId, displayName: modelDraft.displayName, contextWindow: modelDraft.contextWindow ? Number(modelDraft.contextWindow) : undefined, capabilities: modelDraft.capabilities, locality: modelDraft.locality }); setModelDraft(current => ({ ...current, modelId: '', displayName: '', contextWindow: '', capabilities: [] })) }}><Plus size={14} aria-hidden="true" />创建已确认模型</button>
            </div>
          </div>

          <div className="model-catalog models-surface">
            <div className="models-section-heading compact">
              <div><span className="section-kicker">已登记模型</span><h2>{provider.name}</h2></div>
              <label className="compact-search"><Search size={14} aria-hidden="true" /><span className="sr-only">搜索模型</span><input value={modelQuery} onChange={event => setModelQuery(event.target.value)} placeholder="搜索模型" /></label>
            </div>
            <div className="model-list">
              {providerModels.map(model => (
                <article key={model.id}>
                  <span className="model-icon">{model.locality === 'local' ? <Database size={16} aria-hidden="true" /> : <Cloud size={16} aria-hidden="true" />}</span>
                  <div><strong>{model.label}</strong><small>{model.modelId}{model.contextWindow ? ` · ${model.contextWindow.toLocaleString()} tokens` : ''}</small><span className="capability-list">{model.capabilities.map(capability => <span key={capability}>{capabilityLabels[capability]}</span>)}</span></div>
                  <span className={model.enabled ? 'model-ready' : 'model-disabled'}>{model.enabled ? <Check size={13} aria-hidden="true" /> : <X size={13} aria-hidden="true" />}{model.enabled ? '可用' : '已停用'}</span>
                </article>
              ))}
              {providerModels.length === 0 && <div className="model-empty">该供应商下还没有匹配的模型。</div>}
            </div>
          </div>
        </section>
      )}

      <section className="role-routing models-surface">
        <div className="models-section-heading">
          <div className="section-heading-icon"><Bot size={19} aria-hidden="true" /></div>
          <div><span className="section-kicker">处理角色</span><h2>为每个阶段选择兼容模型</h2><p>下拉框只列出能力完整匹配、模型启用且供应商启用的选项。</p></div>
          <div className="routing-valid"><ShieldCheck size={16} aria-hidden="true" />{roles.filter(role => role.modelId).length} / {roles.length} 已绑定</div>
        </div>
        <div className="role-groups">
          {(['语音', '视觉', '笔记'] as const).map(group => (
            <section className="role-group" key={group}>
              <header><span>{group}</span><small>{roles.filter(role => role.group === group).length} 个角色</small></header>
              {roles.filter(role => role.group === group).map(role => {
                const compatibleModels = models.filter(model => {
                  const modelProvider = providers.find(item => item.id === model.providerId)
                  return model.enabled && Boolean(modelProvider?.enabled) && role.requiredCapabilities.every(capability => model.capabilities.includes(capability))
                })
                const currentModel = compatibleModels.find(model => model.id === role.modelId)
                return (
                  <div className="role-row" key={role.id}>
                    <div className="role-copy"><strong>{role.label}</strong><small>{role.description}</small><span>{role.requiredCapabilities.map(capability => capabilityLabels[capability]).join(' · ')}</span></div>
                    <label className="role-select"><span className="sr-only">为{role.label}选择模型</span><select value={currentModel?.id ?? ''} onChange={event => bindRole(role.id, event.target.value)}><option value="">未绑定</option>{compatibleModels.map(model => <option value={model.id} key={model.id}>{model.label} · {model.locality === 'local' ? '本机' : '云端'}</option>)}</select></label>
                    <span className={`role-state ${currentModel ? 'is-ready' : ''}`}>{currentModel ? <Check size={13} aria-hidden="true" /> : <TriangleAlert size={13} aria-hidden="true" />}{currentModel ? '能力匹配' : '等待绑定'}</span>
                  </div>
                )
              })}
            </section>
          ))}
        </div>
        <div className="routing-footnote"><ShieldCheck size={15} aria-hidden="true" />角色要求完全来自后端 configuration catalog；前端不会用近似标签代替真实能力。</div>
      </section>
    </div>
  )
}

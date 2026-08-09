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
  Languages,
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
import { VisualAsset } from '../components/VisualAsset'
import { useStudioStore } from '../store'
import { useI18n } from '../i18n'
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

const capabilityLabelsEn: Record<ModelCapability, string> = {
  text: 'Text understanding',
  vision: 'Image understanding',
  structured_output: 'Structured output',
  long_context: 'Long context',
  embeddings: 'Embeddings',
  asr: 'Speech recognition',
  language_id: 'Language identification',
  segment_timestamps: 'Segment timestamps',
  word_timestamps: 'Word timestamps',
  word_confidence: 'Word confidence',
  ocr: 'Text recognition',
  ocr_boxes: 'OCR bounding boxes',
  ocr_confidence: 'OCR confidence',
  video_frame_metrics: 'Frame-change metrics',
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

const providerKindLabelsEn: Record<ProviderKind, string> = {
  local: 'Local engine',
  openai: 'OpenAI',
  anthropic: 'Anthropic',
  google: 'Google',
  openai_compatible: 'OpenAI-compatible service',
  ollama: 'Ollama',
  custom: 'Custom service',
}

const protocolLabelsEn: Partial<Record<ProviderProtocol, string>> = {
  local: 'Local in-process engine',
  ollama_native_chat: 'Ollama native chat',
  custom_http: 'Custom HTTP (experimental)',
}

const authLabels: Record<ProviderAuthScheme, string> = {
  none: '无需认证',
  bearer: 'Bearer Token',
  x_api_key: 'x-api-key',
  x_goog_api_key: 'x-goog-api-key',
  custom_header: '自定义请求头',
}

const authLabelsEn: Record<ProviderAuthScheme, string> = {
  none: 'No authentication',
  bearer: 'Bearer token',
  x_api_key: 'x-api-key',
  x_goog_api_key: 'x-goog-api-key',
  custom_header: 'Custom request header',
}

const preferenceCopy = {
  responsive: {
    title: '保持电脑流畅',
    titleEn: 'Keep the computer responsive',
    detail: '为浏览器、播放器和其他前台应用保留更多资源。',
    detailEn: 'Reserve more resources for browsers, players, and other foreground apps.',
    icon: Zap,
  },
  balanced: {
    title: '平衡处理',
    titleEn: 'Balanced processing',
    detail: '在处理速度、温度和前台响应之间取得平衡。',
    detailEn: 'Balance processing speed, temperature, and foreground responsiveness.',
    icon: Gauge,
  },
  throughput: {
    title: '尽快完成',
    titleEn: 'Finish as soon as possible',
    detail: '允许任务使用更多空闲资源，适合离开电脑时批处理。',
    detailEn: 'Let tasks use more idle resources when the computer is unattended.',
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

const hardwareTierCopyEn: typeof hardwareTierCopy = {
  cpu_igpu: {
    label: 'CPU / integrated GPU',
    reason: 'Uses lightweight ASR and mobile OCR with serial inference to preserve foreground responsiveness.',
  },
  gpu_8gb: {
    label: '8 GB VRAM',
    reason: 'Prioritizes larger speech recognition with lightweight OCR while controlling peak VRAM.',
  },
  gpu_12gb: {
    label: '12 GB VRAM',
    reason: 'Runs large-v3 ASR and higher-capacity OCR serially for detail with safe VRAM use.',
  },
  gpu_24gb_plus: {
    label: '24 GB+ VRAM',
    reason: 'Uses the highest-accuracy local ASR and OCR combination in the catalog.',
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

const componentStateCopyEn: typeof componentStateCopy = {
  ready: 'Ready',
  missing: 'Download needed',
  incomplete: 'Resumable',
  degraded: 'Repair needed',
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
  labelEn: string
  hint: string
  hintEn: string
  min: number
  max: number
  step?: number
  group: '调度' | '视觉扫描' | 'OCR' | 'ASR' | '验证'
}> = [
  { key: 'cpuWorkers', label: 'CPU worker', labelEn: 'CPU workers', hint: '并行 CPU 工作线程', hintEn: 'Parallel CPU worker threads', min: 1, max: 256, group: '调度' },
  { key: 'remoteModelConcurrency', label: '远程模型并发', labelEn: 'Remote model concurrency', hint: '同时进行的 API 请求', hintEn: 'Concurrent API requests', min: 1, max: 16, group: '调度' },
  { key: 'concurrentGpuStages', label: 'GPU 引擎槽位', labelEn: 'GPU engine slots', hint: '0 强制 CPU；1 允许串行 GPU 推理', hintEn: '0 forces CPU; 1 allows serial GPU inference', min: 0, max: 1, group: '调度' },
  { key: 'visualDecodeThreads', label: '视频解码线程', labelEn: 'Video decode threads', hint: '视觉解码线程数量', hintEn: 'Threads used for visual decoding', min: 1, max: 64, group: '视觉扫描' },
  { key: 'analysisWidth', label: '场景检测宽度', labelEn: 'Scene detection width', hint: '仅用于画面变化检测（px）', hintEn: 'Used only for frame-change detection (px)', min: 320, max: 1920, step: 32, group: '视觉扫描' },
  { key: 'cheapScanFps', label: '粗扫 FPS', labelEn: 'Coarse scan FPS', hint: '低成本变化检测频率', hintEn: 'Low-cost change detection frequency', min: 0.25, max: 30, step: 0.25, group: '视觉扫描' },
  { key: 'expensiveScanFps', label: '精扫 FPS', labelEn: 'Detailed scan FPS', hint: '候选区间精细扫描频率', hintEn: 'Detailed scan frequency inside candidate ranges', min: 0.25, max: 60, step: 0.25, group: '视觉扫描' },
  { key: 'maxFixedSamples', label: '固定采样上限', labelEn: 'Fixed-sample limit', hint: '单段最多允许的固定帧数', hintEn: 'Maximum fixed frames per range', min: 1, max: 20000, group: '视觉扫描' },
  { key: 'ocrInferenceMaxWidth', label: 'OCR 推理宽度', labelEn: 'OCR inference width', hint: '关键帧文字识别的独立缩放上限（px）', hintEn: 'Independent keyframe OCR resize limit (px)', min: 320, max: 4096, step: 32, group: 'OCR' },
  { key: 'ocrCpuThreads', label: 'OCR CPU 线程', labelEn: 'OCR CPU threads', hint: 'OCR 在 CPU 上的线程数', hintEn: 'CPU threads used by OCR', min: 1, max: 64, group: 'OCR' },
  { key: 'asrCpuThreads', label: 'ASR CPU 线程', labelEn: 'ASR CPU threads', hint: 'ASR 在 CPU 上的线程数', hintEn: 'CPU threads used by ASR', min: 1, max: 64, group: 'ASR' },
  { key: 'asrBeamSize', label: 'ASR beam', labelEn: 'ASR beam size', hint: '语音解码搜索宽度', hintEn: 'Speech decoding search width', min: 1, max: 10, group: 'ASR' },
  { key: 'verificationPasses', label: '验证轮次', labelEn: 'Verification passes', hint: '最终事实校验次数', hintEn: 'Final fact-check pass count', min: 0, max: 4, group: '验证' },
  { key: 'screenshotBudgetPerSection', label: '每节截图上限', labelEn: 'Screenshots per section', hint: '每个笔记章节最多截图数', hintEn: 'Maximum screenshots per note section', min: 0, max: 16, group: '验证' },
]

const performanceGroupLabelsEn = {
  调度: 'Scheduling',
  视觉扫描: 'Visual scan',
  OCR: 'OCR',
  ASR: 'ASR',
  验证: 'Verification',
} as const

const roleCopyEn: Record<string, { label: string; description: string }> = {
  'vision.change_detector': { label: 'Frame-change detection', description: 'Scan content changes cheaply and locate candidate ranges' },
  'vision.text_detector': { label: 'On-screen text detection', description: 'Locate text regions and preserve bounding boxes' },
  'vision.frame_explainer': { label: 'Keyframe explanation', description: 'Explain the selected high-value frames' },
  'ocr.primary': { label: 'Primary OCR', description: 'Read on-screen text and positions' },
  'ocr.escalation': { label: 'OCR visual review', description: 'Review only low-confidence or complex layouts' },
  'asr.primary': { label: 'Primary speech recognition', description: 'Produce timestamped transcript segments by default' },
  'asr.secondary': { label: 'Low-confidence review', description: 'Handle only noisy, conflicting, or uncertain segments' },
  'asr.adjudicator': { label: 'Speech result adjudication', description: 'Select the most credible text among recognition candidates' },
  'notes.fact_extractor': { label: 'Fact extraction', description: 'Turn aligned evidence into structured fact cards' },
  'notes.drafter': { label: 'Section drafting', description: 'Create structured long-form notes from fact cards' },
  'notes.verifier': { label: 'Fact verification', description: 'Check statement support and section coverage' },
  translation: { label: 'Translation & terminology', description: 'Handle multilingual content and normalize terminology' },
}

type DeviceSelection = NonNullable<PerformanceOverrides['asrDevice']>

const settingsNavigation = [
  {
    id: 'appearance-settings',
    labelKey: 'settings.appearance',
    detailKey: 'settings.appearance.detail',
    icon: ScanText,
  },
  {
    id: 'runtime-packages',
    labelKey: 'settings.runtime',
    detailKey: 'settings.runtime.detail',
    icon: ServerCog,
  },
  {
    id: 'component-setup',
    labelKey: 'settings.components',
    detailKey: 'settings.components.detail',
    icon: HardDrive,
  },
  {
    id: 'performance-settings',
    labelKey: 'settings.performance',
    detailKey: 'settings.performance.detail',
    icon: Gauge,
  },
  {
    id: 'provider-workbench',
    labelKey: 'settings.providers',
    detailKey: 'settings.providers.detail',
    icon: Cloud,
  },
  {
    id: 'model-registry',
    labelKey: 'settings.registry',
    detailKey: 'settings.registry.detail',
    icon: Database,
  },
  {
    id: 'role-routing',
    labelKey: 'settings.routing',
    detailKey: 'settings.routing.detail',
    icon: Bot,
  },
] as const

type SettingsSectionId = (typeof settingsNavigation)[number]['id']

const isSettingsSectionId = (value: string): value is SettingsSectionId =>
  settingsNavigation.some(item => item.id === value)

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
  const locale = useUiPreferences(state => state.locale)
  const setLocale = useUiPreferences(state => state.setLocale)
  const { t, text } = useI18n()
  const capabilityLabel = (capability: ModelCapability) =>
    (locale === 'zh-CN' ? capabilityLabels : capabilityLabelsEn)[capability]
  const providerKindLabel = (kind: ProviderKind) =>
    (locale === 'zh-CN' ? providerKindLabels : providerKindLabelsEn)[kind]
  const authLabel = (auth: ProviderAuthScheme) =>
    (locale === 'zh-CN' ? authLabels : authLabelsEn)[auth]
  const protocolLabel = (protocol: ProviderProtocol, displayName: string) =>
    locale === 'en-US' ? protocolLabelsEn[protocol] ?? displayName : displayName
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
  const [activeSettingsSection, setActiveSettingsSection] = useState<SettingsSectionId>(
    settingsNavigation[0].id,
  )
  const settingsContentRef = useRef<HTMLDivElement>(null)
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
    ? (locale === 'zh-CN' ? hardwareTierCopy : hardwareTierCopyEn)[componentReport.hardwareTier]
    : undefined
  const componentPreparing = componentPreparationStatus === 'preparing'
  const preparationRoleLabel = (roleId: string): string => {
    const role = roles.find(item => item.id === roleId)
    return locale === 'en-US'
      ? roleCopyEn[roleId]?.label ?? role?.id ?? roleId
      : role?.label ?? roleId
  }
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

  useEffect(() => {
    const root = settingsContentRef.current?.closest('.workspace-main')
    const sections = settingsNavigation
      .map(item => document.getElementById(item.id))
      .filter((section): section is HTMLElement => section !== null)
    if (!root || sections.length === 0) return

    let animationFrame: number | undefined
    const updateActiveSection = () => {
      if (animationFrame !== undefined) window.cancelAnimationFrame(animationFrame)
      animationFrame = window.requestAnimationFrame(() => {
        const rootRect = root.getBoundingClientRect()
        const activationLine = rootRect.top + Math.min(72, rootRect.height * 0.12)
        let active = sections[0]
        for (const section of sections) {
          if (section.getBoundingClientRect().top > activationLine) break
          active = section
        }
        if (active && isSettingsSectionId(active.id)) setActiveSettingsSection(active.id)
      })
    }

    root.addEventListener('scroll', updateActiveSection, { passive: true })
    window.addEventListener('resize', updateActiveSection)
    updateActiveSection()
    return () => {
      root.removeEventListener('scroll', updateActiveSection)
      window.removeEventListener('resize', updateActiveSection)
      if (animationFrame !== undefined) window.cancelAnimationFrame(animationFrame)
    }
  }, [provider])

  const scrollToSettingsSection = (sectionId: SettingsSectionId) => {
    const target = document.getElementById(sectionId)
    if (!target) return
    setActiveSettingsSection(sectionId)
    target.focus({ preventScroll: true })
    target.scrollIntoView?.({
      behavior: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
        ? 'auto'
        : 'smooth',
      block: 'start',
    })
  }

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
        throw new Error(text('协议选项必须是 JSON 对象。', 'Protocol options must be a JSON object.'))
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
      setProviderFormError(
        error instanceof Error
          ? error.message
          : text('协议选项格式不正确。', 'Protocol options are invalid.'),
      )
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
        <VisualAsset
          className="models-hero-visual"
          asset="settingsWorkbench"
          width={720}
          height={720}
          loading="eager"
        />
        <div>
          <span className="section-kicker">{t('settings.kicker')}</span>
          <h1>{t('settings.title')}</h1>
          <p>{t('settings.description')}</p>
        </div>
        <div className="experience-switch" aria-label={text('性能配置模式', 'Performance configuration mode')}>
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
            {t('settings.auto')}
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
            {t('settings.custom')}
          </button>
        </div>
      </header>

      <div className="models-settings-layout">
        <aside className="models-settings-sidebar" aria-label={t('settings.directory')}>
          <div className="models-settings-nav-heading">
            <span className="section-kicker">{t('settings.directory')}</span>
            <strong>{t('settings.sections')}</strong>
          </div>
          <nav className="models-settings-nav" aria-label={text('设置分区', 'Settings sections')}>
            {settingsNavigation
              .filter(item => item.id !== 'model-registry' || Boolean(provider))
              .map(item => {
                const Icon = item.icon
                const active = activeSettingsSection === item.id
                return (
                  <a
                    href={`#${item.id}`}
                    className={active ? 'is-active' : undefined}
                    aria-current={active ? 'location' : undefined}
                    key={item.id}
                    onClick={event => {
                      event.preventDefault()
                      scrollToSettingsSection(item.id)
                    }}
                  >
                    <span className="models-settings-nav-icon"><Icon size={15} aria-hidden="true" /></span>
                    <span className="models-settings-nav-copy">
                      <strong>{t(item.labelKey)}</strong>
                      <small>{t(item.detailKey)}</small>
                    </span>
                  </a>
                )
              })}
          </nav>
        </aside>

        <div className="models-settings-content" ref={settingsContentRef}>
      <section className="appearance-settings-panel models-surface" id="appearance-settings" aria-labelledby="appearance-settings-title" tabIndex={-1}>
        <div className="models-section-heading">
          <div className="section-heading-icon">
            <ScanText size={19} aria-hidden="true" />
          </div>
          <div>
            <span className="section-kicker">{t('settings.appearance')}</span>
            <h2 id="appearance-settings-title">{t('settings.font.title')}</h2>
            <p>{t('settings.font.description')}</p>
          </div>
        </div>
        <div className="language-setting-row">
          <span className="language-setting-icon"><Languages size={18} aria-hidden="true" /></span>
          <div>
            <strong>{t('nav.language')}</strong>
            <small>{text('所有面向用户的标题、按钮、状态和错误摘要都会同步切换。', 'All user-facing titles, controls, statuses, and error summaries switch together.')}</small>
          </div>
          <div className="language-setting-control" role="radiogroup" aria-label={t('nav.language')}>
            <button type="button" role="radio" aria-checked={locale === 'zh-CN'} onClick={() => setLocale('zh-CN')}>{t('language.zh')}</button>
            <button type="button" role="radio" aria-checked={locale === 'en-US'} onClick={() => setLocale('en-US')}>{t('language.en')}</button>
          </div>
        </div>
        <div className="font-size-settings-layout">
          <div className="font-size-options" role="radiogroup" aria-label={t('settings.font.title')}>
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
                  <strong>{t(`settings.font.${option.value}`)}</strong>
                  <small>{t(`settings.font.${option.value}.detail`)}</small>
                </span>
                {fontSizePreset === option.value && <Check size={16} aria-hidden="true" />}
              </button>
            ))}
          </div>
          <div className="typography-preview" aria-live="polite">
            <span>{text('当前预览', 'Current preview')} · {t(`settings.font.${fontSizePreset}`)}</span>
            <strong>{text('画面证据与语音时间线已经对齐', 'Visual evidence and speech are aligned on one timeline')}</strong>
            <p>{text('正文用于阅读笔记内容，辅助文字补充置信度、时间码和模型来源，不再使用难以辨认的微小字号。', 'Body text carries the note while supporting text adds confidence, timecodes, and model provenance without unreadably small type.')}</p>
          </div>
        </div>
      </section>

      <RuntimePackagesPanel experienceMode={performanceDraft.experienceMode} />

      <section className="component-setup-panel models-surface" id="component-setup" aria-labelledby="component-setup-title" tabIndex={-1}>
        <div className="models-section-heading">
          <div className="section-heading-icon">
            <HardDrive size={19} aria-hidden="true" />
          </div>
          <div>
            <span className="section-kicker">{text('本地运行环境', 'Local runtime environment')}</span>
            <h2 id="component-setup-title">{text('本地识别模型权重', 'Local recognition model weights')}</h2>
            <p>{text('运行时就绪后，再按当前硬件下载 ASR / OCR 模型权重；权重与执行环境独立管理。', 'After the runtime is ready, download ASR and OCR weights selected for this hardware. Weights and execution runtimes are managed separately.')}</p>
          </div>
          <div className="component-heading-actions">
            {backend.mode === 'demo' && <span className="component-demo-badge">{text('演示状态样例', 'Demo status sample')}</span>}
            {componentReport && (
              <span
                className={`component-overall-state ${componentReport.inventory.ready ? 'is-ready' : ''}`}
              >
                {componentReport.inventory.ready ? <Check size={13} aria-hidden="true" /> : <CircleDot size={13} aria-hidden="true" />}
                {componentReport.inventory.ready ? text('全部就绪', 'All ready') : text('需要准备', 'Setup needed')}
              </span>
            )}
            <button
              className="component-refresh-button"
              type="button"
              aria-label={text('重新扫描本地组件', 'Rescan local components')}
              title={text('重新扫描本地组件', 'Rescan local components')}
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
                <span>{text('已识别硬件档位', 'Detected hardware tier')}</span>
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
                        ? text(' · CUDA 运行时就绪', ' · CUDA runtime ready')
                        : text(' · CPU 回退', ' · CPU fallback')
                      : text(' · 加速能力检测中', ' · Detecting acceleration')}
                  </dd>
                </div>
                <div>
                  <dt>OCR</dt>
                  <dd title={systemReport?.acceleration.ocr.reason}>
                    {recommendedPlan?.ocrDevice ?? componentReport.recommendation.ocrDevice}
                    {systemReport
                      ? systemReport.acceleration.ocr.cudaAvailable
                        ? text(' · CUDA 运行时就绪', ' · CUDA runtime ready')
                        : text(' · CPU 运行时', ' · CPU runtime')
                      : text(' · 加速能力检测中', ' · Detecting acceleration')}
                  </dd>
                </div>
              </dl>
            </div>

            <div className="component-model-only">
              <section className="managed-models" aria-labelledby="managed-models-title">
                <header>
                  <div>
                    <span className="section-kicker">{text('推荐权重', 'Recommended weights')}</span>
                    <h3 id="managed-models-title">{text('ASR / OCR 本地模型', 'Local ASR / OCR models')}</h3>
                  </div>
                  <span>{localModelComponents.filter(item => item.ready).length} / {localModelComponents.length} {text('就绪', 'ready')}</span>
                </header>

                <div className="component-download-note">
                  <Download size={16} aria-hidden="true" />
                  <p><strong>{text('模型权重不会随 EXE 预装。', 'Model weights are not bundled with the EXE.')}</strong>{text('首次使用时按需下载到本机应用数据目录，已完成的文件会复用，未完成的文件可续传。', ' They download on demand into local app data. Completed files are reused and partial downloads can resume.')}</p>
                </div>

                <fieldset className="managed-model-list">
                  <legend className="sr-only">
                    {performanceDraft.experienceMode === 'professional' ? text('选择要准备的本地模型组件', 'Choose local model components to prepare') : text('推荐的本地模型组件', 'Recommended local model components')}
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
                            aria-label={`${text('选择组件', 'Select component')} ${item.displayName}`}
                            checked={selected}
                            disabled={componentPreparing}
                            onChange={event => setComponentSelected(item.id, event.target.checked)}
                          />
                        )}
                        <span className="managed-model-icon">
                          {isAsr ? <Bot size={17} aria-hidden="true" /> : <ScanText size={17} aria-hidden="true" />}
                        </span>
                        <span className="managed-model-copy">
                          <span>{isAsr ? text('语音识别 · ASR', 'Speech recognition · ASR') : text('画面文字 · OCR', 'On-screen text · OCR')}</span>
                          <strong>{item.displayName}</strong>
                          <small>{item.version ? `${text('版本', 'Version')} ${item.version}` : text('受管本地权重', 'Managed local weights')} · {isAsr ? componentReport.recommendation.asrDevice : componentReport.recommendation.ocrDevice}</small>
                        </span>
                        <span className={`component-state-badge state-${item.state}`}>
                          {item.ready && <Check size={12} aria-hidden="true" />}
                          {(locale === 'zh-CN' ? componentStateCopy : componentStateCopyEn)[item.state]}
                        </span>
                      </label>
                    )
                  })}
                </fieldset>

                <div className="component-prepare-actions">
                  <div className="component-action-copy">
                    {backend.mode === 'real' ? (
                      performanceDraft.experienceMode === 'professional'
                        ? <span>{text('只下载所选权重；全部推荐项就绪后自动写入模型路由。', 'Download only selected weights; recommended roles are routed automatically after setup.')}</span>
                        : <span>{text('自动选择当前硬件档位的推荐组合并在完成后激活。', 'Select the recommended combination for this hardware and activate it after setup.')}</span>
                    ) : (
                      <span>{text('连接真实本机后端后可下载；演示模式不会创建模型文件。', 'Connect the local backend to download. Demo mode does not create model files.')}</span>
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
                      ? text('正在下载并校验…', 'Downloading and verifying...')
                      : performanceDraft.experienceMode === 'professional'
                        ? text('准备所选组件', 'Prepare selected components')
                        : componentReport.inventory.ready
                          ? text('校验并激活推荐模型', 'Verify and activate recommended models')
                          : text('一键准备推荐模型', 'Prepare recommended models')}
                  </button>
                </div>

                <div className="component-preparation-feedback" aria-live="polite">
                  {componentPreparationStatus === 'success' && (
                    <div className="component-feedback is-success motion-inline-feedback">
                      <Check size={15} aria-hidden="true" />
                      <div>
                        <strong>
                          {componentPreparationBlockedRoles.length > 0
                            ? text('组件已准备，部分角色仍不可用', 'Components are ready, but some roles remain unavailable')
                            : componentPreparationActivatedRoles.length > 0 || componentPreparationActivated
                              ? text('本地模型角色已激活', 'Local model roles activated')
                              : text('所选组件已准备', 'Selected components are ready')}
                        </strong>
                        <span>
                          {componentPreparationResults.map(result =>
                            `${result.componentId}${locale === 'zh-CN' ? '：' : ': '}${result.status === 'reused' ? text('已复用', 'reused') : result.resumed ? text('已续传完成', 'resumed and completed') : text('已准备', 'ready')}`,
                          ).join(' · ')}
                        </span>
                        {componentPreparationActivatedRoles.length > 0 && (
                          <span>
                            {text('已激活：', 'Activated: ')}{componentPreparationActivatedRoles.map(preparationRoleLabel).join(locale === 'zh-CN' ? '、' : ', ')}
                          </span>
                        )}
                        {componentPreparationBlockedRoles.length > 0 && (
                          <span>
                            {text('未激活：', 'Not activated: ')}{componentPreparationBlockedRoles.map(preparationRoleLabel).join(locale === 'zh-CN' ? '、' : ', ')}
                          </span>
                        )}
                        {componentPreparationWarnings.map(warning => (
                          <span key={warning}>{text('提示：', 'Details: ')}{warning}</span>
                        ))}
                      </div>
                    </div>
                  )}
                  {componentPreparationStatus === 'error' && componentPreparationError && (
                    <div className="component-feedback is-error motion-inline-feedback" role="alert">
                      <TriangleAlert size={15} aria-hidden="true" />
                      <div><strong>{text('组件准备未完成', 'Component setup did not complete')}</strong><span>{text('底层详情：', 'Technical details: ')}{componentPreparationError}</span></div>
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
              <strong>{backend.mode === 'connecting' ? text('正在扫描本机组件', 'Scanning local components') : text('尚未取得组件清单', 'Component inventory is unavailable')}</strong>
              <span>{backend.mode === 'offline' ? text('请重启桌面应用以重新连接本机后端。', 'Restart the desktop app to reconnect the local backend.') : text('连接后会显示工具、运行时和推荐模型状态。', 'Tools, runtimes, and recommended models appear after the backend connects.')}</span>
            </div>
          </div>
        )}
      </section>

      <section className="performance-panel models-surface" id="performance-settings" aria-labelledby="performance-settings-title" tabIndex={-1}>
        <div className="models-section-heading">
          <div className="section-heading-icon">
            <Gauge size={19} aria-hidden="true" />
          </div>
          <div>
            <span className="section-kicker">{text('性能策略', 'Performance policy')}</span>
            <h2 id="performance-settings-title">{text('这台电脑应该如何工作', 'How this computer should work')}</h2>
            <p>{text('笔记质量模式与资源占用相互独立；这里不会降低你选择的识别精度。', 'Note quality and resource usage are independent; these settings do not reduce the recognition quality you select.')}</p>
          </div>
          <div className="hardware-summary" aria-label={text('硬件建议摘要', 'Hardware recommendation summary')}>
            <span><Cpu size={14} aria-hidden="true" />{recommendation?.budget.cpuWorkers ?? '—'} CPU worker</span>
            <span><Database size={14} aria-hidden="true" />{formatBytes(recommendation?.budget.memoryBudgetBytes)} {text('可用内存', 'available memory')}</span>
            <span><Zap size={14} aria-hidden="true" />{recommendedPlan?.concurrentGpuStages ?? 0} {text('可用 GPU 阶段', 'available GPU stages')}</span>
          </div>
        </div>

        {performanceDraft.experienceMode === 'guided' ? (
          <div className="guided-performance motion-swap-surface" key="guided-performance">
            <div className="preference-grid" role="radiogroup" aria-label={text('性能偏好', 'Performance preference')}>
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
                    <strong>{text(item.title, item.titleEn)}</strong>
                    <small>{text(item.detail, item.detailEn)}</small>
                    <span className="preference-check"><Check size={13} aria-hidden="true" /></span>
                  </button>
                )
              })}
            </div>

            <div className="headroom-card">
              <div>
                <strong>{text('给系统保留余量', 'Reserve system headroom')}</strong>
                <span>{text(`预留 ${headroomPercent}% 的 CPU、内存与显存空间`, `Reserve ${headroomPercent}% of CPU, memory, and VRAM`)}</span>
              </div>
              <label>
                <span className="sr-only">{text('系统资源保留百分比', 'System resource reserve percentage')}</span>
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
                <strong>{text('算法建议', 'Algorithm recommendation')} · {systemReport?.recommendedTier ?? text('正在识别硬件', 'Detecting hardware')}</strong>
                <p>
                  {locale === 'zh-CN' && recommendation?.notes[0]
                    ? recommendation.notes[0]
                    : text(
                        '系统会根据实时 CPU、内存、GPU 与显存余量，为每种质量模式生成安全计划。',
                        'The system creates a safe plan for each quality mode from current CPU, memory, GPU, and VRAM headroom.',
                      )}
                </p>
              </div>
              <dl>
                <div><dt>{text('远程并发', 'Remote concurrency')}</dt><dd>{recommendation?.budget.remoteModelConcurrency ?? '—'}</dd></div>
                <div><dt>{text('显存预算', 'VRAM budget')}</dt><dd>{formatBytes(recommendation?.budget.vramBudgetBytes)}</dd></div>
                <div><dt>{text('CPU 预算', 'CPU budget')}</dt><dd>{recommendation?.budget.cpuBudgetEquivalent.toFixed(1) ?? '—'}</dd></div>
              </dl>
            </div>
          </div>
        ) : (
          <div className="professional-performance motion-swap-surface" key="professional-performance">
            <div className="professional-toolbar">
              <div>
                <strong>{text('专业参数覆盖', 'Professional parameter overrides')}</strong>
                <span>{text('空白参数继续跟随硬件建议；保存时后端会校验安全边界。', 'Unset parameters continue to follow hardware recommendations; the backend validates safety limits when saving.')}</span>
              </div>
              <button
                className="button button-secondary"
                type="button"
                onClick={applyRecommendedPlan}
                disabled={!recommendedPlan}
              >
                <RefreshCw size={14} aria-hidden="true" />
                {text('填入当前建议', 'Apply current recommendation')}
              </button>
            </div>

            <div className="professional-selects">
              <label>{text('OCR 设备', 'OCR device')}
                <select
                  value={availableDeviceSelection(
                    performanceDraft.overrides.ocrDevice ?? recommendedPlan?.ocrDevice,
                    ocrCudaAvailable,
                  )}
                  onChange={event => updateOverride('ocrDevice', event.target.value as 'auto' | 'cpu' | 'cuda')}
                >
                  <option value="auto">{text('自动', 'Automatic')}</option>
                  <option value="cpu">CPU</option>
                  {ocrCudaAvailable && <option value="cuda">CUDA</option>}
                </select>
              </label>
              <label>{text('ASR 设备', 'ASR device')}
                <select
                  value={availableDeviceSelection(
                    performanceDraft.overrides.asrDevice ?? recommendedPlan?.asrDevice,
                    asrCudaAvailable,
                  )}
                  onChange={event => updateOverride('asrDevice', event.target.value as 'auto' | 'cpu' | 'cuda')}
                >
                  <option value="auto">{text('自动', 'Automatic')}</option>
                  <option value="cpu">CPU</option>
                  {asrCudaAvailable && <option value="cuda">CUDA</option>}
                </select>
              </label>
              <label>{text('ASR 计算精度', 'ASR compute precision')}
                <select
                  value={performanceDraft.overrides.asrComputeType ?? recommendedPlan?.asrComputeType ?? 'default'}
                  onChange={event => updateOverride('asrComputeType', event.target.value as NonNullable<PerformanceOverrides['asrComputeType']>)}
                >
                  <option value="default">{text('自动', 'Automatic')}</option><option value="int8">INT8</option><option value="int8_float16">INT8 / FP16</option><option value="float16">FP16</option><option value="float32">FP32</option>
                </select>
              </label>
            </div>

            <div className="parameter-groups">
              {(['调度', '视觉扫描', 'OCR', 'ASR', '验证'] as const).map(group => (
                <section key={group}>
                  <header><strong>{text(group, performanceGroupLabelsEn[group])}</strong><span>{text(`${numericControls.filter(item => item.group === group).length} 项`, `${numericControls.filter(item => item.group === group).length} items`)}</span></header>
                  <div>
                    {numericControls.filter(item => item.group === group).map(control => (
                      <label key={control.key}>
                        <span><strong>{text(control.label, control.labelEn)}</strong><small>{text(control.hint, control.hintEn)}</small></span>
                        <input
                          aria-label={text(control.label, control.labelEn)}
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
          <span><ShieldCheck size={15} aria-hidden="true" />{text('设置只保存在本机，并由后端再次校验。', 'Settings stay on this computer and are validated again by the backend.')}</span>
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
            {text('保存性能设置', 'Save performance settings')}
          </button>
        </footer>
      </section>

      <section className="provider-workbench models-surface" id="provider-workbench" aria-labelledby="provider-workbench-title" tabIndex={-1}>
        <div className="models-section-heading">
          <div className="section-heading-icon"><ServerCog size={19} aria-hidden="true" /></div>
          <div>
            <span className="section-kicker">{t('settings.providers')}</span>
            <h2 id="provider-workbench-title">{text('协议、认证与模型目录', 'Protocols, authentication, and model registry')}</h2>
            <p>{text('协议决定请求格式；模型能力必须由你明确确认，发现结果不会自动推断。', 'The protocol defines the request format. Model capabilities must be explicitly confirmed and are never inferred from discovery.')}</p>
          </div>
          <button
            className="button button-secondary"
            type="button"
            aria-expanded={providerEditorOpen && providerEditorKind === 'new'}
            aria-controls={providerEditorId}
            onClick={event => openNewProvider(event.currentTarget)}
          >
            <Plus size={15} aria-hidden="true" />{text('新增供应商', 'Add provider')}
          </button>
        </div>

        {provider ? (
          <div className="provider-overview">
            <div className="provider-identity">
              <span className="provider-logo">
                {provider.locality === 'local' ? <Database size={21} aria-hidden="true" /> : <Cloud size={21} aria-hidden="true" />}
              </span>
              <div><strong>{provider.name}</strong><small>{provider.endpoint}</small></div>
              <span className="protocol-badge">{selectedProtocol ? protocolLabel(selectedProtocol.protocol, selectedProtocol.displayName) : provider.protocol}</span>
            </div>
            <div className="provider-actions">
              <span className={`provider-connection connection-${provider.status}`}>
                {provider.status === 'testing' ? <LoaderCircle className="spin" size={14} aria-hidden="true" /> : <CircleDot size={14} aria-hidden="true" />}
                {provider.status === 'testing' ? text('测试中', 'Testing') : provider.status === 'connected' ? text('连接正常', 'Connected') : text('未测试', 'Not tested')}
              </span>
              <button className="button button-secondary" type="button" onClick={() => testProvider(provider.id)} disabled={provider.status === 'testing'}>
                <Link2 size={14} aria-hidden="true" />{text('测试连接', 'Test connection')}
              </button>
              <button
                className="button button-secondary"
                type="button"
                aria-expanded={providerEditorOpen && providerEditorKind === 'edit'}
                aria-controls={providerEditorId}
                onClick={event => openExistingProvider(event.currentTarget)}
              >
                <Settings2 size={14} aria-hidden="true" />{text('编辑', 'Edit')}
              </button>
            </div>

            {provider.authScheme !== 'none' && (
              <div className="credential-strip motion-surface-enter">
                <LockKeyhole size={16} aria-hidden="true" />
                <div>
                  <strong>{provider.credentialState === 'stored-locally' ? text('凭据已保存在 Windows 凭据库', 'Credential saved in Windows Credential Manager') : text('还没有保存凭据', 'No credential saved')}</strong>
                  <small>{text('应用只保存 credential reference，不读取或回显原值。', 'The app stores only a credential reference and never reads or reveals the original value.')}</small>
                </div>
                <label><span className="sr-only">{text('供应商凭据', 'Provider credential')}</span><input type="password" value={secret} onChange={event => setSecret(event.target.value)} placeholder={text('输入新值以保存或替换', 'Enter a new value to save or replace')} autoComplete="new-password" /></label>
                <button className="button button-primary" type="button" onClick={() => { saveProviderSecret(provider.id, secret); setSecret('') }} disabled={!secret.trim() || backend.mode !== 'real'}><KeyRound size={14} aria-hidden="true" />{text('保存凭据', 'Save credential')}</button>
                {provider.credentialState === 'stored-locally' && <button className="button button-quiet" type="button" onClick={() => deleteProviderSecret(provider.id)}>{text('删除', 'Delete')}</button>}
              </div>
            )}
          </div>
        ) : (
          <div className="provider-empty">
            <VisualAsset className="inline-empty-visual" asset="emptyProvider" width={192} height={124} />
            <div>
              <Cloud size={24} aria-hidden="true" />
              <strong>{text('还没有供应商', 'No providers yet')}</strong>
              <span>{text('先添加一个协议端点，再发现或手工登记模型。', 'Add a protocol endpoint, then discover or register models manually.')}</span>
            </div>
          </div>
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
              <div><strong id={providerEditorTitleId}>{providerDraft.id ? text('编辑供应商', 'Edit provider') : text('新增供应商', 'Add provider')}</strong><span>{text('连接信息和密钥仅写入本机。', 'Connection details and secrets are stored only on this computer.')}</span></div>
              <button type="button" aria-label={text('关闭供应商编辑器', 'Close provider editor')} onClick={closeProviderEditor}><X size={17} aria-hidden="true" /></button>
            </header>
            <div className="provider-editor-grid">
              <label>{text('供应商 ID', 'Provider ID')}<input value={providerDraft.id} disabled={Boolean(provider && providerDraft.id === provider.id)} onChange={event => setProviderDraft(current => ({ ...current, id: event.target.value }))} placeholder="my-provider" /></label>
              <label>{text('显示名称', 'Display name')}<input value={providerDraft.name} onChange={event => setProviderDraft(current => ({ ...current, name: event.target.value }))} placeholder={text('我的模型服务', 'My model service')} /></label>
              <label>{text('服务类型', 'Service type')}<select value={providerDraft.kind} onChange={event => setProviderDraft(current => ({ ...current, kind: event.target.value as ProviderKind }))}>{(Object.keys(providerKindLabels) as ProviderKind[]).map(kind => <option value={kind} key={kind}>{providerKindLabel(kind)}</option>)}</select></label>
              <label>{text('请求协议', 'Request protocol')}<select aria-label={text('请求协议', 'Request protocol')} value={providerDraft.protocol} onChange={event => changeProtocol(event.target.value as ProviderProtocol)}>{catalog.protocols.map(item => <option value={item.protocol} key={item.protocol}>{protocolLabel(item.protocol, item.displayName)}</option>)}</select></label>
              <label>{text('认证方式', 'Authentication')}<select value={providerDraft.authScheme} onChange={event => setProviderDraft(current => ({ ...current, authScheme: event.target.value as ProviderAuthScheme }))}>{(Object.keys(authLabels) as ProviderAuthScheme[]).map(auth => <option value={auth} key={auth}>{authLabel(auth)}</option>)}</select></label>
              <label className="provider-editor-wide">Base URL<input value={providerDraft.baseUrl} disabled={providerDraft.protocol === 'local'} onChange={event => setProviderDraft(current => ({ ...current, baseUrl: event.target.value }))} placeholder={protocol?.defaultBaseUrl ?? 'https://api.example.com/v1'} /></label>
              <label>{text('模型位置', 'Model location')}<select value={providerDraft.locality} onChange={event => setProviderDraft(current => ({ ...current, locality: event.target.value as 'local' | 'cloud' }))}><option value="local">{text('本机', 'Local')}</option><option value="cloud">{text('云端', 'Cloud')}</option></select></label>
              <label>{text('请求超时（秒）', 'Request timeout (seconds)')}<input type="number" min="1" value={providerDraft.timeoutSeconds} onChange={event => setProviderDraft(current => ({ ...current, timeoutSeconds: clampNumber(event.target.value, 1, 3600) }))} /></label>
              {providerDraft.authScheme !== 'none' && <label className="provider-editor-wide motion-field-enter">{text('保存凭据（可选）', 'Save credential (optional)')}<input type="password" value={providerDraft.credential} onChange={event => setProviderDraft(current => ({ ...current, credential: event.target.value }))} placeholder={text('留空则保留现有凭据', 'Leave blank to keep the existing credential')} autoComplete="new-password" /></label>}
              <label className="provider-editor-wide">{text('协议选项（JSON）', 'Protocol options (JSON)')}<textarea value={providerDraft.protocolOptionsText} onChange={event => setProviderDraft(current => ({ ...current, protocolOptionsText: event.target.value }))} rows={4} spellCheck={false} /></label>
              <label className="provider-enable"><input type="checkbox" checked={providerDraft.enabled} onChange={event => setProviderDraft(current => ({ ...current, enabled: event.target.checked }))} />{text('启用这个供应商', 'Enable this provider')}</label>
            </div>
            {providerFormError && <p className="provider-form-error motion-inline-feedback"><TriangleAlert size={14} aria-hidden="true" />{providerFormError}</p>}
            <footer>
              <span>{protocol?.discoveryPath ? text(`支持从 ${protocol.discoveryPath} 发现模型`, `Model discovery is available at ${protocol.discoveryPath}`) : text('此协议没有标准模型发现接口', 'This protocol has no standard model discovery endpoint')}</span>
              <button className="button button-primary" type="button" onClick={submitProvider} disabled={backend.mode !== 'real'}><Save size={14} aria-hidden="true" />{text('保存供应商', 'Save provider')}</button>
            </footer>
            </div>
          )}
        </MotionPresence>
      </section>

      {provider && (
        <section className="model-registry-layout" id="model-registry" aria-label={t('settings.registry')} tabIndex={-1}>
          <div className="model-discovery models-surface">
            <div className="models-section-heading compact">
              <div>
                <span className="section-kicker">{text('发现模型', 'Discover models')}</span>
                <h2>{text('从目录选择，或手工填写', 'Choose from the registry or enter one manually')}</h2>
                <p>{text('目录只返回 ID 与上下文长度，不代表模型一定支持某项能力。', 'Discovery only returns IDs and context lengths; it does not prove that a model supports a capability.')}</p>
              </div>
              <button className="button button-secondary" type="button" onClick={() => discoverProviderModels(provider.id)} disabled={backend.mode !== 'real' || discoveryStatus === 'loading' || !selectedProtocol?.discoveryPath}>
                {discoveryStatus === 'loading' ? <LoaderCircle className="spin" size={14} aria-hidden="true" /> : <Radar size={14} aria-hidden="true" />}
                {text('发现模型', 'Discover models')}
              </button>
            </div>

            {discoveryProviderId === provider.id && discoveredModels.length > 0 && (
              <div className="discovery-results motion-surface-enter" aria-label={text('发现的模型', 'Discovered models')}>
                {discoveredModels.slice(0, 12).map(model => (
                  <button type="button" key={model.modelId} onClick={() => selectDiscoveredModel(model.modelId)}>
                    <span><strong>{model.displayName}</strong><small>{model.modelId}</small></span>
                    <span>{model.contextWindow ? `${model.contextWindow.toLocaleString()} tokens` : text('上下文未知', 'Context unknown')}</span>
                    <ChevronRight size={15} aria-hidden="true" />
                  </button>
                ))}
              </div>
            )}

            <div className="model-create-form">
              <label>{text('模型 ID', 'Model ID')}<input aria-label={text('模型 ID', 'Model ID')} value={modelDraft.modelId} onChange={event => setModelDraft(current => ({ ...current, modelId: event.target.value }))} placeholder={text('模型目录中的真实 ID', 'Exact ID from the model registry')} /></label>
              <label>{text('显示名称', 'Display name')}<input value={modelDraft.displayName} onChange={event => setModelDraft(current => ({ ...current, displayName: event.target.value }))} placeholder={text('便于自己识别的名称', 'A name you can recognize')} /></label>
              <label>{text('上下文长度', 'Context length')}<input type="number" min="1" value={modelDraft.contextWindow} onChange={event => setModelDraft(current => ({ ...current, contextWindow: event.target.value }))} placeholder={text('可选', 'Optional')} /></label>
              <label>{text('运行位置', 'Execution location')}<select value={modelDraft.locality} onChange={event => setModelDraft(current => ({ ...current, locality: event.target.value as 'local' | 'cloud' }))}><option value="local">{text('本机', 'Local')}</option><option value="cloud">{text('云端', 'Cloud')}</option></select></label>
              <fieldset>
                <legend>{text('明确声明能力', 'Declare capabilities explicitly')} <span>{text('（必须人工确认）', '(manual confirmation required)')}</span></legend>
                <div className="capability-picker">
                  {catalog.capabilities.map(capability => (
                    <label className={modelDraft.capabilities.includes(capability) ? 'is-selected' : ''} key={capability}>
                      <input type="checkbox" checked={modelDraft.capabilities.includes(capability)} onChange={() => toggleCapability(capability)} />
                      {capabilityLabel(capability)}
                    </label>
                  ))}
                </div>
              </fieldset>
              <div className="capability-warning"><TriangleAlert size={15} aria-hidden="true" />{text('请根据供应商文档或实际测试确认能力；发现模型不会自动勾选任何项。', 'Confirm capabilities from provider documentation or real tests. Discovery never selects capabilities automatically.')}</div>
              <button className="button button-primary" type="button" disabled={!modelDraft.modelId.trim() || modelDraft.capabilities.length === 0 || backend.mode !== 'real'} onClick={() => { createModel({ providerId: provider.id, modelId: modelDraft.modelId, displayName: modelDraft.displayName, contextWindow: modelDraft.contextWindow ? Number(modelDraft.contextWindow) : undefined, capabilities: modelDraft.capabilities, locality: modelDraft.locality }); setModelDraft(current => ({ ...current, modelId: '', displayName: '', contextWindow: '', capabilities: [] })) }}><Plus size={14} aria-hidden="true" />{text('创建已确认模型', 'Create confirmed model')}</button>
            </div>
          </div>

          <div className="model-catalog models-surface">
            <div className="models-section-heading compact">
              <div><span className="section-kicker">{text('已登记模型', 'Registered models')}</span><h2>{provider.name}</h2></div>
              <label className="compact-search"><Search size={14} aria-hidden="true" /><span className="sr-only">{text('搜索模型', 'Search models')}</span><input value={modelQuery} onChange={event => setModelQuery(event.target.value)} placeholder={text('搜索模型', 'Search models')} /></label>
            </div>
            <div className="model-list">
              {providerModels.map(model => (
                <article key={model.id}>
                  <span className="model-icon">{model.locality === 'local' ? <Database size={16} aria-hidden="true" /> : <Cloud size={16} aria-hidden="true" />}</span>
                  <div><strong>{model.label}</strong><small>{model.modelId}{model.contextWindow ? ` · ${model.contextWindow.toLocaleString()} tokens` : ''}</small><span className="capability-list">{model.capabilities.map(capability => <span key={capability}>{capabilityLabel(capability)}</span>)}</span></div>
                  <span className={model.enabled ? 'model-ready' : 'model-disabled'}>{model.enabled ? <Check size={13} aria-hidden="true" /> : <X size={13} aria-hidden="true" />}{model.enabled ? text('可用', 'Available') : text('已停用', 'Disabled')}</span>
                </article>
              ))}
              {providerModels.length === 0 && <div className="model-empty">{text('该供应商下还没有匹配的模型。', 'No matching models are registered for this provider.')}</div>}
            </div>
          </div>
        </section>
      )}

      <section className="role-routing models-surface" id="role-routing" aria-labelledby="role-routing-title" tabIndex={-1}>
        <div className="models-section-heading">
          <div className="section-heading-icon"><Bot size={19} aria-hidden="true" /></div>
          <div><span className="section-kicker">{t('settings.routing')}</span><h2 id="role-routing-title">{text('为每个阶段选择兼容模型', 'Choose a compatible model for every stage')}</h2><p>{text('下拉框只列出能力完整匹配、模型启用且供应商启用的选项。', 'Selectors only list enabled models from enabled providers whose capabilities fully match the role.')}</p></div>
          <div className="routing-valid"><ShieldCheck size={16} aria-hidden="true" />{roles.filter(role => role.modelId).length} / {roles.length} {text('已绑定', 'bound')}</div>
        </div>
        <div className="role-groups">
          {(['语音', '视觉', '笔记'] as const).map(group => (
            <section className="role-group" key={group}>
              <header><span>{text(group, group === '语音' ? 'Speech' : group === '视觉' ? 'Vision' : 'Notes')}</span><small>{text(`${roles.filter(role => role.group === group).length} 个角色`, `${roles.filter(role => role.group === group).length} roles`)}</small></header>
              {roles.filter(role => role.group === group).map(role => {
                const compatibleModels = models.filter(model => {
                  const modelProvider = providers.find(item => item.id === model.providerId)
                  return model.enabled && Boolean(modelProvider?.enabled) && role.requiredCapabilities.every(capability => model.capabilities.includes(capability))
                })
                const currentModel = compatibleModels.find(model => model.id === role.modelId)
                const roleCopy = roleCopyEn[role.id]
                const roleLabel = locale === 'en-US' ? roleCopy?.label ?? role.id : role.label
                const roleDescription = locale === 'en-US'
                  ? roleCopy?.description ?? 'Processing role supplied by the backend catalog'
                  : role.description
                return (
                  <div className="role-row" key={role.id}>
                    <div className="role-copy"><strong>{roleLabel}</strong><small>{roleDescription}</small><span>{role.requiredCapabilities.map(capabilityLabel).join(' · ')}</span></div>
                    <label className="role-select"><span className="sr-only">{text(`为${roleLabel}选择模型`, `Choose a model for ${roleLabel}`)}</span><select value={currentModel?.id ?? ''} onChange={event => bindRole(role.id, event.target.value)}><option value="">{text('未绑定', 'Unbound')}</option>{compatibleModels.map(model => <option value={model.id} key={model.id}>{model.label} · {model.locality === 'local' ? text('本机', 'Local') : text('云端', 'Cloud')}</option>)}</select></label>
                    <span className={`role-state ${currentModel ? 'is-ready' : ''}`}>{currentModel ? <Check size={13} aria-hidden="true" /> : <TriangleAlert size={13} aria-hidden="true" />}{currentModel ? text('能力匹配', 'Capabilities match') : text('等待绑定', 'Awaiting binding')}</span>
                  </div>
                )
              })}
            </section>
          ))}
        </div>
        <div className="routing-footnote"><ShieldCheck size={15} aria-hidden="true" />{text('角色要求完全来自后端 configuration catalog；前端不会用近似标签代替真实能力。', 'Role requirements come directly from the backend configuration catalog; the frontend never substitutes approximate labels for real capabilities.')}</div>
      </section>
        </div>
      </div>
    </div>
  )
}

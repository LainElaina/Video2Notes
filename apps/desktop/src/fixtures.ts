import type {
  ComponentReportDefinition,
  EvidenceItem,
  MachineProfile,
  ModelDefinition,
  NoteDocument,
  ProcessingTask,
  ProviderDefinition,
  RoleBinding,
  SourceManifest,
  SupportingMaterial,
  TaskStage,
  TelemetrySample,
  TelemetryValue,
} from './domain'

export const componentReportFixture: ComponentReportDefinition = {
  hardwareTier: 'gpu_24gb_plus',
  recommendation: {
    hardwareTier: 'gpu_24gb_plus',
    asrComponentId: 'asr-faster-whisper-large-v3',
    ocrComponentId: 'ocr-paddle-ppocrv5-server',
    asrDevice: 'cuda',
    asrComputeType: 'float16',
    ocrDevice: 'cpu',
    reason: '24 GiB or larger GPUs accelerate ASR with CUDA while OCR uses the bundled CPU runtime.',
  },
  inventory: {
    ready: false,
    degraded: true,
    capabilities: {
      core: true,
      download: true,
      asr: false,
      ocr: false,
    },
    items: [
      {
        id: 'runtime-root',
        displayName: 'Portable runtime resources',
        kind: 'runtime',
        state: 'ready',
        ready: true,
        degraded: false,
        required: true,
        version: '0.1.0',
        path: 'runtime',
        actions: [],
      },
      {
        id: 'python-runtime',
        displayName: 'Embedded Python runtime',
        kind: 'runtime',
        state: 'ready',
        ready: true,
        degraded: false,
        required: true,
        version: '3.12.10',
        path: 'runtime/python/python.exe',
        actions: [],
      },
      ...[
        ['ffmpeg', 'ffmpeg', '7.1'],
        ['ffprobe', 'ffprobe', '7.1'],
        ['yt-dlp', 'yt-dlp', '2026.07.15'],
        ['psutil', 'psutil resource monitor', '7.0.0'],
        ['faster-whisper', 'faster-whisper runtime', '1.2.0'],
        ['ctranslate2', 'CTranslate2 runtime', '4.6.0'],
        ['huggingface-hub', 'Hugging Face managed download runtime', '0.34.1'],
        ['paddleocr', 'PaddleOCR runtime', '3.1.0'],
        ['paddlepaddle', 'PaddlePaddle runtime', '3.1.0'],
      ].map(([id, displayName, version]) => ({
        id,
        displayName,
        kind: id === 'ffmpeg' || id === 'ffprobe' ? ('tool' as const) : ('runtime' as const),
        state: 'ready' as const,
        ready: true,
        degraded: false,
        required: true,
        version,
        path: `runtime/${id}`,
        actions: [],
      })),
      {
        id: 'asr-faster-whisper-large-v3',
        displayName: 'faster-whisper large-v3',
        kind: 'local_model',
        state: 'missing',
        ready: false,
        degraded: true,
        required: true,
        version: '1.0.0',
        path: 'components/models/asr/faster-whisper-large-v3',
        detail: 'Model payload has not been downloaded.',
        actions: [
          {
            id: 'prepare-asr-faster-whisper-large-v3',
            kind: 'prepare',
            componentId: 'asr-faster-whisper-large-v3',
            label: 'Prepare faster-whisper large-v3',
            automatic: true,
          },
        ],
      },
      {
        id: 'ocr-paddle-ppocrv5-server',
        displayName: 'PaddleOCR PP-OCRv5 server',
        kind: 'local_model',
        state: 'incomplete',
        ready: false,
        degraded: true,
        required: true,
        version: '1.0.0',
        path: 'components/models/ocr/ppocrv5-server',
        detail: 'Interrupted payload can be resumed.',
        actions: [
          {
            id: 'resume-ocr-paddle-ppocrv5-server',
            kind: 'resume',
            componentId: 'ocr-paddle-ppocrv5-server',
            label: 'Resume PaddleOCR PP-OCRv5 server',
            automatic: true,
          },
        ],
      },
    ],
    actions: [
      {
        id: 'prepare-asr-faster-whisper-large-v3',
        kind: 'prepare',
        componentId: 'asr-faster-whisper-large-v3',
        label: 'Prepare faster-whisper large-v3',
        automatic: true,
      },
      {
        id: 'resume-ocr-paddle-ppocrv5-server',
        kind: 'resume',
        componentId: 'ocr-paddle-ppocrv5-server',
        label: 'Resume PaddleOCR PP-OCRv5 server',
        automatic: true,
      },
    ],
  },
}

export const machineFixture: MachineProfile = {
  cpu: 'Ryzen 9 9950X3D',
  gpu: 'RTX 5090 D · 24 GB',
  memory: '64 GB RAM',
  backend: 'ready',
}

export const completedSourceFixture: SourceManifest = {
  id: 'source-evidence-timeline',
  platform: 'bilibili',
  title: '从视频到可追溯知识：统一证据时间轴',
  author: 'Lain Lab',
  durationSeconds: 1518,
  quality: '1080p · 60 fps',
  codec: 'AVC / yuv420p',
  audio: 'AAC · 48 kHz',
  subtitle: '中文平台字幕',
  authLabel: 'Edge · Profile 2',
  sourceLabel: 'BV1EvidenceDemo',
}

export const runningSourceFixture: SourceManifest = {
  id: 'source-visual-changes',
  platform: 'youtube',
  title: 'Adaptive frame sampling for technical lectures',
  author: 'Visual Systems Lab',
  durationSeconds: 2142,
  quality: '1440p · 30 fps',
  codec: 'VP9 / yuv420p',
  audio: 'Opus · 48 kHz',
  subtitle: 'English auto captions',
  authLabel: '游客模式',
  sourceLabel: 'youtube.com/watch?v=adaptive-demo',
}

const stageBlueprint = [
  ['acquire', '获取', '校验来源、字幕与最佳音视频格式'],
  ['normalize', '规范化', '保存 stream time base 与真实 PTS'],
  ['speech', '语音', 'VAD、语言识别与词级时间戳'],
  ['vision', '视觉', '变化粗扫、精扫、关键帧与 OCR'],
  ['fusion', '融合', '按时间交叠生成 EvidenceSpan'],
  ['draft', '写作', '证据块、事实卡与章节草稿'],
  ['verify', '验证', '检查 claim 支持度与内容覆盖'],
  ['render', '导出', '渲染 Markdown、HTML 与 PDF'],
] as const

type DemoStageId = (typeof stageBlueprint)[number][0]

const demoStageData: Record<
  DemoStageId,
  {
    backendStage: string
    metrics: Record<string, TelemetryValue>
    metric?: string
    warnings?: string[]
    artifact: {
      kind: string
      relativePath: string
      sizeBytes: number
      mediaType: string
    }
  }
> = {
  acquire: {
    backendStage: 'source.acquire',
    metrics: { selected_stream_count: 2, subtitle_track_count: 1 },
    artifact: {
      kind: 'source',
      relativePath: 'source/source-manifest.json',
      sizeBytes: 2_816,
      mediaType: 'application/json',
    },
  },
  normalize: {
    backendStage: 'media.probe',
    metrics: { timeline_origin_us: 0, video_stream_count: 1, audio_stream_count: 1 },
    artifact: {
      kind: 'media',
      relativePath: 'media/media-manifest.json',
      sizeBytes: 3_584,
      mediaType: 'application/json',
    },
  },
  speech: {
    backendStage: 'audio.asr',
    metrics: { asr_segment_count: 184, secondary_window_count: 3 },
    metric: '4.8× realtime',
    warnings: ['演示样本：3 个低置信语音窗口使用了选择性复核。'],
    artifact: {
      kind: 'evidence',
      relativePath: 'speech/asr-evidence.json',
      sizeBytes: 148_320,
      mediaType: 'application/json',
    },
  },
  vision: {
    backendStage: 'vision.scan',
    metrics: { visual_state_count: 37, coarse_scan_fps: 138 },
    metric: '138 fps coarse',
    artifact: {
      kind: 'visual',
      relativePath: 'vision/visual-states.json',
      sizeBytes: 42_752,
      mediaType: 'application/json',
    },
  },
  fusion: {
    backendStage: 'evidence.fuse',
    metrics: { evidence_count: 48, conflict_count: 2, window_count: 11 },
    artifact: {
      kind: 'evidence',
      relativePath: 'evidence/fusion.json',
      sizeBytes: 72_640,
      mediaType: 'application/json',
    },
  },
  draft: {
    backendStage: 'notes.compose',
    metrics: { section_count: 4, fact_count: 12 },
    artifact: {
      kind: 'note',
      relativePath: 'notes/document.json',
      sizeBytes: 38_912,
      mediaType: 'application/json',
    },
  },
  verify: {
    backendStage: 'notes.compose',
    metrics: { supported_claim_ratio: 0.96, review_claim_count: 1 },
    warnings: ['演示样本：1 条事实卡保留为待复核。'],
    artifact: {
      kind: 'note',
      relativePath: 'notes/composition.json',
      sizeBytes: 41_216,
      mediaType: 'application/json',
    },
  },
  render: {
    backendStage: 'render.outputs',
    metrics: { output_format_count: 3, embedded_screenshot_count: 4 },
    metric: '3 formats',
    artifact: {
      kind: 'note',
      relativePath: 'notes/note.md',
      sizeBytes: 24_576,
      mediaType: 'text/markdown',
    },
  },
}

export const makeStages = (progress: number): TaskStage[] => {
  const activeIndex = Math.min(stageBlueprint.length - 1, Math.floor(progress / 12.5))
  return stageBlueprint.map(([id, label, detail], index) => {
    const completed = progress >= 100 || index < activeIndex
    const running = progress < 100 && index === activeIndex
    const hasStarted = completed || running
    const localProgress = completed
      ? 100
      : running
        ? Math.min(96, Math.round(((progress % 12.5) / 12.5) * 100))
        : 0
    const demo = demoStageData[id]
    const outputArtifacts = completed
      ? [
          {
            stage: demo.backendStage,
            kind: demo.artifact.kind,
            relativePath: demo.artifact.relativePath,
            sha256: `demo-fixture-${id}`,
            sizeBytes: demo.artifact.sizeBytes,
            mediaType: demo.artifact.mediaType,
            createdAt: '2026-07-28T14:32:00Z',
          },
        ]
      : []
    return {
      id,
      label,
      detail,
      status: completed ? 'completed' : running ? 'running' : 'pending',
      progress: localProgress,
      durationSeconds: completed ? 7 + index * 5 : undefined,
      artifactCount: outputArtifacts.length,
      metrics: hasStarted ? { ...demo.metrics } : {},
      warnings: hasStarted ? [...(demo.warnings ?? [])] : [],
      outputArtifacts,
      metric: hasStarted ? demo.metric : undefined,
    }
  })
}

export const demoRuntimeWarnings = [
  '演示任务的阶段指标与遥测来自固定 fixture，不代表当前机器的真实测量结果。',
]

export const makeDemoMaterials = (runId: string): SupportingMaterial[] => {
  const textContent =
    '演示补充材料：固定间隔抽帧会错过短暂出现的文字，因此应先检测变化，再对候选窗口精扫。'
  const materialKey = runId.replace(/[^A-Za-z0-9]/g, '')
  return [
    {
      id: `material-demo${materialKey}text`,
      runId,
      kind: 'text',
      title: '评论区补充说明（演示内存）',
      originalName: null,
      mediaType: 'text/markdown; charset=utf-8',
      artifact: null,
      sha256: null,
      sizeBytes: new TextEncoder().encode(textContent).byteLength,
      textContent,
      startUs: 32_000_000,
      endUs: 81_000_000,
      status: 'active',
      createdAt: '2026-07-28T14:31:00Z',
      deletedAt: null,
      storage: 'demo-memory',
    },
    {
      id: `material-demo${materialKey}image`,
      runId,
      kind: 'image',
      title: '时间轴参考图（演示仅保存名称与元数据）',
      originalName: 'timeline-reference.png',
      mediaType: 'image/png',
      artifact: null,
      sha256: null,
      sizeBytes: 0,
      textContent: null,
      startUs: 92_000_000,
      endUs: 93_000_000,
      status: 'active',
      createdAt: '2026-07-28T14:31:30Z',
      deletedAt: null,
      storage: 'demo-memory',
    },
  ]
}

export const makeDemoTelemetry = (
  progress: number,
  runId = 'demo-fixture',
): TelemetrySample[] =>
  makeStages(progress)
    .filter(stage => stage.status !== 'pending')
    .map((stage, index, samples) => ({
      sequence: index + 1,
      runId,
      state:
        progress >= 100 && index === samples.length - 1
          ? ('completed' as const)
          : ('running' as const),
      stage: demoStageData[stage.id as DemoStageId].backendStage,
      progress: stage.progress / 100,
      message: `演示遥测 · ${stage.label}${stage.status === 'completed' ? '已完成' : '处理中'}`,
      metrics: { ...stage.metrics },
      createdAt: new Date(Date.UTC(2026, 6, 28, 14, 32, index)).toISOString(),
    }))

export const evidenceFixture: EvidenceItem[] = [
  {
    id: 'ASR-041',
    kind: 'asr',
    startSeconds: 32,
    endSeconds: 81,
    label: '语音：为什么不能按固定秒数抽帧',
    rawText: '固定间隔抽帧无法表达视频内容发生变化的真实节奏。',
    confidence: 0.96,
    provider: 'faster-whisper-large-v3',
  },
  {
    id: 'OCR-018',
    kind: 'ocr',
    startSeconds: 68,
    endSeconds: 112,
    label: '屏幕文字：Presentation timestamp',
    rawText: 'PTS × time_base = presentation time',
    confidence: 0.93,
    provider: 'PaddleOCR PP-OCRv5',
  },
  {
    id: 'FRAME-011',
    kind: 'visual',
    startSeconds: 92,
    endSeconds: 93,
    label: '关键帧：统一物理时间轴示意',
    rawText: '检测到稳定的新幻灯片状态，清晰度 0.92。',
    confidence: 0.92,
    provider: 'adaptive-visual-v1',
  },
  {
    id: 'CHAPTER-01',
    kind: 'chapter',
    startSeconds: 0,
    endSeconds: 278,
    label: '章节：证据优先',
    rawText: '平台字幕、ASR、OCR 和视觉状态进入同一物理时间轴。',
    confidence: 1,
    provider: 'note-boundary-v1',
  },
  {
    id: 'ASR-084',
    kind: 'asr',
    startSeconds: 338,
    endSeconds: 412,
    label: '语音：选择性二次识别',
    rawText: '只有低置信、语言不确定或跨来源冲突的片段才需要第二识别器。',
    confidence: 0.88,
    provider: 'faster-whisper-large-v3',
  },
  {
    id: 'OCR-043',
    kind: 'ocr',
    startSeconds: 385,
    endSeconds: 427,
    label: '屏幕文字：selective escalation',
    rawText: 'low confidence → secondary ASR → aligned adjudication',
    confidence: 0.9,
    provider: 'PaddleOCR PP-OCRv5',
  },
  {
    id: 'FRAME-027',
    kind: 'visual',
    startSeconds: 421,
    endSeconds: 422,
    label: '关键帧：多候选对齐',
    rawText: '新增流程图在 00:07:01 稳定。',
    confidence: 0.95,
    provider: 'adaptive-visual-v1',
  },
  {
    id: 'CHAPTER-02',
    kind: 'chapter',
    startSeconds: 278,
    endSeconds: 711,
    label: '章节：多模态融合',
    rawText: '对齐、校准、仲裁，再生成事实卡。',
    confidence: 1,
    provider: 'note-boundary-v1',
  },
  {
    id: 'ASR-126',
    kind: 'asr',
    startSeconds: 812,
    endSeconds: 903,
    label: '语音：证据约束写作',
    rawText: '重要数字、术语、代码和步骤必须回溯到原始证据。',
    confidence: 0.97,
    provider: 'faster-whisper-large-v3',
  },
  {
    id: 'FRAME-051',
    kind: 'visual',
    startSeconds: 865,
    endSeconds: 866,
    label: '关键帧：事实卡结构',
    rawText: '事实卡包含 claim、时间区间和 evidence refs。',
    confidence: 0.94,
    provider: 'adaptive-visual-v1',
  },
  {
    id: 'CHAPTER-03',
    kind: 'chapter',
    startSeconds: 711,
    endSeconds: 1189,
    label: '章节：笔记生成与验证',
    rawText: '事实卡、章节草稿、claim 验证、Canonical NoteDocument。',
    confidence: 1,
    provider: 'note-boundary-v1',
  },
  {
    id: 'ASR-181',
    kind: 'asr',
    startSeconds: 1236,
    endSeconds: 1312,
    label: '语音：确定性导出',
    rawText: 'Markdown、HTML 和 PDF 必须来自同一份内容树。',
    confidence: 0.95,
    provider: 'faster-whisper-large-v3',
  },
  {
    id: 'CHAPTER-04',
    kind: 'chapter',
    startSeconds: 1189,
    endSeconds: 1518,
    label: '章节：可携带输出',
    rawText: 'Canonical NoteDocument 渲染为多个一致的离线产物。',
    confidence: 1,
    provider: 'note-boundary-v1',
  },
]

export const noteFixture: NoteDocument = {
  title: '从视频到可追溯知识：统一证据时间轴',
  overview:
    '高质量视频笔记的关键不是更快地总结字幕，而是让语音、屏幕文字、画面变化与最终陈述共享同一条可以核验的时间线。',
  sourceSummary: 'Bilibili · 25:18 · 1080p · Accurate · 13 条主要证据',
  generatedAt: '2026-07-28 14:32',
  sections: [
    {
      id: 'evidence-first',
      title: '证据优先，而不是摘要优先',
      summary:
        '平台字幕、ASR、OCR 与视觉状态先形成独立证据，再按真实 PTS 对齐；模型只能在证据允许的边界内写作。',
      screenshotAt: 92,
      claims: [
        {
          id: 'claim-01',
          text: '容器的 presentation timestamp 是音频、画面、字幕和 OCR 的共同物理时间真值。',
          timeSeconds: 92,
          evidenceIds: ['ASR-041', 'OCR-018', 'FRAME-011'],
          emphasis: true,
        },
        {
          id: 'claim-02',
          text: '固定每 N 秒抽帧会漏掉 PPT 逐项出现、代码滚动和短时间内的关键状态变化。',
          timeSeconds: 68,
          evidenceIds: ['ASR-041', 'OCR-018'],
        },
      ],
    },
    {
      id: 'selective-ensemble',
      title: '只在值得的片段上增加精度',
      summary:
        'Fast 模式保持单路识别和较少候选；Accurate 模式也不会盲目多跑全片，而是把算力集中在低置信或冲突窗口。',
      screenshotAt: 421,
      claims: [
        {
          id: 'claim-03',
          text: '第二 ASR 的触发条件应是低置信、语言不确定、强噪声或平台字幕冲突。',
          timeSeconds: 385,
          evidenceIds: ['ASR-084', 'OCR-043'],
          emphasis: true,
        },
        {
          id: 'claim-04',
          text: '候选必须先按时间约束对齐并校准置信度，自动规则仍无法决定时才升级到仲裁模型。',
          timeSeconds: 421,
          evidenceIds: ['ASR-084', 'FRAME-027'],
        },
      ],
    },
    {
      id: 'note-generation',
      title: '从证据块生成可验证的章节',
      summary:
        '生成链依次产出证据块、事实卡、章节草稿和 claim 验证结果，最终形成唯一的 Canonical NoteDocument。',
      screenshotAt: 865,
      claims: [
        {
          id: 'claim-05',
          text: '重要数字、术语、代码和操作步骤都应保存 evidence reference，无法支持的内容不进入最终笔记。',
          timeSeconds: 865,
          evidenceIds: ['ASR-126', 'FRAME-051'],
          emphasis: true,
        },
      ],
    },
    {
      id: 'portable-output',
      title: '一次写作，多种确定性输出',
      summary:
        'Markdown 是第一产物，HTML 与 PDF 从相同内容树渲染，因而不会出现三个导出版本相互矛盾。',
      claims: [
        {
          id: 'claim-06',
          text: 'Markdown、离线 HTML 与 PDF 共享正文、截图、时间戳和证据引用。',
          timeSeconds: 1236,
          evidenceIds: ['ASR-181'],
        },
      ],
    },
  ],
}

export const makeTaskFixtures = (): ProcessingTask[] => [
  {
    id: 'task-running',
    source: runningSourceFixture,
    mode: 'accurate',
    status: 'running',
    progress: 36,
    etaSeconds: 1062,
    createdAt: '今天 15:08',
    stages: makeStages(36),
    telemetry: makeDemoTelemetry(36, 'task-running'),
    runtimeWarnings: [...demoRuntimeWarnings],
    materials: makeDemoMaterials('task-running'),
    operations: [],
    evidence: evidenceFixture.slice(0, 7),
  },
  {
    id: 'task-complete',
    source: completedSourceFixture,
    mode: 'accurate',
    status: 'completed',
    progress: 100,
    etaSeconds: 0,
    createdAt: '今天 14:07',
    stages: makeStages(100),
    telemetry: makeDemoTelemetry(100, 'task-complete'),
    runtimeWarnings: [...demoRuntimeWarnings],
    materials: makeDemoMaterials('task-complete'),
    operations: [],
    evidence: evidenceFixture,
    note: noteFixture,
  },
]

export const providerFixtures: ProviderDefinition[] = [
  {
    id: 'provider-local',
    name: 'Local Engine',
    kind: 'local',
    protocol: 'local',
    authScheme: 'none',
    endpoint: '本机 worker',
    locality: 'local',
    enabled: true,
    timeoutSeconds: 180,
    protocolOptions: {},
    status: 'connected',
    credentialState: 'not-needed',
    lastChecked: '刚刚',
  },
  {
    id: 'provider-ollama',
    name: 'Ollama',
    kind: 'ollama',
    protocol: 'ollama_native_chat',
    authScheme: 'none',
    endpoint: 'http://127.0.0.1:11434',
    locality: 'local',
    enabled: true,
    timeoutSeconds: 180,
    protocolOptions: {},
    status: 'connected',
    credentialState: 'not-needed',
    lastChecked: '2 分钟前',
  },
  {
    id: 'provider-openai',
    name: 'OpenAI compatible',
    kind: 'openai_compatible',
    protocol: 'openai_chat_completions',
    authScheme: 'bearer',
    endpoint: 'https://api.example.invalid/v1',
    locality: 'cloud',
    enabled: true,
    timeoutSeconds: 180,
    protocolOptions: {},
    status: 'disconnected',
    credentialState: 'stored-locally',
  },
]

export const modelFixtures: ModelDefinition[] = [
  {
    id: 'model-whisper',
    providerId: 'provider-local',
    label: 'Whisper large-v3',
    modelId: 'faster-whisper-large-v3',
    locality: 'local',
    capabilities: [
      'asr',
      'language_id',
      'segment_timestamps',
      'word_timestamps',
      'word_confidence',
    ],
    enabled: true,
  },
  {
    id: 'model-funasr',
    providerId: 'provider-local',
    label: 'FunASR Paraformer',
    modelId: 'paraformer-zh',
    locality: 'local',
    capabilities: ['asr', 'language_id', 'segment_timestamps'],
    enabled: true,
  },
  {
    id: 'model-paddleocr',
    providerId: 'provider-local',
    label: 'PaddleOCR',
    modelId: 'pp-ocrv5-server',
    locality: 'local',
    capabilities: ['ocr', 'ocr_boxes', 'ocr_confidence'],
    enabled: true,
  },
  {
    id: 'model-qwen',
    providerId: 'provider-ollama',
    label: 'Qwen 2.5 VL',
    modelId: 'qwen2.5vl:7b',
    locality: 'local',
    capabilities: ['text', 'vision', 'structured_output', 'long_context'],
    enabled: true,
  },
  {
    id: 'model-text-local',
    providerId: 'provider-ollama',
    label: 'Qwen 3 14B',
    modelId: 'qwen3:14b',
    locality: 'local',
    capabilities: ['text', 'structured_output', 'long_context'],
    enabled: true,
  },
  {
    id: 'model-cloud',
    providerId: 'provider-openai',
    label: 'Cloud reasoner',
    modelId: 'configure-me',
    locality: 'cloud',
    capabilities: ['text', 'vision', 'structured_output', 'long_context'],
    enabled: true,
  },
]

export const roleFixtures: RoleBinding[] = [
  {
    id: 'asr.primary',
    group: '语音',
    label: '主要语音识别',
    description: '默认输出词级时间戳与置信度',
    requiredCapabilities: ['asr', 'segment_timestamps'],
    modelId: 'model-whisper',
    fallbackModelId: 'model-funasr',
  },
  {
    id: 'asr.secondary',
    group: '语音',
    label: '低置信复核',
    description: '只处理冲突、噪声与语言不确定窗口',
    requiredCapabilities: ['asr', 'segment_timestamps'],
    modelId: 'model-funasr',
  },
  {
    id: 'ocr.primary',
    group: '视觉',
    label: '屏幕文字',
    description: '输出原始文本、box 和时间区间',
    requiredCapabilities: ['ocr', 'ocr_boxes'],
    modelId: 'model-paddleocr',
  },
  {
    id: 'vision.frame_explainer',
    group: '视觉',
    label: '关键帧解释',
    description: '仅解释已筛选的少量高价值画面',
    requiredCapabilities: ['text', 'vision'],
    modelId: 'model-qwen',
  },
  {
    id: 'notes.fact_extractor',
    group: '笔记',
    label: '事实提取',
    description: '把证据块转为有引用的事实卡',
    requiredCapabilities: ['text', 'structured_output'],
    modelId: 'model-text-local',
  },
  {
    id: 'notes.drafter',
    group: '笔记',
    label: '章节写作',
    description: '根据事实卡生成结构化中文笔记',
    requiredCapabilities: ['text', 'long_context'],
    modelId: 'model-text-local',
  },
  {
    id: 'notes.verifier',
    group: '笔记',
    label: '事实验证',
    description: '检查 claim 支持度与章节覆盖率',
    requiredCapabilities: ['text', 'structured_output'],
    modelId: 'model-text-local',
    fallbackModelId: 'model-cloud',
  },
]

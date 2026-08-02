import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useStudioStore } from './store'

describe('studio store', () => {
  beforeEach(() => {
    useStudioStore.getState().resetDemo()
  })

  it('probes a supported URL and creates a resumable task', () => {
    const store = useStudioStore.getState()
    store.setDraftInput('https://www.youtube.com/watch?v=typed-fixture')
    store.setDraftMode('fast')
    store.probeSource()

    expect(useStudioStore.getState().draft.manifest?.platform).toBe('youtube')
    expect(useStudioStore.getState().draft.status).toBe('ready')

    store.createTask()
    const state = useStudioStore.getState()
    expect(state.view).toBe('tasks')
    expect(state.tasks[0]).toMatchObject({
      mode: 'fast',
      status: 'running',
      progress: 0,
      telemetry: [
        {
          sequence: 1,
          state: 'running',
          stage: 'source.acquire',
          progress: 0,
        },
      ],
    })
    expect(state.tasks[0].runtimeWarnings[0]).toContain('固定 fixture')

    state.pauseTask(state.tasks[0].id)
    expect(useStudioStore.getState().tasks[0].status).toBe('paused')
    state.resumeTask(state.tasks[0].id)
    expect(useStudioStore.getState().tasks[0].status).toBe('running')
  })

  it('keeps the previous manifest as stale until changed probe inputs are verified again', () => {
    const store = useStudioStore.getState()
    store.probeSource()
    const verifiedManifest = useStudioStore.getState().draft.manifest
    expect(useStudioStore.getState().draft.status).toBe('ready')

    store.setDraftMode('fast')
    expect(useStudioStore.getState().draft).toMatchObject({
      status: 'stale',
      manifest: verifiedManifest,
    })

    store.probeSource()
    expect(useStudioStore.getState().draft.status).toBe('ready')
    store.setDraftAuthKind('browser_profile')
    expect(useStudioStore.getState().draft.status).toBe('stale')

    store.probeSource()
    store.setDraftProfile('Default')
    expect(useStudioStore.getState().draft.status).toBe('stale')

    store.probeSource()
    store.setDraftInput('https://www.youtube.com/watch?v=changed-source')
    expect(useStudioStore.getState().draft).toMatchObject({
      status: 'stale',
      manifest: expect.objectContaining({ platform: 'bilibili' }),
    })
  })

  it('does not create a task from a stale manifest', () => {
    const store = useStudioStore.getState()
    store.probeSource()
    store.setDraftMode('fast')
    const taskCount = useStudioStore.getState().tasks.length

    store.createTask()

    expect(useStudioStore.getState().tasks).toHaveLength(taskCount)
    expect(useStudioStore.getState().draft.status).toBe('stale')
    expect(useStudioStore.getState().draft.error).toContain('重新探测')
  })

  it('defaults to full audio-visual processing and reuses the source probe when scope changes', () => {
    const store = useStudioStore.getState()
    expect(store.draft.processingScope).toBe('audio_visual')
    store.probeSource()
    const previousManifest = useStudioStore.getState().draft.manifest

    store.setProcessingScope('audio_only')

    expect(useStudioStore.getState().draft).toMatchObject({
      processingScope: 'audio_only',
      status: 'ready',
    })
    expect(useStudioStore.getState().draft.manifest).toBe(previousManifest)
    expect(useStudioStore.getState().processingEstimates).toEqual({})
  })

  it('does not expose audio-only to a real backend without the declared capability', () => {
    useStudioStore.setState({
      backend: { mode: 'real', version: 'legacy', detail: 'legacy backend' },
    })

    useStudioStore.getState().setProcessingScope('audio_only')

    expect(useStudioStore.getState().draft.processingScope).toBe('audio_visual')
    expect(useStudioStore.getState().notice).toContain('后端版本不支持仅音频')
  })

  it('keeps audio-only demo tasks free of visual evidence, stages, screenshots, and rework', () => {
    const store = useStudioStore.getState()
    store.probeSource()
    store.setProcessingScope('audio_only')
    store.createTask()
    const taskId = useStudioStore.getState().tasks[0].id
    let task = useStudioStore.getState().tasks[0]

    expect(task.processingScope).toBe('audio_only')
    expect(task.evidence.every(item => item.kind !== 'ocr' && item.kind !== 'visual')).toBe(true)
    expect(task.stages.some(stage => stage.id === 'vision')).toBe(false)
    expect(task.telemetry.some(sample => sample.stage === 'vision.scan')).toBe(false)

    useStudioStore.setState(state => ({
      tasks: state.tasks.map(item =>
        item.id === taskId ? { ...item, progress: 99.9 } : item,
      ),
    }))
    useStudioStore.getState().advanceTasks()
    task = useStudioStore.getState().tasks.find(item => item.id === taskId)!
    expect(task.status).toBe('completed')
    expect(task.telemetry.at(-1)?.metrics).toMatchObject({
      embedded_screenshot_count: 0,
    })
    expect(
      task.note?.sections.every(
        section =>
          section.screenshotAt === undefined &&
          section.screenshotPath === undefined &&
          section.screenshotUrl === undefined,
      ),
    ).toBe(true)

    const operationCount = task.operations.length
    useStudioStore.getState().runVisionRework(taskId, {
      startSeconds: 0,
      endSeconds: 10,
      mode: 'adaptive',
      runOcr: true,
    })
    expect(
      useStudioStore.getState().tasks.find(item => item.id === taskId)?.operations,
    ).toHaveLength(operationCount)
    expect(useStudioStore.getState().notice).toContain('仅音频任务没有视觉基线')
  })

  it('loads the redistributable demo media without an account', () => {
    useStudioStore.getState().chooseBundledDemo()

    expect(useStudioStore.getState().draft).toMatchObject({
      input: 'evidence-demo.mp4',
      sourceKind: 'local',
      status: 'ready',
    })
  })

  it('blocks task creation when the fixed sampling budget exceeds 5,000 frames', () => {
    const store = useStudioStore.getState()
    store.setDraftInput('https://www.youtube.com/watch?v=sampling-budget')
    store.probeSource()
    store.setSamplingMode('fixed_interval')
    store.setSamplingIntervalSeconds(0.1)
    const taskCount = useStudioStore.getState().tasks.length

    store.createTask()

    expect(useStudioStore.getState().tasks).toHaveLength(taskCount)
    expect(useStudioStore.getState().view).toBe('create')
    expect(useStudioStore.getState().notice).toContain('超过上限')
  })

  it('labels demo stage metrics and telemetry as fixture data', () => {
    const completed = useStudioStore
      .getState()
      .tasks.find(task => task.id === 'task-complete')

    expect(completed?.realBackend).not.toBe(true)
    expect(completed?.runtimeWarnings).toEqual([
      expect.stringContaining('不代表当前机器的真实测量结果'),
    ])
    expect(completed?.stages.find(stage => stage.id === 'vision')).toMatchObject({
      metrics: { visual_state_count: 37, coarse_scan_fps: 138 },
      outputArtifacts: [
        {
          stage: 'vision.scan',
          relativePath: 'vision/visual-states.json',
          sha256: 'demo-fixture-vision',
        },
      ],
    })
    expect(completed?.telemetry.at(-1)).toMatchObject({
      runId: 'task-complete',
      state: 'completed',
      stage: 'render.outputs',
      metrics: { output_format_count: 3, embedded_screenshot_count: 4 },
    })
  })

  it('keeps demo text and image materials honestly in memory', () => {
    const store = useStudioStore.getState()
    const taskId = 'task-complete'
    const initial = store.tasks.find(task => task.id === taskId)?.materials ?? []
    expect(initial).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ kind: 'text', storage: 'demo-memory', artifact: null }),
        expect.objectContaining({
          kind: 'image',
          storage: 'demo-memory',
          artifact: null,
          originalName: 'timeline-reference.png',
          sizeBytes: 0,
        }),
      ]),
    )

    store.addTextMaterial(taskId, {
      title: '手工补充',
      content: '  只保存在演示内存中的文字。  ',
      startUs: 1_000_000,
      endUs: 2_000_000,
    })
    const image = new File([new Uint8Array([1, 2, 3])], 'reference.png', {
      type: 'image/png',
    })
    useStudioStore.getState().addFileMaterial(taskId, image, {
      title: '本地参考图',
      startUs: 3_000_000,
      endUs: 4_000_000,
    })

    const withAdded = useStudioStore.getState().tasks.find(task => task.id === taskId)
    expect(withAdded?.materials).toHaveLength(initial.length + 2)
    const addedText = withAdded?.materials.find(material => material.title === '手工补充')
    expect(addedText).toMatchObject({
      textContent: '只保存在演示内存中的文字。',
      storage: 'demo-memory',
      artifact: null,
      sha256: null,
    })
    expect(
      withAdded?.materials.find(material => material.title === '本地参考图'),
    ).toMatchObject({
      kind: 'image',
      originalName: 'reference.png',
      sizeBytes: 3,
      storage: 'demo-memory',
      artifact: null,
      sha256: null,
    })
    expect(useStudioStore.getState().notice).toContain('没有读取内容或伪造后端上传')

    useStudioStore.getState().deleteMaterial(taskId, addedText!.id)
    expect(
      useStudioStore
        .getState()
        .tasks.find(task => task.id === taskId)
        ?.materials.some(material => material.id === addedText!.id),
    ).toBe(false)
    useStudioStore.getState().refreshMaterials(taskId)
    expect(useStudioStore.getState().notice).toContain('仅保存在当前内存')
  })

  it('records demo model rework as preview without changing evidence', () => {
    const taskId = 'task-complete'
    const before = useStudioStore
      .getState()
      .tasks.find(task => task.id === taskId)!
    const evidenceBefore = structuredClone(before.evidence)

    useStudioStore.getState().runVisionRework(taskId, {
      startSeconds: 12.25,
      endSeconds: 18.5,
      mode: 'fixed_interval',
      intervalSeconds: 0.25,
      runOcr: true,
    })
    useStudioStore.getState().runAsrRework(taskId, {
      range: { startSeconds: 20, endSeconds: 25.125 },
      languageHints: ['zh-CN', ' en ', 'zh-CN'],
    })

    const after = useStudioStore
      .getState()
      .tasks.find(task => task.id === taskId)!
    expect(after.evidence).toEqual(evidenceBefore)
    expect(after.operations).toHaveLength(before.operations.length + 2)
    expect(after.operations.slice(-2)).toEqual([
      expect.objectContaining({
        kind: 'vision_rescan',
        status: 'demo-preview',
        source: 'demo-memory',
        startSeconds: 12.25,
        endSeconds: 18.5,
        intervalSeconds: 0.25,
        runOcr: true,
      }),
      expect.objectContaining({
        kind: 'asr_retranscribe',
        status: 'demo-preview',
        source: 'demo-memory',
        languageHints: ['zh-CN', 'en'],
      }),
    ])
    expect(useStudioStore.getState().notice).toContain('演示不执行模型')
  })

  it('applies demo manual correction in memory while preserving evidence provenance fields', () => {
    const taskId = 'task-complete'
    const beforeTask = useStudioStore
      .getState()
      .tasks.find(task => task.id === taskId)!
    const before = beforeTask.evidence[0]

    useStudioStore.getState().correctEvidence(taskId, {
      evidenceId: before.id,
      newText: '  人工确认后的证据文本  ',
      reason: '  专有名词修正  ',
    })

    const afterTask = useStudioStore
      .getState()
      .tasks.find(task => task.id === taskId)!
    const after = afterTask.evidence.find(item => item.id === before.id)!
    expect(after).toMatchObject({
      id: before.id,
      rawText: '人工确认后的证据文本',
      provider: 'user/demo-memory',
      startSeconds: before.startSeconds,
      endSeconds: before.endSeconds,
      confidence: before.confidence,
    })
    expect(afterTask.note).toEqual(beforeTask.note)
    expect(afterTask.operations.at(-1)).toMatchObject({
      kind: 'evidence_correct',
      status: 'completed',
      source: 'demo-memory',
      evidenceId: before.id,
      newText: '人工确认后的证据文本',
      reason: '专有名词修正',
    })
    expect(useStudioStore.getState().notice).toContain('报告尚未重生成')
  })

  it('rejects unsafe rework intervals and fixed sampling budgets before execution', () => {
    const taskId = 'task-complete'
    const initialCount =
      useStudioStore.getState().tasks.find(task => task.id === taskId)?.operations
        .length ?? 0

    useStudioStore.getState().runVisionRework(taskId, {
      startSeconds: 0,
      endSeconds: 10,
      mode: 'fixed_interval',
      intervalSeconds: 0.099,
      runOcr: false,
    })
    expect(useStudioStore.getState().notice).toContain('不能小于 0.1 秒')

    useStudioStore.getState().runVisionRework(taskId, {
      startSeconds: 0,
      endSeconds: 600.1,
      mode: 'fixed_interval',
      intervalSeconds: 0.1,
      runOcr: false,
    })
    expect(useStudioStore.getState().notice).toContain('5000')
    expect(
      useStudioStore.getState().tasks.find(task => task.id === taskId)?.operations,
    ).toHaveLength(initialCount)
  })

  it('creates an honestly labelled in-memory report revision in demo mode', async () => {
    const taskId = 'task-complete'
    const before = useStudioStore
      .getState()
      .tasks.find(task => task.id === taskId)!

    await useStudioStore.getState().generateReportRevision(taskId, {
      preset: 'executive',
      includeScreenshots: false,
      includePdf: true,
    })

    const after = useStudioStore
      .getState()
      .tasks.find(task => task.id === taskId)!
    expect(after.note).toEqual(before.note)
    expect(after.reportRevisions).toHaveLength(
      (before.reportRevisions?.length ?? 0) + 1,
    )
    expect(after.reportRevisions?.[0]).toMatchObject({
      preset: 'executive',
      formats: ['markdown', 'html', 'pdf'],
      source: 'demo-memory',
      artifactPaths: {},
      warnings: [expect.stringContaining('未调用 LLM')],
    })
    expect(useStudioStore.getState().notice).toContain('演示报告 revision')
  })

  it('rejects a role binding when the model lacks capabilities', () => {
    const store = useStudioStore.getState()
    const before = store.roles.find(role => role.id === 'asr.primary')?.modelId

    store.bindRole('asr.primary', 'model-text-local')

    const state = useStudioStore.getState()
    expect(state.roles.find(role => role.id === 'asr.primary')?.modelId).toBe(before)
    expect(state.notice).toContain('能力')
  })

  it('exposes a visible provider test lifecycle', () => {
    vi.useFakeTimers()
    const store = useStudioStore.getState()
    store.testProvider('provider-openai')
    expect(
      useStudioStore.getState().providers.find(provider => provider.id === 'provider-openai')
        ?.status,
    ).toBe('testing')

    vi.advanceTimersByTime(700)
    expect(
      useStudioStore.getState().providers.find(provider => provider.id === 'provider-openai')
        ?.status,
    ).toBe('connected')
    vi.useRealTimers()
  })
})

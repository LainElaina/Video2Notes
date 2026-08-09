import heroEvidenceTimeline from './visuals/hero-evidence-timeline.webp'
import sourceBilibili from './visuals/source-bilibili.webp'
import sourceYoutube from './visuals/source-youtube.webp'
import sourceX from './visuals/source-x.webp'
import sourceLocal from './visuals/source-local.webp'
import scopeAudioVisual from './visuals/scope-audio-visual.webp'
import scopeAudioOnly from './visuals/scope-audio-only.webp'
import modeFast from './visuals/mode-fast.webp'
import modeBalanced from './visuals/mode-balanced.webp'
import modeAccurate from './visuals/mode-accurate.webp'
import samplingAdaptive from './visuals/sampling-adaptive.webp'
import samplingFixed from './visuals/sampling-fixed.webp'
import samplingSkip from './visuals/sampling-skip.webp'
import reportConcise from './visuals/report-concise.webp'
import reportDetailed from './visuals/report-detailed.webp'
import reportProfessional from './visuals/report-professional.webp'
import reportBeginner from './visuals/report-beginner.webp'
import reportExecutive from './visuals/report-executive.webp'
import demoTimeline from './visuals/demo-timeline.webp'
import demoRework from './visuals/demo-rework.webp'
import demoEvidenceSpan from './visuals/demo-evidence-span.webp'
import demoNoteDocument from './visuals/demo-note-document.webp'
import emptyTasks from './visuals/empty-tasks.webp'
import emptyReader from './visuals/empty-reader.webp'
import emptyProvider from './visuals/empty-provider.webp'
import emptyRuntime from './visuals/empty-runtime.webp'
import emptyMaterials from './visuals/empty-materials.webp'
import emptyReportHistory from './visuals/empty-report-history.webp'
import emptyProcessingLog from './visuals/empty-processing-log.webp'
import settingsWorkbench from './visuals/settings-workbench.webp'

export const visualAssets = {
  heroEvidenceTimeline,
  sourceBilibili,
  sourceYoutube,
  sourceX,
  sourceLocal,
  scopeAudioVisual,
  scopeAudioOnly,
  modeFast,
  modeBalanced,
  modeAccurate,
  samplingAdaptive,
  samplingFixed,
  samplingSkip,
  reportConcise,
  reportDetailed,
  reportProfessional,
  reportBeginner,
  reportExecutive,
  demoTimeline,
  demoRework,
  demoEvidenceSpan,
  demoNoteDocument,
  emptyTasks,
  emptyReader,
  emptyProvider,
  emptyRuntime,
  emptyMaterials,
  emptyReportHistory,
  emptyProcessingLog,
  settingsWorkbench,
} as const

export type VisualAssetKey = keyof typeof visualAssets


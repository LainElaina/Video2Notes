import { useId, useState } from 'react'
import {
  ChevronDown,
  CircleGauge,
  Cpu,
  Database,
  Gauge,
  MemoryStick,
} from 'lucide-react'
import type { ResourceBudget, ResourceReserve } from '../domain'
import { useI18n } from '../i18n'
import { useStudioStore } from '../store'
import { MotionPresence } from './MotionPresence'

type Localize = (zh: string, en: string) => string
type BenchmarkRow = readonly [string, string, string, string]

const measuredRows = (text: Localize): readonly BenchmarkRow[] => [
  [text('完整处理时间（worker；PDF 另计）', 'Full processing time (worker; PDF excluded)'), text('526.994 秒', '526.994 seconds'), text('1662.287 秒', '1662.287 seconds'), text('3013.919 秒', '3013.919 seconds')],
  [text('换算', 'Converted duration'), '8:46.994', '27:42.287', '50:13.919'],
  [text('相对视频时长', 'Relative to video duration'), '1.17×', '3.68×', '6.66×'],
  [text('同机总耗时指数（Accurate = 100）', 'Same-machine total-time index (Accurate = 100)'), '17.5', '55.2', '100'],
  [text('视觉扫描', 'Visual scan'), text('50.513 秒', '50.513 seconds'), text('129.111 秒', '129.111 seconds'), text('568.793 秒', '568.793 seconds')],
  ['CUDA ASR', text('23.831 秒', '23.831 seconds'), text('34.969 秒', '34.969 seconds'), text('79.113 秒', '79.113 seconds')],
  ['CPU OCR', text('449.710 秒', '449.710 seconds'), text('1493.339 秒', '1493.339 seconds'), text('2358.735 秒', '2358.735 seconds')],
  [text('视觉状态', 'Visual states'), '88', '115', '213'],
  [text('OCR 接受行', 'OCR accepted lines'), '1,362', '3,198', '5,847'],
  [text('OCR 合并证据', 'OCR merged evidence'), '1,014', '2,355', '4,383'],
  [text('不重复规范化 OCR 文本', 'Unique normalized OCR text'), '769', '1,595', '2,607'],
]

const stageShareRows = (text: Localize): readonly BenchmarkRow[] => [
  [text('视觉扫描 / 完整耗时', 'Visual scan / full time'), '9.6%', '7.8%', '18.9%'],
  ['CUDA ASR / ' + text('完整耗时', 'full time'), '4.5%', '2.1%', '2.6%'],
  ['CPU OCR / ' + text('完整耗时', 'full time'), '85.3%', '89.8%', '78.3%'],
]

const audioOnlyCounterfactualRows = (text: Localize): readonly BenchmarkRow[] => [
  [text('保留阶段反推耗时', 'Inferred time for retained stages'), text('26.771 秒', '26.771 seconds'), text('39.837 秒', '39.837 seconds'), text('86.391 秒', '86.391 seconds')],
  [text('相对视频时长', 'Relative to video duration'), '0.0592×', '0.0881×', '0.1910×'],
  [text('相对完整音画可跳过时间', 'Time avoidable versus full audio + video'), '94.92%', '97.60%', '97.13%'],
]

const telemetryRows = (text: Localize): readonly BenchmarkRow[] => [
  [text('进程树 CPU 平均 / 峰值', 'Process-tree CPU average / peak'), '11.5% / 13.9%', '11.1% / 24.6%', '8.0% / 10.5%'],
  [
    text('进程树内存平均 / 峰值（整机占比）', 'Process-tree memory average / peak (share of system)'),
    '1.17 / 1.49 GiB · 1.25% / 1.59%',
    '1.29 / 1.89 GiB · 1.37% / 2.02%',
    '1.13 / 1.88 GiB · 1.21% / 2.01%',
  ],
  [text('整卡 GPU 平均：基线 → 运行', 'Whole-GPU average: baseline → run'), '20.2% → 24.4%', '21.8% → 45.3%', '55.8% → 46.6%'],
  [text('整卡 GPU 峰值：基线 → 运行', 'Whole-GPU peak: baseline → run'), '21.0% → 76.0%', '22.0% → 82.0%', '57.0% → 91.0%'],
  [
    text('整卡显存平均：基线 → 运行（占卡）', 'Whole-GPU VRAM average: baseline → run (share of card)'),
    '8.29 → 11.27 GiB · 47.2%',
    '11.15 → 15.07 GiB · 63.1%',
    '16.00 → 14.71 GiB · 61.6%',
  ],
  [
    text('整卡显存峰值：基线 → 运行（占卡）', 'Whole-GPU VRAM peak: baseline → run (share of card)'),
    '8.29 → 12.58 GiB · 52.7%',
    '11.16 → 17.60 GiB · 73.7%',
    '16.06 → 18.13 GiB · 75.9%',
  ],
]

const percentage = (ratio: number): string => `${Math.round(ratio * 100)}%`

const gibibytes = (bytes?: number): string =>
  bytes === undefined ? '—' : `${(bytes / 1024 ** 3).toFixed(bytes >= 10 * 1024 ** 3 ? 1 : 2)} GiB`

const budgetShare = (budget?: number, available?: number): string | undefined => {
  if (budget === undefined || available === undefined || available <= 0) return undefined
  return percentage(budget / available)
}

function CurrentMachineBudget({
  budget,
  reserve,
  demo,
  text,
}: {
  budget?: ResourceBudget
  reserve?: ResourceReserve
  demo: boolean
  text: Localize
}) {
  if (!budget) {
    return (
      <div className="benchmark-budget-empty" role="status">
        <CircleGauge size={18} aria-hidden="true" />
        <span>
          <strong>{text('本机资源报告尚未就绪', 'Machine resource report is not ready')}</strong>
          <small>{text('连接本地后端后，这里会显示当前余量和规划器预算。', 'Connect to the local backend to see current headroom and the planner budget.')}</small>
        </span>
      </div>
    )
  }

  const cpuShare = budgetShare(
    budget.cpuBudgetEquivalent,
    budget.cpuAvailableEquivalent,
  )
  const memoryShare = budgetShare(budget.memoryBudgetBytes, budget.memoryAvailableBytes)
  const vramShare = budgetShare(budget.vramBudgetBytes, budget.vramAvailableBytes)

  return (
    <div className="benchmark-budget-block">
      <div className="benchmark-budget-heading">
        <div>
          <span className="section-kicker">{text('实时资源预算', 'LIVE RESOURCE BUDGET')}</span>
          <h5>{demo ? text('演示资源预算', 'Demo resource budget') : text('当前本机规划预算', 'Current machine planner budget')}</h5>
        </div>
        <span>{budget.gpuName ?? text('未发现独立 GPU', 'No discrete GPU detected')}</span>
      </div>
      <div className="benchmark-budget-grid">
        <div>
          <Cpu size={16} aria-hidden="true" />
          <span>{text('CPU 预算', 'CPU budget')}</span>
          <strong>
            {budget.cpuBudgetEquivalent.toFixed(1)} /{' '}
            {budget.cpuAvailableEquivalent.toFixed(1)} {text('等效核心', 'equivalent cores')}
          </strong>
          <small>{cpuShare ? text(`约占报告可用池 ${cpuShare}`, `About ${cpuShare} of the reported available pool`) : text('可用池比例未知', 'Available-pool share unknown')}</small>
        </div>
        <div>
          <MemoryStick size={16} aria-hidden="true" />
          <span>{text('内存预算', 'Memory budget')}</span>
          <strong>
            {gibibytes(budget.memoryBudgetBytes)} / {gibibytes(budget.memoryAvailableBytes)}
          </strong>
          <small>{memoryShare ? text(`约占报告可用内存 ${memoryShare}`, `About ${memoryShare} of reported available memory`) : text('可用内存比例未知', 'Available-memory share unknown')}</small>
        </div>
        <div>
          <Gauge size={16} aria-hidden="true" />
          <span>{text('GPU 计算预算', 'GPU compute budget')}</span>
          <strong>
            {budget.gpuComputeBudgetRatio === undefined
              ? text('未报告', 'Not reported')
              : `${percentage(budget.gpuComputeBudgetRatio)} / ${text('整卡', 'whole GPU')}`}
          </strong>
          <small>
            {budget.gpuComputeAvailableRatio === undefined
              ? text('计算余量未知', 'Compute headroom unknown')
              : text(`报告可用比例约 ${percentage(budget.gpuComputeAvailableRatio)}`, `Reported available share is about ${percentage(budget.gpuComputeAvailableRatio)}`)}
          </small>
        </div>
        <div>
          <Database size={16} aria-hidden="true" />
          <span>{text('显存预算', 'VRAM budget')}</span>
          <strong>
            {gibibytes(budget.vramBudgetBytes)} / {gibibytes(budget.vramAvailableBytes)}
          </strong>
          <small>{vramShare ? text(`约占报告可用显存 ${vramShare}`, `About ${vramShare} of reported available VRAM`) : text('可用显存比例未知', 'Available-VRAM share unknown')}</small>
        </div>
      </div>
      {reserve && (
        <p className="benchmark-reserve-note">
          {text(
            `规划器从已报告的可用池中按用户设置继续保护：CPU ${percentage(reserve.cpuReserveRatio)}、内存 ${percentage(reserve.memoryReserveRatio)}、GPU ${percentage(reserve.gpuReserveRatio)}、显存 ${percentage(reserve.vramReserveRatio)}，并叠加固定保留下限与安全系数。遥测缺失时可用池可能按总容量兼容估算；这些预算是允许上限，不是实时利用率或任务必然占用值。`,
            `The planner applies the user’s additional reserves to the reported available pool: CPU ${percentage(reserve.cpuReserveRatio)}, memory ${percentage(reserve.memoryReserveRatio)}, GPU ${percentage(reserve.gpuReserveRatio)}, and VRAM ${percentage(reserve.vramReserveRatio)}, plus fixed reserve floors and safety factors. When telemetry is missing, the available pool may be estimated from total capacity. These budgets are allowed ceilings, not live utilization or guaranteed task consumption.`,
          )}
        </p>
      )}
    </div>
  )
}

function BenchmarkTable({
  caption,
  rows,
  text,
}: {
  caption: string
  rows: ReadonlyArray<readonly [string, string, string, string]>
  text: Localize
}) {
  return (
    <div className="benchmark-table-scroll" tabIndex={0} aria-label={text(`${caption}，可横向滚动`, `${caption}; horizontally scrollable`)}>
      <table className="benchmark-table">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            <th scope="col">{text('指标', 'Metric')}</th>
            <th scope="col">{text('快速 · Fast', 'Fast')}</th>
            <th scope="col">{text('均衡 · Balanced', 'Balanced')}</th>
            <th scope="col">{text('精确 · Accurate · CUDA', 'Accurate · CUDA')}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(row => (
            <tr key={row[0]}>
              <th scope="row">{row[0]}</th>
              <td>{row[1]}</td>
              <td>{row[2]}</td>
              <td>{row[3]}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function ProcessingBenchmarkGuide() {
  const { text } = useI18n()
  const [expanded, setExpanded] = useState(false)
  const panelId = useId()
  const backend = useStudioStore(state => state.backend)
  const report = useStudioStore(state => state.systemReport)
  const budget = report?.recommendation.budget
  const reserve = report?.performance.reserve ?? report?.recommendation.reserve

  return (
    <section className={`benchmark-guide ${expanded ? 'is-expanded' : ''}`}>
      <button
        className="benchmark-guide-trigger"
        type="button"
        aria-expanded={expanded}
        aria-controls={panelId}
        onClick={() => setExpanded(value => !value)}
      >
        <span className="benchmark-guide-mark" aria-hidden="true">
          <CircleGauge size={19} />
        </span>
        <span className="benchmark-guide-copy">
          <strong>{text('实测档位与性能说明', 'Measured profiles and performance notes')}</strong>
          <small>{text('完整 7:32 视频 · CUDA ASR + CPU OCR · 跨会话校正基线', 'Full 7:32 video · CUDA ASR + CPU OCR · cross-session corrected baseline')}</small>
        </span>
        <span className="benchmark-guide-ratios" aria-hidden="true">
          <span>1.17×</span>
          <span>3.68×</span>
          <span>6.66×</span>
        </span>
        <ChevronDown className="benchmark-guide-chevron" size={17} aria-hidden="true" />
      </button>

      <MotionPresence
        show={expanded}
        className="motion-presence-benchmark"
        exitMs={140}
      >
        {expanded && (
          <div className="benchmark-guide-collapse">
            <div className="benchmark-guide-overflow">
              <div className="benchmark-guide-content" id={panelId}>
            <div className="benchmark-guide-intro">
              <div>
                <span className="section-kicker">BV12hsEz3ELL · {text('完整运行', 'FULL RUN')}</span>
                <h4>{text('同一视频与模型的三档校正实测', 'Corrected measurements for three profiles on the same video and models')}</h4>
                <p>
                  {text(
                    '基准机为 Ryzen 9 9950X3D、93.61 GiB 内存和 RTX 5090 D v2 23.88 GiB。三档使用相同 faster-whisper-small 与 PP-OCRv5 mobile。Fast / Balanced 来自原串行会话的 responsive 资源策略；Accurate 是原轮受实时 GPU 负载安全回退后，以 throughput 策略单独重跑的校正值，三档均使用 50% CPU 上限（16 线程）。因而这是跨会话对比，不能把档位差异解释成大模型升级。',
                    'The benchmark machine used a Ryzen 9 9950X3D, 93.61 GiB of memory, and an RTX 5090 D v2 with 23.88 GiB. All profiles used the same faster-whisper-small and PP-OCRv5 mobile models. Fast and Balanced came from the original serial session using the responsive resource policy. Accurate is a corrected standalone rerun using the throughput policy after the original run safely fell back under live GPU load. All profiles used a 50% CPU limit (16 threads). This is therefore a cross-session comparison; profile differences must not be interpreted as a larger-model upgrade.',
                  )}
                </p>
              </div>
              <span className="benchmark-reference-chip">{text('2026-08-02 · 452.279s · worker 无 PDF', '2026-08-02 · 452.279s · worker without PDF')}</span>
            </div>

            <BenchmarkTable caption={text('完整视频三档处理指标', 'Three-profile full-video processing metrics')} rows={measuredRows(text)} text={text} />

            <div className="benchmark-explanation-grid">
              <section aria-labelledby={`${panelId}-stage-share`}>
                <div className="benchmark-subheading">
                  <h5 id={`${panelId}-stage-share`}>{text('时间花在哪里', 'Where the time goes')}</h5>
                  <span>{text('阶段耗时 ÷ 完整处理时间', 'Stage time ÷ full processing time')}</span>
                </div>
                <BenchmarkTable caption={text('三档阶段耗时占比', 'Stage-time share for three profiles')} rows={stageShareRows(text)} text={text} />
                <p>
                  {text('这是墙钟时间占比，不是 CPU/GPU 利用率。完整基准中 OCR 仍在 CPU，因而是三档首要瓶颈；正式版现已支持 GPU OCR，但不能用短片 POC 倒推这组完整视频耗时。', 'These are shares of wall-clock time, not CPU/GPU utilization. OCR still ran on CPU in the full benchmark and was therefore the main bottleneck for all profiles. The release build now supports GPU OCR, but a short-video proof of concept cannot be used to extrapolate these full-video times.')}
                </p>
              </section>
              <section aria-labelledby={`${panelId}-telemetry`}>
                <div className="benchmark-subheading">
                  <h5 id={`${panelId}-telemetry`}>{text('基准机遥测', 'Benchmark-machine telemetry')}</h5>
                  <span>{text('平均 / 峰值与运行前基线', 'Average / peak and pre-run baseline')}</span>
                </div>
                <BenchmarkTable caption={text('三档基准机资源遥测', 'Resource telemetry for three profiles')} rows={telemetryRows(text)} text={text} />
                <p>
                  {text('CPU 与 RSS 是进程树读数；GPU 与显存是整张显卡读数，包含桌面和其他进程，不能归因成 Video2Notes 独占。它们也不是最低硬件要求或识别准确率。', 'CPU and RSS are process-tree readings. GPU and VRAM readings cover the whole graphics card, including the desktop and other processes, and cannot be attributed exclusively to Video2Notes. They are neither minimum hardware requirements nor recognition accuracy figures.')}
                </p>
              </section>
              <section aria-labelledby={`${panelId}-audio-counterfactual`}>
                <div className="benchmark-subheading">
                  <h5 id={`${panelId}-audio-counterfactual`}>{text('仅音频历史反事实', 'Historical audio-only counterfactual')}</h5>
                  <span>{text('不是一次仅音频实测，也不是速度承诺', 'Not an audio-only measurement or a speed promise')}</span>
                </div>
                <BenchmarkTable
                  caption={text('三档仅音频历史反事实', 'Audio-only historical counterfactual for three profiles')}
                  rows={audioOnlyCounterfactualRows(text)}
                  text={text}
                />
                <p>
                  {text('仅用本次完整 run 的“总耗时 − 视觉扫描 − CPU OCR”反推保留阶段，因此能直观看出为什么跳过画面可能大幅省时。它仍受当时外部负载影响，并沿用同一个 small ASR；没有计入换用 large-v3、secondary ASR 或额外仲裁的成本。当前本机 API 的完整音画区间已用这次 CPU-OCR 实测作保守校准；仅音频仍是独立工程宽区间，完成更多本机任务后还需继续校准。', 'The retained stages are inferred only as “total time − visual scan − CPU OCR” from this full run, making it easy to see why skipping visuals may save substantial time. The estimate still reflects external load at the time and uses the same small ASR model; it does not include the cost of large-v3, secondary ASR, or additional arbitration. The local API’s full audio + video ranges are conservatively calibrated with this CPU-OCR measurement. Audio-only remains a separate broad engineering range that needs further calibration after more local tasks.')}
                </p>
              </section>
            </div>

              <CurrentMachineBudget
                budget={budget}
                reserve={reserve}
                demo={backend.mode === 'demo'}
                text={text}
              />

            <div className="benchmark-truth-note">
              <CircleGauge size={16} aria-hidden="true" />
              <p>
                <strong>{text('如何用于选档：', 'How to choose a profile:')}</strong>{' '}
                {text('Fast 适合快速索引，Balanced 是屏幕教程的默认平衡点，Accurate 主要增加瞬时界面和小字召回。没有人工逐字稿与关键帧真值时，不能把证据数量或跨档一致性称为准确率。表中“完整处理时间”是受保护 worker 墙钟时间，包含流水线 Markdown / HTML 渲染，但本轮 worker 设置为不生成 PDF；后续补导出的 PDF 不计入这三项耗时。', 'Fast is suitable for quick indexing, Balanced is the default tradeoff for screen tutorials, and Accurate mainly improves recall of transient interfaces and small text. Without a human transcript and keyframe ground truth, evidence counts or cross-profile consistency cannot be called accuracy. “Full processing time” is protected worker wall-clock time and includes pipeline Markdown / HTML rendering, but the worker was configured not to generate PDF in these runs. PDFs exported later are not included in these times.')}
              </p>
            </div>
              </div>
            </div>
          </div>
        )}
      </MotionPresence>
    </section>
  )
}

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
import { useStudioStore } from '../store'
import { MotionPresence } from './MotionPresence'

const measuredRows = [
  ['完整处理时间（worker；PDF 另计）', '526.994 秒', '1662.287 秒', '3013.919 秒'],
  ['换算', '8:46.994', '27:42.287', '50:13.919'],
  ['相对视频时长', '1.17×', '3.68×', '6.66×'],
  ['同机总耗时指数（Accurate = 100）', '17.5', '55.2', '100'],
  ['视觉扫描', '50.513 秒', '129.111 秒', '568.793 秒'],
  ['CUDA ASR', '23.831 秒', '34.969 秒', '79.113 秒'],
  ['CPU OCR', '449.710 秒', '1493.339 秒', '2358.735 秒'],
  ['视觉状态', '88', '115', '213'],
  ['OCR 接受行', '1,362', '3,198', '5,847'],
  ['OCR 合并证据', '1,014', '2,355', '4,383'],
  ['不重复规范化 OCR 文本', '769', '1,595', '2,607'],
] as const

const stageShareRows = [
  ['视觉扫描 / 完整耗时', '9.6%', '7.8%', '18.9%'],
  ['CUDA ASR / 完整耗时', '4.5%', '2.1%', '2.6%'],
  ['CPU OCR / 完整耗时', '85.3%', '89.8%', '78.3%'],
] as const

const audioOnlyCounterfactualRows = [
  ['保留阶段反推耗时', '26.771 秒', '39.837 秒', '86.391 秒'],
  ['相对视频时长', '0.0592×', '0.0881×', '0.1910×'],
  ['相对完整音画可跳过时间', '94.92%', '97.60%', '97.13%'],
] as const

const telemetryRows = [
  ['进程树 CPU 平均 / 峰值', '11.5% / 13.9%', '11.1% / 24.6%', '8.0% / 10.5%'],
  [
    '进程树内存平均 / 峰值（整机占比）',
    '1.17 / 1.49 GiB · 1.25% / 1.59%',
    '1.29 / 1.89 GiB · 1.37% / 2.02%',
    '1.13 / 1.88 GiB · 1.21% / 2.01%',
  ],
  ['整卡 GPU 平均：基线 → 运行', '20.2% → 24.4%', '21.8% → 45.3%', '55.8% → 46.6%'],
  ['整卡 GPU 峰值：基线 → 运行', '21.0% → 76.0%', '22.0% → 82.0%', '57.0% → 91.0%'],
  [
    '整卡显存平均：基线 → 运行（占卡）',
    '8.29 → 11.27 GiB · 47.2%',
    '11.15 → 15.07 GiB · 63.1%',
    '16.00 → 14.71 GiB · 61.6%',
  ],
  [
    '整卡显存峰值：基线 → 运行（占卡）',
    '8.29 → 12.58 GiB · 52.7%',
    '11.16 → 17.60 GiB · 73.7%',
    '16.06 → 18.13 GiB · 75.9%',
  ],
] as const

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
}: {
  budget?: ResourceBudget
  reserve?: ResourceReserve
  demo: boolean
}) {
  if (!budget) {
    return (
      <div className="benchmark-budget-empty" role="status">
        <CircleGauge size={18} aria-hidden="true" />
        <span>
          <strong>本机资源报告尚未就绪</strong>
          <small>连接本地后端后，这里会显示当前余量和规划器预算。</small>
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
          <span className="section-kicker">LIVE RESOURCE BUDGET</span>
          <h5>{demo ? '演示资源预算' : '当前本机规划预算'}</h5>
        </div>
        <span>{budget.gpuName ?? '未发现独立 GPU'}</span>
      </div>
      <div className="benchmark-budget-grid">
        <div>
          <Cpu size={16} aria-hidden="true" />
          <span>CPU 预算</span>
          <strong>
            {budget.cpuBudgetEquivalent.toFixed(1)} /{' '}
            {budget.cpuAvailableEquivalent.toFixed(1)} 等效核心
          </strong>
          <small>{cpuShare ? `约占报告可用池 ${cpuShare}` : '可用池比例未知'}</small>
        </div>
        <div>
          <MemoryStick size={16} aria-hidden="true" />
          <span>内存预算</span>
          <strong>
            {gibibytes(budget.memoryBudgetBytes)} / {gibibytes(budget.memoryAvailableBytes)}
          </strong>
          <small>{memoryShare ? `约占报告可用内存 ${memoryShare}` : '可用内存比例未知'}</small>
        </div>
        <div>
          <Gauge size={16} aria-hidden="true" />
          <span>GPU 计算预算</span>
          <strong>
            {budget.gpuComputeBudgetRatio === undefined
              ? '未报告'
              : `${percentage(budget.gpuComputeBudgetRatio)} / 整卡`}
          </strong>
          <small>
            {budget.gpuComputeAvailableRatio === undefined
              ? '计算余量未知'
              : `报告可用比例约 ${percentage(budget.gpuComputeAvailableRatio)}`}
          </small>
        </div>
        <div>
          <Database size={16} aria-hidden="true" />
          <span>显存预算</span>
          <strong>
            {gibibytes(budget.vramBudgetBytes)} / {gibibytes(budget.vramAvailableBytes)}
          </strong>
          <small>{vramShare ? `约占报告可用显存 ${vramShare}` : '可用显存比例未知'}</small>
        </div>
      </div>
      {reserve && (
        <p className="benchmark-reserve-note">
          规划器从已报告的可用池中按用户设置继续保护：CPU {percentage(reserve.cpuReserveRatio)}、内存{' '}
          {percentage(reserve.memoryReserveRatio)}、GPU {percentage(reserve.gpuReserveRatio)}、显存{' '}
          {percentage(reserve.vramReserveRatio)}，并叠加固定保留下限与安全系数。遥测缺失时可用池可能按总容量兼容估算；这些预算是允许上限，不是实时利用率或任务必然占用值。
        </p>
      )}
    </div>
  )
}

function BenchmarkTable({
  caption,
  rows,
}: {
  caption: string
  rows: ReadonlyArray<readonly [string, string, string, string]>
}) {
  return (
    <div className="benchmark-table-scroll" tabIndex={0} aria-label={`${caption}，可横向滚动`}>
      <table className="benchmark-table">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            <th scope="col">指标</th>
            <th scope="col">Fast</th>
            <th scope="col">Balanced</th>
            <th scope="col">Accurate · CUDA</th>
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
          <strong>实测档位与性能说明</strong>
          <small>完整 7:32 视频 · CUDA ASR + CPU OCR · 跨会话校正基线</small>
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
                <span className="section-kicker">BV12hsEz3ELL · FULL RUN</span>
                <h4>同一视频与模型的三档校正实测</h4>
                <p>
                  基准机为 Ryzen 9 9950X3D、93.61 GiB 内存和 RTX 5090 D v2 23.88 GiB。
                  三档使用相同 faster-whisper-small 与 PP-OCRv5 mobile。Fast / Balanced 来自原串行会话的
                  responsive 资源策略；Accurate 是原轮受实时 GPU 负载安全回退后，以 throughput 策略单独重跑的校正值，
                  三档均使用 50% CPU 上限（16 线程）。因而这是跨会话对比，不能把档位差异解释成大模型升级。
                </p>
              </div>
              <span className="benchmark-reference-chip">2026-08-02 · 452.279s · worker 无 PDF</span>
            </div>

            <BenchmarkTable caption="完整视频三档处理指标" rows={measuredRows} />

            <div className="benchmark-explanation-grid">
              <section aria-labelledby={`${panelId}-stage-share`}>
                <div className="benchmark-subheading">
                  <h5 id={`${panelId}-stage-share`}>时间花在哪里</h5>
                  <span>阶段耗时 ÷ 完整处理时间</span>
                </div>
                <BenchmarkTable caption="三档阶段耗时占比" rows={stageShareRows} />
                <p>
                  这是墙钟时间占比，不是 CPU/GPU 利用率。完整基准中 OCR 仍在 CPU，因而是三档首要瓶颈；
                  正式版现已支持 GPU OCR，但不能用短片 POC 倒推这组完整视频耗时。
                </p>
              </section>
              <section aria-labelledby={`${panelId}-telemetry`}>
                <div className="benchmark-subheading">
                  <h5 id={`${panelId}-telemetry`}>基准机遥测</h5>
                  <span>平均 / 峰值与运行前基线</span>
                </div>
                <BenchmarkTable caption="三档基准机资源遥测" rows={telemetryRows} />
                <p>
                  CPU 与 RSS 是进程树读数；GPU 与显存是整张显卡读数，包含桌面和其他进程，不能归因成
                  Video2Notes 独占。它们也不是最低硬件要求或识别准确率。
                </p>
              </section>
              <section aria-labelledby={`${panelId}-audio-counterfactual`}>
                <div className="benchmark-subheading">
                  <h5 id={`${panelId}-audio-counterfactual`}>仅音频历史反事实</h5>
                  <span>不是一次仅音频实测，也不是速度承诺</span>
                </div>
                <BenchmarkTable
                  caption="三档仅音频历史反事实"
                  rows={audioOnlyCounterfactualRows}
                />
                <p>
                  仅用本次完整 run 的“总耗时 − 视觉扫描 − CPU OCR”反推保留阶段，因此能直观看出为什么跳过画面可能大幅省时。
                  它仍受当时外部负载影响，并沿用同一个 small ASR；没有计入换用 large-v3、secondary ASR 或额外仲裁的成本。
                  当前本机 API 的完整音画区间已用这次 CPU-OCR 实测作保守校准；仅音频仍是独立工程宽区间，完成更多本机任务后还需继续校准。
                </p>
              </section>
            </div>

            <CurrentMachineBudget
              budget={budget}
              reserve={reserve}
              demo={backend.mode === 'demo'}
            />

            <div className="benchmark-truth-note">
              <CircleGauge size={16} aria-hidden="true" />
              <p>
                <strong>如何用于选档：</strong>Fast 适合快速索引，Balanced 是屏幕教程的默认平衡点，
                Accurate 主要增加瞬时界面和小字召回。没有人工逐字稿与关键帧真值时，不能把证据数量或跨档一致性称为准确率。
                表中“完整处理时间”是受保护 worker 墙钟时间，包含流水线 Markdown / HTML 渲染，但本轮 worker 设置为不生成 PDF；后续补导出的 PDF 不计入这三项耗时。
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

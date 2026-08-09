import { useEffect, useState, type ReactNode } from 'react'
import {
  Activity,
  BookOpenText,
  Bot,
  Check,
  ChevronRight,
  CircleDot,
  Clock3,
  Cpu,
  Palette,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Search,
  X,
} from 'lucide-react'
import type { ViewId } from '../domain'
import { formatTime, platformLabel, statusLabel } from '../domain'
import { preferredScrollBehavior } from '../motion'
import { useStudioStore } from '../store'
import { useUiPreferences, type ThemePreset } from '../stores/uiPreferences'
import { MotionPresence } from './MotionPresence'

const navItems: Array<{
  view: ViewId
  label: string
  icon: typeof Plus
}> = [
  { view: 'create', label: '新建任务', icon: Plus },
  { view: 'tasks', label: '任务运行', icon: Activity },
  { view: 'reader', label: '笔记阅读', icon: BookOpenText },
  { view: 'models', label: '模型设置', icon: Bot },
]

function BrandMark() {
  return (
    <div className="brand-mark" aria-hidden="true">
      <span className="brand-frame brand-frame-back" />
      <span className="brand-frame brand-frame-front" />
      <span className="brand-playhead" />
    </div>
  )
}

const themeOptions: Array<{ value: ThemePreset; label: string }> = [
  { value: 'precision-light', label: '矿物玻璃' },
  { value: 'paper-light', label: '纸张亮色' },
  { value: 'studio-graphite', label: '工作室石墨' },
]

function TopNavigation() {
  const view = useStudioStore(state => state.view)
  const navigate = useStudioStore(state => state.navigate)
  const backend = useStudioStore(state => state.backend)
  const workspaceMode = useUiPreferences(state => state.workspaceMode)
  const setWorkspaceMode = useUiPreferences(state => state.setWorkspaceMode)
  const themePreset = useUiPreferences(state => state.themePreset)
  const setThemePreset = useUiPreferences(state => state.setThemePreset)

  const backendLabel =
    backend.mode === 'real'
      ? `后端 ${backend.version ?? '正常'}`
      : backend.mode === 'demo'
        ? '演示模式'
        : backend.mode === 'connecting'
          ? '正在连接'
          : '后端离线'

  return (
    <header className="top-navigation">
      <button
        className="topbar-brand"
        type="button"
        onClick={() => navigate('create')}
        aria-label="返回新建任务"
      >
        <BrandMark />
        <span className="brand-copy" aria-hidden="true">
          <strong>Video2Notes</strong>
          <small>LOCAL ANALYSIS</small>
        </span>
      </button>
      <nav className="top-navigation-items" aria-label="主导航">
        {navItems.map(item => {
          const Icon = item.icon
          const selected = view === item.view
          return (
            <button
              className="top-navigation-button"
              type="button"
              key={item.view}
              aria-current={selected ? 'page' : undefined}
              aria-label={item.label}
              title={item.label}
              onClick={() => navigate(item.view)}
            >
              <Icon aria-hidden="true" size={19} />
              <span>{item.label}</span>
            </button>
          )
        })}
      </nav>
      <div className="top-navigation-controls">
        <div className="workspace-mode-switch" role="group" aria-label="界面工作模式">
          <button
            type="button"
            aria-label="简约视图"
            aria-pressed={workspaceMode === 'guided'}
            onClick={() => setWorkspaceMode('guided')}
          >
            简约视图
          </button>
          <button
            type="button"
            aria-label="数据工作室"
            aria-pressed={workspaceMode === 'professional'}
            onClick={() => setWorkspaceMode('professional')}
          >
            数据工作室
          </button>
        </div>
        <label className="theme-preset-control">
          <Palette size={14} aria-hidden="true" />
          <span className="sr-only">主题预置</span>
          <select
            value={themePreset}
            aria-label="主题预置"
            onChange={event => setThemePreset(event.currentTarget.value as ThemePreset)}
          >
            {themeOptions.map(option => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <div
          className={`topbar-backend backend-${backend.mode}`}
          role="group"
          aria-label={`后端状态：${backendLabel}`}
          title={backend.detail}
        >
          <CircleDot size={13} aria-hidden="true" />
          <span>{backendLabel}</span>
        </div>
      </div>
    </header>
  )
}

function TaskList() {
  const tasks = useStudioStore(state => state.tasks)
  const activeTaskId = useStudioStore(state => state.activeTaskId)
  const taskSearch = useStudioStore(state => state.taskSearch)
  const setTaskSearch = useStudioStore(state => state.setTaskSearch)
  const selectTask = useStudioStore(state => state.selectTask)

  const visibleTasks = tasks.filter(task =>
    `${task.source.title} ${task.source.author}`.toLowerCase().includes(taskSearch.toLowerCase()),
  )

  return (
    <>
      <label className="context-search">
        <Search size={15} aria-hidden="true" />
        <span className="sr-only">搜索任务</span>
        <input
          value={taskSearch}
          onChange={event => setTaskSearch(event.target.value)}
          placeholder="搜索标题或作者"
        />
      </label>
      <div className="context-task-list" aria-label="任务列表">
        {visibleTasks.map(task => (
          <article
            className={`context-task ${task.id === activeTaskId ? 'is-active' : ''}`}
            key={task.id}
          >
            <button
              type="button"
              className="context-task-main"
              onClick={() => selectTask(task.id, task.status === 'completed' ? 'reader' : 'tasks')}
            >
              <span className={`source-monogram source-${task.source.platform}`}>
                {platformLabel[task.source.platform].slice(0, 1)}
              </span>
              <span className="context-task-copy">
                <strong>{task.source.title}</strong>
                <span>
                  {statusLabel[task.status]} ·{' '}
                  {task.mode === 'fast'
                    ? 'Fast'
                    : task.mode === 'balanced'
                      ? 'Balanced'
                      : 'Accurate'}
                </span>
              </span>
              <ChevronRight size={15} aria-hidden="true" />
            </button>
            <div className="context-task-meta">
              <span>{task.createdAt}</span>
              <span>{Math.round(task.progress)}%</span>
            </div>
          </article>
        ))}
        {visibleTasks.length === 0 && (
          <div className="context-empty">
            <Search size={18} aria-hidden="true" />
            <p>没有匹配的任务</p>
          </div>
        )}
      </div>
    </>
  )
}

function ReaderContents() {
  const setCurrentTime = useStudioStore(state => state.setCurrentTime)
  const completedTask = useStudioStore(state => {
    const task = state.tasks.find(item => item.id === state.activeTaskId)
    return task?.note ? task : state.tasks.find(item => item.note)
  })
  const note = completedTask?.note

  if (!note) return <p className="context-helper">完成一个任务后，这里会显示笔记目录。</p>

  return (
    <div className="contents-list">
      <button
        type="button"
        onClick={() =>
          document
            .getElementById('note-overview')
            ?.scrollIntoView({ behavior: preferredScrollBehavior() })
        }
      >
        <span>摘要</span>
        <span>00:00</span>
      </button>
      {note.sections.map((section, index) => {
        const time = section.startSeconds ?? section.claims[0]?.timeSeconds ?? 0
        return (
          <button
            type="button"
            key={section.id}
            onClick={() => {
              setCurrentTime(time)
              document
                .getElementById(section.id)
                ?.scrollIntoView({ behavior: preferredScrollBehavior() })
            }}
          >
            <span>
              <small>{(index + 1).toString().padStart(2, '0')}</small>
              {section.title}
            </span>
            <span>{formatTime(time)}</span>
          </button>
        )
      })}
    </div>
  )
}

function ProviderList() {
  const providers = useStudioStore(state => state.providers)
  const selectedProviderId = useStudioStore(state => state.selectedProviderId)
  const selectProvider = useStudioStore(state => state.selectProvider)

  return (
    <div className="provider-context-list">
      {providers.map(provider => (
        <button
          type="button"
          key={provider.id}
          className={provider.id === selectedProviderId ? 'is-active' : ''}
          onClick={() => selectProvider(provider.id)}
        >
          <span className={`provider-dot status-${provider.status}`} aria-hidden="true" />
          <span>
            <strong>{provider.name}</strong>
            <small>{provider.endpoint}</small>
          </span>
          {provider.id === selectedProviderId && <Check size={14} aria-hidden="true" />}
        </button>
      ))}
    </div>
  )
}

function ContextPanel() {
  const view = useStudioStore(state => state.view)
  const runningTaskCount = useStudioStore(
    state => state.tasks.filter(task => task.status === 'running').length,
  )
  const toggleContext = useStudioStore(state => state.toggleContext)

  const metadata: Record<ViewId, { eyebrow: string; title: string; description: string }> = {
    create: {
      eyebrow: 'WORKBENCH',
      title: '最近任务',
      description: '来源、模式与上次运行结果',
    },
    tasks: {
      eyebrow: 'RUN QUEUE',
      title: '任务队列',
      description: `${runningTaskCount} 个任务正在运行`,
    },
    reader: {
      eyebrow: 'CONTENTS',
      title: '笔记目录',
      description: '点击章节同步证据时间',
    },
    models: {
      eyebrow: 'PROVIDERS',
      title: '模型供应商',
      description: '密钥不会出现在此界面',
    },
  }

  return (
    <aside className="context-panel">
      <header className="context-header">
        <div>
          <span className="eyebrow">{metadata[view].eyebrow}</span>
          <h2>{metadata[view].title}</h2>
          <p>{metadata[view].description}</p>
        </div>
        <button
          type="button"
          className="icon-button"
          onClick={toggleContext}
          aria-label="收起上下文栏"
          title="收起上下文栏"
        >
          <PanelLeftClose size={18} aria-hidden="true" />
        </button>
      </header>
      <div className="context-body">
        {(view === 'create' || view === 'tasks') && <TaskList />}
        {view === 'reader' && <ReaderContents />}
        {view === 'models' && <ProviderList />}
      </div>
      {view !== 'models' && (
        <footer className="context-footer">
          <Clock3 size={14} aria-hidden="true" />
          <span>所有 artifact 仅保存在本机</span>
        </footer>
      )}
    </aside>
  )
}

function WorkspaceHeader() {
  const machine = useStudioStore(state => state.machine)
  const view = useStudioStore(state => state.view)
  const activeTask = useStudioStore(state =>
    state.tasks.find(task => task.id === state.activeTaskId),
  )
  const fallbackNoteTitle = useStudioStore(
    state => state.tasks.find(task => task.note)?.note?.title,
  )

  const viewTitle: Record<ViewId, string> = {
    create: '新建任务',
    tasks: activeTask?.source.title ?? '任务运行',
    reader: activeTask?.note?.title ?? fallbackNoteTitle ?? '笔记阅读',
    models: '模型与角色路由',
  }

  return (
    <header className="workspace-header">
      <div className="workspace-title">
        <span className="eyebrow">VIDEO2NOTES / {view.toUpperCase()}</span>
        <h1>{viewTitle[view]}</h1>
      </div>
      <div
        className="machine-status"
        aria-label={`本机运行状态：${machine.gpu}，${machine.cpu}`}
        title={`${machine.gpu} · ${machine.cpu} · ${machine.memory}`}
      >
        <Cpu size={16} aria-hidden="true" />
        <span>
          <strong>{machine.gpu}</strong>
          <small>
            {machine.cpu} · {machine.memory}
          </small>
        </span>
      </div>
    </header>
  )
}

export function AppShell({ children }: { children: ReactNode }) {
  const view = useStudioStore(state => state.view)
  const contextCollapsed = useStudioStore(state => state.contextCollapsed)
  const toggleContext = useStudioStore(state => state.toggleContext)
  const notice = useStudioStore(state => state.notice)
  const clearNotice = useStudioStore(state => state.clearNotice)
  const workspaceMode = useUiPreferences(state => state.workspaceMode)
  const themePreset = useUiPreferences(state => state.themePreset)
  const professionalReader = view === 'reader' && workspaceMode === 'professional'
  const contextShouldShow = !contextCollapsed && !professionalReader
  const [contextSlotOpen, setContextSlotOpen] = useState(contextShouldShow)

  useEffect(() => {
    if (contextShouldShow) {
      setContextSlotOpen(true)
      return
    }

    const closeTimer = window.setTimeout(() => setContextSlotOpen(false), 140)
    return () => window.clearTimeout(closeTimer)
  }, [contextShouldShow])

  const layoutContextCollapsed = contextCollapsed && !contextSlotOpen

  return (
    <div
      className={`app-shell ${layoutContextCollapsed ? 'context-is-collapsed' : ''} ${
        professionalReader ? 'detail-workspace' : ''
      }`}
      data-theme={themePreset}
      data-workspace-mode={workspaceMode}
    >
      <TopNavigation />
      <MotionPresence
        show={contextShouldShow}
        className="motion-presence-context-panel"
        exitMs={140}
      >
        {contextShouldShow && <ContextPanel />}
      </MotionPresence>
      <section className="workspace">
        <WorkspaceHeader />
        {contextCollapsed && !professionalReader && (
          <button
            type="button"
            className="context-expand"
            onClick={toggleContext}
            aria-label="展开上下文栏"
          >
            <PanelLeftOpen size={17} aria-hidden="true" />
            展开侧栏
          </button>
        )}
        <MotionPresence
          show={Boolean(notice)}
          className="motion-presence-notice"
          exitMs={120}
        >
          <div className="notice-bar" role="status">
            <CircleDot size={14} aria-hidden="true" />
            <span>{notice}</span>
            <button type="button" onClick={clearNotice} aria-label="关闭提示">
              <X size={15} aria-hidden="true" />
            </button>
          </div>
        </MotionPresence>
        <main className="workspace-main">
          <div className="workspace-page-surface" key={view}>
            {children}
          </div>
        </main>
      </section>
    </div>
  )
}

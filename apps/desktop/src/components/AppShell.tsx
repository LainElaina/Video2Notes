import { useEffect, useRef, useState, type ReactNode } from 'react'
import {
  Activity,
  BookOpenText,
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
  Settings2,
  X,
} from 'lucide-react'
import type { ViewId } from '../domain'
import { formatTime, platformLabel } from '../domain'
import { preferredScrollBehavior } from '../motion'
import { useStudioStore } from '../store'
import { useUiPreferences, type ThemePreset, type Locale } from '../stores/uiPreferences'
import { useI18n } from '../i18n'
import { localizeUserMessage, userMessageTone } from '../userMessages'
import { MotionPresence } from './MotionPresence'

const navItems: Array<{
  view: ViewId
  labelKey: string
  icon: typeof Plus
}> = [
  { view: 'create', labelKey: 'nav.create', icon: Plus },
  { view: 'tasks', labelKey: 'nav.tasks', icon: Activity },
  { view: 'reader', labelKey: 'nav.reader', icon: BookOpenText },
  { view: 'models', labelKey: 'nav.settings', icon: Settings2 },
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

const themeOptions: Array<{ value: ThemePreset; zh: string; en: string }> = [
  { value: 'precision-light', zh: '矿物玻璃', en: 'Mineral glass' },
  { value: 'paper-light', zh: '纸张亮色', en: 'Paper light' },
  { value: 'studio-graphite', zh: '工作室石墨', en: 'Studio graphite' },
]

function TopNavigation() {
  const view = useStudioStore(state => state.view)
  const navigate = useStudioStore(state => state.navigate)
  const backend = useStudioStore(state => state.backend)
  const workspaceMode = useUiPreferences(state => state.workspaceMode)
  const setWorkspaceMode = useUiPreferences(state => state.setWorkspaceMode)
  const themePreset = useUiPreferences(state => state.themePreset)
  const setThemePreset = useUiPreferences(state => state.setThemePreset)
  const locale = useUiPreferences(state => state.locale)
  const setLocale = useUiPreferences(state => state.setLocale)
  const { t, text } = useI18n()

  const backendLabel =
    backend.mode === 'real'
      ? t('nav.backend.real', { version: backend.version ?? '' })
      : backend.mode === 'demo'
        ? t('nav.backend.demo')
        : backend.mode === 'connecting'
          ? t('nav.backend.connecting')
          : t('nav.backend.offline')

  return (
    <header className="top-navigation">
      <button
        className="topbar-brand"
        type="button"
        onClick={() => navigate('create')}
        aria-label={t('nav.brand.back')}
      >
        <BrandMark />
        <span className="brand-copy" aria-hidden="true">
          <strong>Video2Notes</strong>
          <small>{text('本地分析', 'LOCAL ANALYSIS')}</small>
        </span>
      </button>
      <nav className="top-navigation-items" aria-label={t('nav.main')}>
        {navItems.map(item => {
          const Icon = item.icon
          const selected = view === item.view
          return (
            <button
              className="top-navigation-button"
              type="button"
              key={item.view}
              aria-current={selected ? 'page' : undefined}
              aria-label={t(item.labelKey)}
              title={t(item.labelKey)}
              onClick={() => navigate(item.view)}
            >
              <Icon aria-hidden="true" size={19} />
              <span>{t(item.labelKey)}</span>
            </button>
          )
        })}
      </nav>
      <div className="top-navigation-controls">
        <div className="workspace-mode-switch" role="group" aria-label={t('nav.workspaceMode')}>
          <button
            type="button"
            aria-label={t('nav.guided')}
            aria-pressed={workspaceMode === 'guided'}
            onClick={() => setWorkspaceMode('guided')}
          >
            {t('nav.guided')}
          </button>
          <button
            type="button"
            aria-label={t('nav.professional')}
            aria-pressed={workspaceMode === 'professional'}
            onClick={() => setWorkspaceMode('professional')}
          >
            {t('nav.professional')}
          </button>
        </div>
        <label className="theme-preset-control">
          <Palette size={14} aria-hidden="true" />
          <span className="sr-only">{t('nav.theme')}</span>
          <select
            value={themePreset}
            aria-label={t('nav.theme')}
            onChange={event => setThemePreset(event.currentTarget.value as ThemePreset)}
          >
            {themeOptions.map(option => (
              <option key={option.value} value={option.value}>
                {text(option.zh, option.en)}
              </option>
            ))}
          </select>
        </label>
        <label className="locale-control">
          <span className="sr-only">{t('nav.language')}</span>
          <select
            value={locale}
            aria-label={t('nav.language')}
            onChange={event => setLocale(event.currentTarget.value as Locale)}
          >
            <option value="zh-CN">{t('nav.language.zh')}</option>
            <option value="en-US">{t('nav.language.en')}</option>
          </select>
        </label>
        <div
          className={`topbar-backend backend-${backend.mode}`}
          role="group"
          aria-label={`${t('nav.backend.status')}: ${backendLabel}`}
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
  const { text } = useI18n()
  const localizedStatus = {
    running: text('处理中', 'Processing'),
    paused: text('已暂停', 'Paused'),
    cancelled: text('已取消', 'Cancelled'),
    completed: text('已完成', 'Completed'),
    failed: text('失败', 'Failed'),
  } as const

  const visibleTasks = tasks.filter(task =>
    `${task.source.title} ${task.source.author}`.toLowerCase().includes(taskSearch.toLowerCase()),
  )

  return (
    <>
      <label className="context-search">
        <Search size={15} aria-hidden="true" />
        <span className="sr-only">{text('搜索任务', 'Search tasks')}</span>
        <input
          value={taskSearch}
          onChange={event => setTaskSearch(event.target.value)}
          placeholder={text('搜索标题或作者', 'Search title or author')}
        />
      </label>
      <div className="context-task-list" aria-label={text('任务列表', 'Task list')}>
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
                {task.source.platform === 'local'
                  ? text('本', 'L')
                  : platformLabel[task.source.platform].slice(0, 1)}
              </span>
              <span className="context-task-copy">
                <strong title={task.source.title}>{task.source.title}</strong>
                <span>
                  {localizedStatus[task.status]} ·{' '}
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
            <p>{text('没有匹配的任务', 'No matching tasks')}</p>
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
  const { text } = useI18n()

  if (!note) return <p className="context-helper">{text('完成一个任务后，这里会显示笔记目录。', 'The note outline appears after a task completes.')}</p>

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
        <span>{text('摘要', 'Summary')}</span>
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
            <span title={section.title}>
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
            <strong title={provider.name}>{provider.name}</strong>
            <small title={provider.endpoint}>{provider.endpoint}</small>
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
  const { t, text } = useI18n()

  const metadata: Record<ViewId, { eyebrow: string; title: string; description: string }> = {
    create: {
      eyebrow: text('工作台', 'WORKBENCH'),
      title: text('最近任务', 'Recent tasks'),
      description: text('来源、模式与上次运行结果', 'Sources, modes, and latest results'),
    },
    tasks: {
      eyebrow: text('运行队列', 'RUN QUEUE'),
      title: text('任务队列', 'Run queue'),
      description: text(`${runningTaskCount} 个任务正在运行`, `${runningTaskCount} tasks running`),
    },
    reader: {
      eyebrow: text('目录', 'CONTENTS'),
      title: text('笔记目录', 'Note outline'),
      description: text('点击章节同步证据时间', 'Select a chapter to sync evidence time'),
    },
    models: {
      eyebrow: text('供应商', 'PROVIDERS'),
      title: text('模型供应商', 'Model providers'),
      description: text('密钥不会出现在此界面', 'Secrets never appear in this view'),
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
          aria-label={t('nav.context.collapse')}
          title={t('nav.context.collapse')}
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
          <span>{text('所有 artifact 仅保存在本机', 'All artifacts stay on this computer')}</span>
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
  const { text } = useI18n()

  const viewTitle: Record<ViewId, string> = {
    create: text('新建任务', 'New task'),
    tasks: activeTask?.source.title ?? text('任务运行', 'Runs'),
    reader: activeTask?.note?.title ?? fallbackNoteTitle ?? text('笔记阅读', 'Reader'),
    models: text('模型与角色路由', 'Models and role routing'),
  }
  const viewEyebrow: Record<ViewId, string> = {
    create: text('新建任务', 'CREATE'),
    tasks: text('任务运行', 'RUNS'),
    reader: text('笔记阅读', 'READER'),
    models: text('设置', 'SETTINGS'),
  }

  return (
    <header className="workspace-header">
      <div className="workspace-title">
        <span className="eyebrow">VIDEO2NOTES / {viewEyebrow[view]}</span>
        <h1 title={viewTitle[view]}>{viewTitle[view]}</h1>
      </div>
      <div
        className="machine-status"
        aria-label={`${text('本机运行状态', 'Local runtime status')}: ${machine.gpu}, ${machine.cpu}`}
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
  const fontSizePreset = useUiPreferences(state => state.fontSizePreset)
  const { locale, t } = useI18n()
  const localizedNotice = notice ? localizeUserMessage(notice, locale) : undefined
  const professionalReader = view === 'reader' && workspaceMode === 'professional'
  const contextShouldShow = !contextCollapsed && !professionalReader
  const [narrowLayout, setNarrowLayout] = useState(false)
  const [noticeInteracting, setNoticeInteracting] = useState(false)
  const pageSurfaceRef = useRef<HTMLDivElement>(null)
  const contextExpandRef = useRef<HTMLButtonElement>(null)
  const contextOverlayOpen = narrowLayout && contextShouldShow
  const noticeTone = notice ? userMessageTone(notice) : 'neutral'

  // Non-error notices dismiss themselves; the countdown pauses while the bar
  // is hovered or focused and restarts when a new notice replaces the old one.
  useEffect(() => {
    if (!notice || noticeTone === 'error' || noticeInteracting) return
    const timer = window.setTimeout(clearNotice, 4_500)
    return () => window.clearTimeout(timer)
  }, [clearNotice, notice, noticeInteracting, noticeTone])

  // Below 1120px the context panel leaves the grid and becomes a slide-over.
  // The slide-over starts dismissed so it never blocks the workspace on load;
  // the wide-layout collapsed preference is restored when leaving narrow mode.
  const autoCollapsedContextRef = useRef(false)
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const query = window.matchMedia('(max-width: 1120px)')
    const updateLayout = () => {
      const narrow = query.matches
      setNarrowLayout(narrow)
      const { contextCollapsed: collapsed, toggleContext: toggle } = useStudioStore.getState()
      if (narrow && !collapsed) {
        autoCollapsedContextRef.current = true
        toggle()
      } else if (!narrow && autoCollapsedContextRef.current) {
        autoCollapsedContextRef.current = false
        if (collapsed) toggle()
      }
    }
    updateLayout()
    query.addEventListener('change', updateLayout)
    return () => query.removeEventListener('change', updateLayout)
  }, [])

  // Navigating with the slide-over open dismisses it so the destination page
  // is never left hidden behind the scrim.
  const previousViewRef = useRef(view)
  useEffect(() => {
    if (previousViewRef.current === view) return
    previousViewRef.current = view
    if (!narrowLayout) return
    const { contextCollapsed: collapsed, toggleContext: toggle } = useStudioStore.getState()
    if (!collapsed) {
      autoCollapsedContextRef.current = true
      toggle()
    }
  }, [view, narrowLayout])

  // Backend (re)initialization resets contextCollapsed through initialData;
  // re-apply the narrow-layout auto collapse whenever the backend mode flips.
  const backendMode = useStudioStore(state => state.backend.mode)
  useEffect(() => {
    if (!narrowLayout) return
    const { contextCollapsed: collapsed, toggleContext: toggle } = useStudioStore.getState()
    if (!collapsed) {
      autoCollapsedContextRef.current = true
      toggle()
    }
  }, [backendMode, narrowLayout])

  // Retrigger the page-surface enter transition on navigation without
  // remounting the page; the attribute only drives opacity and transform.
  useEffect(() => {
    const surface = pageSurfaceRef.current
    if (!surface) return
    surface.setAttribute('data-entering', '')
    let clearFrame: number | undefined
    const nextFrame = window.requestAnimationFrame(() => {
      clearFrame = window.requestAnimationFrame(() => {
        surface.removeAttribute('data-entering')
      })
    })
    return () => {
      window.cancelAnimationFrame(nextFrame)
      if (clearFrame !== undefined) window.cancelAnimationFrame(clearFrame)
    }
  }, [view])

  useEffect(() => {
    if (!contextOverlayOpen) return
    const dismissOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return
      event.preventDefault()
      toggleContext()
    }
    document.addEventListener('keydown', dismissOnEscape)
    return () => document.removeEventListener('keydown', dismissOnEscape)
  }, [contextOverlayOpen, toggleContext])

  return (
    <div
      className={`app-shell ${contextCollapsed ? 'context-is-collapsed' : ''} ${
        professionalReader ? 'detail-workspace' : ''
      }`}
      data-theme={themePreset}
      data-workspace-mode={workspaceMode}
      data-font-size={fontSizePreset}
    >
      <TopNavigation />
      <MotionPresence
        show={contextShouldShow}
        className="motion-presence-context-panel"
        exitMs={140}
        focusMode={contextOverlayOpen ? 'modal' : undefined}
        restoreFocusRef={contextExpandRef}
      >
        {contextShouldShow && (
          <>
            {narrowLayout && (
              <button
                type="button"
                className="context-scrim"
                aria-label={t('nav.context.collapse')}
                tabIndex={-1}
                onClick={toggleContext}
              />
            )}
            <ContextPanel />
          </>
        )}
      </MotionPresence>
      <section className="workspace">
        <WorkspaceHeader />
        {contextCollapsed && !professionalReader && (
          <button
            type="button"
            className="context-expand"
            ref={contextExpandRef}
            onClick={toggleContext}
            aria-label={t('nav.context.expand')}
          >
            <PanelLeftOpen size={17} aria-hidden="true" />
            {t('nav.context.expand')}
          </button>
        )}
        <MotionPresence
          show={Boolean(localizedNotice)}
          className="motion-presence-notice"
          exitMs={120}
        >
          <div
            className="notice-bar"
            role="status"
            onMouseEnter={() => setNoticeInteracting(true)}
            onMouseLeave={() => setNoticeInteracting(false)}
            onFocus={() => setNoticeInteracting(true)}
            onBlur={event => {
              if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
                setNoticeInteracting(false)
              }
            }}
          >
            <CircleDot size={14} aria-hidden="true" />
            <span>{localizedNotice}</span>
            <button type="button" onClick={clearNotice} aria-label={t('nav.notice.close')}>
              <X size={15} aria-hidden="true" />
            </button>
          </div>
        </MotionPresence>
        <main className="workspace-main">
          <div className="workspace-page-surface" ref={pageSurfaceRef}>
            {children}
          </div>
        </main>
      </section>
    </div>
  )
}

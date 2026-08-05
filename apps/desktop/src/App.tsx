import { useEffect } from 'react'
import { AppShell } from './components/AppShell'
import { CreatePage } from './pages/CreatePage'
import { ModelsPage } from './pages/ModelsPage'
import { ReaderPage } from './pages/ReaderPage'
import { RunPage } from './pages/RunPage'
import { useStudioStore } from './store'
import { useUiPreferences } from './stores/uiPreferences'

export default function App() {
  const view = useStudioStore(state => state.view)
  const advanceTasks = useStudioStore(state => state.advanceTasks)
  const initializeBackend = useStudioStore(state => state.initializeBackend)
  const backendMode = useStudioStore(state => state.backend.mode)
  const hasActiveTasks = useStudioStore(state =>
    state.tasks.some(task => task.status === 'running'),
  )
  const themePreset = useUiPreferences(state => state.themePreset)
  const workspaceMode = useUiPreferences(state => state.workspaceMode)

  useEffect(() => {
    const root = document.documentElement
    root.dataset.theme = themePreset
    root.dataset.workspaceMode = workspaceMode

    return () => {
      if (root.dataset.theme === themePreset) delete root.dataset.theme
      if (root.dataset.workspaceMode === workspaceMode) delete root.dataset.workspaceMode
    }
  }, [themePreset, workspaceMode])

  useEffect(() => {
    initializeBackend()
  }, [initializeBackend])

  useEffect(() => {
    let timer: number | undefined

    const schedule = () => {
      const delay =
        document.visibilityState === 'hidden'
          ? 10_000
          : hasActiveTasks
            ? 1_000
            : backendMode === 'real'
              ? 5_000
              : 0

      if (delay === 0) return
      timer = window.setTimeout(() => {
        advanceTasks()
        schedule()
      }, delay)
    }

    const reschedule = () => {
      if (timer !== undefined) window.clearTimeout(timer)
      timer = undefined
      schedule()
    }

    schedule()
    document.addEventListener('visibilitychange', reschedule)
    return () => {
      document.removeEventListener('visibilitychange', reschedule)
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [advanceTasks, backendMode, hasActiveTasks])

  useEffect(() => {
    document.querySelector<HTMLElement>('.workspace-main')?.scrollTo({ top: 0 })
  }, [view])

  return (
    <AppShell>
      {view === 'create' && <CreatePage />}
      {view === 'tasks' && <RunPage />}
      {view === 'reader' && <ReaderPage />}
      {view === 'models' && <ModelsPage />}
    </AppShell>
  )
}

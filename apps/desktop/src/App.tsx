import { useEffect } from 'react'
import { AppShell } from './components/AppShell'
import { CreatePage } from './pages/CreatePage'
import { ModelsPage } from './pages/ModelsPage'
import { ReaderPage } from './pages/ReaderPage'
import { RunPage } from './pages/RunPage'
import { useStudioStore } from './store'

export default function App() {
  const view = useStudioStore(state => state.view)
  const advanceTasks = useStudioStore(state => state.advanceTasks)
  const initializeBackend = useStudioStore(state => state.initializeBackend)

  useEffect(() => {
    initializeBackend()
  }, [initializeBackend])

  useEffect(() => {
    const timer = window.setInterval(advanceTasks, 1000)
    return () => window.clearInterval(timer)
  }, [advanceTasks])

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

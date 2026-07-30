import { create } from 'zustand'

export type WorkspaceMode = 'simple' | 'detailed'
export type ThemePreset = 'precision-light' | 'paper-light' | 'studio-graphite'

export interface UiPreferences {
  workspaceMode: WorkspaceMode
  themePreset: ThemePreset
}

interface UiPreferencesState extends UiPreferences {
  setWorkspaceMode: (workspaceMode: WorkspaceMode) => void
  setThemePreset: (themePreset: ThemePreset) => void
  hydratePreferences: () => void
  resetPreferences: () => void
}

export const UI_PREFERENCES_STORAGE_KEY = 'video2notes.ui-preferences.v1'

export const DEFAULT_UI_PREFERENCES: UiPreferences = {
  workspaceMode: 'simple',
  themePreset: 'precision-light',
}

const workspaceModes = new Set<WorkspaceMode>(['simple', 'detailed'])
const themePresets = new Set<ThemePreset>([
  'precision-light',
  'paper-light',
  'studio-graphite',
])

function readPreferences(): UiPreferences {
  if (typeof window === 'undefined') return DEFAULT_UI_PREFERENCES

  try {
    const stored = window.localStorage.getItem(UI_PREFERENCES_STORAGE_KEY)
    if (!stored) return DEFAULT_UI_PREFERENCES

    const value = JSON.parse(stored) as Partial<UiPreferences>
    return {
      workspaceMode: workspaceModes.has(value.workspaceMode as WorkspaceMode)
        ? (value.workspaceMode as WorkspaceMode)
        : DEFAULT_UI_PREFERENCES.workspaceMode,
      themePreset: themePresets.has(value.themePreset as ThemePreset)
        ? (value.themePreset as ThemePreset)
        : DEFAULT_UI_PREFERENCES.themePreset,
    }
  } catch {
    return DEFAULT_UI_PREFERENCES
  }
}

function writePreferences(preferences: UiPreferences) {
  if (typeof window === 'undefined') return

  try {
    window.localStorage.setItem(UI_PREFERENCES_STORAGE_KEY, JSON.stringify(preferences))
  } catch {
    // A blocked or full localStorage must never prevent the local app from rendering.
  }
}

export const useUiPreferences = create<UiPreferencesState>((set, get) => ({
  ...readPreferences(),
  setWorkspaceMode: workspaceMode => {
    const preferences = { workspaceMode, themePreset: get().themePreset }
    writePreferences(preferences)
    set({ workspaceMode })
  },
  setThemePreset: themePreset => {
    const preferences = { workspaceMode: get().workspaceMode, themePreset }
    writePreferences(preferences)
    set({ themePreset })
  },
  hydratePreferences: () => {
    set(readPreferences())
  },
  resetPreferences: () => {
    writePreferences(DEFAULT_UI_PREFERENCES)
    set(DEFAULT_UI_PREFERENCES)
  },
}))

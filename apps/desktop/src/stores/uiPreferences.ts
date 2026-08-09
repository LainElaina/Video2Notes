import { create } from 'zustand'

export type WorkspaceMode = 'guided' | 'professional'
export type ThemePreset = 'precision-light' | 'paper-light' | 'studio-graphite'
export type FontSizePreset = 'compact' | 'comfortable' | 'large'

export interface UiPreferences {
  workspaceMode: WorkspaceMode
  themePreset: ThemePreset
  fontSizePreset: FontSizePreset
}

interface UiPreferencesState extends UiPreferences {
  setWorkspaceMode: (workspaceMode: WorkspaceMode) => void
  setThemePreset: (themePreset: ThemePreset) => void
  setFontSizePreset: (fontSizePreset: FontSizePreset) => void
  hydratePreferences: () => void
  resetPreferences: () => void
}

export const UI_PREFERENCES_STORAGE_KEY = 'video2notes.ui-preferences.v3'
export const PREVIOUS_UI_PREFERENCES_STORAGE_KEY = 'video2notes.ui-preferences.v2'
export const LEGACY_UI_PREFERENCES_STORAGE_KEY = 'video2notes.ui-preferences.v1'

export const DEFAULT_UI_PREFERENCES: UiPreferences = {
  workspaceMode: 'guided',
  themePreset: 'precision-light',
  fontSizePreset: 'comfortable',
}

const workspaceModes = new Set<WorkspaceMode>(['guided', 'professional'])
const themePresets = new Set<ThemePreset>([
  'precision-light',
  'paper-light',
  'studio-graphite',
])
const fontSizePresets = new Set<FontSizePreset>(['compact', 'comfortable', 'large'])

const migrateWorkspaceMode = (value: unknown): WorkspaceMode => {
  if (workspaceModes.has(value as WorkspaceMode)) return value as WorkspaceMode
  if (value === 'detailed') return 'professional'
  return 'guided'
}

const normalizePreferences = (value: Partial<Record<keyof UiPreferences, unknown>>): UiPreferences => ({
  workspaceMode: migrateWorkspaceMode(value.workspaceMode),
  themePreset: themePresets.has(value.themePreset as ThemePreset)
    ? (value.themePreset as ThemePreset)
    : DEFAULT_UI_PREFERENCES.themePreset,
  fontSizePreset: fontSizePresets.has(value.fontSizePreset as FontSizePreset)
    ? (value.fontSizePreset as FontSizePreset)
    : DEFAULT_UI_PREFERENCES.fontSizePreset,
})

const readStoredPreferences = (
  key: string,
): Partial<Record<keyof UiPreferences, unknown>> | undefined => {
  if (typeof window === 'undefined') return undefined

  try {
    const stored = window.localStorage.getItem(key)
    return stored
      ? (JSON.parse(stored) as Partial<Record<keyof UiPreferences, unknown>>)
      : undefined
  } catch {
    return undefined
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

function readPreferences(): UiPreferences {
  if (typeof window === 'undefined') return DEFAULT_UI_PREFERENCES

  const current = readStoredPreferences(UI_PREFERENCES_STORAGE_KEY)
  if (current) return normalizePreferences(current)

  const legacy =
    readStoredPreferences(PREVIOUS_UI_PREFERENCES_STORAGE_KEY) ??
    readStoredPreferences(LEGACY_UI_PREFERENCES_STORAGE_KEY)
  if (!legacy) return DEFAULT_UI_PREFERENCES

  const migrated = normalizePreferences(legacy)
  writePreferences(migrated)
  return migrated
}

export const useUiPreferences = create<UiPreferencesState>((set, get) => ({
  ...readPreferences(),
  setWorkspaceMode: workspaceMode => {
    const preferences = {
      workspaceMode,
      themePreset: get().themePreset,
      fontSizePreset: get().fontSizePreset,
    }
    writePreferences(preferences)
    set({ workspaceMode })
  },
  setThemePreset: themePreset => {
    const preferences = {
      workspaceMode: get().workspaceMode,
      themePreset,
      fontSizePreset: get().fontSizePreset,
    }
    writePreferences(preferences)
    set({ themePreset })
  },
  setFontSizePreset: fontSizePreset => {
    const preferences = {
      workspaceMode: get().workspaceMode,
      themePreset: get().themePreset,
      fontSizePreset,
    }
    writePreferences(preferences)
    set({ fontSizePreset })
  },
  hydratePreferences: () => {
    set(readPreferences())
  },
  resetPreferences: () => {
    writePreferences(DEFAULT_UI_PREFERENCES)
    if (typeof window !== 'undefined') {
      try {
        window.localStorage.removeItem(PREVIOUS_UI_PREFERENCES_STORAGE_KEY)
        window.localStorage.removeItem(LEGACY_UI_PREFERENCES_STORAGE_KEY)
      } catch {
        // Preference cleanup is best-effort for restricted localStorage environments.
      }
    }
    set(DEFAULT_UI_PREFERENCES)
  },
}))

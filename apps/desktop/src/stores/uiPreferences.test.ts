import { beforeEach, describe, expect, it } from 'vitest'
import {
  DEFAULT_UI_PREFERENCES,
  UI_PREFERENCES_STORAGE_KEY,
  useUiPreferences,
} from './uiPreferences'

describe('UI preferences', () => {
  beforeEach(() => {
    window.localStorage.clear()
    useUiPreferences.setState(DEFAULT_UI_PREFERENCES)
  })

  it('starts in the light precision theme and simple workspace mode', () => {
    expect(useUiPreferences.getState()).toMatchObject(DEFAULT_UI_PREFERENCES)
  })

  it('persists and restores the selected theme and workspace mode', () => {
    useUiPreferences.getState().setWorkspaceMode('detailed')
    useUiPreferences.getState().setThemePreset('studio-graphite')

    expect(JSON.parse(window.localStorage.getItem(UI_PREFERENCES_STORAGE_KEY) ?? '{}')).toEqual({
      workspaceMode: 'detailed',
      themePreset: 'studio-graphite',
    })

    useUiPreferences.setState(DEFAULT_UI_PREFERENCES)
    useUiPreferences.getState().hydratePreferences()

    expect(useUiPreferences.getState()).toMatchObject({
      workspaceMode: 'detailed',
      themePreset: 'studio-graphite',
    })
  })

  it('falls back field by field when persisted values are invalid', () => {
    window.localStorage.setItem(
      UI_PREFERENCES_STORAGE_KEY,
      JSON.stringify({ workspaceMode: 'compact', themePreset: 'paper-light' }),
    )

    useUiPreferences.getState().hydratePreferences()

    expect(useUiPreferences.getState()).toMatchObject({
      workspaceMode: 'simple',
      themePreset: 'paper-light',
    })
  })
})

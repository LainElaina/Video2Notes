import { beforeEach, describe, expect, it } from 'vitest'
import {
  DEFAULT_UI_PREFERENCES,
  LEGACY_UI_PREFERENCES_STORAGE_KEY,
  PREVIOUS_UI_PREFERENCES_STORAGE_KEY,
  UI_PREFERENCES_STORAGE_KEY,
  useUiPreferences,
} from './uiPreferences'

describe('UI preferences', () => {
  beforeEach(() => {
    window.localStorage.clear()
    useUiPreferences.setState(DEFAULT_UI_PREFERENCES)
  })

  it('starts in the light precision theme and guided workspace mode', () => {
    expect(useUiPreferences.getState()).toMatchObject(DEFAULT_UI_PREFERENCES)
  })

  it('persists and restores the selected theme and workspace mode', () => {
    useUiPreferences.getState().setWorkspaceMode('professional')
    useUiPreferences.getState().setThemePreset('studio-graphite')
    useUiPreferences.getState().setFontSizePreset('large')

    expect(JSON.parse(window.localStorage.getItem(UI_PREFERENCES_STORAGE_KEY) ?? '{}')).toEqual({
      workspaceMode: 'professional',
      themePreset: 'studio-graphite',
      fontSizePreset: 'large',
    })

    useUiPreferences.setState(DEFAULT_UI_PREFERENCES)
    useUiPreferences.getState().hydratePreferences()

    expect(useUiPreferences.getState()).toMatchObject({
      workspaceMode: 'professional',
      themePreset: 'studio-graphite',
      fontSizePreset: 'large',
    })
  })

  it('persists and restores the English locale', () => {
    useUiPreferences.getState().setLocale('en-US')

    expect(JSON.parse(window.localStorage.getItem(UI_PREFERENCES_STORAGE_KEY) ?? '{}')).toEqual({
      workspaceMode: 'guided',
      themePreset: 'precision-light',
      fontSizePreset: 'comfortable',
      locale: 'en-US',
    })

    useUiPreferences.setState(DEFAULT_UI_PREFERENCES)
    useUiPreferences.getState().hydratePreferences()

    expect(useUiPreferences.getState().locale).toBe('en-US')
  })

  it('falls back field by field when persisted values are invalid', () => {
    window.localStorage.setItem(
      UI_PREFERENCES_STORAGE_KEY,
      JSON.stringify({ workspaceMode: 'compact', themePreset: 'paper-light' }),
    )

    useUiPreferences.getState().hydratePreferences()

    expect(useUiPreferences.getState()).toMatchObject({
      workspaceMode: 'guided',
      themePreset: 'paper-light',
      fontSizePreset: 'comfortable',
    })
  })

  it('migrates v2 preferences and adds the comfortable font default', () => {
    window.localStorage.setItem(
      PREVIOUS_UI_PREFERENCES_STORAGE_KEY,
      JSON.stringify({ workspaceMode: 'professional', themePreset: 'paper-light' }),
    )

    useUiPreferences.getState().hydratePreferences()

    expect(useUiPreferences.getState()).toMatchObject({
      workspaceMode: 'professional',
      themePreset: 'paper-light',
      fontSizePreset: 'comfortable',
    })
  })

  it('defaults old preferences without a locale to Chinese during migration', () => {
    window.localStorage.setItem(
      PREVIOUS_UI_PREFERENCES_STORAGE_KEY,
      JSON.stringify({
        workspaceMode: 'professional',
        themePreset: 'paper-light',
        fontSizePreset: 'large',
      }),
    )

    useUiPreferences.getState().hydratePreferences()

    expect(useUiPreferences.getState()).toMatchObject({
      workspaceMode: 'professional',
      themePreset: 'paper-light',
      fontSizePreset: 'large',
      locale: 'zh-CN',
    })
    expect(JSON.parse(window.localStorage.getItem(UI_PREFERENCES_STORAGE_KEY) ?? '{}')).toEqual({
      workspaceMode: 'professional',
      themePreset: 'paper-light',
      fontSizePreset: 'large',
    })
  })

  it('defaults a current-key preference written by an older build to Chinese', () => {
    window.localStorage.setItem(
      UI_PREFERENCES_STORAGE_KEY,
      JSON.stringify({
        workspaceMode: 'professional',
        themePreset: 'studio-graphite',
        fontSizePreset: 'compact',
      }),
    )

    useUiPreferences.getState().hydratePreferences()

    expect(useUiPreferences.getState()).toMatchObject({
      workspaceMode: 'professional',
      themePreset: 'studio-graphite',
      fontSizePreset: 'compact',
      locale: 'zh-CN',
    })
  })

  it('migrates v1 simple and detailed modes to the v2 experience modes', () => {
    window.localStorage.setItem(
      LEGACY_UI_PREFERENCES_STORAGE_KEY,
      JSON.stringify({ workspaceMode: 'detailed', themePreset: 'studio-graphite' }),
    )

    useUiPreferences.getState().hydratePreferences()

    expect(useUiPreferences.getState()).toMatchObject({
      workspaceMode: 'professional',
      themePreset: 'studio-graphite',
      fontSizePreset: 'comfortable',
    })
    expect(JSON.parse(window.localStorage.getItem(UI_PREFERENCES_STORAGE_KEY) ?? '{}')).toEqual({
      workspaceMode: 'professional',
      themePreset: 'studio-graphite',
      fontSizePreset: 'comfortable',
    })

    window.localStorage.clear()
    window.localStorage.setItem(
      LEGACY_UI_PREFERENCES_STORAGE_KEY,
      JSON.stringify({ workspaceMode: 'simple', themePreset: 'paper-light' }),
    )
    useUiPreferences.getState().hydratePreferences()

    expect(useUiPreferences.getState()).toMatchObject({
      workspaceMode: 'guided',
      themePreset: 'paper-light',
      fontSizePreset: 'comfortable',
    })
  })
})

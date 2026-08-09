import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { makeTaskFixtures } from '../fixtures'
import { useStudioStore } from '../store'
import { useUiPreferences } from '../stores/uiPreferences'
import { ProcessingBenchmarkGuide } from './ProcessingBenchmarkGuide'
import { ReportRevisionDrawer } from './ReportRevisionDrawer'
import { SupportingMaterialsDrawer } from './SupportingMaterialsDrawer'

describe('localized workflow panels', () => {
  beforeEach(() => {
    useUiPreferences.getState().resetPreferences()
    useStudioStore.getState().resetDemo()
  })

  it('renders supporting-material controls in English', () => {
    useUiPreferences.getState().setLocale('en-US')
    const task = makeTaskFixtures().find(item => item.id === 'task-complete')!

    render(<SupportingMaterialsDrawer task={task} onClose={vi.fn()} />)

    expect(screen.getByRole('dialog', { name: 'Supporting materials' })).toBeInTheDocument()
    expect(screen.getByLabelText('Material title')).toHaveAttribute(
      'placeholder',
      'For example: references and corrections from the comments',
    )
    expect(screen.getByRole('button', { name: /Choose PNG, JPEG, or WebP files/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Done' })).toBeInTheDocument()
  })

  it('provides a localized report error summary while retaining technical details', () => {
    useUiPreferences.getState().setLocale('en-US')

    render(
      <ReportRevisionDrawer
        taskTitle="Test task"
        materialsCount={0}
        activeEvidenceRevisionId="evidence-revision-1"
        revisions={[]}
        busy={false}
        error="provider timeout"
        onClose={vi.fn()}
        onGenerate={vi.fn()}
        onDownload={vi.fn()}
      />,
    )

    expect(screen.getByRole('dialog', { name: 'Regenerate report' })).toBeInTheDocument()
    expect(screen.getByText('Report generation failed')).toBeInTheDocument()
    expect(screen.getByText('Technical details: provider timeout')).toBeInTheDocument()
    expect(screen.getByRole('radiogroup', { name: 'Report type' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Generate new revision' })).toBeInTheDocument()
  })

  it('localizes the expanded performance reference', () => {
    useUiPreferences.getState().setLocale('en-US')

    render(<ProcessingBenchmarkGuide />)
    fireEvent.click(
      screen.getByRole('button', { name: /Measured profiles and performance notes/ }),
    )

    expect(screen.getByRole('heading', { name: /Corrected measurements/ })).toBeInTheDocument()
    expect(screen.getAllByRole('columnheader', { name: 'Metric' }).length).toBeGreaterThan(0)
    expect(screen.getByRole('heading', { name: 'Where the time goes' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Demo resource budget' })).toBeInTheDocument()
  })
})

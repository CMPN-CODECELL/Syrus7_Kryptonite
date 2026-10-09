import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import { toConvergenceRows } from './charts/chartData'
import { ChartFrame } from './charts/ChartFrame'
import { ErrorState, Segmented } from './ui'

describe('ErrorState', () => {
  it('shows the server message, code and request id, and retries', async () => {
    const onRetry = vi.fn()
    render(<ErrorState error={new ApiError({ status: 500, code: 'internal_error', message: 'The worker crashed.', requestId: 'req-9' })} title="Could not load" onRetry={onRetry} />)
    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('Could not load')
    expect(alert).toHaveTextContent('The worker crashed.')
    expect(alert).toHaveTextContent('code internal_error · request req-9')
    await userEvent.click(screen.getByRole('button', { name: /try again|retry/i }))
    expect(onRetry).toHaveBeenCalledOnce()
  })

  it('says the API is unreachable on a network error', () => {
    render(<ErrorState error={new ApiError({ status: 0, code: 'network_error', message: 'Could not reach the QuantFolio API.' })} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Cannot reach the API')
  })
})

describe('Segmented', () => {
  function Harness() {
    const [v, setV] = useState<'a' | 'b' | 'c'>('a')
    return (
      <>
        <Segmented name="s" legend="Solver" value={v} onChange={setV} options={[{ value: 'a', label: 'A' }, { value: 'b', label: 'B' }, { value: 'c', label: 'C', disabled: true }]} />
        <output>{v}</output>
      </>
    )
  }
  it('is a labelled radio group that works by keyboard and skips disabled options', async () => {
    render(<Harness />)
    expect(screen.getByRole('group', { name: 'Solver' })).toBeInTheDocument()
    await userEvent.tab()
    expect(screen.getByRole('radio', { name: 'A' })).toHaveFocus()
    await userEvent.keyboard('{ArrowRight}')
    expect(screen.getByRole('radio', { name: 'B' })).toBeChecked()
    expect(screen.getByRole('status')).toHaveTextContent('b')
    expect(screen.getByRole('radio', { name: 'C' })).toBeDisabled()
  })
})

describe('ChartFrame', () => {
  it('gives every chart a text summary and a data table', () => {
    render(
      <ChartFrame title="Convergence" summary="12 measured evaluations." table={<table><tbody><tr><td>row</td></tr></tbody></table>}>
        <svg />
      </ChartFrame>,
    )
    const img = screen.getByRole('img', { name: 'Convergence' })
    expect(img).toHaveAccessibleDescription('12 measured evaluations.')
    expect(screen.getByText('Data table')).toBeInTheDocument()
    expect(screen.getByRole('cell', { hidden: true, name: 'row' })).toBeInTheDocument()
  })
})

describe('toConvergenceRows', () => {
  it('orders warm-start preparation before optimisation and splits the series', () => {
    const p = (phase: 'optimisation' | 'warm_start_preparation', evaluation: number) => ({ phase, evaluation, energy: evaluation, best_feasible_objective: null, feasible_fraction: 1, elapsed_s: 0 })
    const rows = toConvergenceRows([p('optimisation', 2), p('warm_start_preparation', 1), p('optimisation', 1)] as never)
    expect(rows.map((r) => [r.i, r.phase, r.evaluation])).toEqual([
      [1, 'warm_start_preparation', 1],
      [2, 'optimisation', 1],
      [3, 'optimisation', 2],
    ])
    expect(rows[0].prepEnergy).toBe(1)
    expect(rows[0].optEnergy).toBeNull()
    expect(rows[2].optEnergy).toBe(2)
  })
})

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { QaoaCircuitDiagram } from './QaoaCircuitDiagram'

function renderDiagram(runDepth = 1, qubits = 23) {
  const order = Array.from({ length: runDepth }, (_, i) => [`gamma[${i}]`, `beta[${i}]`]).flat()
  const values = order.map((_, i) => 1 + i / 10)
  return render(
    <QaoaCircuitDiagram
      qubits={qubits}
      runDepth={runDepth}
      parameterOrder={order}
      optimalParameters={values}
      gateCounts={{ rzz: 209 * runDepth, rz: 23 * runDepth, rx: 23 * runDepth, h: 23 }}
    />,
  )
}

const blocks = (c: HTMLElement, kind: 'cost' | 'mixer') => c.querySelectorAll(`[data-block="${kind}"]`).length

describe('QaoaCircuitDiagram', () => {
  it('defaults to the run depth, shows its optimised angles and collapses extra qubits', () => {
    const { container } = renderDiagram(1)
    expect(screen.getByRole('radio', { name: /1 this run/ })).toBeChecked()
    expect(blocks(container, 'cost')).toBe(1)
    expect(blocks(container, 'mixer')).toBe(1)
    expect(screen.getByRole('img', { name: /p = 1 layer on 23 qubits/ })).toBeInTheDocument()
    expect(container).toHaveTextContent('γ[0] 1.000')
    expect(container).toHaveTextContent('β[0] 1.100')
    expect(container).toHaveTextContent('+17 more qubits')
    expect(container).toHaveTextContent('Each cost block holds 209 RZZ and 23 RZ gates')
  })

  it('redraws one cost and one mixer block per layer for p = 2 and 3, by click or keyboard', async () => {
    const { container } = renderDiagram(1)
    await userEvent.click(screen.getByRole('radio', { name: '2' }))
    expect(blocks(container, 'cost')).toBe(2)
    expect(blocks(container, 'mixer')).toBe(2)
    // layers beyond the run's depth carry symbolic angles only
    expect(container).toHaveTextContent('γ[1]')
    expect(container).not.toHaveTextContent('γ[1] 1')
    expect(container).toHaveTextContent('Dashed layers were not run (this run used p = 1).')
    await userEvent.keyboard('{ArrowRight}')
    expect(screen.getByRole('radio', { name: '3' })).toBeChecked()
    expect(blocks(container, 'cost')).toBe(3)
    expect(blocks(container, 'mixer')).toBe(3)
    expect(screen.getByRole('img', { name: /p = 3 layers/ })).toBeInTheDocument()
  })

  it('clamps a deeper run to three drawn layers and draws every wire for small circuits', () => {
    const { container } = renderDiagram(5, 4)
    expect(screen.getByRole('radio', { name: '3' })).toBeChecked()
    expect(blocks(container, 'cost')).toBe(3)
    expect(container).toHaveTextContent('γ[2] 1.400')
    expect(container).not.toHaveTextContent('more qubit')
    expect(container).toHaveTextContent('This run used p = 5; up to 3 layers are drawn.')
  })
})

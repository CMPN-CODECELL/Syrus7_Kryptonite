import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { Candle } from '../../api/contract.gen'
import { CandleChart } from './CandleChart'

const CANDLES: Candle[] = [
  { t: '2026-10-09T09:15:00+05:30', o: 100, h: 104, l: 99, c: 103 },
  { t: '2026-10-09T09:20:00+05:30', o: 103, h: 105, l: 101, c: 102 },
  { t: '2026-10-09T09:25:00+05:30', o: 102, h: 106, l: 100, c: 105 },
]

describe('CandleChart', () => {
  it('draws one bar per candle, green up and red down, with a spoken summary and no grid', () => {
    const { container } = render(<CandleChart candles={CANDLES} interval="5m" prevClose={101} label="NIFTY 50" width={480} height={160} />)
    const svg = screen.getByRole('img')
    expect(svg).toHaveAccessibleName('NIFTY 50: opened at 100.00, high 106.00, low 99.00, last 105.00. Previous close 101.00.')
    const bars = container.querySelectorAll('g[data-up]')
    expect([...bars].map((g) => g.getAttribute('data-up'))).toEqual(['1', '0', '1'])
    expect(bars[0].querySelector('rect')).toHaveAttribute('fill', 'var(--color-teal-600)')
    expect(bars[1].querySelector('rect')).toHaveAttribute('fill', 'var(--color-danger)')
    expect(bars[1].querySelector('line')).toHaveAttribute('stroke', 'var(--color-danger)')
    // The only line that is not a wick is the dashed previous close.
    expect(container.querySelectorAll('svg > line')).toHaveLength(1)
    expect(container.querySelector('svg > line')).toHaveAttribute('stroke-dasharray')
  })

  it('reads one bar out on focus and with the arrow keys', () => {
    render(<CandleChart candles={CANDLES} interval="5m" label="NIFTY 50" width={480} />)
    const chart = screen.getByTestId('candle-chart')
    const readout = screen.getByTestId('candle-readout')
    expect(readout).toBeEmptyDOMElement()
    fireEvent.focus(chart)
    expect(readout).toHaveTextContent('09:25Open 102.00High 106.00Low 100.00Close 105.00')
    fireEvent.keyDown(chart, { key: 'ArrowLeft' })
    expect(readout).toHaveTextContent('09:20Open 103.00')
    fireEvent.keyDown(chart, { key: 'Home' })
    fireEvent.keyDown(chart, { key: 'ArrowLeft' })
    expect(readout).toHaveTextContent('09:15')
    fireEvent.blur(chart)
    expect(readout).toBeEmptyDOMElement()
  })

  it('draws wider bodies when asked', () => {
    const { container } = render(<CandleChart candles={CANDLES} interval="5m" label="NIFTY 50" width={800} maxBody={17} />)
    expect(container.querySelector('g[data-up] rect')).toHaveAttribute('width', '17')
  })

  it('renders nothing without candles', () => {
    const { container } = render(<CandleChart candles={[]} interval="5m" label="NIFTY 50" width={480} />)
    expect(container).toBeEmptyDOMElement()
  })
})

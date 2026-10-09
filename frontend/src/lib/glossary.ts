// Plain-language explanations for the terms the app shows, written for someone seeing them for the first time.
// Used by the (i) help buttons next to metrics and controls.

export const GLOSSARY = {
  qubits:
    'A qubit is the quantum version of a bit. Here each stock you select gets one qubit: measured as 1 it is bought, as 0 it is skipped. Extra "slack" qubits help enforce rules such as sector caps.',
  slackQubits: 'Helper qubits that let the circuit respect "at most" rules (for example a sector cap). They are not stocks.',
  qaoa:
    'QAOA (Quantum Approximate Optimisation Algorithm) is a short quantum circuit with tunable angles. A classical optimiser adjusts the angles so that measuring the circuit tends to give good portfolios.',
  depth: 'How many times the circuit repeats its "score, then mix" layer (p). More layers can find better answers but take longer to tune.',
  shots: 'How many times the circuit is run and measured. Each shot gives one candidate portfolio; more shots give more reliable statistics.',
  evaluations: 'How many times the optimiser may try new circuit angles before it stops.',
  objective:
    'The one score every solver tries to make as small as possible: a risk penalty, minus expected return, plus trading costs. Lower is better. Compare it only between solvers on the same problem.',
  gap: 'How much worse the QAOA score is than the best answer (exact search when it ran), as a share of the range from the best to the worst valid portfolio. 0 % means QAOA found the best one.',
  feasible: 'A portfolio is feasible when it follows every rule you set: the number of holdings, sector caps, lots and so on.',
  testReturn:
    'Return of the chosen portfolio over a later period the optimiser never saw (the test window). It is an honest check, not a forecast.',
  validationReturn: 'Return over the period just after training, used to compare settings before looking at the test window.',
  expectedReturn: 'Average yearly return estimated from the training period. Past averages are a weak guide to the future.',
  volatility: 'How much the portfolio value tends to swing in a year (standard deviation). Higher means bumpier.',
  sharpe: 'Return earned per unit of risk, after the risk-free rate. Higher is better.',
  maxDrawdown: 'The largest fall from a peak to a later low during the period.',
  riskAversion: 'How much you dislike risk (q). Higher values make the optimiser prefer steadier stocks over higher expected return.',
  cardinality: 'K, the exact number of stocks the portfolio must hold.',
  sectorCaps: 'The most stocks (or weight) allowed from one sector, so the portfolio is not concentrated in, say, banks.',
  lots: 'In lots mode the budget is split into equal parts (lots) and the circuit decides how many lots each stock gets.',
  costs: 'Trading costs: brokerage in basis points (bps; 100 bps = 1 %) plus a fixed rupee charge per trade.',
  bps: 'Basis points: hundredths of a percent. 25 bps = 0.25 %.',
  budget: 'The amount of money to invest, in rupees. Shares are rounded down to whole shares, so a little cash may be left over.',
  trainingWindow: 'The past period used to estimate returns and risk. Results are then checked on later data the optimiser did not see.',
  baselines:
    'Classical methods that solve the same problem for comparison: exact search (the true best when it is small enough), a relaxation, simulated annealing and a greedy pick.',
  simulator: 'The circuit runs on Qiskit Aer, a quantum simulator on this computer. No real quantum hardware is used.',
  price: 'Closing price on the National Stock Exchange (NSE), in rupees per share.',
  change1y: 'Change in the adjusted closing price over the last year, including dividends.',
} as const

export type GlossaryKey = keyof typeof GLOSSARY

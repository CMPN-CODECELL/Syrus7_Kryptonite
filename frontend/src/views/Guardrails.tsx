// Every guardrail in plain words, with the live limits this server enforces.

import { useCapabilities } from '../api/hooks'
import { Card, IconCheck, IconShield } from '../components/ui'
import { guardrailFamilies, liveLimits } from '../lib/guardrails'
import { FAMILY_ICON } from './shared/guardIcons'

export default function Guardrails() {
  const caps = useCapabilities()
  const L = liveLimits(caps.data)
  const families = guardrailFamilies(L)
  const basis = caps.data?.limits?.qubit_caps_basis
  const limits = [
    { label: 'Qubits per circuit', value: L.qubits, hint: `${L.noiseQubits} with hardware noise` },
    { label: 'Memory per run', value: L.memory, hint: 'estimated before it starts' },
    { label: 'Exact search', value: `${L.exact} stocks`, hint: 'tries every option' },
    { label: 'Circuit depth', value: L.depths, hint: 'QAOA layers allowed' },
    { label: 'Jobs in the queue', value: L.queue, hint: 'run one at a time' },
    { label: 'CSV upload', value: L.upload, hint: `${L.body} for settings` },
  ]

  return (
    <div className="mx-auto max-w-[1440px] px-4 py-6 sm:px-6 lg:px-8">
      <h1 className="text-2xl font-semibold tracking-tight text-ink sm:text-3xl">Guardrails</h1>
      <p className="mt-1 max-w-3xl text-sm text-muted sm:text-base">
        QuantFolio checks every run automatically. A run that cannot work is stopped with a plain reason, the quantum answer is never swapped for a classical
        one, and your data stays on this computer. Here is every check, in six groups.
      </p>

      <Card className="mt-6" aria-labelledby="limits-h">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="limits-h" className="text-base font-semibold text-ink">
            Limits on this server
          </h2>
          <p className="text-xs text-muted">Read live from the server</p>
        </div>
        <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 xl:grid-cols-6" data-testid="guard-limits">
          {limits.map((l) => (
            <div key={l.label} className="min-w-0">
              <dt className="text-xs font-medium text-muted">{l.label}</dt>
              <dd className="mt-1 text-xl font-semibold tracking-tight text-ink tabular">{l.value}</dd>
              <dd className="mt-0.5 text-xs text-faint">{l.hint}</dd>
            </div>
          ))}
        </dl>
        {typeof basis === 'string' && basis ? (
          <details className="mt-4 rounded-md bg-mist px-3 py-2 text-sm">
            <summary className="cursor-pointer font-medium text-ink-2">Why {L.qubits} qubits?</summary>
            <p className="mt-2 leading-relaxed text-muted">{basis}</p>
          </details>
        ) : null}
      </Card>

      <nav aria-label="Guardrail groups" className="mt-6">
        <ul className="flex flex-wrap gap-2">
          {families.map((f) => (
            <li key={f.id}>
              <a
                href={`#${f.id}`}
                className="inline-flex min-h-11 items-center gap-2 rounded-full border border-line bg-canvas px-3 text-sm font-medium text-ink-2 hover:border-cobalt-200 hover:text-cobalt pointer-fine:min-h-9"
              >
                <span className="text-xs font-semibold text-faint">{f.letter}</span>
                {f.title}
              </a>
            </li>
          ))}
        </ul>
      </nav>

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
        {families.map((f) => {
          const Icon = FAMILY_ICON[f.id] ?? IconShield
          return (
            <Card key={f.id} id={f.id} className="scroll-mt-24" aria-labelledby={`${f.id}-h`} data-testid="guard-family">
              <div className="flex items-start gap-3">
                <span className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-cobalt-50 text-cobalt">
                  <Icon size={20} />
                </span>
                <div className="min-w-0">
                  <p className="eyebrow">Group {f.letter}</p>
                  <h2 id={`${f.id}-h`} className="text-lg font-semibold tracking-tight text-ink">
                    {f.title}
                  </h2>
                </div>
              </div>
              <p className="mt-3 text-sm leading-relaxed text-muted">{f.intro}</p>
              <ul className="mt-4 divide-y divide-line border-t border-line">
                {f.items.map((it) => (
                  <li key={it.title} className="flex gap-3 py-3">
                    <IconCheck size={16} className="mt-0.5 shrink-0 text-teal" />
                    <div className="min-w-0">
                      <p className="text-sm font-semibold text-ink">{it.title}</p>
                      <p className="mt-0.5 text-sm leading-relaxed text-muted">{it.body}</p>
                      {it.code ? <p className="mt-1.5 text-xs break-words text-faint">{it.code}</p> : null}
                    </div>
                  </li>
                ))}
              </ul>
            </Card>
          )
        })}
      </div>
    </div>
  )
}

import { useEffect, useState } from 'react'
import { IconCheck, IconCopy } from './icons'

/** Icon button that copies a value (such as a full run id) to the clipboard; the outcome is announced politely. */
export function CopyButton({ value, label, className = '' }: { value: string; label: string; className?: string }) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle')
  useEffect(() => {
    if (state === 'idle') return
    const id = window.setTimeout(() => setState('idle'), 2500)
    return () => window.clearTimeout(id)
  }, [state])
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setState('copied')
    } catch {
      setState('failed')
    }
  }
  return (
    <span className={`inline-flex items-center gap-1 ${className}`}>
      <button
        type="button"
        onClick={() => void copy()}
        aria-label={label}
        title={`${label}: ${value}`}
        className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-md text-muted hover:bg-mist-2 hover:text-ink pointer-fine:min-h-8 pointer-fine:min-w-8"
      >
        {state === 'copied' ? <IconCheck size={16} /> : <IconCopy size={16} />}
      </button>
      <span className="text-xs text-muted" role="status">
        {state === 'copied' ? 'Copied' : state === 'failed' ? 'Could not copy; select the id instead' : ''}
      </span>
    </span>
  )
}

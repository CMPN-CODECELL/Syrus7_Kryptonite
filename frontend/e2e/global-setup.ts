import { rmSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

// Each run starts a fresh console-guard log for its tag (see the guard fixture in helpers.ts).
export default function globalSetup() {
  const here = dirname(fileURLToPath(import.meta.url))
  rmSync(resolve(here, '..', '..', 'evidence', 'e2e', `console-guard-${process.env.QF_E2E_TAG ?? 'local'}.jsonl`), { force: true })
}

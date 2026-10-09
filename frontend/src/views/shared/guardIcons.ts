import type { ComponentType } from 'react'
import { IconCpu, IconDatabase, IconLock, IconMonitor, IconScale, IconShield } from '../../components/ui'

/** One icon per guardrail family (see lib/guardrails.ts). */
export const FAMILY_ICON: Record<string, ComponentType<{ size?: number; className?: string }>> = {
  preflight: IconShield,
  fairness: IconScale,
  data: IconDatabase,
  runtime: IconCpu,
  security: IconLock,
  interface: IconMonitor,
}

export type Window = {
  name: string
  pct: number
  resets_at: number | null
  hours: number
}
export type Account = {
  role: string
  label: string
  plan?: string | null
  kind: 'bars' | 'pools' | 'monthly' | 'reason'
  five?: number | null
  week?: number | null
  monthly?: number | null
  pools?: { name: string; pct: number }[]
  windows?: Window[]
  reason?: string
  notes?: string[]
  age?: number | null
  stale?: boolean
}
export type Card = {
  configured: boolean
  thresholds?: { five: number; week: number }
  now?: number
  accounts?: Account[]
}

declare module 'claude-code' {
  interface PluginState {
    'usage-panel': {
      card: Card | null
      busy: string | null
      message: string | null
      checkedAt: number
      hidden: boolean
    }
  }
}

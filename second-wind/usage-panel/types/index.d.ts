export type Account = {
  role: string
  label: string
  kind: 'bars' | 'pools' | 'monthly' | 'reason'
  five?: number | null
  week?: number | null
  monthly?: number | null
  pools?: { name: string; pct: number }[]
  reason?: string
  notes?: string[]
  age?: number | null
  stale?: boolean
}
export type Preset = { model: string | null; effort: string | null; text: string; note: string }
export type Destination = { worker: string; label: string; headroom: number | null; usage: string; models: Preset[] }
export type Route = { worker: string; label: string; model: string | null; effort: string | null; mode: string | null }
export type Card = {
  configured: boolean
  thresholds?: { five: number; week: number }
  accounts?: Account[]
  destinations?: Destination[]
  route?: Route | null
}

declare module 'claude-code' {
  interface PluginState {
    'usage-panel': {
      card: Card | null
      expanded: string | null
      preset: string
      mode: string
      busy: string | null
      message: string | null
      checkedAt: number
      hidden: boolean
    }
  }
}

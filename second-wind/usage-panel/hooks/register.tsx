import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Account, Card, Window } from '../types'

const PANE = 'usage'
const card = atom({ plugin: 'usage-panel', key: 'card' } as const, null)
const busy = atom({ plugin: 'usage-panel', key: 'busy' } as const, null)
const message = atom({ plugin: 'usage-panel', key: 'message' } as const, null)
const checkedAt = atom({ plugin: 'usage-panel', key: 'checkedAt' } as const, 0)
const hidden = atom({ plugin: 'usage-panel', key: 'hidden' } as const, false)

const GREEN = '#2fa36b'
const AMBER = '#e0a030'
const RED = '#e0524f'
const GREY = '#9aa0a6'
const BLUE = '#3b7ddd'
const TRACK = '#8080804d'

// Green while there is plenty left, amber from half way, red once the
// second-wind handover threshold for that window is reached.
function colour(pct: number | null | undefined, limit: number): string {
  if (pct === null || pct === undefined) return GREY
  if (pct >= limit) return RED
  if (pct >= 50) return AMBER
  return GREEN
}

function limitFor(w: Window, t: { five: number; week: number }): number {
  return w.hours <= 5 ? t.five : t.week
}

// A label short enough for the strip: "Work Claude" is "Work", and a
// hyphenated "Codex-team-main" keeps its first and last words, "Codex main".
function shortName(label: string): string {
  const words = label.replace(/ Claude$/, '').split(/[-\s]+/).filter(Boolean)
  return words.length > 2 ? `${words[0]} ${words[words.length - 1]}` : words.join(' ')
}

function clock(epoch: number): string {
  const d = new Date(epoch * 1000)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

// Time left in a window, and where the average rate so far takes it by the
// reset. An estimate, and drawn as one: a striped tail, never a solid bar.
type Pace = { left: number | null; share: number | null; atReset: number | null; runsOut: number | null }
function pace(w: Window, now: number): Pace {
  if (!w.resets_at) return { left: null, share: null, atReset: null, runsOut: null }
  const span = w.hours * 3600
  const left = Math.max(0, w.resets_at - now)
  const elapsed = span - left
  if (elapsed <= 0 || w.pct <= 0) return { left, share: Math.min(1, left / span), atReset: w.pct, runsOut: null }
  const rate = w.pct / elapsed
  const runsOut = now + (100 - w.pct) / rate
  return {
    left,
    share: Math.min(1, left / span),
    atReset: Math.min(100, w.pct + rate * left),
    runsOut: w.pct < 100 && runsOut < w.resets_at ? runsOut : null,
  }
}

function untilText(seconds: number): string {
  if (seconds < 3570) return `${Math.max(1, Math.round(seconds / 60))}m`
  if (seconds < 48 * 3600) return `${Math.round(seconds / 3600)}h`
  return `${Math.round(seconds / 86400)}d`
}

// Every drawing is a fixed-size image. Without a size, or drawn interactive,
// an Svg is laid out in a default-sized frame that shrinks what is inside it.
const BAR_W = 112
const BAR_H = 10
function barSvg(w: Window, p: Pace, limit: number): string {
  const x = (pct: number) => (Math.min(pct, 100) / 100) * BAR_W
  const used = w.pct > 0 ? Math.max(BAR_H, x(w.pct)) : 0
  const tail = p.atReset !== null && p.atReset > w.pct ? x(p.atReset) - used : 0
  const fill = colour(w.pct, limit)
  const id = `s${fill.slice(1)}`
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${BAR_W}" height="${BAR_H}" viewBox="0 0 ${BAR_W} ${BAR_H}">` +
    `<defs><pattern id="${id}" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">` +
    `<rect width="2" height="4" fill="${fill}" opacity=".55"/></pattern>` +
    `<clipPath id="c"><rect width="${BAR_W}" height="${BAR_H}" rx="${BAR_H / 2}"/></clipPath></defs>` +
    `<g clip-path="url(#c)"><rect width="${BAR_W}" height="${BAR_H}" fill="${TRACK}"/>` +
    (tail > 0 ? `<rect x="${used}" width="${tail}" height="${BAR_H}" fill="url(#${id})"/>` : '') +
    (used ? `<rect width="${used}" height="${BAR_H}" rx="${BAR_H / 2}" fill="${fill}"/>` : '') +
    `<rect x="${x(limit) - 1}" width="2" height="${BAR_H}" fill="#6b7078"/></g></svg>`
}

const RING = 32
function ringSvg(p: Pace): string {
  const r = 13
  const c = 2 * Math.PI * r
  const share = p.share ?? 0
  const label = p.left === null ? '–' : untilText(p.left)
  const ink = p.left === null ? GREY : BLUE
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${RING}" height="${RING}" viewBox="0 0 ${RING} ${RING}">` +
    `<circle cx="16" cy="16" r="${r}" fill="none" stroke="${TRACK}" stroke-width="3"/>` +
    (share > 0 ? `<circle cx="16" cy="16" r="${r}" fill="none" stroke="${BLUE}" stroke-width="3" stroke-linecap="round" ` +
      `stroke-dasharray="${(c * share).toFixed(2)} ${c.toFixed(2)}" transform="rotate(-90 16 16)"/>` : '') +
    `<text x="16" y="20.5" text-anchor="middle" font-family="-apple-system,system-ui,sans-serif" font-size="${label.length > 2 ? 9.5 : 11.5}" ` +
    `font-weight="700" fill="${ink}">${label}</text></svg>`
}

// A small usage ring for the strip.
const DOT = 16
function dotSvg(pct: number | null, limit: number): string {
  const r = 6
  const c = 2 * Math.PI * r
  const share = pct === null ? 0 : Math.min(pct, 100) / 100
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${DOT}" height="${DOT}" viewBox="0 0 ${DOT} ${DOT}">` +
    `<circle cx="8" cy="8" r="${r}" fill="none" stroke="${TRACK}" stroke-width="2.5"/>` +
    `<circle cx="8" cy="8" r="${r}" fill="none" stroke="${colour(pct, limit)}" stroke-width="2.5" ` +
    `stroke-dasharray="${(c * share).toFixed(2)} ${c.toFixed(2)}" transform="rotate(-90 8 8)"/></svg>`
}

// The figure that stands for an account in the strip: its fullest window.
function fullest(a: Account, t: { five: number; week: number }): { pct: number | null; limit: number } {
  const ws = a.windows ?? []
  if (!ws.length) return { pct: null, limit: t.week }
  const top = ws.reduce((m, w) => (w.pct > m.pct ? w : m))
  return { pct: top.pct, limit: limitFor(top, t) }
}

function textBar(pct: number | null | undefined): string {
  if (pct === null || pct === undefined) return '·'.repeat(10) + '  n/a'
  const n = Math.round((Math.min(pct, 100) / 100) * 10)
  return '█'.repeat(n) + '░'.repeat(10 - n) + ` ${String(Math.round(pct)).padStart(3)}%`
}

let sw = ''
let sessionId = ''
let last: Record<string, number> = {}
let stopTimer: (() => void) | null = null

// Where second-wind lives and which session this is, found once per load.
async function locate($: EngineInterface): Promise<void> {
  if (sw && sessionId) return
  const r = await $.process.run([
    'sh', '-c',
    'd=$(python3 -c "import json,os;print(os.path.expanduser(json.load(open(os.path.expanduser(\'~/.second-wind/config.json\'))).get(\'skill_dir\') or \'\'))" 2>/dev/null); ' +
      '[ -d "$d" ] || d="$HOME/.claude/skills/second-wind"; printf %s "$d"',
  ])
  sw = r.stdout.trim()
  sessionId = await $.session.id()
}

// This account's own reading. The session is signed in through the app, which
// keeps that credential to itself, so no outside reader can see this account;
// the engine can. The figures go through statusline.sh, the one writer of the
// shared cache, keyed by this session's profile exactly as a terminal's are.
async function recordHere($: EngineInterface): Promise<void> {
  const usage = await $.session.usage()
  const five = usage.rateLimits.find(r => r.kind === 'five_hour')
  const week = usage.rateLimits.find(r => r.kind === 'seven_day')
  if (!five && !week) return
  const epoch = (iso?: string) => (iso ? Math.floor(Date.parse(iso) / 1000) : undefined)
  const windowOf = (r?: { percentUsed: number; resetsAt?: string }) =>
    r ? { used_percentage: r.percentUsed, resets_at: epoch(r.resetsAt) } : undefined
  const payload = {
    version: (await $.session.version()).version,
    rate_limits: { five_hour: windowOf(five), seven_day: windowOf(week) },
  }
  await $.process.run(['sh', `${sw}/scripts/statusline.sh`], { stdin: JSON.stringify(payload) })
}

async function load($: EngineInterface): Promise<void> {
  await locate($)
  if (!sw) return
  await recordHere($).catch(() => undefined)
  const r = await $.process.run(['python3', `${sw}/scripts/card_json.py`, '--session', sessionId])
  if (r.exitCode !== 0) return
  let next: Card
  try {
    next = JSON.parse(r.stdout) as Card
  } catch {
    return
  }
  // A toast the moment any window crosses its handover threshold.
  const t = next.thresholds ?? { five: 90, week: 80 }
  const seen: Record<string, number> = {}
  for (const a of next.accounts ?? []) {
    for (const w of a.windows ?? []) {
      const key = `${a.role}:${w.name}`
      const limit = limitFor(w, t)
      seen[key] = w.pct
      if (last[key] !== undefined && last[key] < limit && w.pct >= limit) {
        $.ui.toast(`${a.label} ${w.name.toLowerCase()} limit at ${Math.round(w.pct)}%`, { timeoutMs: 8000 })
      }
    }
  }
  last = seen
  await update($, card, () => next)
  await update($, checkedAt, () => Date.now())
}

async function refresh($: EngineInterface): Promise<void> {
  await update($, busy, () => 'refresh')
  await update($, message, () => 'Reading every account, this takes a minute or two…')
  const r = await $.process.run(['sh', `${sw}/scripts/usage-refresh.sh`, '--force'], { timeoutMs: 300_000 })
    .catch(() => null)
  await load($)
  await update($, busy, () => null)
  await update($, message, () => (r && r.exitCode === 0 ? null : 'Refresh finished with an error; showing the latest readings.'))
}

async function setHidden($: EngineInterface, value: boolean): Promise<void> {
  await update($, hidden, () => value)
  await $.store.set('hidden', value)
  if (value) $.ui.toast('Usage strip hidden. /usage-panel brings it back.')
}

async function openPane($: EngineInterface): Promise<void> {
  await $.ui.open({ id: PANE, title: 'Usage', focus: true, closeOnEscape: true, rows: 30, columns: 64 })
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await locate($)
    await update($, hidden, () => false)
    if ((await $.store.get('hidden')) === true) await update($, hidden, () => true)
    await $.command.register({ name: 'usage-panel', description: 'Open the second-wind usage panel' })
    await load($)
    stopTimer?.()
    stopTimer = $.clock.every(60_000, () => void load($))
    return next(e)
  })

  on('session.measure', async ($, e, next) => {
    if (e.changed.includes('rateLimits')) await load($)
    return next(e)
  })

  on('command.run', { command: 'usage-panel' }, async $ => {
    await load($)
    await openPane($)
    return { text: 'Usage panel opened.' }
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const c = await read($, card)
    if (e.props.hasSurvey || !c || !c.configured || (await read($, hidden))) return next(e)
    const els = $.ui.resolve(e)
    const { Box, Button, Text } = els
    const t = c.thresholds ?? { five: 90, week: 80 }
    return (
      <Box columnGap={2} rowGap={0} flexWrap="wrap" alignItems="center">
        {(c.accounts ?? []).map(a => {
          const f = fullest(a, t)
          return (
            <Box key={`chip-${a.role}`} columnGap={1} alignItems="center">
              {'Svg' in els ? (
                <els.Svg source={dotSvg(f.pct, f.limit)} alt="" width={DOT} height={DOT} />
              ) : (
                <Text color={colour(f.pct, f.limit)}>●</Text>
              )}
              <Text dimColor>{shortName(a.label)}</Text>
              <Text bold color={f.pct === null ? undefined : colour(f.pct, f.limit)}>
                {f.pct === null ? '–' : `${Math.round(f.pct)}%`}
              </Text>
            </Box>
          )
        })}
        <Button key="open" label="Details" plain onPress={() => void openPane($)} />
        <Button key="hide" label="Hide" plain dimColor onPress={() => void setHidden($, true)} />
      </Box>
    )
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const els = $.ui.resolve(e)
    const { Box, Button, Markdown, Text } = els
    const c = await read($, card)
    if (!c) return <Text dimColor>Loading readings…</Text>
    if (!c.configured) return <Text>second-wind is not set up. Run /second-wind.</Text>
    const t = c.thresholds ?? { five: 90, week: 80 }
    const working = await read($, busy)
    const note = await read($, message)
    const isHidden = await read($, hidden)
    const at = await read($, checkedAt)
    const now = Date.now() / 1000
    const hasSvg = 'Svg' in els

    // One row per limit, full width in fixed columns, so every bar, figure
    // and ring sits in the same place whatever the label or the account.
    const row = (a: Account, w: Window) => {
      const p = pace(w, now)
      const limit = limitFor(w, t)
      const over = w.pct >= limit
      return (
        <Box key={`${a.role}-${w.name}`} flexDirection="column" width="100%">
          <Box width="100%" alignItems="center">
            <Box width="34%">
              <Text dimColor wrap="truncate-end">{w.name}</Text>
            </Box>
            <Box width="40%">
              {hasSvg && 'Svg' in els ? (
                <els.Svg source={barSvg(w, p, limit)} alt={`${w.name} ${Math.round(w.pct)}% used`} width={BAR_W} height={BAR_H} />
              ) : (
                <Text>{textBar(w.pct)}</Text>
              )}
            </Box>
            <Box width="12%" justifyContent="flex-end">
              <Text bold color={colour(w.pct, limit)}>{`${Math.round(w.pct)}%`}</Text>
            </Box>
            <Box width="14%" justifyContent="flex-end">
              {hasSvg && 'Svg' in els ? (
                <els.Svg source={ringSvg(p)} alt={p.left === null ? 'reset time not known' : `resets in ${untilText(p.left)}`} width={RING} height={RING} />
              ) : (
                <Text dimColor>{p.left === null ? '–' : untilText(p.left)}</Text>
              )}
            </Box>
          </Box>
          {p.runsOut !== null && (
            <Text color={RED}>⚠ Runs out about {clock(p.runsOut)} at this pace, before its {clock(w.resets_at ?? 0)} refill</Text>
          )}
          {over && p.runsOut === null && w.hours > 5 && (
            <Text color={RED}>Over the {limit}% handover line</Text>
          )}
        </Box>
      )
    }

    return (
      <Box flexDirection="column" rowGap={1}>
        <Box columnGap={1} alignItems="center" flexWrap="wrap">
          <Text dimColor>{at ? `Updated ${clock(at / 1000)}` : ''}</Text>
          <Button key="refresh" label={working === 'refresh' ? 'Refreshing…' : 'Refresh'} onPress={() => void refresh($)} />
          <Button key="strip" label={isHidden ? 'Show strip' : 'Hide strip'} onPress={() => void setHidden($, !isHidden)} />
        </Box>
        {note && <Text dimColor>{note}</Text>}
        {(c.accounts ?? []).map((a, i) => {
          const ws = a.windows ?? []
          return (
            <Box key={`acc-${a.role}`} flexDirection="column" width="100%">
              {i > 0 && <Markdown key={`rule-${a.role}`} text="---" dimColor />}
              <Box columnGap={1}>
                <Text bold>{a.label}</Text>
                {a.plan && <Text dimColor>{a.plan}</Text>}
              </Box>
              {a.kind === 'reason' || !ws.length
                ? <Text dimColor>{a.reason ?? 'no reading yet'}</Text>
                : ws.map(w => row(a, w))}
            </Box>
          )
        })}
        <Text dimColor>Bar: used · stripes: where this pace takes it by the refill · ring: time left to refill</Text>
      </Box>
    )
  })
}

import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Account, Card, Destination } from '../types'

const PANE = 'usage'
const card = atom({ plugin: 'usage-panel', key: 'card' } as const, null)
const expanded = atom({ plugin: 'usage-panel', key: 'expanded' } as const, null)
const preset = atom({ plugin: 'usage-panel', key: 'preset' } as const, '0')
const mode = atom({ plugin: 'usage-panel', key: 'mode' } as const, 'work')
const busy = atom({ plugin: 'usage-panel', key: 'busy' } as const, null)
const message = atom({ plugin: 'usage-panel', key: 'message' } as const, null)
const checkedAt = atom({ plugin: 'usage-panel', key: 'checkedAt' } as const, 0)
const hidden = atom({ plugin: 'usage-panel', key: 'hidden' } as const, false)

const GREEN = '#2fa36b'
const AMBER = '#e0a030'
const RED = '#e0524f'
const GREY = '#9aa0a6'

// Green while there is plenty left, amber from half way, red once the
// second-wind handover threshold for that window is reached.
function colour(pct: number | null | undefined, limit: number): string {
  if (pct === null || pct === undefined) return GREY
  if (pct >= limit) return RED
  if (pct >= 50) return AMBER
  return GREEN
}

// A label short enough for the band: "Work Claude" is "Work", and a
// hyphenated "Codex-team-main" keeps its first and last words, "Codex main".
function shortName(label: string): string {
  const words = label.replace(/ Claude$/, '').split(/[-\s]+/).filter(Boolean)
  return words.length > 2 ? `${words[0]} ${words[words.length - 1]}` : words.join(' ')
}

function age(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return ''
  const m = Math.round(seconds / 60)
  return m < 60 ? `${m}m` : `${Math.round(m / 60)}h`
}

// One thin bar as a fixed-size image. Everything else is the surface's own
// text, so it reads at the app's size; an Svg with no size, or an interactive
// one, is laid out in a default-sized frame and shrinks what is inside it.
const BAR_W = 96
const BAR_H = 8
function barSvg(pct: number | null | undefined, limit: number): string {
  const fill = pct === null || pct === undefined ? 0 : Math.max(BAR_H, (Math.min(pct, 100) / 100) * BAR_W)
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${BAR_W}" height="${BAR_H}" viewBox="0 0 ${BAR_W} ${BAR_H}">` +
    `<rect width="${BAR_W}" height="${BAR_H}" rx="${BAR_H / 2}" fill="#8080804d"/>` +
    (fill ? `<rect width="${fill}" height="${BAR_H}" rx="${BAR_H / 2}" fill="${colour(pct, limit)}"/>` : '') +
    '</svg>'
}

function pctText(pct: number | null | undefined): string {
  return pct === null || pct === undefined ? 'n/a' : `${Math.round(pct)}%`
}

// The figure that decides an account's dot: its fullest window.
function worst(a: Account, t: { five: number; week: number }): { pct: number | null; limit: number } {
  if (a.kind === 'bars') {
    const five = a.five ?? -1
    const week = a.week ?? -1
    const pct = Math.max(five, week)
    return { pct: pct < 0 ? null : pct, limit: five > week ? t.five : t.week }
  }
  if (a.kind === 'monthly') return { pct: a.monthly ?? null, limit: t.week }
  if (a.kind === 'pools') {
    const pct = Math.max(-1, ...(a.pools ?? []).map(p => p.pct))
    return { pct: pct < 0 ? null : pct, limit: t.week }
  }
  return { pct: null, limit: t.week }
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
    if (a.kind !== 'bars') continue
    for (const [win, pct, limit] of [['5-hour', a.five, t.five], ['weekly', a.week, t.week]] as const) {
      if (pct === null || pct === undefined) continue
      const key = `${a.role}:${win}`
      seen[key] = pct
      if (last[key] !== undefined && last[key] < limit && pct >= limit) {
        $.ui.toast(`${a.label} ${win} limit at ${Math.round(pct)}%`, { timeoutMs: 8000 })
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
  await update($, message, () => (r && r.exitCode === 0 ? 'Readings refreshed.' : 'Refresh finished with an error; showing the latest readings.'))
}

async function route($: EngineInterface, d: Destination): Promise<void> {
  const p = d.models[Number(await read($, preset))] ?? d.models[0]
  const m = await read($, mode)
  const argv = ['python3', `${sw}/scripts/route.py`, '--set', d.worker, '--session', sessionId, '--mode', m]
  if (p?.model) argv.push('--model', p.model)
  if (p?.effort) argv.push('--effort', p.effort)
  await update($, busy, () => 'route')
  const r = await $.process.run(argv)
  await update($, busy, () => null)
  await update($, expanded, () => null)
  await update($, message, () =>
    r.exitCode === 0 ? `Next tasks go to ${d.label}${p ? `, ${p.text}` : ''}, ${m === 'review' ? 'review only' : 'full access'}.` : `Routing failed: ${r.stderr.trim()}`)
  await load($)
  $.ui.toast(r.exitCode === 0 ? `Routing to ${d.label}` : 'Routing failed')
}

async function stopRoute($: EngineInterface): Promise<void> {
  await $.process.run(['python3', `${sw}/scripts/route.py`, '--clear', '--session', sessionId])
  await update($, message, () => 'Routing off. Work stays on this account.')
  await load($)
}

async function setHidden($: EngineInterface, value: boolean): Promise<void> {
  await update($, hidden, () => value)
  await $.store.set('hidden', value)
  if (value) $.ui.toast('Usage strip hidden. /usage-panel brings it back.')
}

async function openPane($: EngineInterface): Promise<void> {
  await $.ui.open({ id: PANE, title: 'Usage', focus: true, closeOnEscape: true, rows: 22, columns: 72 })
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
    const { Box, Button, Text } = $.ui.resolve(e)
    const t = c.thresholds ?? { five: 90, week: 80 }
    return (
      <Box columnGap={2} rowGap={0} flexWrap="wrap" alignItems="center">
        {(c.accounts ?? []).map(a => {
          const w = worst(a, t)
          return (
            <Box key={`chip-${a.role}`} columnGap={1}>
              <Text color={colour(w.pct, w.limit)}>●</Text>
              <Text bold>{w.pct === null ? '–' : `${Math.round(w.pct)}%`}</Text>
              <Text dimColor>{shortName(a.label)}</Text>
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
    const { Box, Button, Text } = els
    const c = await read($, card)
    if (!c) return <Text dimColor>Loading readings…</Text>
    if (!c.configured) return <Text>second-wind is not set up. Run /second-wind.</Text>
    const t = c.thresholds ?? { five: 90, week: 80 }
    const open = await read($, expanded)
    const working = await read($, busy)
    const note = await read($, message)
    const chosen = await read($, preset)
    const chosenMode = await read($, mode)
    const isHidden = await read($, hidden)
    const ages = (c.accounts ?? []).map(a => a.age).filter((n): n is number => typeof n === 'number')
    const oldest = ages.length ? age(Math.max(...ages)) : ''
    const dests = new Map((c.destinations ?? []).map(d => [d.worker, d]))

    const meter = (label: string, pct: number | null | undefined, limit: number, key: string) => (
      <Box key={key} columnGap={1} alignItems="center">
        <Text dimColor>{label}</Text>
        {'Svg' in els ? (
          <els.Svg source={barSvg(pct, limit)} alt={`${label} ${pctText(pct)}`} width={BAR_W} height={BAR_H} />
        ) : (
          <Text>{textBar(pct)}</Text>
        )}
        <Text bold={pct !== null && pct !== undefined && pct >= limit} color={pct !== null && pct !== undefined && pct >= limit ? RED : undefined}>
          {pctText(pct)}
        </Text>
      </Box>
    )

    return (
      <Box flexDirection="column" rowGap={1}>
        <Box columnGap={1} alignItems="center" flexWrap="wrap">
          <Text dimColor>{oldest ? `Updated ${oldest} ago` : ''}</Text>
          <Button key="refresh" label={working === 'refresh' ? 'Refreshing…' : 'Refresh'} onPress={() => void refresh($)} />
          <Button key="strip" label={isHidden ? 'Show strip' : 'Hide strip'} onPress={() => void setHidden($, !isHidden)} />
        </Box>
        {c.route && (
          <Box columnGap={1} alignItems="center" flexWrap="wrap">
            <Text color={AMBER} bold>
              Routing to {c.route.label}{c.route.model ? `, ${c.route.model}` : ''}{c.route.effort ? ` ${c.route.effort}` : ''}, {c.route.mode === 'review' ? 'review only' : 'full access'}
            </Text>
            <Button key="stop" label="Stop routing" onPress={() => void stopRoute($)} />
          </Box>
        )}
        {note && <Text dimColor>{note}</Text>}
        {(c.accounts ?? []).map(a => {
          const d = dests.get(a.role)
          const w = worst(a, t)
          const notes = a.kind === 'reason' ? a.reason ?? '' : (a.notes ?? []).join(' · ')
          return (
            <Box key={`row-${a.role}`} flexDirection="column">
              <Box columnGap={1} alignItems="center" justifyContent="space-between">
                <Box columnGap={1} alignItems="center">
                  <Text color={colour(w.pct, w.limit)}>●</Text>
                  <Text bold>{a.label}</Text>
                </Box>
                {d && (
                  <Button
                    key={`go-${a.role}`}
                    label={open === a.role ? 'Cancel' : 'Send tasks here'}
                    plain
                    onPress={() => {
                      void update($, expanded, cur => (cur === a.role ? null : a.role))
                      void update($, preset, () => '0')
                    }}
                  />
                )}
              </Box>
              {a.kind === 'bars' && (
                <Box columnGap={3} flexWrap="wrap">
                  {meter('5h', a.five, t.five, `five-${a.role}`)}
                  {meter('week', a.week, t.week, `week-${a.role}`)}
                </Box>
              )}
              {a.kind === 'monthly' && meter('month', a.monthly, t.week, `month-${a.role}`)}
              {a.kind === 'pools' && (
                <Text>
                  {(a.pools ?? []).map(p => `${p.name} ${Math.round(p.pct)}%`).join('  ·  ')}
                </Text>
              )}
              {notes && <Text dimColor>{notes}</Text>}
              {d && open === a.role && (
                <Box columnGap={1} alignItems="center" flexWrap="wrap" marginTop={1}>
                  <els.Select
                    key={`preset-${a.role}`}
                    label="Model"
                    value={chosen}
                    options={d.models.map((m, i) => ({ value: String(i), label: m.note ? `${m.text}: ${m.note}` : m.text }))}
                    onSelect={v => void update($, preset, () => v)}
                  />
                  <els.Select
                    key={`mode-${a.role}`}
                    label="Access"
                    value={chosenMode}
                    options={[
                      { value: 'work', label: 'Full access (does the work)' },
                      { value: 'review', label: 'Review only (read, no edits)' },
                    ]}
                    onSelect={v => void update($, mode, () => v)}
                  />
                  <Button
                    key={`route-${a.role}`}
                    variant="primary"
                    label={working === 'route' ? 'Routing…' : 'Route'}
                    onPress={() => void route($, d)}
                  />
                </Box>
              )}
            </Box>
          )
        })}
        <Text dimColor>Green under 50% · amber from 50% · red at your handover limit ({t.five}% 5-hour, {t.week}% weekly)</Text>
      </Box>
    )
  })
}

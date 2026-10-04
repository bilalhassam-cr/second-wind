import { test, expect, mock } from 'claude-code/testing'

const PANE = { component: 'Pane', requestId: 'usage', props: { title: 'Usage', isFocused: true, bodyColumns: 80, placement: 'dock' } as never } as const
const BAND = { component: 'AbovePrompt', props: { hasSurvey: false } as never } as const

// Placeholder accounts, times relative to the moment the test runs.
function card(now: number) {
  const h = 3600
  const win = (name: string, pct: number, left: number, hours: number) => ({ name, pct, resets_at: now + left, hours })
  return {
    configured: true, thresholds: { five: 90, week: 80 }, now,
    accounts: [
      { role: 'primary', label: 'Work Claude', kind: 'bars', plan: 'Claude Team',
        windows: [win('5-hour', 10, 2 * h, 5), win('Week', 86, 9 * h, 168), win('Fable week', 96, 9 * h, 168)] },
      { role: 'secondary', label: 'Personal Claude', kind: 'bars', plan: 'Claude Max',
        windows: [win('5-hour', 62, 1 * h, 5), win('Week', 27, 96 * h, 168)] },
      { role: 'codex', label: 'Codex-team-main', kind: 'bars', plan: 'Business',
        windows: [win('Week', 2, 126 * h, 168)] },
      { role: 'grok', label: 'Grok', kind: 'reason', reason: 'login expired' },
      { role: 'cursor', label: 'Cursor', kind: 'pools', plan: 'Pro',
        windows: [win('Month', 0, 26 * 24 * h, 720)] },
    ],
  }
}

test('the panel draws each limit as a bar, a figure and a ring, and the strip hides', async ($, on) => {
  let CARD = JSON.stringify(card(Math.floor(Date.now() / 1000)))
  const calls: string[][] = []
  const stdins: string[] = []
  on('process.run', async (_$, e) => {
    const argv = [...e.argv]
    calls.push(argv)
    if (e.init?.stdin) stdins.push(e.init.stdin)
    const out = argv[0] === 'sh' && argv[1] === '-c' ? '/sw' : argv.some(a => a.endsWith('card_json.py')) ? CARD : 'ok'
    return { value: { exitCode: 0, stdout: out, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('session.id', async () => ({ value: 'sess-1' }))
  on('session.usage', async () => ({ value: { rateLimits: [
    { kind: 'five_hour', percentUsed: 9, resetsAt: '2030-01-01T17:40:00.000Z' },
    { kind: 'seven_day', percentUsed: 86, resetsAt: '2030-01-02T01:00:00.000Z' },
  ] } as never }))
  on('session.version', async () => ({ value: { version: '9.9.9' } as never }))
  const toasts: string[] = []
  mock.store(on)
  on('ui.render', { component: 'AbovePrompt' }, async () => ({ type: 'Box', props: {}, children: [] }) as never)
  on('ui.open', async () => ({ value: { isPlaced: true } as never }))
  on('ui.toast', async (_$, e) => { toasts.push(String((e as { text?: string }).text ?? e)); return { value: undefined as never } })

  await $.command.run({ command: 'usage-panel' })

  // This account's figures go to statusline.sh in the status line's own shape.
  expect(JSON.parse(stdins[0]!)).toEqual({ version: '9.9.9', rate_limits: {
    five_hour: { used_percentage: 9, resets_at: 1893519600 },
    seven_day: { used_percentage: 86, resets_at: 1893546000 } } })

  const pane = await $.ui.mount({ plugin: 'usage-panel', surface: 'desktop', ...PANE })
  const svgs = await pane.findAll({ type: 'Svg' })
  // 7 limits with readings, each a bar and a ring; the account with no reading draws none.
  expect(svgs.filter(s => String(s.props.alt).includes('% used')).length).toBe(7)
  expect(svgs.filter(s => String(s.props.alt).startsWith('resets in')).length).toBe(7)
  for (const s of svgs) {
    expect(typeof s.props.width).toBe('number')
    expect(typeof s.props.height).toBe('number')
    expect(s.props.isInteractive).toBeUndefined()
  }
  const fable = svgs.find(s => s.props.alt === 'Fable week 96% used')
  expect(String(fable?.props.source)).toContain('#e0524f')
  expect(String(svgs.find(s => s.props.alt === '5-hour 62% used')?.props.source)).toContain('#e0a030')
  // The pace tail: Personal week at 27% with 4 of 7 days left is striped onwards.
  expect(String(svgs.find(s => s.props.alt === 'Week 27% used')?.props.source)).toContain('url(#s2fa36b)')
  expect(svgs.find(s => s.props.alt === 'resets in 26d')).toBeDefined()
  expect(svgs.find(s => s.props.alt === 'resets in 9h')).toBeDefined()
  // Fable at 96% with 9 of 168 hours left runs out before its refill.
  expect((await pane.find({ type: 'Text', text: /Runs out about/ }))).toBeDefined()
  expect(await pane.find({ type: 'Text', text: 'login expired' })).toBeDefined()
  // No routing in the panel any more.
  expect(await pane.find({ type: 'Button', text: /Send/ })).toBeUndefined()
  expect(await pane.find({ type: 'Select' })).toBeUndefined()

  const band = await $.ui.mount({ plugin: 'usage-panel', surface: 'desktop', ...BAND })
  expect(await band.find({ type: 'Text', text: '96%' })).toBeDefined()
  expect(await band.find({ type: 'Text', text: 'Work' })).toBeDefined()
  await band.press({ key: 'hide' })
  expect(await band.find({ key: 'open' })).toBeUndefined()
  await pane.press({ key: 'strip' })
  expect(await band.find({ key: 'open' })).toBeDefined()
  await band.unmount()
  await pane.unmount()

  // Codex-team-main jumps from 2% to 85% weekly between readings: one toast.
  const next = JSON.parse(CARD)
  next.accounts[2].windows[0].pct = 85
  CARD = JSON.stringify(next)
  await $.command.run({ command: 'usage-panel' })
  expect(toasts.some(t => t.includes('Codex-team-main week limit at 85%'))).toBe(true)

  const term = await $.ui.mount({ plugin: 'usage-panel', surface: 'terminal', ...BAND })
  expect(await term.find({ type: 'Text', text: 'Personal' })).toBeDefined()
  await term.unmount()
})

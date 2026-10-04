import { test, expect, mock } from 'claude-code/testing'

let CARD: string = "{\"configured\": true, \"thresholds\": {\"five\": 90, \"week\": 80}, \"route\": null, \"accounts\": [{\"role\": \"primary\", \"label\": \"Work Claude\", \"kind\": \"reason\", \"reason\": \"no Claude Code sign-in for its reader profile\"}, {\"role\": \"secondary\", \"label\": \"Personal Claude\", \"kind\": \"bars\", \"five\": 62.0, \"week\": 27.0, \"notes\": [\"resets 13:00 Oct 8\", \"Fable week 35%\"], \"age\": 600, \"stale\": false}, {\"role\": \"codex\", \"label\": \"Codex-team-main\", \"kind\": \"bars\", \"five\": null, \"week\": 2.0, \"notes\": [\"resets 18:34 Oct 9\"], \"age\": 600, \"stale\": false}, {\"role\": \"codex2\", \"label\": \"Codex-team-spare\", \"kind\": \"bars\", \"five\": null, \"week\": 85.0, \"notes\": [\"resets 17:54 Oct 9\"], \"age\": 600, \"stale\": false}, {\"role\": \"codex3\", \"label\": \"Codex personal\", \"kind\": \"bars\", \"five\": null, \"week\": 5.0, \"notes\": [\"resets 17:13 Oct 9\"], \"age\": 600, \"stale\": false}, {\"role\": \"grok\", \"label\": \"Grok\", \"kind\": \"bars\", \"five\": null, \"week\": 1.0, \"notes\": [\"resets 07:11 Oct 9\"], \"age\": 600, \"stale\": false}, {\"role\": \"cursor\", \"label\": \"Cursor\", \"kind\": \"pools\", \"pools\": [{\"name\": \"included\", \"pct\": 0.0}, {\"name\": \"auto\", \"pct\": 0.0}, {\"name\": \"api\", \"pct\": 0.0}], \"notes\": [\"resets Oct 30\"], \"age\": 600, \"stale\": false}], \"destinations\": [{\"worker\": \"cursor\", \"label\": \"Cursor\", \"headroom\": 100, \"usage\": \"\", \"models\": [{\"model\": null, \"effort\": null, \"text\": \"default\", \"note\": \"its own model\"}]}, {\"worker\": \"grok\", \"label\": \"Grok\", \"headroom\": 99, \"usage\": \"\", \"models\": [{\"model\": null, \"effort\": \"high\", \"text\": \"high effort\", \"note\": \"\"}]}, {\"worker\": \"codex\", \"label\": \"Codex-team-main\", \"headroom\": 98, \"usage\": \"\", \"models\": [{\"model\": \"gpt-6.1-sol\", \"effort\": \"high\", \"text\": \"gpt-6.1-sol high\", \"note\": \"high effort\"}, {\"model\": \"gpt-6.1-sol\", \"effort\": \"medium\", \"text\": \"gpt-6.1-sol medium\", \"note\": \"everyday tasks\"}, {\"model\": \"gpt-6-astra\", \"effort\": \"high\", \"text\": \"gpt-6-astra high\", \"note\": \"the most demanding work\"}]}, {\"worker\": \"codex3\", \"label\": \"Codex personal\", \"headroom\": 95, \"usage\": \"\", \"models\": [{\"model\": \"gpt-6.1-sol\", \"effort\": \"high\", \"text\": \"gpt-6.1-sol high\", \"note\": \"high effort\"}, {\"model\": \"gpt-6.1-sol\", \"effort\": \"medium\", \"text\": \"gpt-6.1-sol medium\", \"note\": \"everyday tasks\"}, {\"model\": \"gpt-6-astra\", \"effort\": \"high\", \"text\": \"gpt-6-astra high\", \"note\": \"the most demanding work\"}]}, {\"worker\": \"codex2\", \"label\": \"Codex-team-spare\", \"headroom\": 15, \"usage\": \"\", \"models\": [{\"model\": \"gpt-6.1-sol\", \"effort\": \"high\", \"text\": \"gpt-6.1-sol high\", \"note\": \"high effort\"}, {\"model\": \"gpt-6.1-sol\", \"effort\": \"medium\", \"text\": \"gpt-6.1-sol medium\", \"note\": \"everyday tasks\"}, {\"model\": \"gpt-6-astra\", \"effort\": \"high\", \"text\": \"gpt-6-astra high\", \"note\": \"the most demanding work\"}]}, {\"worker\": \"secondary\", \"label\": \"Personal Claude\", \"headroom\": 38, \"usage\": \"\", \"models\": [{\"model\": \"claude-sonnet-5-5\", \"effort\": \"medium\", \"text\": \"sonnet medium\", \"note\": \"\"}]}]}"
const PANE = { component: 'Pane', requestId: 'usage', props: { title: 'Usage', isFocused: true, bodyColumns: 80, placement: 'dock' } as never } as const
const BAND = { component: 'AbovePrompt', props: { hasSurvey: false } as never } as const

test('pane and band draw, and routing calls route.py', async ($, on) => {
  const calls: string[][] = []
  on('process.run', async (_$, e) => {
    const argv = [...e.argv]
    calls.push(argv)
    if (e.init?.stdin) stdins.push(e.init.stdin)
    const out = argv[0] === 'sh' && argv[1] === '-c' ? '/sw' : argv.some(a => a.endsWith('card_json.py')) ? CARD : 'ok'
    return { value: { exitCode: 0, stdout: out, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('session.id', async () => ({ value: 'sess-1' }))
  const stdins: string[] = []
  on('session.usage', async () => ({ value: { rateLimits: [
    { kind: 'five_hour', percentUsed: 9, resetsAt: '2030-01-01T17:40:00.000Z' },
    { kind: 'seven_day', percentUsed: 86, resetsAt: '2030-01-02T01:00:00.000Z' },
  ] } as never }))
  on('session.version', async () => ({ value: { version: '9.9.9' } as never }))
  const toasts: string[] = []
  mock.store(on)
  // The engine's own strip, drawn when the plugin passes: nothing to show.
  on('ui.render', { component: 'AbovePrompt' }, async () => ({ type: 'Box', props: {}, children: [] }) as never)
  on('ui.open', async () => ({ value: { isPlaced: true } as never }))
  on('ui.toast', async (_$, e) => { toasts.push(String((e as { text?: string }).text ?? e)); return { value: undefined as never } })

  await $.command.run({ command: 'usage-panel' })

  // This account's figures go to statusline.sh in the status line's own shape.
  expect(calls.some(c => c.join(' ') === 'sh /sw/scripts/statusline.sh')).toBe(true)
  expect(JSON.parse(stdins[0])).toEqual({ version: '9.9.9', rate_limits: {
    five_hour: { used_percentage: 9, resets_at: 1893519600 },
    seven_day: { used_percentage: 86, resets_at: 1893546000 } } })

  const pane = await $.ui.mount({ plugin: 'usage-panel', surface: 'desktop', ...PANE })
  // Labels and figures are the surface's own text; only the bars are images,
  // each drawn at a fixed size so no frame of default size inflates the row.
  expect(await pane.find({ type: 'Text', text: 'Codex-team-spare' })).toBeDefined()
  const bars = await pane.findAll({ type: 'Svg' })
  expect(bars.length).toBe(10)
  for (const b of bars) {
    expect(b.props.width).toBe(96)
    expect(b.props.height).toBe(8)
    expect(b.props.isInteractive).toBeUndefined()
  }
  expect(String(bars.find(b => b.props.alt === 'week 85%')?.props.source)).toContain('#e0524f')
  expect(String(bars.find(b => b.props.alt === '5h 62%')?.props.source)).toContain('#e0a030')

  // Six destinations get a button; the primary account does not.
  expect((await pane.findAll({ type: 'Button', text: /Send tasks here/ })).length).toBe(6)

  await pane.press({ key: 'go-codex' })
  expect(await pane.find({ key: 'preset-codex' })).toBeDefined()
  await pane.select({ key: 'preset-codex', value: '2' })
  await pane.select({ key: 'mode-codex', value: 'review' })
  await pane.press({ key: 'route-codex' })
  const routed = calls.find(c => c.some(a => a.endsWith('route.py')))
  expect(routed).toEqual(['python3', '/sw/scripts/route.py', '--set', 'codex', '--session', 'sess-1', '--mode', 'review', '--model', 'gpt-6-astra', '--effort', 'high'])
  expect((await pane.find({ type: 'Text', text: /Next tasks go to/ }))?.text).toContain('review only')
  await pane.unmount()

  const band = await $.ui.mount({ plugin: 'usage-panel', surface: 'desktop', ...BAND })
  expect(await band.find({ type: 'Svg' })).toBeUndefined()
  expect(await band.find({ type: 'Text', text: '85%' })).toBeDefined()
  expect(await band.find({ key: 'open' })).toBeDefined()
  // Hide takes the strip away; the panel's button brings it back.
  await band.press({ key: 'hide' })
  expect(await band.find({ key: 'open' })).toBeUndefined()
  const again = await $.ui.mount({ plugin: 'usage-panel', surface: 'desktop', ...PANE })
  expect((await again.find({ key: 'strip' }))?.text).toContain('Show strip')
  await again.press({ key: 'strip' })
  expect(await band.find({ key: 'open' })).toBeDefined()
  await again.unmount()
  await band.unmount()

  // Codex personal jumps from 5% to 95% weekly between readings: one toast.
  const next = JSON.parse(CARD)
  next.accounts.find((a: { role: string }) => a.role === 'codex3').week = 95
  CARD = JSON.stringify(next)
  await $.command.run({ command: 'usage-panel' })
  expect(toasts.some(t => t.includes('Codex personal weekly limit at 95%'))).toBe(true)

  const term = await $.ui.mount({ plugin: 'usage-panel', surface: 'terminal', ...BAND })
  expect(await term.find({ type: 'Text', text: /Personal/ })).toBeDefined()
  await term.unmount()
})

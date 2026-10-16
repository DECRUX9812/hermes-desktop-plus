#!/usr/bin/env node
/**
 * Browser proof for Hermes Desktop Plus against a real stock Hermes.
 *
 * Drives the packaged UI served by `hermes-plus serve` with headless
 * Chromium: boot, a real chat turn, sessions that survive a reload, a bot
 * team and bot identity round-trip, the plugin rail, /api/plus/info, and the
 * auth gate. Nothing here is stubbed: the page talks to the sidecar, which
 * talks to a real `hermes dashboard` child and a real model provider.
 *
 *   node proof.mjs --token-url <url-with-token> --base http://127.0.0.1:9230 \
 *                  --out <dir> --chrome <chromium binary> [--json <report path>]
 *
 * Writes numbered screenshots into --out and a JSON report next to them.
 */

import { mkdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { chromium } from 'playwright-core'

const VIEWPORT = { width: 1440, height: 900 }
// Session rows in the chat sidebar. Session rows carry no `data-*` hook, so
// this is the class pair the app's own sidebar tests use
// (`session-row.tsx` renders `SidebarRowShell className="group row-hover ..."`
// and `chat-sidebar.integration.test.tsx` resolves rows with
// `closest('.group.row-hover')`). Titles are the model's short session title,
// which is what step (d) matches after a reload.
const SESSION_ROW_SELECTOR = '.group.row-hover'

function parseArgs(argv) {
  const out = {}
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i]
    if (!key.startsWith('--')) {continue}
    out[key.slice(2)] = argv[i + 1]
    i += 1
  }
  return out
}

const args = parseArgs(process.argv.slice(2))
const BASE = (args['base'] || 'http://127.0.0.1:9230').replace(/\/$/, '')
const TOKEN_URL = args['token-url'] || `${BASE}/`
const OUT = args['out']
const CHROME = args['chrome']
const REPORT = args['json'] || join(OUT, 'browser-report.json')
// A plugin the runner installed through the disk door
// (`<hermes home>/desktop-plugins/<id>/plugin.js`) before serving, so the proof
// can assert the runtime door end to end. Empty when the runner installed none.
const DESKTOP_PLUGIN = args['desktop-plugin'] || ''
// The status-bar marker each known disk-door fixture paints, keyed by its id.
// The repo's hello-runtime example is the reference implementation of this door
// and documents the chip it contributes; an unknown id only gets the inventory
// assertion (the proof cannot know what an arbitrary plugin paints).
const DESKTOP_PLUGIN_MARKERS = {'hello-runtime': '⚡'}

if (!OUT || !CHROME) {
  console.error('usage: node proof.mjs --out DIR --chrome BINARY --token-url URL [--base URL]')
  process.exit(2)
}
mkdirSync(OUT, { recursive: true })

const results = []
const diagnostics = {}

function record(name, status, detail) {
  results.push({ name, status, detail })
  const tag = status === 'pass' ? 'PASS' : status === 'blocked' ? 'BLOCKED' : 'BROKEN'
  console.log(`[${tag}] ${name}: ${detail}`)
}

async function check(name, fn) {
  try {
    const detail = await fn()
    record(name, 'pass', detail === undefined ? 'ok' : String(detail))
  } catch (error) {
    record(name, 'broken', error && error.message ? error.message : String(error))
  }
}

async function blocked(name, reason) {
  record(name, 'blocked', reason)
}

async function shot(page, name) {
  const target = join(OUT, `${name}.png`)
  await page.screenshot({ path: target, animations: 'disabled' })
  return target
}

/**
 * RPC through a WebSocket built in the page, credentialed exactly the way the
 * app itself does it (packages/ui/src/lib/browser-transport.ts): an ungated
 * page sends its injected `?token=`, a cookie-authenticated page first mints a
 * one-use ticket from `POST /api/auth/ws-ticket` and sends `?ticket=`.
 */
const RPC_HELPER = `
window.__proofRpc = (method, params) => new Promise(async (resolve, reject) => {
  const token = window.__HERMES_SESSION_TOKEN__ || ''
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws'
  let query = 'token=' + encodeURIComponent(token)
  let credential = token ? 'session token' : ''
  if (!token) {
    try {
      const response = await fetch('/api/auth/ws-ticket', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { accept: 'application/json' },
      })
      if (!response.ok) { reject(new Error('ws-ticket HTTP ' + response.status)); return }
      const body = await response.json()
      if (!body || !body.ticket) { reject(new Error('ws-ticket returned no ticket')); return }
      query = 'ticket=' + encodeURIComponent(body.ticket)
      credential = 'ticket (ttl ' + body.ttl_seconds + 's)'
    } catch (error) {
      reject(new Error('ws-ticket request failed: ' + error))
      return
    }
  }
  const socket = new WebSocket(scheme + '://' + location.host + '/api/ws?' + query)
  const id = Math.floor(Math.random() * 1e9)
  const timer = setTimeout(() => { try { socket.close() } catch {} ; reject(new Error('rpc timeout: ' + method)) }, 60000)
  socket.onerror = () => { clearTimeout(timer); reject(new Error('rpc socket error: ' + method + ' via ' + credential)) }
  socket.onopen = () => socket.send(JSON.stringify({ jsonrpc: '2.0', id, method, params: params || {} }))
  socket.onmessage = event => {
    let frame
    try { frame = JSON.parse(event.data) } catch { return }
    if (frame && frame.id === id) {
      clearTimeout(timer)
      try { socket.close() } catch {}
      if (frame.error) { reject(new Error(method + ': ' + JSON.stringify(frame.error))) }
      else { resolve(frame.result) }
    }
  }
})
`

async function rpc(page, method, params) {
  return await page.evaluate(
    ([m, p]) => window.__proofRpc(m, p),
    [method, params || {}],
  )
}

async function composer(page) {
  const root = page.locator('[data-slot="composer-root"]')
  await root.locator('[contenteditable="true"]').first().waitFor({ state: 'visible', timeout: 180000 })
  return root
}

async function sendMessage(page, text) {
  const editor = page.locator('[data-slot="composer-root"] [contenteditable="true"]').first()
  await editor.click()
  await page.keyboard.type(text)
  await page.waitForTimeout(150)
  await page.keyboard.press('Enter')
}

// The chrome inside an assistant message that is NOT the model's answer: the
// reasoning disclosure (its "Thought …" chip and, while open, the reasoning
// body), the turn activity/duration rows, and the tool cards. Raw `innerText`
// of the message root is not a reply: a live turn keeps its reasoning body
// mounted, so a phrase the model only *thought about* matches it, and after a
// reload the same disclosure is collapsed and unmounts that body, so the same
// phrase disappears. S39b measured both halves of that: turn 1 "passed" on
// text that only ever existed as reasoning, then failed after the reload —
// while the turn had in fact been aborted by this proof's own next prompt
// (`[interrupt: no assistant output for this turn]` in the stored transcript),
// so the session legitimately held no reply for it.
const ANSWER_CHROME_SLOTS = [
  // The disclosure carries the "Thought …" chip and, while it is open, the
  // reasoning body (`aui_thinking-body` / `aui_reasoning-text`).
  'aui_thinking-disclosure',
  'aui_turn-activity',
  'aui_turn-duration',
  'aui_response-loading',
  'tool-activity-stack',
  'tool-block',
  'aui_msg-actions',
  'aui_msg-reactions',
  'aui_message-streaming-marker',
]

/** Every assistant message's own answer text, with the chrome above removed.
 *  Detached clones have no layout, so `textContent` is the only working read. */
const assistantTexts = page => page.$$eval(
  '[data-slot="aui_assistant-message-root"]',
  (nodes, chrome) => nodes.map(node => {
    const clone = node.cloneNode(true)
    for (const slot of chrome) {
      clone.querySelectorAll(`[data-slot="${slot}"]`).forEach(child => child.remove())
    }
    return (clone.textContent || '').replace(/\s+/g, ' ').trim()
  }),
  ANSWER_CHROME_SLOTS,
)

/** Raw message text, reasoning and tool chrome included. Diagnostics only. */
const messageTexts = page => page.$$eval('[data-slot="aui_assistant-message-root"]', nodes =>
  nodes.map(node => (node.innerText || '').replace(/\s+/g, ' ').trim()))

/**
 * Wait until the current turn is settled, so screenshots show a finished turn
 * and the next prompt cannot land inside a running one.
 *
 * The app's own settled-row signal is the streaming marker
 * (`assistant-message.tsx` StreamingMarker, `data-message-streaming`); the
 * composer's Stop button is the second half. The viewport-text comparison this
 * used to do alone never repeats while a model streams reasoning, so it timed
 * out mid-turn and — because callers ignored the false — the next prompt was
 * sent into the running turn, which aborts it (S39b: that is exactly how the
 * first session ended up with no reply to show after the reload).
 */
async function waitForSettled(page, timeoutMs = 300000) {
  const deadline = Date.now() + timeoutMs
  let idleSamples = 0
  while (Date.now() < deadline) {
    const busy = await page.evaluate(() => {
      const streaming = document.querySelectorAll(
        '[data-slot="aui_message-streaming-marker"][data-message-streaming="true"]',
      ).length
      const stop = document.querySelector('[data-slot="composer-root"] button[aria-label="Stop"]')
      return streaming > 0 || !!stop
    })
    idleSamples = busy ? 0 : idleSamples + 1
    // Two samples: one could land in the gap between two turns of a tool loop.
    if (idleSamples >= 2) {return true}
    await page.waitForTimeout(2000)
  }
  return false
}

/**
 * The session's own stored transcript, read the way the app reads it: the
 * sidebar row carries the stored key, the gateway's session-scoped RPCs take
 * the runtime id `session.resume` hands back, and `session.history` is the
 * persisted row set. Used to assert what the SESSION holds, next to what the
 * DOM paints.
 */
async function storedTranscript(page, storedId) {
  const resumed = await rpc(page, 'session.resume', { session_id: storedId })
  const runtimeId = resumed && resumed.session_id ? String(resumed.session_id) : ''
  const history = runtimeId
    ? await rpc(page, 'session.history', { session_id: runtimeId })
    : null
  return { runtimeId, json: JSON.stringify({ resumed, history }) }
}

/** How many assistant messages the transcript holds. */
const assistantRoots = page => page.locator('[data-slot="aui_assistant-message-root"]').count()

/**
 * Wait for the turn a prompt just started to show itself: the app marks the
 * message running (the streaming marker) and swaps Send for Stop, and a new
 * message root counts too, so a turn that began and ended inside one poll is
 * still seen. Without this a settle check can pass before the turn starts, and
 * the prompt after it would then land inside a running turn.
 */
async function waitForTurnStart(page, rootsBefore, timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const busy = await page.evaluate(() => ({
      streaming: document.querySelectorAll(
        '[data-slot="aui_message-streaming-marker"][data-message-streaming="true"]',
      ).length,
      stop: !!document.querySelector('[data-slot="composer-root"] button[aria-label="Stop"]'),
    }))
    if (busy.streaming || busy.stop || (await assistantRoots(page)) > rootsBefore) {return true}
    await page.waitForTimeout(500)
  }
  return false
}

/** Send a prompt, then wait for its turn to start and to settle. */
async function sendAndSettle(page, text, sender = sendMessage, timeoutMs = 300000) {
  const rootsBefore = await assistantRoots(page)
  await sender(page, text)
  if (!(await waitForTurnStart(page, rootsBefore))) {return { started: false, settled: false }}
  return { started: true, settled: await waitForSettled(page, timeoutMs) }
}

/** The session the app is showing: the route carries its STORED key (the one
 *  `session.list` rows report); the session-scoped RPCs take the runtime id
 *  that `session.resume` hands back. */
async function shownSessionId(page) {
  return (await page.evaluate(() => window.location.hash)).replace(/^#\/?/, '').split('/')[0]
}

/** The transcript's own text (the scroll viewport), not the whole app shell. */
const threadText = page => page.evaluate(() => {
  const viewport = document.querySelector('[data-slot="aui_thread-viewport"]')
  return (viewport ? viewport.innerText : '').replace(/\s+/g, ' ')
})

async function bodyText(page) {
  return (await page.evaluate(() => document.body.innerText || '')).replace(/\s+\n/g, '\n')
}

/** Wait until the rail paints at least `min` rows (it hydrates after a reload). */
async function waitForRailRows(page, min, timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const count = await page.locator(SESSION_ROW_SELECTOR).count()
    if (count >= min) {return count}
    await page.waitForTimeout(1000)
  }
  return await page.locator(SESSION_ROW_SELECTOR).count()
}

/** Wait until a rail row carries `title` (the rail hydrates after a reload). */
async function waitForRailTitle(page, title, timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const titles = await sessionRailTitles(page)
    if (titles.some(text => text.includes(title))) {return titles}
    await page.waitForTimeout(1000)
  }
  return await sessionRailTitles(page)
}

/** Session rows live in the left rail; find them by title text and click one. */
async function clickSessionByTitle(page, title) {
  const rows = page.locator(SESSION_ROW_SELECTOR)
  const count = await rows.count()
  for (let i = 0; i < count; i += 1) {
    const row = rows.nth(i)
    const text = ((await row.innerText()) || '').trim()
    if (text.includes(title)) {
      await row.click()
      return true
    }
  }
  return false
}

async function sessionRailTitles(page) {
  return await page.$$eval(SESSION_ROW_SELECTOR, nodes =>
    nodes.map(node => (node.innerText || '').replace(/\s+/g, ' ').trim()).filter(Boolean))
}

/**
 * A fresh page holding this run's session cookie, booted straight into the chat.
 *
 * The parity checks cannot reuse the long-lived page: the app's route resume
 * rewrites the hash back to the last route (`#/capabilities`) between a
 * hash-only `goto` and the next click, so a reload lands on the capabilities
 * route with no composer and a click races a re-mount. A new context has no
 * persisted route, so the SPA boots into the chat and stays there. The cookie
 * is copied, so it is the same session the browser pass holds.
 */
async function withFreshPage(fn) {
  const fresh = await browser.newContext({ viewport: VIEWPORT })
  try {
    await fresh.addCookies(await context.cookies())
    await fresh.addInitScript(RPC_HELPER)
    const page = await fresh.newPage()
    await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' })
    await composer(page)
    await page.waitForTimeout(1500)
    return await fn(page)
  } finally {
    await fresh.close()
  }
}

/** Click something React may re-mount underneath the click. */
async function clickStable(locator, page, timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs
  let lastError = null
  while (Date.now() < deadline) {
    try {
      await locator.click({ timeout: 10000 })
      return
    } catch (error) {
      lastError = error
      await page.waitForTimeout(1500)
    }
  }
  const reason = lastError && lastError.message ? lastError.message.split('\n')[0] : 'unknown'
  throw new Error(`the target never became clickable: ${reason}`)
}

/** `sendMessage`, but the editor click survives a re-mount. */
async function sendMessageStable(page, text) {
  const editor = page.locator('[data-slot="composer-root"] [contenteditable="true"]').first()
  await clickStable(editor, page)
  await page.keyboard.type(text)
  await page.waitForTimeout(150)
  await page.keyboard.press('Enter')
}

const browser = await chromium.launch({
  executablePath: CHROME,
  headless: true,
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
})

let context = null

try {
  // ── 1. No-cookie context: the app must demand a credential ───────────────
  const anonymous = await browser.newContext({ viewport: VIEWPORT })
  const anonymousPage = await anonymous.newPage()
  const anonymousStatus = await anonymousPage.goto(BASE + '/', { waitUntil: 'domcontentloaded' })
    .then(response => response.status())
  const gateText = (await bodyText(anonymousPage)).slice(0, 200)
  await shot(anonymousPage, '00-gate')
  const apiStatus = await anonymousPage.evaluate(async () => {
    const response = await fetch('/api/plus/info', { headers: { accept: 'application/json' } })
    return response.status
  })
  diagnostics.gate = { anonymousStatus, gateText, apiStatus }
  await check('auth gate', () => {
    if (anonymousStatus !== 401) {throw new Error(`unauthenticated GET / returned ${anonymousStatus}, expected 401`)}
    if (!/authentication required/i.test(gateText)) {throw new Error(`no auth notice in page text: ${gateText}`)}
    if (apiStatus !== 401) {throw new Error(`unauthenticated /api/plus/info returned ${apiStatus}, expected 401`)}
    return `GET / -> 401, /api/plus/info -> 401`
  })
  await anonymous.close()

  // ── 2. Token URL boots the app ───────────────────────────────────────────
  context = await browser.newContext({ viewport: VIEWPORT })
  const page = await context.newPage()
  const consoleErrors = []
  const failedRequests = []
  const watch = target => {
    // A console error with no URL is not actionable: Chromium's
    // "Failed to load resource: the server responded with a status of 500"
    // carries the failed URL only in the message's location.
    target.on('console', message => {
      if (message.type() !== 'error') {return}
      const location = typeof message.location === 'function' ? message.location() : null
      const where = location && location.url
        ? ` @ ${location.url}${location.lineNumber ? `:${location.lineNumber}` : ''}`
        : ''
      consoleErrors.push(`${message.text().slice(0, 300)}${where}`)
    })
    target.on('pageerror', error => consoleErrors.push(`pageerror: ${String(error).slice(0, 300)}`))
  }

  // Every page of the context is watched, popups included: the Bot Room and 3D
  // Pane overlays are separate tabs (`lib/browser-window-bridges.ts`), so a
  // handler bound to `page` alone would miss their traffic entirely.
  watch(page)
  context.on('page', opened => watch(opened))
  // Requests are watched at the CONTEXT, so a popup's very first request is
  // covered too — a page-level listener can attach after its document started.
  context.on('response', response => {
    if (response.status() < 400) {return}
    failedRequests.push({
      status: response.status(),
      method: response.request().method(),
      url: response.url(),
      resourceType: response.request().resourceType(),
    })
  })
  context.on('requestfailed', request => failedRequests.push({
    status: 0,
    method: request.method(),
    url: request.url(),
    resourceType: request.resourceType(),
    error: request.failure() ? request.failure().errorText : '',
  }))

  const landed = await page.goto(TOKEN_URL, { waitUntil: 'domcontentloaded' })
  const landingUrl = page.url()
  await page.addInitScript(RPC_HELPER)
  await page.evaluate(RPC_HELPER)

  await check('token URL opens the app with a session cookie', async () => {
    const cookies = await context.cookies()
    if (!cookies.some(cookie => cookie.name === 'hermes_plus_session')) {
      throw new Error(`no session cookie after ${landingUrl}: ${cookies.map(c => c.name).join(',') || 'none'}`)
    }
    if (landingUrl.includes('token=')) {throw new Error(`token survived in the URL: ${landingUrl}`)}
    return `${landed.status()} then ${landingUrl}, cookie set`
  })

  await composer(page)
  await page.waitForTimeout(2500)
  await shot(page, '01-chat')
  diagnostics.boot = {
    bodyText: (await bodyText(page)).slice(0, 3000),
    slots: await page.$$eval('[data-slot]', nodes =>
      [...new Set(nodes.map(node => node.getAttribute('data-slot')))].slice(0, 200)),
    labelled: await page.$$eval('[aria-label], [title]', nodes =>
      nodes.map(node => (node.getAttribute('aria-label') || node.getAttribute('title') || '').trim())
        .filter(Boolean).slice(0, 120)),
    sessionRows: await page.$$eval(SESSION_ROW_SELECTOR, nodes =>
      nodes.slice(0, 6).map(node => node.outerHTML.slice(0, 600))),
  }
  await check('app boots into the chat UI', async () => {
    const bridge = await page.evaluate(() => ({
      desktop: typeof window.hermesDesktop,
      surface: document.documentElement.getAttribute('data-hermes-desktop-host'),
      bootstrap: typeof window.__HERMES_PLUS__,
      authRequired: window.__HERMES_AUTH_REQUIRED__,
    }))
    diagnostics.bridge = bridge
    const fatal = /desktop boot failed|uncaught|application error/i
    if (consoleErrors.some(text => fatal.test(text))) {
      throw new Error(`boot error in console: ${consoleErrors.join(' | ')}`)
    }
    if (bridge.desktop !== 'object') {
      throw new Error(`the browser bridge did not install: window.hermesDesktop is ${bridge.desktop}`)
    }
    const text = await bodyText(page)
    if (/failed to render|Hermes couldn't start/i.test(text)) {
      throw new Error(`a surface fell back to its error boundary: ${text.replace(/\n/g, ' / ').slice(0, 300)}`)
    }
    const loop = consoleErrors.find(item => /Maximum update depth/i.test(item))
    if (loop) {throw new Error(`render loop in the console: ${loop}`)}
    return `bridge ${bridge.desktop}, host ${bridge.surface}, authRequired ${bridge.authRequired}, no render loop`
  })

  // ── 3. A real chat turn ──────────────────────────────────────────────────
  // The assertion is on the model's ANSWER, not on whatever the message root
  // renders: matching raw message text lets a turn that only ever *reasoned*
  // about the phrase pass (S39b). The turn must also be settled before the
  // next prompt goes out — sending one into a running turn aborts it, which is
  // what left the first session with nothing to show after the reload. A model
  // that answers with no text at all is a BLOCKED turn, not a product failure
  // (same shape as the tool-card turn below), so it gets one stricter retry.
  const FIRST_PROMPTS = [
    'Reply with exactly: hello from the proof',
    'Answer with the exact text, and nothing else: hello from the proof',
  ]
  const FIRST_REPLY = 'hello from the proof'
  let firstReply = null
  // False once a turn would not settle: nothing else may be typed into it.
  let turnSettled = true
  for (let attempt = 0; attempt < FIRST_PROMPTS.length && !firstReply && turnSettled; attempt += 1) {
    const turn = await sendAndSettle(page, FIRST_PROMPTS[attempt])
    if (!turn.started) {
      turnSettled = false
      diagnostics.firstReplyAttempts = [
        ...(diagnostics.firstReplyAttempts || []),
        `attempt ${attempt + 1}: the prompt never started a turn`,
      ]
      await shot(page, '02-reply')
      break
    }
    if (!turn.settled) {
      turnSettled = false
      diagnostics.firstReplyAttempts = [
        ...(diagnostics.firstReplyAttempts || []),
        `attempt ${attempt + 1}: the turn was still running after 5 minutes`,
      ]
      await shot(page, '02-reply')
      break
    }
    const texts = await assistantTexts(page)
    firstReply = texts.find(text => text.toLowerCase().includes(FIRST_REPLY)) || null
    if (!firstReply) {
      diagnostics.firstReplyAttempts = [
        ...(diagnostics.firstReplyAttempts || []),
        `attempt ${attempt + 1}: settled with no answer text (${JSON.stringify(texts.slice(-1)).slice(0, 160)})`,
      ]
    }
    await shot(page, '02-reply')
  }
  diagnostics.assistantTexts = await assistantTexts(page)
  diagnostics.messageTexts = await messageTexts(page)
  if (firstReply) {
    await check('chat reply streams from the configured model', () => `assistant said "${firstReply}"`)
  } else {
    await blocked(
      'chat reply streams from the configured model',
      `no answer text carrying "${FIRST_REPLY}" after ${FIRST_PROMPTS.length} prompt(s)`
      + ` (${(diagnostics.firstReplyAttempts || ['no attempt was made']).join('; ')}): `
      + JSON.stringify((await assistantTexts(page)).slice(-2)).slice(0, 240),
    )
  }

  // The session the first turn opened: `session.list` is authoritative, and the
  // route has to agree once the turn is settled.
  let firstStoredSessionId = await shownSessionId(page)
  if (!firstStoredSessionId) {
    const listing = await rpc(page, 'session.list', { limit: 50 }).catch(() => null)
    firstStoredSessionId = String(listing?.sessions?.[0]?.id || '')
  }
  diagnostics.firstStoredSessionId = firstStoredSessionId

  // ── 4. Tool card (a turn that must call a tool) ──────────────────────────
  // The card slots are the UI's own (`tool-activity-stack` for a run of tool
  // calls, `tool-block` for a single one). Nothing else counts: a "More tools"
  // chrome button is not a tool card, and reporting one as such would be a
  // false pass. The turn is deterministic by construction: run-proof.sh drops
  // a file holding a run-unique token into the session workspace, and the
  // prompt demands reading it — the token cannot be quoted without a tool. If
  // the model still answers bare, the turn is retried once with a stricter
  // instruction; a second bare answer is BROKEN, not BLOCKED (the S28
  // plain-reply turn could silently drop the check on model whim).
  const TOOL_CARD_SELECTOR = '[data-slot="tool-activity-stack"], [data-slot="tool-block"]'
  const TOOL_FILE = args['tool-file'] || 'proof-token.txt'
  const TOOL_TOKEN = args['tool-token'] || ''
  // True only when the tool turn really left the run-unique token in an ANSWER
  // of this session: step 5 asserts the reopened session still shows it.
  let toolTurnQuoted = false
  if (!TOOL_TOKEN) {
    await blocked('tool card rendered', 'no --tool-token given; the proof turn cannot demand a verifiable tool read')
  } else if (!turnSettled) {
    await blocked('tool card rendered', 'the turn before it never settled; no further prompt is sent into a running turn')
  } else {
    const TOOL_PROMPTS = [
      `Read the file ${TOOL_FILE} in the workspace root with your file-read tool, then reply with the exact token written in it. Using the tool is required; do not answer from memory.`,
      `You MUST call a tool before answering. Call your file-read tool on ${TOOL_FILE} in the workspace root and reply with the token string it contains and nothing else. A reply without a tool call is wrong.`,
    ]
    let toolDetail = null
    let unsettled = 0
    let unstarted = 0
    for (let attempt = 0; attempt < TOOL_PROMPTS.length && !toolDetail && !unsettled && !unstarted; attempt += 1) {
      // A prompt must never go out while the previous turn is still running:
      // that aborts it (S39b's first turn ended as `[interrupt: no assistant
      // output for this turn]`). If the turn will not start or settle, there is
      // nothing to assert — say so instead of measuring a turn still going.
      const turn = await sendAndSettle(page, TOOL_PROMPTS[attempt])
      if (!turn.started) {
        unstarted = attempt + 1
        turnSettled = false
        break
      }
      if (!turn.settled) {
        unsettled = attempt + 1
        turnSettled = false
        break
      }
      const texts = await assistantTexts(page)
      const quoted = texts.some(text => text.toLowerCase().includes(TOOL_TOKEN.toLowerCase()))
      const card = await page.evaluate(selector => {
        const node = document.querySelector(selector)
        return node ? (node.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 200) : ''
      }, TOOL_CARD_SELECTOR)
      diagnostics[`toolAttempt${attempt + 1}`] = { quoted, card, texts: texts.slice(-2) }
      if (quoted && card) {
        toolDetail = `token quoted in the answer, attempt ${attempt + 1}; card text: ${card}`
      }
    }
    if (toolDetail) {
      toolTurnQuoted = true
      await shot(page, '02-tool-card')
      await check('tool card rendered', () => toolDetail)
    } else if (unstarted) {
      await shot(page, '02-tool-card')
      await blocked(
        'tool card rendered',
        `the prompt ${unstarted} never started a turn, so there is no card or answer to assert`,
      )
    } else if (unsettled) {
      await shot(page, '02-tool-card')
      await blocked(
        'tool card rendered',
        `the turn after prompt ${unsettled} was still running after 5 minutes, so the card and the`
        + ' answer cannot be asserted (the next prompt must not land inside a running turn)',
      )
    } else {
      const texts = await assistantTexts(page)
      const last = texts.length ? texts[texts.length - 1] : '(no assistant text)'
      record(
        'tool card rendered',
        'broken',
        `no token quote plus tool card after ${TOOL_PROMPTS.length} prompt(s); last assistant text: ${last.slice(0, 200)}`,
      )
    }
  }

  // ── 5. Sessions survive a reload ─────────────────────────────────────────
  const SECOND_PROMPT = 'Second session: reply with exactly session two ok'
  const SECOND_TITLE = 'Second session'
  const NEW_SESSION_LABELS = ['New session', 'New chat', 'New session tab']
  const sessionCount = value => {
    if (!value || value.error) {return -1}
    if (Array.isArray(value.sessions)) {return value.sessions.length}
    return Array.isArray(value) ? value.length : -1
  }
  const sessionsBefore = sessionCount(
    await rpc(page, 'session.list', { limit: 50 }).catch(error => ({ error: String(error) })),
  )
  diagnostics.sessionsBeforeSecond = sessionsBefore
  await check('start a second session from the UI', async () => {
    for (const label of NEW_SESSION_LABELS) {
      const button = page.getByRole('button', { name: label, exact: false }).first()
      if (await button.count()) {
        await button.click()
        await composer(page)
        return `clicked "${label}" (${sessionsBefore} session(s) before)`
      }
    }
    throw new Error(
      `no new-session affordance among ${NEW_SESSION_LABELS.join(', ')}; rail: `
      + JSON.stringify(await sessionRailTitles(page)).slice(0, 200),
    )
  })

  await composer(page)
  await page.waitForTimeout(1500)
  // Never type into a turn that is still running (see step 3): with an earlier
  // turn unsettled there is no honest verdict here, so the check says so.
  let secondSettled = false
  let secondStarted = false
  let secondReply = null
  if (turnSettled) {
    const turn = await sendAndSettle(page, SECOND_PROMPT)
    secondStarted = turn.started
    secondSettled = turn.settled
    if (secondSettled) {
      const texts = await assistantTexts(page)
      secondReply = texts.find(text => text.toLowerCase().includes('session two ok')) || null
    }
  }
  await shot(page, '03-second-session')
  if (secondReply) {
    await check('second session reply', () => `assistant said "${secondReply}"`)
  } else if (!turnSettled) {
    await blocked('second session reply', 'an earlier turn never settled; nothing was sent into a running turn')
  } else if (!secondStarted) {
    await blocked('second session reply', 'the prompt never started a turn')
  } else if (!secondSettled) {
    await blocked('second session reply', 'the turn was still running after 5 minutes')
  } else {
    await blocked(
      'second session reply',
      `settled with no answer text: ${JSON.stringify((await assistantTexts(page)).slice(-1)).slice(0, 200)}`,
    )
  }

  await page.reload({ waitUntil: 'domcontentloaded' })
  await composer(page)
  await page.evaluate(RPC_HELPER)
  await waitForRailRows(page, 1)
  const sessions = await rpc(page, 'session.list', { limit: 50 }).catch(error => ({ error: String(error) }))
  diagnostics.sessions = sessions
  const sessionRows = Array.isArray(sessions?.sessions) ? sessions.sessions : []
  const sessionTitles = sessionRows.map(session => session.title || session.name || '')
  // The rail titles a session from its first user message, so the first
  // session's rail label is the model's short title, not the prompt.
  const firstTitle = sessionTitles.find(title => !title.includes(SECOND_TITLE)) || FIRST_PROMPTS[0]
  const secondTitle = sessionTitles.find(title => title.includes(SECOND_TITLE)) || SECOND_TITLE
  diagnostics.sessionTitlesAfterReload = sessionTitles
  diagnostics.firstTitle = firstTitle
  diagnostics.secondTitle = secondTitle
  const railTitles = await waitForRailTitle(page, secondTitle.slice(0, 10))
  const railRowCount = await page.locator(SESSION_ROW_SELECTOR).count()
  diagnostics.railTitles = railTitles
  diagnostics.railRowCount = railRowCount
  await shot(page, '03-sessions')

  await check('both sessions are listed after a reload', async () => {
    const after = sessionCount(sessions)
    if (after < 0) {
      throw new Error(`session.list failed: ${JSON.stringify(sessions).slice(0, 200)}`)
    }
    if (after < sessionsBefore + 1 && sessionsBefore >= 0) {
      throw new Error(`session.list has ${after} sessions, expected more than the ${sessionsBefore} before the second one`)
    }
    if (!sessionTitles.some(title => title.includes(SECOND_TITLE))) {
      throw new Error(`second session missing from session.list: ${JSON.stringify(sessionTitles).slice(0, 200)}`)
    }
    if (railRowCount < 2) {
      throw new Error(`the rail painted ${railRowCount} session rows, expected both sessions: ${JSON.stringify(railTitles).slice(0, 200)}`)
    }
    if (!railTitles.some(title => title.includes(secondTitle.slice(0, 10)))) {
      throw new Error(`second session missing from the rail: ${JSON.stringify(railTitles).slice(0, 200)}`)
    }
    return `${after} sessions via session.list (${sessionTitles.join(' | ')}), ${railRowCount} rail rows`
  })

  await check('the first session still holds its messages after a reload', async () => {
    // Reopening a session is a route change, and a reload leaves the app's own
    // route resume for the session it was on still settling (`use-route-resume`
    // retries a stranded resume with backoff). A click that lands while that is
    // in flight can be re-navigated away, so the click is retried — the
    // assertions below are what decide, and a stale resume cannot satisfy them.
    //
    // What has to hold, measured rather than assumed: the reopened route is the
    // FIRST session's own stored key, that session's first prompt is on screen,
    // the tool turn's answer (the run-unique token, which no model can quote
    // without the tool read) is on screen again, and the stored transcript read
    // back the way the app reads it (session.resume → session.history) still
    // carries both. The first turn's reply is asserted the same way when step 3
    // got one — a model that settles without answering blocks step 3 instead of
    // failing this check, which is what S39 got wrong: it typed the tool prompt
    // into a running first turn, the turn was stored as
    // `[interrupt: no assistant output for this turn]`, and this check then
    // failed for the only correct reason — the session had no reply to hold.
    const has = (texts, needle) => texts.some(text => text.toLowerCase().includes(String(needle).toLowerCase()))
    let stored = null
    let routeId = ''
    let texts = []
    let visible = ''
    let mismatch = ''
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const clicked = await clickSessionByTitle(page, firstTitle.slice(0, 12))
      if (!clicked) {
        throw new Error(`could not click the first session row "${firstTitle}": ${JSON.stringify(railTitles).slice(0, 200)}`)
      }
      await waitForSettled(page, 120000)
      await page.waitForTimeout(3000)
      routeId = await shownSessionId(page)
      texts = await assistantTexts(page)
      visible = await threadText(page)
      stored = await storedTranscript(page, routeId).catch(error => ({ json: '', error: String(error) }))
      const problems = []
      if (firstStoredSessionId && routeId !== firstStoredSessionId) {
        problems.push(`route is ${routeId}, expected the first session ${firstStoredSessionId}`)
      }
      if (!visible.includes(FIRST_PROMPTS[0])) { problems.push("the first turn's prompt is not on screen") }
      if (toolTurnQuoted && !has(texts, TOOL_TOKEN)) { problems.push('the tool turn\'s stored answer (the token) is not on screen') }
      if (firstReply && !has(texts, FIRST_REPLY)) { problems.push(`the first reply "${FIRST_REPLY}" is not on screen`) }
      if (stored.error) {
        // A transport failure is not a lost message: name it as itself.
        problems.push(`the stored transcript could not be read: ${String(stored.error).slice(0, 160)}`)
      } else {
        if (!stored.json.includes(FIRST_PROMPTS[0])) { problems.push('the stored transcript no longer holds the first prompt') }
        if (toolTurnQuoted && !stored.json.includes(TOOL_TOKEN)) { problems.push('the stored transcript no longer holds the tool answer') }
      }
      mismatch = problems.join('; ')
      if (!problems.length) { break }
      await page.waitForTimeout(3000)
    }
    diagnostics.firstSessionReopened = {
      routeId, firstStoredSessionId, firstReply, toolTurnQuoted,
      answerTexts: texts, storedTranscriptBytes: (stored?.json || '').length, storedError: stored?.error,
    }
    await shot(page, '03-sessions')
    if (mismatch) {
      throw new Error(`reopened session: ${mismatch}`
        + ` (route ${routeId}, answers ${JSON.stringify(texts).slice(0, 300)},`
        + ` stored transcript ${stored?.error ? `unreadable: ${String(stored.error).slice(0, 200)}` : `${(stored.json || '').length} bytes`})`)
    }
    return `reopened "${firstTitle}" (${routeId}): its first prompt`
      + `${firstReply ? ' and reply' : ''}${toolTurnQuoted ? ' and the tool answer' : ''} are on screen,`
      + ` and the stored transcript still holds them (${stored?.runtimeId || 'resumed'})`
  })

  // ── 6. Bot team ──────────────────────────────────────────────────────────
  const TEAM_NAME = 'Proof Team'
  let teamId = null
  await check('create a bot team over the app transport', async () => {
    // `bots_team.create` answers with the team view (`{team: {...}}`), not the
    // bare row.
    const created = await rpc(page, 'bots_team.create', {
      name: TEAM_NAME,
      mission: 'Prove the group chat surface on stock Hermes.',
      lead_decides: true,
    })
    teamId = created?.team?.id ?? created?.id ?? null
    if (!teamId) {throw new Error(`bots_team.create returned ${JSON.stringify(created).slice(0, 200)}`)}
    return `team ${teamId}`
  })

  {
    // ── 6b. Bot Room overlay (browser host) ────────────────────────────────
    // The Bot Room is a separate window in Electron, fed roster/room state by
    // the main renderer. The browser host stands a same-origin popup tab in
    // for it (`lib/browser-window-bridges.ts`) and relays that same push/control
    // traffic over BroadcastChannel, so the button the roster toolbar owns opens
    // a real overlay and the team's own bot paints a mascot in it.
    const BOT_PROFILE = 'proof-bot'
    await check('the Bot Room overlay opens from the roster toolbar and paints the team bot', async () => {
      // A bot IS a Hermes profile. `no_alias` keeps the create from dropping a
      // launcher wrapper outside this proof's temp home. Re-runnable: a profile
      // left by an earlier run in a kept temp home is reused, not re-created.
      const listed = await rpc(page, 'profiles.list', {})
      const already = (listed?.profiles ?? []).some(p => p.name === BOT_PROFILE)
      if (!already) {
        const created = await rpc(page, 'profiles.create', {
          clone_from: 'default',
          description: 'Proof bot for the Bot Room overlay',
          name: BOT_PROFILE,
          no_alias: true,
        })
        if (created?.ok !== true) {
          throw new Error(`profiles.create answered ${JSON.stringify(created).slice(0, 200)}`)
        }
      }
      // Seat the bot on the team by its PROFILE, which is the real UI path
      // (plugins/hermes-bots/team-org.tsx SeatForm sends `profile`). It is data:
      // the team store stays the connection's, so the seat lands on the team
      // created above instead of being looked up in the bot's own plus.db.
      const added = await rpc(page, 'bots_team.member.upsert', {
        profile: BOT_PROFILE,
        role: 'proofer',
        team_id: teamId,
        title: BOT_PROFILE,
      })
      if (added?.member?.profile !== BOT_PROFILE) {
        throw new Error(`bots_team.member.upsert answered ${JSON.stringify(added).slice(0, 200)}`)
      }

      // Reach the roster toolbar the way a user does: the icon rail's Bots
      // panel button shows the sidebar pane whose toolbar owns the Bot Room
      // button. The rail entry is data-addressed (`data-rail-entry`) so the
      // proof does not depend on localized tooltip text.
      const railBots = page.locator('button[data-rail-entry="hermes-bots:pane"]').first()
      await railBots.waitFor({ state: 'visible', timeout: 60000 })
      const button = page.getByRole('button', { name: 'Bot Room on desktop' }).first()
      await railBots.click()
      try {
        await button.waitFor({ state: 'visible', timeout: 15000 })
      } catch {
        // The panel was already showing and the click folded it away; press again.
        await railBots.click()
        await button.waitFor({ state: 'visible', timeout: 60000 })
      }

      // `page.waitForEvent('popup')` (not the context-level twin) is the
      // reliable signal for a `window.open` popup: the context event does not
      // resolve for this same-origin popup even though the page lands in the
      // context.
      const [overlay] = await Promise.all([
        page.waitForEvent('popup', { timeout: 60000 }),
        button.click(),
      ])
      await overlay.waitForLoadState('domcontentloaded')
      diagnostics.botRoomUrl = overlay.url()
      if (!overlay.url().includes('win=botroom')) {
        throw new Error(`the Bot Room button opened ${overlay.url()}, not the ?win=botroom overlay`)
      }

      // The stage lives in a shadow root; Playwright's locators pierce open
      // shadow roots, so this is the mascot's own hit node.
      const mascot = overlay.locator(`.hr-hit[data-bot-id="profile:${BOT_PROFILE}"]`)
      try {
        await mascot.waitFor({ state: 'attached', timeout: 90000 })
      } catch (error) {
        await shot(overlay, '04-botroom-window')
        const text = (await bodyText(overlay)).slice(0, 400).replace(/\n/g, ' / ')
        throw new Error(`no mascot for ${BOT_PROFILE} painted in the overlay: ${text || '(empty document)'}`)
      }

      const painted = (await mascot.locator('.hr-name').innerText()).trim()
      const deck = await overlay.locator('.hr-deck-chip').count()
      const canvases = await overlay.locator('.hr-canvas').count()
      await shot(overlay, '04-botroom-window')
      diagnostics.botRoom = { canvases, deck, painted }
      if (painted !== BOT_PROFILE) {
        throw new Error(`the overlay mascot is labelled "${painted}", expected ${BOT_PROFILE}`)
      }
      if (deck < 1) {throw new Error('the overlay deck listed no bot')}
      if (canvases !== 1) {throw new Error(`the overlay stage canvas is missing (${canvases} found)`)}
      return `popup ${overlay.url()} painted mascot "${painted}" with ${deck} deck chip(s) and 1 stage canvas`
    })

    // The `&demo=1` film/visual path replaces the bridge with a canned roster;
    // it must keep working now that the browser host has a real one.
    await check('the Bot Room demo path still paints its canned roster', async () => {
      const demo = await context.newPage()
      await demo.goto(`${BASE}/?win=botroom&demo=1`, { waitUntil: 'domcontentloaded' })
      const muse = demo.locator('.hr-hit[data-bot-id="profile:muse"]')
      await muse.waitFor({ state: 'attached', timeout: 60000 })
      const label = (await muse.locator('.hr-name').innerText()).trim()
      const deck = await demo.locator('.hr-deck-chip').count()
      await demo.close()
      if (label !== 'Muse') {throw new Error(`the demo overlay labelled its first mascot "${label}", expected Muse`)}
      return `demo overlay painted "${label}" with ${deck} deck chip(s)`
    })
  }

  await page.goto(BASE + '/#/team', { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(6000)
  await shot(page, '04-team')
  diagnostics.teamPage = (await bodyText(page)).slice(0, 3000)
  await check('the team appears on the Team page', async () => {
    const text = await bodyText(page)
    if (!text.includes(TEAM_NAME)) {
      throw new Error(`/team does not list ${TEAM_NAME}: ${text.replace(/\n/g, ' / ').slice(0, 300)}`)
    }
    return `Team page lists ${TEAM_NAME}`
  })

  await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' })
  await composer(page)
  await page.evaluate(RPC_HELPER)
  await check('the team survives a reload', async () => {
    const listed = await rpc(page, 'bots_team.list', {})
    const teams = (listed && (listed.teams || listed)) || []
    const found = Array.isArray(teams)
      ? teams.find(team => team && (team.name === TEAM_NAME || team.id === teamId))
      : null
    if (!found) {throw new Error(`bots_team.list has no ${TEAM_NAME}: ${JSON.stringify(listed).slice(0, 300)}`)}
    return `team ${found.id} still present`
  })

  // ── 7. Bot identity round-trip ───────────────────────────────────────────
  await check('bot identity round-trips and survives a reload', async () => {
    const written = await rpc(page, 'fork.bot.identity.set', {
      profile: 'demo-bot',
      display_name: 'Demo Bot',
      role: 'proofer',
      instructions: 'Keep answers short.',
    })
    const read = await rpc(page, 'fork.bot.identity.get', { profile: 'demo-bot' })
    if (read.display_name !== 'Demo Bot' || read.role !== 'proofer') {
      throw new Error(`identity did not round-trip: ${JSON.stringify(read).slice(0, 200)}`)
    }
    // The bot pane is the plugin pane registered as `hermes-bots:pane`; its
    // command-palette row is labelled "Bots" (the `/roster` route is the
    // fleet-run roster, a different surface). The pane's roster container
    // carries `data-slot="bots-roster"` (`roster-pane-content.tsx`) — unique
    // to this pane, and invisible while the pane sits hidden as a keep-alive
    // tab — so a VISIBLE roster container is the open pane.
    const BOTS_ROSTER_SELECTOR = '[data-slot="bots-roster"]'
    await page.keyboard.press('Control+k')
    await page.waitForTimeout(1200)
    await page.keyboard.type('Bots')
    await page.waitForTimeout(1200)
    await shot(page, '05-bot-palette')
    await page.keyboard.press('Enter')
    await page.waitForTimeout(800)
    // Enter can leave the palette row un-activated (S28 saw it still open
    // seconds later); fold whatever remains so it cannot photobomb the shot.
    await page.keyboard.press('Escape')
    await page.waitForTimeout(500)
    await check('bot pane opens', async () => {
      const roster = page.locator(BOTS_ROSTER_SELECTOR)
      if (!(await roster.isVisible().catch(() => false))) {
        // The user path the roster's own rail button owns (the same toggle the
        // Bot Room overlay flow uses): click it only when the pane is closed,
        // because the rail entry is a toggle, not a reveal.
        const railBots = page.locator('button[data-rail-entry="hermes-bots:pane"]').first()
        await railBots.waitFor({ state: 'visible', timeout: 30000 })
        await railBots.click()
      }
      await roster.waitFor({ state: 'visible', timeout: 30000 })
      return 'the Bots pane roster (data-slot bots-roster) is on screen'
    })
    await page.waitForTimeout(2500)
    await shot(page, '05-bot')
    diagnostics.botPane = (await bodyText(page)).slice(0, 1500)
    await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' })
    await composer(page)
    await page.evaluate(RPC_HELPER)
    const again = await rpc(page, 'fork.bot.identity.get', { profile: 'demo-bot' })
    if (again.display_name !== 'Demo Bot' || again.role !== 'proofer') {
      throw new Error(`identity lost after reload: ${JSON.stringify(again).slice(0, 200)}`)
    }
    return `written revision ${written.revision !== undefined ? written.revision : 'n/a'}, read back unchanged after reload`
  })

  // ── 8. Plugin rail ───────────────────────────────────────────────────────
  await check('plugin rail renders', async () => {
    await page.goto(BASE + '/#/capabilities', { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(5000)
    const rail = page.getByText(/plugin/i).first()
    if (await rail.count()) {await rail.click().catch(() => {})}
    await page.waitForTimeout(2500)
    await shot(page, '06-plugins')
    const text = await bodyText(page)
    diagnostics.plugins = text.slice(0, 2000)
    if (!/plugin/i.test(text)) {throw new Error(`no plugin surface in: ${text.slice(0, 200)}`)}
    return `plugin surface text: ${text.replace(/\n/g, ' / ').slice(0, 140)}`
  })

  // ── 9. /api/plus/info ────────────────────────────────────────────────────
  await check('GET /api/plus/info reports the Hermes version and compat', async () => {
    const info = await page.evaluate(async () => {
      const response = await fetch('/api/plus/info')
      return { status: response.status, body: await response.json() }
    })
    diagnostics.plusInfo = info.body
    if (info.status !== 200) {throw new Error(`status ${info.status}`)}
    if (!info.body.hermes_version && !info.body.hermes_commit) {
      throw new Error(`no Hermes version: ${JSON.stringify(info.body).slice(0, 200)}`)
    }
    const compat = info.body.compat && info.body.compat.status
    if (!['supported', 'untested', 'unsupported'].includes(compat)) {
      throw new Error(`no compat status: ${JSON.stringify(info.body.compat)}`)
    }
    return `hermes ${info.body.hermes_version} @ ${info.body.hermes_commit}, compat ${compat}`
  })

  // ── 10. Parity surfaces the earlier steps never touched (S39) ────────────
  // Every PARITY.md row that is a UI surface gets a real check here. Backend
  // surfaces (our RPC namespaces, our HTTP routes, the host-terminal WS, the
  // stock cron cross-check) are covered by proof/parity-proof.py, which the
  // proof runner calls right after this browser pass with the same session
  // credential this page holds.
  //
  // ── S34: the 3D Pane opener and the bot mascots (merged from master) ─────
  // Two feat/hermes-next surfaces are separate OS windows in Electron, opened
  // by the MAIN process (`apps/desktop/electron/pane3d-ipc.ts`,
  // `botroom-mascots.ts`), so the shared renderer has no user gesture to
  // mirror. The browser host renders both itself — a same-origin popup tab for
  // `?win=pane3d`, an in-page layer for the mascots — and carries its own doors
  // for them: the palette rows `app/contrib/browser-window-commands.ts`
  // registers (browser host only, so Electron's palette is unchanged) and the
  // roster toolbar's eye button. The steps below walk the user path through
  // those doors and assert the surfaces, not the registration.

  /**
   * Commit a command-palette row with the keyboard.
   *
   * The palette list is `pointer-events: none` until the pointer actually
   * moves (`usePointerQuiet`), so a Playwright click on a row is intercepted by
   * `cmdk-root`. Walking the selection with `ArrowDown` and pressing `Enter` is
   * the path a keyboard user takes, and the only one that works headless.
   */
  async function commitPaletteRow(target, label) {
    await target.keyboard.press('Control+k')
    const input = target.locator('[data-slot="command-input"]').first()
    await input.waitFor({ state: 'visible', timeout: 30000 })
    await input.fill(label)
    await target.locator('[data-slot="command-item"]').filter({ hasText: label }).first()
      .waitFor({ state: 'visible', timeout: 30000 })
    for (let press = 0; press < 40; press += 1) {
      const selected = target.locator('[data-slot="command-item"][data-selected="true"]').first()
      const text = (await selected.count()) ? ((await selected.innerText()) || '') : ''
      if (text.includes(label)) {return}
      await target.keyboard.press('ArrowDown')
      await target.waitForTimeout(120)
    }
    const rows = await target.locator('[data-slot="command-item"]').allInnerTexts()
    throw new Error(`the palette never selected a row reading ${JSON.stringify(label)};`
      + ` rows: ${JSON.stringify(rows).slice(0, 300)}`)
  }

  /**
   * Bring the Bots pane's roster on screen.
   *
   * The rail entry is a toggle, not a reveal: it is `data-active` while its pane
   * tab is the open one, and a click then folds the pane away. So the loop reads
   * that flag, waits out a tab that is already active but still hydrating, and
   * retries the click (a click can also be swallowed by a re-mount).
   */
  async function openBotsPane(target) {
    const roster = target.locator('[data-slot="bots-roster"]')
    const entry = target.locator('button[data-rail-entry="hermes-bots:pane"]').first()
    for (let attempt = 0; attempt < 4; attempt += 1) {
      if (await roster.isVisible().catch(() => false)) {return}
      await entry.waitFor({ state: 'visible', timeout: 30000 })
      if ((await entry.getAttribute('data-active')) === 'true') {
        await target.waitForTimeout(3000)
        if (await roster.isVisible().catch(() => false)) {return}
      }
      await clickStable(entry, target)
      await target.waitForTimeout(2500)
    }
    if (!(await roster.isVisible().catch(() => false))) {
      throw new Error('the Bots pane roster (data-slot bots-roster) never became visible')
    }
  }

  // A. The palette's `Open 3D Pane` row opens the pane as a popup tab, and the  //    tab paints real avatars. The pane is idle by design (it waits for a
  //    state), so the check drives its own dev door — the same
  //    `hermesDesktop.pane3d.playDemo` the Electron pane answers — and asserts
  //    the painted scene.
  await check('the command palette opens the 3D Pane in a browser tab', async () => {
    await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' })
    await composer(page)
    // A fresh home blocks the whole app behind the first-run picker
    // (`z-(--z-onboarding)`), so nothing else is clickable until it is
    // dismissed the way its own button does.
    const later = page.getByRole('button', { name: "I'll choose a provider later" })
    if (await later.count()) {
      await later.first().click().catch(() => {})
      await page.waitForTimeout(2000)
    }
    await commitPaletteRow(page, 'Open 3D Pane')
    await shot(page, '06a-palette-3d-pane')
    const [pane] = await Promise.all([
      page.waitForEvent('popup', { timeout: 60000 }),
      page.keyboard.press('Enter'),
    ])
    await pane.waitForLoadState('domcontentloaded')
    const url = pane.url()
    if (!/win=pane3d/.test(url)) {
      throw new Error(`the palette opened ${url}, expected a ?win=pane3d tab`)
    }
    const demo = await page.evaluate(() => window.hermesDesktop.pane3d.playDemo('launch'))
    await pane.locator('[data-avatar-id]').first().waitFor({ state: 'attached', timeout: 60000 })
    const canvases = await pane.locator('canvas').count()
    const avatar = await pane.locator('[data-avatar-id]').first().getAttribute('data-avatar-id')
    await pane.waitForTimeout(2500)
    await shot(pane, '06b-pane3d-demo')
    await pane.close()
    if (demo?.ok !== true) {
      throw new Error(`pane3d.playDemo answered ${JSON.stringify(demo)}`)
    }
    if (canvases < 1) {
      throw new Error(`the pane tab painted no canvas (avatar ${avatar})`)
    }
    return `the row opened ${url.replace(BASE, '')} and the launch demo painted avatar "${avatar}"`
      + ` on ${canvases} canvas`
  })

  // B. The roster toolbar's mascot button floats the in-page layer: one real
  //    bot slot (the roster's own `profiles.list` rows, relayed over the
  //    `botroomMascot` bridge) with a live three.js canvas, and the slot's
  //    action dot opens the task pill at the size Electron's window uses.
  await check('the roster toolbar floats the bot mascots and their task pill', async () => {
    try {
      await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' })
      await composer(page)
      await openBotsPane(page)
      const toggle = page.getByRole('button', { name: 'Bot mascots over the app' }).first()
      await toggle.waitFor({ state: 'visible', timeout: 30000 })
      await clickStable(toggle, page)
      const slot = page.locator('.hr-layer-slot[data-bot-id]').first()
      await slot.waitFor({ state: 'attached', timeout: 30000 })
      const botId = await slot.getAttribute('data-bot-id')
      const name = ((await slot.locator('.hr-mw-name').first().innerText()) || '').trim()
      const canvas = await slot.locator('canvas').first().evaluate(node => ({
        height: node.getAttribute('height'), width: node.getAttribute('width'),
      }))
      await page.waitForTimeout(1200)
      await shot(page, '06c-mascot-layer')
      if (!/^profile:.+/.test(botId || '')) {
        throw new Error(`the layer's first slot is ${JSON.stringify(botId)}, expected a profile: bot id`)
      }
      if (name !== botId.replace(/^profile:/, '')) {
        throw new Error(`the slot for ${botId} is labelled ${JSON.stringify(name)}`)
      }
      if (canvas.width !== '150' || canvas.height !== '165') {
        throw new Error(`the mascot canvas is ${canvas.width}x${canvas.height}, expected 150x165`)
      }
      // Liveness: the canvas is a live three.js scene, so two frames of the same
      // slot cannot be byte-identical.
      const first = await slot.screenshot()
      await page.waitForTimeout(900)
      const second = await slot.screenshot()
      await shot(page, '06d-mascot-slot')
      if (first.equals(second)) {
        throw new Error('the mascot canvas did not change between frames (a still, not a live scene)')
      }
      // The pill: the slot's action dot is its only door.
      await slot.hover()
      await clickStable(slot.locator('.hr-mw-dot').first(), page)
      const pill = page.locator('.hr-layer-pill[data-bot-id]').first()
      await pill.waitFor({ state: 'attached', timeout: 30000 })
      const pillBot = await pill.getAttribute('data-bot-id')
      const box = await pill.boundingBox()
      const viewport = page.viewportSize()
      await page.waitForTimeout(800)
      await shot(page, '06e-mascot-pill')
      if (pillBot !== botId) {
        throw new Error(`the pill is for ${JSON.stringify(pillBot)}, expected ${JSON.stringify(botId)}`)
      }
      if (!box || Math.round(box.width) !== 320 || Math.round(box.height) !== 132) {
        throw new Error(`the pill box is ${box ? `${Math.round(box.width)}x${Math.round(box.height)}` : 'unmeasurable'},`
          + ' expected 320x132')
      }
      if (box.x < 0 || box.y < 0 || box.x + box.width > viewport.width || box.y + box.height > viewport.height) {
        throw new Error(`the pill at ${Math.round(box.x)},${Math.round(box.y)} is not fully inside`
          + ` the ${viewport.width}x${viewport.height} viewport`)
      }
      await page.keyboard.press('Escape')
      await pill.waitFor({ state: 'detached', timeout: 15000 })
      // Off again through the palette toggle: the layer unmounts and the app
      // keeps running.
      await commitPaletteRow(page, 'Bot mascots over the app')
      await page.keyboard.press('Enter')
      await page.locator('.hr-layer').waitFor({ state: 'detached', timeout: 30000 })
      await shot(page, '06f-mascot-layer-off')
      return `the layer floated ${botId} ("${name}", canvas 150x165, live),`
        + ` its dot opened the 320x132 pill at ${Math.round(box.x)},${Math.round(box.y)},`
        + ' and the palette toggle took the layer back off'
    } catch (error) {
      // A bare `locator.waitFor` timeout says nothing about what the pane or the
      // layer looked like; the dump is what makes a failure actionable.
      const state = await page.evaluate(() => ({
        buttons: [...document.querySelectorAll('button[aria-label]')]
          .map(node => node.getAttribute('aria-label')).filter(Boolean).slice(0, 40),
        hrLayer: document.querySelectorAll('.hr-layer').length,
        hrSlots: document.querySelectorAll('.hr-layer-slot').length,
        railEntries: [...document.querySelectorAll('[data-rail-entry]')]
          .map(node => node.getAttribute('data-rail-entry')),
        roster: document.querySelectorAll('[data-slot="bots-roster"]').length,
        route: location.hash,
        text: (document.body.innerText || '').replace(/\s+/g, ' ').slice(0, 300),
      })).catch(() => null)
      diagnostics.mascots = state
      await shot(page, '06c-mascot-failure').catch(() => {})
      throw new Error(`${error && error.message ? error.message : error}; page state ${JSON.stringify(state)}`)
    }
  })

  // (a) The bundled extras skills are real, installed skills: the Skills tab
  //     of the Capabilities page renders one toggle row per installed skill,
  //     and each row's switch carries the skill name as its accessible name.
  const EXTRA_SKILLS = ['ambition', 'first-task', 'bot-team-builder', 'bot-team-retro', 'excalidraw', 'initiate-setup']
  await check('the bundled extras skills are listed on the Skills tab', () => withFreshPage(async tab => {
    await tab.goto(BASE + '/#/capabilities?tab=skills', { waitUntil: 'domcontentloaded' })
    await tab.waitForTimeout(6000)
    await shot(tab, '07-skills')
    const listed = []
    for (const name of EXTRA_SKILLS) {
      // Each installed skill row renders a toggle whose accessible name is the
      // skill name (`CapRow` -> `Switch aria-label={toggleLabel ?? title}`).
      const switchCount = await tab.locator(`[data-slot="switch"][aria-label="${name}"]`).count()
      if (switchCount < 1) {continue}
      listed.push(name)
    }
    diagnostics.skillsTab = { listed, body: (await bodyText(tab)).slice(0, 1200) }
    const missing = EXTRA_SKILLS.filter(name => !listed.includes(name))
    if (missing.length) {
      throw new Error(
        `the Skills tab has no row for ${missing.join(', ')} (found ${listed.join(', ') || 'none'});`
        + ` page text: ${(await bodyText(tab)).replace(/\n/g, ' / ').slice(0, 300)}`)
    }
    return `all ${EXTRA_SKILLS.length} extras skills are installed and listed (${listed.join(', ')})`
  }))

  // (b) `/ambition` completes to a real skill chip in the composer. The row is
  //     backend data (`complete.slash`), so a chip can only appear if stock
  //     Hermes knows the installed skill; the chip carries the skill kind.
  const SKILL_CHIP_SELECTOR = '[data-slot="composer-rich-input"] [data-slash-kind="skill"]'
  await check('typing /ambition offers the skill and inserts a skill chip', () => withFreshPage(async page => {
    const editor = page.locator('[data-slot="composer-root"] [contenteditable="true"]').first()
    await clickStable(editor, page)
    await page.keyboard.type('/amb')
    const drawer = page.locator('[data-slot="composer-completion-drawer"][role="listbox"]')
    await drawer.waitFor({ state: 'visible', timeout: 30000 })
    const row = drawer.locator('button:has(span[data-ref="skill"])').filter({ hasText: '/ambition' }).first()
    try {
      await row.waitFor({ state: 'visible', timeout: 30000 })
    } catch (error) {
      const rows = await drawer.locator('button').allInnerTexts()
      await shot(page, '07-ambition-chip')
      throw new Error(`no /ambition skill row in the completion drawer; rows: ${JSON.stringify(rows).slice(0, 300)}`)
    }
    await clickStable(row, page)
    const chip = page.locator(SKILL_CHIP_SELECTOR).first()
    await chip.waitFor({ state: 'attached', timeout: 15000 })
    const refText = await chip.getAttribute('data-ref-text')
    await shot(page, '07-ambition-chip')
    if (refText !== '/ambition') {
      throw new Error(`the chip carries data-ref-text=${JSON.stringify(refText)}, expected /ambition`)
    }
    return `the completion drawer offered the skill row and inserting it produced a skill chip (data-ref-text="${refText}")`
  }))

  // (c) File upload: the composer's own attach path posts the file to
  //     POST /api/chat/file-upload and shows an attachment pill, then a prompt
  //     carrying it gets a reply that quotes a token only the file holds.
  const ATTACHMENT_NAME = `hp-proof-attachment-${Math.random().toString(16).slice(2, 8)}.txt`
  const ATTACHMENT_TOKEN = `hp-attach-${Math.random().toString(16).slice(2, 10)}`
  const ATTACHMENT_PATH = join(tmpdir(), ATTACHMENT_NAME)
  writeFileSync(ATTACHMENT_PATH, `The attachment token is ${ATTACHMENT_TOKEN}.\n`, 'utf8')
  const uploadResponses = []

  // Both attachment checks run on ONE fresh page: the pill and the draft it
  // carries must survive from the attach step into the prompt step.
  let attachmentFlowStarted = false
  try {
    await withFreshPage(async page => {
      attachmentFlowStarted = true
      page.context().on('response', response => {
        if (response.url().includes('/api/chat/file-upload')) {
          uploadResponses.push({ status: response.status(), method: response.request().method() })
        }
      })
      await check('attaching a file uploads it and paints an attachment in the composer', async () => {
        const attachButton = page.getByRole('button', { name: 'Add context' }).first()
        await attachButton.waitFor({ state: 'visible', timeout: 30000 })
        await clickStable(attachButton, page)
        const [chooser] = await Promise.all([
          page.waitForEvent('filechooser', { timeout: 30000 }),
          clickStable(page.getByRole('menuitem', { name: /^Files/ }).first(), page),
        ])
        await chooser.setFiles(ATTACHMENT_PATH)
        const pill = page.locator('[data-slot="composer-attachments"] button')
          .filter({ hasText: ATTACHMENT_NAME }).first()
        try {
          await pill.waitFor({ state: 'visible', timeout: 60000 })
        } catch (error) {
          await shot(page, '08-attachment')
          const text = await bodyText(page)
          throw new Error(
            `no attachment pill for ${ATTACHMENT_NAME}; composer text: ${text.replace(/\n/g, ' / ').slice(0, 300)}`)
        }
        await shot(page, '08-attachment')
        diagnostics.attachment = { uploadResponses, pillText: (await pill.innerText()).trim() }
        const ok = uploadResponses.filter(item => item.status === 200)
        if (!ok.length) {
          throw new Error(`no successful POST /api/chat/file-upload; saw ${JSON.stringify(uploadResponses)}`)
        }
        return `POST /api/chat/file-upload answered 200 and the composer painted "${(await pill.innerText()).trim()}"`
      })

      const ATTACHMENT_PROMPTS = [
        `The file ${ATTACHMENT_NAME} is attached. Read it and reply with the token written inside it and nothing else.`,
        `You must read the attached file ${ATTACHMENT_NAME} before answering. Reply with only the token it contains. A reply without that token is wrong.`,
      ]
      let attachmentReply = null
      let attachmentPromptError = null
      let attachmentUnsettled = 0
      for (let attempt = 0; attempt < ATTACHMENT_PROMPTS.length && !attachmentReply; attempt += 1) {
        try {
          // Same rule as every other turn here: the next prompt only goes out
          // once this one has started and settled, or it would abort the turn
          // in flight.
          const turn = await sendAndSettle(page, ATTACHMENT_PROMPTS[attempt], sendMessageStable)
          if (!turn.started || !turn.settled) {
            attachmentUnsettled = attempt + 1
            break
          }
        } catch (error) {
          attachmentPromptError = error
          break
        }
        const texts = await assistantTexts(page)
        const quoted = texts.find(text => text.toLowerCase().includes(ATTACHMENT_TOKEN.toLowerCase())) || null
        diagnostics[`attachmentAttempt${attempt + 1}`] = {
          quoted: !!quoted, last: texts.length ? texts[texts.length - 1].slice(0, 200) : '',
        }
        if (quoted) {attachmentReply = quoted}
      }
      await shot(page, '08-attachment-reply')
      if (attachmentReply) {
        await check('a prompt carrying the attachment gets a reply that quotes it', () =>
          `the reply quoted the file's token: "${attachmentReply.slice(0, 160)}"`)
      } else if (attachmentUnsettled) {
        await blocked(
          'a prompt carrying the attachment gets a reply that quotes it',
          `the turn after prompt ${attachmentUnsettled} was still running after 5 minutes,`
          + ' so this check cannot be asserted (and no prompt is sent into a running turn)',
        )
      } else if (attachmentPromptError) {
        record(
          'a prompt carrying the attachment gets a reply that quotes it',
          'broken',
          `the prompt could not be sent: ${attachmentPromptError.message ? attachmentPromptError.message.split('\n')[0] : attachmentPromptError}`,
        )
      } else {
        const texts = await assistantTexts(page)
        record(
          'a prompt carrying the attachment gets a reply that quotes it',
          'broken',
          `no reply quoted ${ATTACHMENT_TOKEN} after ${ATTACHMENT_PROMPTS.length} prompt(s);`
          + ` last assistant text: ${(texts.length ? texts[texts.length - 1] : '(none)').slice(0, 200)}`,
        )
      }
    })
  } catch (error) {
    if (!attachmentFlowStarted) {
      record(
        'attaching a file uploads it and paints an attachment in the composer',
        'broken',
        `the chat page never became usable: ${error && error.message ? error.message.split('\n')[0] : error}`,
      )
    }
  }

  // (d) Share links: a grant for a session renders the filtered projection for
  //     an anonymous reader, and the projection carries no system/tool rows.
  let shareSessionId = ''
  let sharePath = ''
  await check('a share link can be created for a session', () => withFreshPage(async page => {
    const shareRows = (await rpc(page, 'session.list', { limit: 50 }).catch(() => ({})))?.sessions || []
    // The share target is chosen by CONTENT, not recency: the projection is
    // built from the stock `/api/sessions/<id>/messages` rows, so the session to
    // share is the one whose rows hold the first turn's reply. (Recency would
    // pick whichever session ran last — the attachment turn — and the viewer
    // check would then be about the wrong transcript.)
    const found = await page.evaluate(async ({ ids, needle }) => {
      for (const id of ids) {
        const response = await fetch(
          `/api/sessions/${id}/messages?order=oldest&limit=50&offset=0&inline_images=false`)
        if (!response.ok) {continue}
        const body = await response.json()
        const flat = (body.messages || []).map(row => String(row.display_content ?? row.content ?? '')).join('\n')
        if (flat.toLowerCase().includes(needle)) {return id}
      }
      return null
    }, { ids: shareRows.map(row => row.id), needle: FIRST_REPLY })
    shareSessionId = found || ''
    if (!shareSessionId) {
      throw new Error(`no session's message rows hold the first reply "${FIRST_REPLY}":`
        + ` ${JSON.stringify(shareRows.map(row => row.title || row.name)).slice(0, 250)}`)
    }
    const created = await page.evaluate(async sessionId => {
      const response = await fetch('/api/share/create', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId }),
      })
      return { status: response.status, body: await response.json() }
    }, shareSessionId)
    diagnostics.shareCreate = created
    if (created.status !== 200 || !created.body.token || !created.body.path) {
      throw new Error(`POST /api/share/create answered ${created.status}: ${JSON.stringify(created.body).slice(0, 200)}`)
    }
    sharePath = created.body.path
    return `share grant for ${shareSessionId} at ${sharePath} (expires ${created.body.expires_at})`
  }))

  await check('an anonymous reader sees the filtered projection at /share/view', async () => {
    if (!sharePath) {throw new Error('no share link: the create step did not complete')}
    const anonymous = await browser.newContext({ viewport: VIEWPORT })
    try {
      const viewer = await anonymous.newPage()
      const response = await viewer.goto(BASE + sharePath, { waitUntil: 'domcontentloaded' })
      if (!response || response.status() !== 200) {
        throw new Error(`GET ${sharePath} -> HTTP ${response ? response.status() : 'no response'}`)
      }
      // The viewer polls /api/share/view and paints one `.m` node per event.
      await viewer.waitForFunction(
        () => document.querySelectorAll('main#chat .m').length > 0, null, { timeout: 60000 })
      const rendered = (await viewer.evaluate(() => document.querySelector('main#chat').innerText))
        .replace(/\s+/g, ' ').trim()
      // The same projection, read directly: roles and keys must be filtered.
      const projection = await viewer.evaluate(async () => {
        const response = await fetch(`/api/share/view${location.search}`)
        return { status: response.status, body: await response.json() }
      })
      await shot(viewer, '09-share')
      diagnostics.share = { path: sharePath, rendered: rendered.slice(0, 600), projection: projection.body }
      if (projection.status !== 200) {throw new Error(`/api/share/view -> HTTP ${projection.status}`)}
      const events = projection.body.events
      if (!Array.isArray(events) || !events.length) {
        throw new Error(`the projection has no events: ${JSON.stringify(projection.body).slice(0, 250)}`)
      }
      const allowedRoles = new Set(['user', 'assistant'])
      const allowedKeys = new Set(['id', 'role', 'text', 'tools', 'timestamp'])
      for (const event of events) {
        if (!allowedRoles.has(event.role)) {
          throw new Error(`the projection leaked a ${event.role} row: ${JSON.stringify(event).slice(0, 200)}`)
        }
        const extra = Object.keys(event).filter(key => !allowedKeys.has(key))
        if (extra.length) {
          throw new Error(`the projection leaked keys ${extra.join(', ')}: ${JSON.stringify(event).slice(0, 200)}`)
        }
        if (!Array.isArray(event.tools) || event.tools.some(tool => typeof tool !== 'string')) {
          throw new Error(`tool entries are not bare names: ${JSON.stringify(event.tools).slice(0, 200)}`)
        }
      }
      if (!rendered.toLowerCase().includes(FIRST_REPLY)) {
        throw new Error(`the viewer did not render the shared transcript: ${rendered.slice(0, 300)}`)
      }
      return `${events.length} event(s), roles only user/assistant, keys only ${[...allowedKeys].join('/')},`
        + ` viewer rendered the shared reply`
    } finally {
      await anonymous.close()
    }
  })

  // (e) Mobile companion: the phone page is public; the pairing endpoints are
  //     the operator side and answer on the authenticated session.
  await check('the mobile companion page loads without a cookie', async () => {
    const anonymous = await browser.newContext({ viewport: { width: 390, height: 844 } })
    try {
      const phone = await anonymous.newPage()
      const response = await phone.goto(BASE + '/mobile', { waitUntil: 'domcontentloaded' })
      await shot(phone, '10-mobile')
      const text = (await bodyText(phone)).slice(0, 400)
      diagnostics.mobilePage = text
      if (!response || response.status() !== 200) {
        throw new Error(`GET /mobile -> HTTP ${response ? response.status() : 'no response'}`)
      }
      if (!/Hermes companion/i.test(text)) {
        throw new Error(`the mobile page has no companion shell: ${text.replace(/\n/g, ' / ')}`)
      }
      return `GET /mobile -> 200 and painted the companion shell`
    } finally {
      await anonymous.close()
    }
  })

  await check('the mobile pairing endpoint answers on the session', () => withFreshPage(async page => {
    const pairing = await page.evaluate(async () => {
      const response = await fetch('/api/mobile/pairing', { method: 'POST' })
      return { status: response.status, body: await response.json() }
    })
    diagnostics.mobilePairing = { status: pairing.status, keys: Object.keys(pairing.body || {}) }
    if (pairing.status !== 200 || !pairing.body.code || pairing.body.page !== '/mobile') {
      throw new Error(`POST /api/mobile/pairing answered ${pairing.status}: ${JSON.stringify(pairing.body).slice(0, 200)}`)
    }
    return `POST /api/mobile/pairing answered a ${String(pairing.body.code).length}-char code`
      + ` (expires in ${pairing.body.expires_in}s, page ${pairing.body.page})`
  }))

  // (f) Profiles switcher: the sidebar rail lists the profiles this backend
  //     serves, and picking one re-scopes the app to that profile.
  await check('the profiles switcher lists and switches the served profiles', () => withFreshPage(async page => {
    const rail = page.locator('[data-slot="profile-rail"]').first()
    await rail.waitFor({ state: 'visible', timeout: 30000 })
    const railLabel = await rail.getAttribute('aria-label')
    const served = (await page.evaluate(async () => {
      const response = await fetch('/api/profiles')
      return response.ok ? (await response.json()).profiles : null
    })) || []
    const named = served.filter(profile => !profile.is_default).map(profile => profile.name)
    if (!named.length) {
      throw new Error(`the backend served no named profile to switch to: ${JSON.stringify(served).slice(0, 200)}`)
    }
    // The rail's own controls: the default's pill (aria-pressed) and one square
    // per named profile in the wide layout, or a condensed dropdown whose rows
    // are the named profiles (`ProfileDropdown` gets `named`, the default stays
    // on its pill).
    const squares = await rail.locator('button[aria-pressed]')
      .evaluateAll(nodes => nodes.map(node => node.getAttribute('aria-label') || ''))
    const dropdown = rail.locator('[data-slot="profile-dropdown"]').first()
    let rows = []
    if (await dropdown.count()) {
      await clickStable(dropdown, page)
      try {
        await page.locator('[role="menuitemradio"]').first().waitFor({ state: 'visible', timeout: 20000 })
      } catch (error) {
        await shot(page, '11-profiles')
        throw new Error(`the profiles dropdown painted no rows (rail label ${JSON.stringify(railLabel)})`)
      }
      rows = await page.locator('[role="menuitemradio"]')
        .evaluateAll(nodes => nodes.map(node => (node.innerText || '').replace(/\s+/g, ' ').trim()))
    }
    const listed = squares.concat(rows)
    await shot(page, '11-profiles')
    diagnostics.profilesSwitcher = { railLabel, served: served.map(profile => profile.name), squares, rows }
    if (!listed.length) {
      throw new Error(`the rail (aria-label ${JSON.stringify(railLabel)}) painted no profile control`)
    }
    const missing = named.filter(name =>
      !listed.some(label => label.toLowerCase().includes(name.toLowerCase())))
    if (missing.length) {
      throw new Error(`the rail offers no control for ${missing.join(', ')}; controls: ${JSON.stringify(listed).slice(0, 250)}`)
    }

    // Switch: select the named profile through whichever control the rail
    // exposes, then require the app to re-scope its requests to it. Nothing
    // short of a `profile=` round trip proves the switcher did anything.
    const target = named[0]
    const scoped = []
    page.on('request', request => {
      if (request.url().includes(`profile=${encodeURIComponent(target)}`)) {scoped.push(request.url())}
    })
    if (rows.length) {
      await clickStable(page.locator('[role="menuitemradio"]').filter({ hasText: target }).first(), page)
    } else {
      await clickStable(rail.getByRole('button', { name: new RegExp(`^${target}`, 'i') }).first(), page)
    }
    const deadline = Date.now() + 60000
    while (!scoped.length && Date.now() < deadline) {await page.waitForTimeout(500)}
    const active = await rail.locator('button[aria-pressed="true"]')
      .evaluateAll(nodes => nodes.map(node => node.getAttribute('aria-label') || ''))
    await shot(page, '11-profiles-switched')
    diagnostics.profilesSwitch = { target, scoped: scoped.slice(0, 3), active }
    if (!scoped.length) {
      throw new Error(`selecting ${target} produced no request scoped to that profile;`
        + ` the rail's active control is ${JSON.stringify(active)}`)
    }
    return `${served.length} profiles served (${served.map(profile => profile.name).join(', ')}),`
      + ` the rail lists ${listed.length} control(s); selecting ${target} scoped ${scoped.length} request(s) to it`
      + ` (e.g. ...${scoped[0].slice(-48)})`
  }))

  // (g) The bundled UI plugins (PARITY row 32). Each one is a real plugin in
  //     the packaged build, so the Plugins inventory must carry its row, with
  //     the per-half switches the page renders for every installed plugin.
  //     `hello-runtime` is deliberately NOT here: its file is `plugin.runtime.js`
  //     and the boot glob is `plugin.{js,ts,tsx}`, so it only ever loads through
  //     the runtime door — check (g2) below proves that path instead.
  const BUNDLED_PLUGINS = ['AgentCraft Studio', 'Bots', 'Kanban', 'Radio']
  await check('the bundled UI plugins are installed on the Plugins tab', () => withFreshPage(async page => {
    await page.goto(BASE + '/#/capabilities?tab=plugins', { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(8000)
    await shot(page, '12-plugins-tab')
    const rows = {}
    for (const name of BUNDLED_PLUGINS) {
      // `plugins-tab.tsx` labels each half's switch `${Desktop|Agent}: ${pkg.name}`.
      rows[name] = await page.locator(`[data-slot="switch"][aria-label$=": ${name}"]`)
        .evaluateAll(nodes => nodes.map(node => node.getAttribute('aria-label')))
    }
    diagnostics.pluginsTab = rows
    const missing = BUNDLED_PLUGINS.filter(name => !rows[name].length)
    if (missing.length) {
      const seen = await page.locator('[data-slot="switch"]')
        .evaluateAll(nodes => nodes.map(node => node.getAttribute('aria-label')))
      throw new Error(`no Plugins-tab row for ${missing.join(', ')}; the page's plugin switches are`
        + ` ${JSON.stringify(seen).slice(0, 300)}`)
    }
    const summary = BUNDLED_PLUGINS.map(name => `${name} (${rows[name].join(' + ')})`)
    return `the Plugins inventory carries a toggle row for each bundled plugin: ${summary.join(', ')}`
  }))

  // (g2) The runtime door: a plugin.js dropped into
  //      `<hermes home>/desktop-plugins/<id>/` is loaded by the app's watcher,
  //      appears in the same inventory as a `kind: runtime` plugin, and paints
  //      its status-bar contribution. run-proof.sh installs the repo's own
  //      hello-runtime fixture this way and passes --desktop-plugin.
  if (DESKTOP_PLUGIN) {
    await check(`the plugin installed through the disk door (${DESKTOP_PLUGIN}) is live`, () =>
      withFreshPage(async page => {
        const marker = DESKTOP_PLUGIN_MARKERS[DESKTOP_PLUGIN] || ''
        const statusText = await page.locator('[data-slot="statusbar"]').first().innerText().catch(() => '')
        await page.goto(BASE + '/#/capabilities?tab=plugins', { waitUntil: 'domcontentloaded' })
        await page.waitForTimeout(9000)
        await shot(page, '12-desktop-plugin')
        const switches = await page.locator('[data-slot="switch"]')
          .evaluateAll(nodes => nodes.map(node => node.getAttribute('aria-label')))
        // The inventory labels a row by the plugin's TITLE, not its directory
        // id (`hello-runtime` is listed as `Desktop: Hello Runtime`), so the id
        // is humanized before matching.
        const humanized = DESKTOP_PLUGIN.replace(/[-_]+/g, ' ').replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
        const listed = switches.filter(label => /(desktop|agent)/i.test(label || '')
          && new RegExp(`: ${humanized}$`, 'i').test(label || ''))
        const text = await bodyText(page)
        diagnostics.desktopPlugin = { id: DESKTOP_PLUGIN, switches: listed, marker, statusText }
        if (!listed.length) {
          throw new Error(`the Plugins inventory has no row for the disk-door plugin ${DESKTOP_PLUGIN};`
            + ` its switches are ${JSON.stringify(switches).slice(0, 300)}`)
        }
        if (marker && !statusText.includes(marker)) {
          throw new Error(`the status bar does not carry the ${DESKTOP_PLUGIN} contribution`
            + ` (expected ${JSON.stringify(marker)}): ${statusText.replace(/\n/g, ' / ').slice(0, 200)}`)
        }
        return `the disk-door plugin is listed (${listed.join(', ')})`
          + (marker ? ` and its status-bar chip ${JSON.stringify(marker)} is on screen` : '')
          + ` (page text ${text.length} chars)`
      }))
  }

  // (h) The bundled plugins' own surfaces actually mount: two contributed full
  //     pages, and the two overlay views (which are the app's own route views,
  //     not plugin contributions, but the same PARITY row names them).
  await check('the bundled plugin pages and overlays render', () => withFreshPage(async page => {
    const surfaces = []

    await page.goto(BASE + '/#/kanban', { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(8000)
    await shot(page, '12-kanban')
    const kanbanText = await bodyText(page)
    diagnostics.kanban = kanbanText.slice(0, 800)
    if (!/New task/.test(kanbanText) || !/Triage/.test(kanbanText)) {
      throw new Error(`the /kanban page did not paint the board: ${kanbanText.replace(/\n/g, ' / ').slice(0, 300)}`)
    }
    surfaces.push('kanban board at /kanban')

    await page.goto(BASE + '/#/studio', { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(10000)
    await shot(page, '12-studio')
    const studioRoots = await page.locator('.ac-page').count()
    diagnostics.studio = { acPage: studioRoots, text: (await bodyText(page)).slice(0, 400) }
    if (!studioRoots) {
      throw new Error(`the /studio page painted no AgentCraft root (.ac-page);`
        + ` body: ${(await bodyText(page)).replace(/\n/g, ' / ').slice(0, 300)}`)
    }
    surfaces.push('agentcraft studio at /studio')

    await page.goto(BASE + '/#/starmap', { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(12000)
    await shot(page, '12-starmap')
    const starmapOverlay = await page.locator('[data-overlay-surface]').count()
    // The map is a canvas; with nothing learned yet (or a scan that failed) the
    // same overlay states that instead. Both are real outcomes, so the check
    // requires the overlay AND one of the two states.
    const starmapCanvas = await page.locator('canvas.touch-none.select-none').count()
    const starmapText = await bodyText(page)
    const starmapState = starmapCanvas
      ? `${starmapCanvas} canvas`
      : (/Nothing learned yet/.test(starmapText) ? 'the empty state ("Nothing learned yet")'
        : (/Could not load memory graph/.test(starmapText) ? 'the load-failure state'
          : 'neither the map nor an empty/failed state'))
    diagnostics.starmap = { overlay: starmapOverlay, canvas: starmapCanvas, text: starmapText.slice(0, 400) }
    if (!starmapOverlay || (!starmapCanvas && !/Nothing learned yet|Could not load memory graph/.test(starmapText))) {
      throw new Error(`/starmap opened no memory-graph overlay content`
        + ` (overlay surfaces ${starmapOverlay}, ${starmapState};`
        + ` text ${starmapText.replace(/\n/g, ' / ').slice(0, 250)})`)
    }
    surfaces.push(`memory-graph overlay at /starmap (${starmapState})`)

    await page.goto(BASE + '/#/mission-control', { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(9000)
    await shot(page, '12-mission-control')
    const missionOverlay = await page.locator('[data-overlay-surface]').count()
    const missionCards = await page.locator('[data-overview-card]').count()
    const missionText = await bodyText(page)
    diagnostics.missionControl = { overlay: missionOverlay, cards: missionCards, text: missionText.slice(0, 400) }
    if (!missionOverlay || (missionCards < 1 && !/No live sessions/.test(missionText))) {
      throw new Error(`/mission-control opened no session-overview overlay`
        + ` (overlay surfaces ${missionOverlay}, cards ${missionCards},`
        + ` text ${missionText.replace(/\n/g, ' / ').slice(0, 200)})`)
    }
    surfaces.push(`mission-control overlay at /mission-control (${missionCards} card(s))`)

    return `rendered ${surfaces.join(', ')}`
  }))

  // (i) i18n and themes: the Appearance settings own both — the language
  //     switcher (`Switch language`, on the General sub-page) and the look /
  //     colour-mode segmented controls the theme system drives (Theme
  //     sub-page). The appearance view redirects to `page=general` by default,
  //     so each half is opened by its own sub-page.
  await check('the appearance settings expose the language switcher and the theme controls', () =>
    withFreshPage(async page => {
      await page.goto(BASE + '/#/settings?tab=config:appearance&page=general', { waitUntil: 'domcontentloaded' })
      await page.waitForTimeout(9000)
      await shot(page, '12-appearance')
      const languageButtons = await page.locator('button[aria-label]')
        .evaluateAll(nodes => nodes.map(node => node.getAttribute('aria-label')).filter(label => /switch language/i.test(label || '')))
      const generalText = await bodyText(page)
      if (!languageButtons.length) {
        throw new Error(`no language switcher on the Appearance page:`
          + ` ${generalText.replace(/\n/g, ' / ').slice(0, 300)}`)
      }
      await page.goto(BASE + '/#/settings?tab=config:appearance&page=theme', { waitUntil: 'domcontentloaded' })
      await page.waitForTimeout(9000)
      await shot(page, '12-appearance-theme')
      const pressed = await page.locator('button[aria-pressed]')
        .evaluateAll(nodes => nodes.map(node => (node.innerText || '').trim()).filter(Boolean))
      const text = await bodyText(page)
      diagnostics.appearance = { languageButtons, pressed: pressed.slice(0, 20), text: text.slice(0, 800) }
      const looks = pressed.filter(label => /^(soft|classic)$/i.test(label))
      const modes = pressed.filter(label => /^(light|dark|system|auto)$/i.test(label))
      if (!looks.length || !modes.length) {
        throw new Error(`the theme controls are missing (look options ${JSON.stringify(looks)},`
          + ` colour-mode options ${JSON.stringify(modes)}); pressed controls: ${JSON.stringify(pressed).slice(0, 250)}`
          + `; page text: ${text.replace(/\n/g, ' / ').slice(0, 250)}`)
      }
      return `the language switcher (${languageButtons.length} control) and the theme controls are on screen:`
        + ` look ${JSON.stringify(looks)}, mode ${JSON.stringify(modes)}`
    }))

  diagnostics.consoleErrors = consoleErrors
  diagnostics.failedRequests = failedRequests
  await context.close()
} catch (error) {
  record('proof run', 'broken', `aborted: ${error && error.message ? error.message : String(error)}`)
} finally {
  if (context) {await context.close().catch(() => {})}
  await browser.close().catch(() => {})
}

const report = {
  base: BASE,
  viewport: VIEWPORT,
  generatedAt: new Date().toISOString(),
  results,
  diagnostics,
}
writeFileSync(REPORT, JSON.stringify(report, null, 2))

const broken = results.filter(item => item.status === 'broken')
const blockedItems = results.filter(item => item.status === 'blocked')
console.log(`\n${results.length - broken.length - blockedItems.length} checks passed, ${blockedItems.length} blocked, ${broken.length} broken`)
if (diagnostics.failedRequests && diagnostics.failedRequests.length) {
  console.log('failed requests:')
  for (const item of diagnostics.failedRequests) {
    console.log(`  - ${item.status || item.error} ${item.method} ${item.url} (${item.resourceType})`)
  }
}
if (diagnostics.consoleErrors && diagnostics.consoleErrors.length) {
  console.log('console errors:')
  for (const text of diagnostics.consoleErrors) {console.log(`  - ${text}`)}
}
if (broken.length) {
  console.log('broken:')
  for (const item of broken) {console.log(`  - ${item.name}: ${item.detail}`)}
}
console.log(`report: ${REPORT}`)
process.exit(broken.length ? 1 : 0)

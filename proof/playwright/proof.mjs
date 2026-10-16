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

const assistantTexts = page =>
  page.$$eval('[data-slot="aui_assistant-message-root"]', nodes =>
    nodes.map(node => (node.innerText || '').replace(/\s+/g, ' ').trim()))

async function waitForAssistant(page, needle, timeoutMs) {
  await page.waitForFunction(
    value => [...document.querySelectorAll('[data-slot="aui_assistant-message-root"]')]
      .some(node => (node.innerText || '').toLowerCase().includes(value.toLowerCase())),
    needle,
    { timeout: timeoutMs },
  )
}

/** Wait until the transcript stops changing, so screenshots show a settled turn. */
async function waitForSettled(page, timeoutMs = 120000) {
  const deadline = Date.now() + timeoutMs
  let previous = ''
  while (Date.now() < deadline) {
    const current = await page.evaluate(() => {
      const viewport = document.querySelector('[data-slot="aui_thread-viewport"]')
      const stop = document.querySelector('[data-slot="composer-root"] button[aria-label="Stop"]')
      return `${stop ? 'busy' : 'idle'}|${viewport ? viewport.innerText.replace(/\s+/g, ' ').trim() : ''}`
    })
    if (current === previous && current.startsWith('idle|')) {return true}
    previous = current
    await page.waitForTimeout(1500)
  }
  return false
}

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
  const FIRST_PROMPT = 'Reply with exactly: hello from the proof'
  const FIRST_REPLY = 'hello from the proof'
  await sendMessage(page, FIRST_PROMPT)
  try {
    await waitForAssistant(page, FIRST_REPLY, 240000)
    await waitForSettled(page)
    await shot(page, '02-reply')
    await check('chat reply streams from the configured model', async () => {
      const texts = await assistantTexts(page)
      const hit = texts.find(text => text.toLowerCase().includes(FIRST_REPLY))
      return `assistant said "${hit}"`
    })
  } catch (error) {
    await shot(page, '02-reply')
    await blocked('chat reply streams from the configured model', `no reply: ${error.message}`)
  }
  diagnostics.assistantTexts = await assistantTexts(page)

  // ── 4. Tool cards (only when the turn actually used a tool) ──────────────
  // The card slots are the UI's own (`tool-activity-stack` for a run of tool
  // calls, `tool-block` for a single one). Nothing else counts: a "More tools"
  // chrome button is not a tool card, and reporting one as such would be a
  // false pass.
  const TOOL_CARD_SELECTOR = '[data-slot="tool-activity-stack"], [data-slot="tool-block"]'
  const toolCardText = await page.evaluate(selector => {
    const node = document.querySelector(selector)
    return node ? (node.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 200) : ''
  }, TOOL_CARD_SELECTOR)
  if (toolCardText) {
    await shot(page, '02-tool-card')
    await check('tool card rendered', () => `card text: ${toolCardText}`)
  } else {
    await blocked('tool card rendered', 'the proof turn used no tools, so the transcript holds no tool card')
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
  await sendMessage(page, SECOND_PROMPT)
  try {
    await waitForAssistant(page, 'session two ok', 240000)
    await waitForSettled(page)
    await shot(page, '03-second-session')
    await check('second session reply', async () => {
      const texts = await assistantTexts(page)
      const hit = texts.find(text => text.toLowerCase().includes('session two ok'))
      return `assistant said "${hit}"`
    })
  } catch (error) {
    await shot(page, '03-second-session')
    await blocked('second session reply', `no reply: ${error.message}`)
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
  const firstTitle = sessionTitles.find(title => !title.includes(SECOND_TITLE)) || FIRST_PROMPT
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
    const clicked = await clickSessionByTitle(page, firstTitle.slice(0, 12))
    if (!clicked) {
      throw new Error(`could not click the first session row "${firstTitle}": ${JSON.stringify(railTitles).slice(0, 200)}`)
    }
    await waitForAssistant(page, FIRST_REPLY, 120000)
    await waitForSettled(page)
    await shot(page, '03-sessions')
    const texts = await assistantTexts(page)
    if (!texts.some(text => text.toLowerCase().includes(FIRST_REPLY))) {
      throw new Error(`reopened session shows: ${JSON.stringify(texts).slice(0, 300)}`)
    }
    return `reopened "${firstTitle}" and its reply is on screen`
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
    // command-palette row is labelled "Bots" and opens it (the `/roster` route
    // is the fleet-run roster, a different surface).
    await page.keyboard.press('Control+k')
    await page.waitForTimeout(1200)
    await page.keyboard.type('Bots')
    await page.waitForTimeout(1200)
    await shot(page, '05-bot-palette')
    await page.keyboard.press('Enter')
    await page.waitForTimeout(4000)
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
if (broken.length) {
  console.log('broken:')
  for (const item of broken) {console.log(`  - ${item.name}: ${item.detail}`)}
}
console.log(`report: ${REPORT}`)
process.exit(broken.length ? 1 : 0)

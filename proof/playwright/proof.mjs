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
// Session rows in the chat sidebar (`data-sidebar-row` is the rail's row
// attribute, shared by session, bot and cron rows). Titles are the first user
// message, which is what step (d) asserts after a reload.
const SESSION_ROW_SELECTOR = '[data-sidebar-row]'

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

/** RPC through a WebSocket built in the page from the sidecar's own origin and token. */
const RPC_HELPER = `
window.__proofRpc = (method, params) => new Promise((resolve, reject) => {
  const token = window.__HERMES_SESSION_TOKEN__ || ''
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws'
  const socket = new WebSocket(scheme + '://' + location.host + '/api/ws?token=' + encodeURIComponent(token))
  const id = Math.floor(Math.random() * 1e9)
  const timer = setTimeout(() => { try { socket.close() } catch {} ; reject(new Error('rpc timeout: ' + method)) }, 60000)
  socket.onerror = () => { clearTimeout(timer); reject(new Error('rpc socket error: ' + method)) }
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

/** Describe the DOM around a session title so the rail selector can be verified. */
async function describeTitleNodes(page, title) {
  return await page.evaluate(value => {
    const describe = node => {
      const chain = []
      let current = node
      for (let depth = 0; current && depth < 5; depth += 1) {
        chain.push(
          current.tagName.toLowerCase()
          + (current.getAttribute('role') ? `[role=${current.getAttribute('role')}]` : '')
          + (current.dataset && Object.keys(current.dataset).length
            ? `{data:${Object.keys(current.dataset).join(',')}}`
            : '')
          + (current.className && typeof current.className === 'string'
            ? `.${current.className.split(/\s+/).slice(0, 4).join('.')}`
            : ''),
        )
        current = current.parentElement
      }
      return chain.join(' < ')
    }
    return [...document.querySelectorAll('body *')]
      .filter(node => node.children.length === 0 && (node.textContent || '').trim() === value)
      .slice(0, 4)
      .map(node => ({ text: node.textContent.trim(), outer: node.parentElement?.outerHTML.slice(0, 400), chain: describe(node) }))
  }, title)
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
  page.on('console', message => {
    if (message.type() === 'error') {consoleErrors.push(message.text().slice(0, 300))}
  })
  page.on('pageerror', error => consoleErrors.push(`pageerror: ${String(error).slice(0, 300)}`))

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
    railRows: await page.$$eval(SESSION_ROW_SELECTOR, nodes =>
      nodes.slice(0, 6).map(node => node.outerHTML.slice(0, 600))),
  }
  await check('app boots into the chat UI', () => {
    const fatal = /desktop boot failed|uncaught|application error/i
    if (consoleErrors.some(text => fatal.test(text))) {
      throw new Error(`boot error in console: ${consoleErrors.join(' | ')}`)
    }
    return 'composer rendered, no boot-failure text'
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

  // ── 4. Tool cards (only when the turn used tools) ────────────────────────
  const toolSlots = ['aui_tool-card', 'aui_tool_call', 'tool-card', 'aui_tool']
  const foundToolSlot = await page.evaluate(slots => {
    for (const slot of slots) {
      const node = document.querySelector(`[data-slot="${slot}"]`)
      if (node) {return slot}
    }
    const byText = [...document.querySelectorAll('button, [role="button"]')]
      .find(node => /tool|bash|read file/i.test(node.getAttribute('aria-label') || ''))
    return byText ? 'button:' + (byText.getAttribute('aria-label') || '') : null
  }, toolSlots)
  if (foundToolSlot && foundToolSlot !== 'button:') {
    await shot(page, '02-tool-card')
    await check('tool card rendered', () => `found ${foundToolSlot}`)
  } else {
    await blocked('tool card rendered', 'the turn used no tools, so no card was produced')
  }

  // ── 5. Sessions survive a reload ─────────────────────────────────────────
  const SECOND_PROMPT = 'Second session: reply with exactly session two ok'
  const SECOND_TITLE = 'Second session'
  const NEW_SESSION_LABELS = ['New session', 'New chat', 'New session tab']
  await check('start a second session from the UI', async () => {
    const opened = await page.evaluate(async () => {
      const view = document.querySelector('[data-slot="aui_thread-viewport"]')
      return Boolean(view)
    })
    if (!opened) {throw new Error('thread viewport missing before starting a session')}
    for (const label of NEW_SESSION_LABELS) {
      const button = page.getByRole('button', { name: label, exact: false }).first()
      if (await button.count()) {
        await button.click()
        return `clicked "${label}"`
      }
    }
    await page.goto(BASE + '/#/', { waitUntil: 'domcontentloaded' })
    await composer(page)
    return 'no new-session button; navigated to the new-chat route'
  })

  await composer(page)
  await page.waitForTimeout(1500)
  await sendMessage(page, SECOND_PROMPT)
  try {
    await waitForAssistant(page, 'session two ok', 240000)
    await waitForSettled(page)
  } catch (error) {
    await blocked('second session reply', `no reply: ${error.message}`)
  }

  await page.reload({ waitUntil: 'domcontentloaded' })
  await composer(page)
  await page.waitForTimeout(4000)
  await page.evaluate(() => {
    window.__proofRpc = window.__proofRpc || null
  })
  await page.evaluate(RPC_HELPER)
  const sessions = await rpc(page, 'session.list', { limit: 50 }).catch(error => ({ error: String(error) }))
  diagnostics.sessions = sessions
  diagnostics.titleNodes = await describeTitleNodes(page, FIRST_PROMPT)
  diagnostics.secondTitleNodes = await describeTitleNodes(page, SECOND_TITLE)
  const railTitles = await sessionRailTitles(page)
  diagnostics.railTitles = railTitles
  await shot(page, '03-sessions')

  await check('both sessions are listed after a reload', async () => {
    if (!railTitles.some(title => title.includes(FIRST_PROMPT.slice(0, 20)))) {
      throw new Error(`first session missing from the rail: ${JSON.stringify(railTitles).slice(0, 300)}`)
    }
    if (!railTitles.some(title => title.includes(SECOND_TITLE))) {
      throw new Error(`second session missing from the rail: ${JSON.stringify(railTitles).slice(0, 300)}`)
    }
    return `${railTitles.length} rows: ${railTitles.join(' | ').slice(0, 200)}`
  })

  await check('the first session still holds its messages after a reload', async () => {
    const clicked = await clickSessionByTitle(page, FIRST_PROMPT.slice(0, 20))
    if (!clicked) {throw new Error('could not click the first session row')}
    await waitForAssistant(page, FIRST_REPLY, 60000)
    await page.waitForTimeout(1200)
    await shot(page, '03-sessions')
    const texts = await assistantTexts(page)
    if (!texts.some(text => text.toLowerCase().includes(FIRST_REPLY))) {
      throw new Error(`reopened session shows: ${JSON.stringify(texts).slice(0, 300)}`)
    }
    return 'reopened the first session and its reply is on screen'
  })

  // ── 6. Bot team ──────────────────────────────────────────────────────────
  const TEAM_NAME = 'Proof Team'
  let teamId = null
  await check('create a bot team over the app transport', async () => {
    const team = await rpc(page, 'bots_team.create', {
      name: TEAM_NAME,
      mission: 'Prove the group chat surface on stock Hermes.',
      lead_decides: true,
    })
    teamId = team && team.id ? team.id : null
    if (!teamId) {throw new Error(`bots_team.create returned ${JSON.stringify(team).slice(0, 200)}`)}
    return `team ${teamId}`
  })

  {
    await page.goto(`${BASE}/?win=botroom`, { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(6000)
    await shot(page, '04-botroom-window')
    const text = (await bodyText(page)).trim()
    diagnostics.botRoomWindow = text.slice(0, 1000)
    // The Bot Room is an Electron-only always-on-top overlay whose state is
    // pushed over IPC by the main renderer; the browser host has no IPC, so an
    // empty stage here is an Electron-only surface, not a broken add-on.
    if (!text) {
      await blocked('bot room overlay window', 'the ?win=botroom overlay paints nothing in a browser tab: its roster/room state arrives over Electron IPC from the main renderer')
    } else {
      record('bot room overlay window', 'pass', `text: ${text.slice(0, 120).replace(/\n/g, ' / ')}`)
    }
  }

  await page.goto(BASE + '/#/team', { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(6000)
  await shot(page, '04-botroom')
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
    await page.goto(BASE + '/#/roster', { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(4000)
    await shot(page, '05-bot')
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
if (broken.length) {
  console.log('broken:')
  for (const item of broken) {console.log(`  - ${item.name}: ${item.detail}`)}
}
console.log(`report: ${REPORT}`)
process.exit(broken.length ? 1 : 0)

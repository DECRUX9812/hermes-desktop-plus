import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

import {
  RELAY_DELIVER_BACKEND_CEILING_MS,
  RELAY_DELIVER_SETTLEMENT_MARGIN_MS,
  RELAY_DELIVER_TIMEOUT_MS,
  RELAY_TURN_ATTEMPT_MS,
  RELAY_TURN_LOCK_WAIT_MS,
  RELAY_TURN_MAX_ATTEMPTS
} from './relay-budget'

// #93911 review follow-up: the Desktop deadline for bot_relay.deliver mirrors
// three backend numbers. Nothing in the type system links a TS constant to a
// Python default, so this file is the seam: it reads the backend defaults and
// fails when a mirror drifts or the settlement margin stops being positive.
// Without it, raising the backend turn timeout would silently reintroduce
// #93911 — the client giving up before a valid typed settlement arrives.
//
// In this repo the backend python sources live in an upstream hermes-agent
// checkout, not beside the UI: resolve it from HERMES_PLUS_TEST_HERMES_CHECKOUT
// (same variable the python suites use) and skip the suite with a reason when
// no checkout is available (the ui job has none).

const upstreamRoot = process.env.HERMES_PLUS_TEST_HERMES_CHECKOUT ?? ''
const configDefaultsPath = join(upstreamRoot, 'hermes_cli/config_defaults.py')
const relayPlumbingPath = join(upstreamRoot, 'tools/bot_relay.py')
const haveBackendSources = existsSync(configDefaultsPath) && existsSync(relayPlumbingPath)

if (!haveBackendSources) {
  console.warn(
    `relay budget drift-check SKIPPED: HERMES_PLUS_TEST_HERMES_CHECKOUT (${upstreamRoot || 'unset'}) ` +
      'has no hermes_cli/config_defaults.py and tools/bot_relay.py; point it at a hermes-agent checkout to run it'
  )
}

// Reads happen inside the tests: a skipped suite's describe body still runs
// at collection time, and the files do not exist in this repo without a
// checkout.
const readConfigDefaults = (): string => readFileSync(configDefaultsPath, 'utf8')
const readRelayPlumbing = (): string => readFileSync(relayPlumbingPath, 'utf8')

const maybeDescribe = haveBackendSources ? describe : describe.skip

maybeDescribe('bot_relay.deliver budget mirrors', () => {
  function pyConstant(name: string): number {
    const match = readRelayPlumbing().match(new RegExp(`^${name}\\s*=\\s*(\\d+)`, 'm'))
    expect(match, `${name} must exist as a literal in tools/bot_relay.py`).toBeTruthy()

    return Number(match![1])
  }

  it('mirrors the backend turn-lock default', () => {
    const lockWaitMatch = readConfigDefaults().match(/"turn_wait_seconds":\s*(\d+)/)

    expect(lockWaitMatch, 'bot_mode.turn_wait_seconds default must exist in config_defaults.py').toBeTruthy()
    expect(RELAY_TURN_LOCK_WAIT_MS).toBe(Number(lockWaitMatch![1]) * 1000)
  })

  it('mirrors the backend per-attempt turn timeout', () => {
    // The backend names both numbers explicitly (tools/bot_relay.py) so the mirror is a
    // constant-to-constant check, not a count of textual subprocess.run(...) call sites.
    expect(RELAY_TURN_ATTEMPT_MS).toBe(pyConstant('TURN_ATTEMPT_TIMEOUT_SECONDS') * 1000)
    expect(RELAY_TURN_MAX_ATTEMPTS).toBe(pyConstant('TURN_MAX_ATTEMPTS'))
  })

  it('shares its settlement margin with the sender-side waiter budget', () => {
    // tests/tools/test_bot_relay.py checks that REPLY_WAIT_SECONDS exceeds the rebuilt sum.
    expect(RELAY_DELIVER_SETTLEMENT_MARGIN_MS).toBe(pyConstant('DESKTOP_DELIVER_SETTLEMENT_MARGIN_SECONDS') * 1000)
  })

  it('keeps the client deadline strictly greater than the backend ceiling', () => {
    // Strictly greater, not equal: a backend that answers at its own limit
    // still has to serialize and transport that answer.
    expect(RELAY_DELIVER_SETTLEMENT_MARGIN_MS, 'settlement margin must be positive').toBeGreaterThan(0)

    // The ceiling is the backend's own worst case: lock wait + every attempt;
    // the mirror tests above tie each term to its Python default.
    expect(RELAY_DELIVER_BACKEND_CEILING_MS).toBe(
      RELAY_TURN_LOCK_WAIT_MS + RELAY_TURN_ATTEMPT_MS * RELAY_TURN_MAX_ATTEMPTS
    )
    expect(RELAY_DELIVER_TIMEOUT_MS).toBe(RELAY_DELIVER_BACKEND_CEILING_MS + RELAY_DELIVER_SETTLEMENT_MARGIN_MS)
  })
})

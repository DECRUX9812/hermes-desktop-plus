import { describe, expect, it } from 'vitest'

import { resolveBrowserTransportConfig } from './browser-transport'

describe('browser transport configuration', () => {
  it.each([
    {
      expectedApi: 'https://runtime.example/api',
      input: {
        buildApiBase: 'https://build.example/api',
        injectedBasePath: '/injected',
        runtime: { apiBase: 'https://runtime.example/api', basePath: '/runtime' }
      },
      name: 'runtime API base and path'
    },
    {
      expectedApi: 'https://build.example/api',
      input: { buildApiBase: 'https://build.example/api', injectedBasePath: '/injected' },
      name: 'build API base when runtime is absent'
    },
    {
      expectedApi: '',
      input: { injectedBasePath: '/injected' },
      name: 'same origin when no API base is configured'
    }
  ])('$name', ({ expectedApi, input }) => {
    expect(resolveBrowserTransportConfig(input)).toMatchObject({ apiBase: expectedApi })
  })

  it('gives runtime WebSocket and path settings their own precedence', () => {
    expect(
      resolveBrowserTransportConfig({
        buildApiBase: 'https://build.example/api',
        injectedBasePath: '/injected/',
        runtime: { basePath: '/runtime/', wsBase: 'wss://socket.example/ws/' }
      })
    ).toEqual({
      apiBase: 'https://build.example/api',
      basePath: '/runtime',
      wsBase: 'wss://socket.example/ws'
    })
  })
})

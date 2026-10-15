/**
 * Regression guard for the chat pane's runtime mount.
 *
 * `AssistantRuntimeProvider` renders `@assistant-ui/tap` resources, and tap's
 * `useSyncExternalStore` compares the snapshot a fiber rendered against a fresh
 * `getSnapshot()` on every commit. `@assistant-ui/core`'s
 * `ThreadListRuntimeImpl.getState()` builds a new state object per call, so a
 * tap version that re-checks the snapshot at commit time (0.9.23 and later)
 * re-renders forever and throws "Maximum update depth exceeded" — which takes
 * the whole workspace pane down with it and leaves the app showing
 * `"workspace" failed to render`.
 *
 * The vendored tree therefore keeps tap at the version the desktop ships; the
 * pin lives in the root `package.json` (`overrides`) because tap is a
 * transitive dependency of `@assistant-ui/react`.
 */
import { AssistantRuntimeProvider, type ThreadMessage, useExternalStoreRuntime } from '@assistant-ui/react'
import { render, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'

function Harness() {
  const runtime = useExternalStoreRuntime<ThreadMessage>({
    messages: [],
    isRunning: false,
    onNew: async () => {}
  })

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <div data-testid="harness-child" />
    </AssistantRuntimeProvider>
  )
}

it('mounts the runtime without a useSyncExternalStore loop', async () => {
  const logged: string[] = []
  const spy = vi.spyOn(console, 'error').mockImplementation((...args: unknown[]) => {
    logged.push(args.map(String).join(' '))
  })

  try {
    const { getByTestId } = render(<Harness />)
    await waitFor(() => expect(getByTestId('harness-child')).toBeTruthy())
    // Let the resource fibers run a few commit-time snapshot checks.
    await new Promise(resolve => setTimeout(resolve, 100))
  } finally {
    spy.mockRestore()
  }

  const loop = logged.filter(line => /Maximum update depth|should be cached/.test(line))
  expect(loop).toEqual([])
})

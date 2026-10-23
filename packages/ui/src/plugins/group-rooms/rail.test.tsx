/**
 * The group-rooms thread rail + composer against a mocked `@hermes/plugin-sdk`
 * host: render, thread selection, and the composer's send door — every send
 * must hit the plugin's own `plugin.group_rooms.*` RPC with the right method
 * and params.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { GroupRoomsRail } from './rail'

const { requestMock, notifyErrorMock } = vi.hoisted(() => ({
  requestMock: vi.fn(),
  notifyErrorMock: vi.fn()
}))

vi.mock('@hermes/plugin-sdk', () => ({
  host: { request: requestMock, notifyError: notifyErrorMock }
}))

const ROOMS = {
  rooms: [
    { id: 1, name: 'ops', mode: 'collab', goal: 'keep the lights on', topic: '' },
    { id: 2, name: 'port', mode: 'lead', goal: 'port the fork', topic: '' }
  ]
}

const OPS_STATE = {
  state: {
    room: { id: 1, name: 'ops', mode: 'collab', goal: 'keep the lights on', topic: '' },
    seats: [
      { bot: 'atlas', role: 'lead', status: 'working', status_note: 'answering' },
      { bot: 'vex', role: 'member', status: 'idle', status_note: '' }
    ],
    threads: [
      { id: 2, title: 'handoff plan', origin_message_id: null },
      { id: 3, title: 'follow-ups', origin_message_id: null }
    ],
    messages: [{ id: 1, sender: 'user', text: 'map the room model', thread_id: null }],
    handoffs: []
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  requestMock.mockImplementation(async (method: string) => {
    if (method === 'plugin.group_rooms.config') {
      return { thread_rail_enabled: true, default_room_mode: 'collab', max_seats: 6, max_message_chars: 8000 }
    }
    if (method === 'plugin.group_rooms.rooms.list') {
      return ROOMS
    }
    if (method === 'plugin.group_rooms.rooms.state') {
      return OPS_STATE
    }
    return {}
  })
})

describe('GroupRoomsRail', () => {
  it('renders rooms, threads and live seat status over the plugin RPC', async () => {
    render(<GroupRoomsRail />)

    await screen.findByText('handoff plan')
    expect(requestMock).toHaveBeenCalledWith('plugin.group_rooms.rooms.list', {})
    expect(requestMock).toHaveBeenCalledWith('plugin.group_rooms.rooms.state', { room: 'ops' })
    expect(screen.getByText('follow-ups')).toBeTruthy()
    expect(screen.getByText('atlas').textContent).toContain('atlas')
    expect(screen.getByTitle('answering').textContent).toContain('working')
  })

  it('selects a thread and sends through the composer with the right method and params', async () => {
    render(<GroupRoomsRail />)

    fireEvent.click(await screen.findByText('follow-ups'))
    expect(screen.getByRole('button', { name: 'follow-ups' }).getAttribute('aria-current')).toBe('true')

    fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'ship the rail' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => {
      expect(requestMock).toHaveBeenCalledWith('plugin.group_rooms.message.post', {
        room: 'ops',
        sender: 'user',
        text: 'ship the rail',
        thread_id: 3
      })
    })
    // The composer refreshes the room after posting.
    expect(requestMock).toHaveBeenCalledWith('plugin.group_rooms.rooms.state', { room: 'ops' })
    expect(screen.getByLabelText('Message')).toHaveProperty('value', '')
  })

  it('sends without a thread_id while the room (not a thread) is selected', async () => {
    render(<GroupRoomsRail />)

    fireEvent.change(await screen.findByLabelText('Message'), { target: { value: 'room-wide note' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => {
      expect(requestMock).toHaveBeenCalledWith('plugin.group_rooms.message.post', {
        room: 'ops',
        sender: 'user',
        text: 'room-wide note',
        thread_id: null
      })
    })
  })

  it('gates on the thread_rail_enabled config knob', async () => {
    requestMock.mockImplementation(async (method: string) =>
      method === 'plugin.group_rooms.config' ? { thread_rail_enabled: false } : ROOMS)

    render(<GroupRoomsRail />)

    expect(await screen.findByText(/thread rail is disabled/i)).toBeTruthy()
    expect(requestMock).not.toHaveBeenCalledWith('plugin.group_rooms.rooms.list', expect.anything())
  })
})

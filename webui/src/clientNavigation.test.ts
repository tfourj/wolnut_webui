import { describe, expect, it } from 'vitest'
import { clientNavigationReducer, INITIAL_CLIENT_NAVIGATION } from './clientNavigation'

describe('client configuration navigation', () => {
  it('selects a client while preserving the active configuration section', () => {
    const agentState = clientNavigationReducer(INITIAL_CLIENT_NAVIGATION, {
      type: 'select-section',
      section: 'agent',
    })
    const selected = clientNavigationReducer(agentState, {
      type: 'select-client',
      index: 2,
      clientCount: 3,
    })

    expect(selected).toEqual({ clientIndex: 2, section: 'agent' })
  })

  it('selects a newly added client and opens its main configuration', () => {
    const selected = clientNavigationReducer({ clientIndex: 1, section: 'agent' }, {
      type: 'add-client',
      previousCount: 3,
    })

    expect(selected).toEqual({ clientIndex: 3, section: 'main' })
  })

  it('preserves the agent section when configuration reloads', () => {
    const reloaded = clientNavigationReducer({ clientIndex: 1, section: 'agent' }, {
      type: 'sync-client-count',
      clientCount: 2,
    })

    expect(reloaded).toEqual({ clientIndex: 1, section: 'agent' })
  })

  it('keeps the same logical client selected when an earlier client is removed', () => {
    const selected = clientNavigationReducer({ clientIndex: 2, section: 'agent' }, {
      type: 'remove-client',
      removedIndex: 0,
      previousCount: 3,
    })

    expect(selected).toEqual({ clientIndex: 1, section: 'agent' })
  })

  it('selects the previous client when the final selected client is removed', () => {
    const selected = clientNavigationReducer({ clientIndex: 2, section: 'main' }, {
      type: 'remove-client',
      removedIndex: 2,
      previousCount: 3,
    })

    expect(selected).toEqual({ clientIndex: 1, section: 'main' })
  })

  it('clamps selection after configuration reloads or becomes empty', () => {
    const clamped = clientNavigationReducer({ clientIndex: 4, section: 'agent' }, {
      type: 'sync-client-count',
      clientCount: 2,
    })
    const empty = clientNavigationReducer(clamped, { type: 'sync-client-count', clientCount: 0 })

    expect(clamped).toEqual({ clientIndex: 1, section: 'agent' })
    expect(empty).toEqual({ clientIndex: 0, section: 'agent' })
  })
})

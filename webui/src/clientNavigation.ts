export type ClientConfigSection = 'main' | 'agent'

export interface ClientNavigationState {
  clientIndex: number
  section: ClientConfigSection
}

export type ClientNavigationAction =
  | { type: 'select-client'; index: number; clientCount: number }
  | { type: 'select-section'; section: ClientConfigSection }
  | { type: 'add-client'; previousCount: number }
  | { type: 'remove-client'; removedIndex: number; previousCount: number }
  | { type: 'sync-client-count'; clientCount: number }

export const INITIAL_CLIENT_NAVIGATION: ClientNavigationState = { clientIndex: 0, section: 'main' }

export function clampClientIndex(index: number, clientCount: number): number {
  if (clientCount <= 0) return 0
  return Math.min(Math.max(index, 0), clientCount - 1)
}

export function clientIndexAfterRemoval(selectedIndex: number, removedIndex: number, previousCount: number): number {
  const nextCount = Math.max(previousCount - 1, 0)
  if (removedIndex < selectedIndex) return clampClientIndex(selectedIndex - 1, nextCount)
  return clampClientIndex(selectedIndex, nextCount)
}

export function clientNavigationReducer(
  state: ClientNavigationState,
  action: ClientNavigationAction,
): ClientNavigationState {
  switch (action.type) {
    case 'select-client':
      return { ...state, clientIndex: clampClientIndex(action.index, action.clientCount) }
    case 'select-section':
      return { ...state, section: action.section }
    case 'add-client':
      return { clientIndex: action.previousCount, section: 'main' }
    case 'remove-client':
      return {
        ...state,
        clientIndex: clientIndexAfterRemoval(state.clientIndex, action.removedIndex, action.previousCount),
      }
    case 'sync-client-count':
      return { ...state, clientIndex: clampClientIndex(state.clientIndex, action.clientCount) }
  }
}

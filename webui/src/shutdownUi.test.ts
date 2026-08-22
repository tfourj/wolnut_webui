import { describe, expect, it } from 'vitest'

import {
  isCertificateFingerprintValid,
  isEnrollmentTerminal,
  isShutdownConfirmationValid,
  normalizeShutdownClient,
} from './shutdownUi'

describe('secure shutdown UI guards', () => {
  it('accepts colon-delimited SHA-256 fingerprints only', () => {
    expect(isCertificateFingerprintValid(Array(32).fill('AA').join(':'))).toBe(true)
    expect(isCertificateFingerprintValid('AA:BB')).toBe(false)
    expect(isCertificateFingerprintValid(`${'A'.repeat(63)}Z`)).toBe(false)
  })

  it('requires an exact, case-sensitive device name', () => {
    expect(isShutdownConfirmationValid('server-1', 'server-1')).toBe(true)
    expect(isShutdownConfirmationValid('server-1', 'Server-1')).toBe(false)
    expect(isShutdownConfirmationValid('', '')).toBe(false)
  })

  it('adds safe shutdown defaults to legacy clients', () => {
    const client = normalizeShutdownClient({
      name: 'server',
      host: 'server.local',
      mac: 'auto',
      shutdown: undefined as never,
    })

    expect(client.wake_enabled).toBe(true)
    expect(client.shutdown).toEqual({
      enabled: false,
      battery_percent: 20,
      agent_id: null,
      agent_port: 8184,
    })
  })

  it('keeps polling only while one-line enrollment can still progress', () => {
    expect(isEnrollmentTerminal('pending')).toBe(false)
    expect(isEnrollmentTerminal('processing')).toBe(false)
    expect(isEnrollmentTerminal('paired')).toBe(true)
    expect(isEnrollmentTerminal('failed')).toBe(true)
    expect(isEnrollmentTerminal('expired')).toBe(true)
  })
})

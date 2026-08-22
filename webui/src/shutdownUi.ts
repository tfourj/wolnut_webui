import type { AgentEnrollmentStatus, ClientConfig } from './api'

export function isCertificateFingerprintValid(value: string): boolean {
  const normalized = value.replace(/[:\s-]/g, '')
  return /^[a-fA-F0-9]{64}$/.test(normalized)
}

export function isShutdownConfirmationValid(clientName: string, confirmation: string): boolean {
  return clientName.length > 0 && confirmation === clientName
}

export function normalizeShutdownClient(client: ClientConfig): ClientConfig {
  return {
    ...client,
    wake_enabled: client.wake_enabled ?? true,
    shutdown: {
      enabled: false,
      battery_percent: 20,
      agent_id: null,
      agent_port: 8184,
      ...(client.shutdown || {}),
    },
  }
}

export function isEnrollmentTerminal(status: AgentEnrollmentStatus['status']): boolean {
  return ['paired', 'failed', 'expired', 'superseded'].includes(status)
}

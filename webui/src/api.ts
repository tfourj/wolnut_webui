export interface NutConfig {
  ups: string
  username?: string
  password?: string
  port?: number
  timeout?: number
}

export interface WakeOnConfig {
  restore_delay_sec: number
  min_battery_percent: number
  client_timeout_sec: number
  reattempt_delay: number
}

export interface ClientConfig {
  name: string
  host: string
  mac: string
  always_wake?: boolean
  enabled?: boolean
  wake_enabled?: boolean
  shutdown: ShutdownConfig
}

export interface ShutdownConfig {
  enabled: boolean
  battery_percent: number
  agent_id?: string | null
  agent_port: number
  auto_update: boolean
}

export interface WebUIConfig {
  suppress_mac_warnings: boolean
}

export interface DiscordNotificationConfig {
  enabled: boolean
  webhook_url: string
}

export interface GotifyNotificationConfig {
  enabled: boolean
  url: string
  token: string
  priority: number
}

export interface NtfyNotificationConfig {
  enabled: boolean
  url: string
  topic: string
  token: string
  priority: number
}

export interface NotificationEventsConfig {
  power_loss: boolean
  power_restored: boolean
  wake_sent: boolean
  client_recovered: boolean
  errors: boolean
  shutdown_acknowledged: boolean
  shutdown_failed: boolean
}

export interface NotificationsConfig {
  discord: DiscordNotificationConfig
  gotify: GotifyNotificationConfig
  ntfy: NtfyNotificationConfig
  events: NotificationEventsConfig
}

export type NotificationProvider = 'discord' | 'gotify' | 'ntfy'

export interface WolnutConfig {
  log_level: string
  poll_interval: number
  status_file: string
  nut: NutConfig
  wake_on: WakeOnConfig
  clients: ClientConfig[]
  webui: WebUIConfig
  notifications: NotificationsConfig
}

const TOKEN_KEY = 'wolnut_token'
export function getToken(): string | null {
  try { return localStorage.getItem(TOKEN_KEY) } catch { return null }
}
export function setToken(t: string) { try { localStorage.setItem(TOKEN_KEY, t) } catch {} }
export function clearToken() { try { localStorage.removeItem(TOKEN_KEY) } catch {} }
function authHeaders(): Record<string, string> {
  const t = getToken()
  return t ? { Authorization: `Bearer ${t}` } : {}
}
async function authFetch(url: string, opts: RequestInit = {}) {
  const headers = { ...(opts.headers as Record<string, string> || {}), ...authHeaders() }
  return fetch(url, { ...opts, headers })
}

export async function getAuthStatus(): Promise<{
  auth_enabled: boolean
  user?: string | null
  shutdown_admin_configured: boolean
  secure_transport: boolean
}> {
  const res = await fetch('/api/auth/status')
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}
export async function login(username: string, password: string) {
  const res = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  if (!res.ok) throw new Error(await res.text())
  const j = await res.json()
  if (j.access_token) setToken(j.access_token)
  return j as { access_token: string; token_type: string }
}
export async function getMe() {
  const res = await authFetch('/api/auth/me')
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}
export async function logout() {
  try { await authFetch('/api/auth/logout', { method: 'POST' }) } catch {}
  clearToken()
}

export async function fetchConfig(): Promise<WolnutConfig> {
  const res = await authFetch('/api/config')
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function saveConfig(cfg: WolnutConfig) {
  const res = await authFetch('/api/config', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(cfg),
  })
  if (!res.ok) {
    const txt = await res.text()
    throw new Error(txt)
  }
  return res.json()
}

export async function fetchStatus() {
  const res = await authFetch('/api/status')
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function sendWol(mac: string) {
  const res = await authFetch('/api/wol', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mac }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function sendWolClient(name: string) {
  const res = await authFetch(`/api/wol/client/${encodeURIComponent(name)}`, { method: 'POST' })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function resolveMac(host: string) {
  const res = await authFetch('/api/resolve-mac', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ host }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json() as Promise<{ host: string; mac: string }>
}

export async function pingHost(host: string) {
  const res = await authFetch('/api/ping', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ host }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json() as Promise<{ host: string; online: boolean }>
}

export async function testNotification(
  provider: NotificationProvider,
  notifications: NotificationsConfig,
) {
  const res = await authFetch('/api/notifications/test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider, notifications }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail || `Failed to test ${provider}`)
  }
  return res.json() as Promise<{ status: string; provider: string }>
}

async function agentRequest(path: string, payload?: unknown) {
  const res = await authFetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload ?? {}),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail || 'Agent request failed')
  }
  return res.json()
}

export function pairAgent(
  clientName: string,
  agentPort: number,
  pairingCode: string,
  fingerprint: string,
) {
  return agentRequest('/api/agents/pair', {
    client_name: clientName,
    agent_port: agentPort,
    pairing_code: pairingCode,
    fingerprint,
  })
}

export interface AgentEnrollment {
  enrollment_id: string
  expires_at: number
  install_command: string
}

export interface AgentEnrollmentStatus {
  client_name: string
  agent_port: number
  expires_at: number
  status: 'pending' | 'processing' | 'paired' | 'failed' | 'expired' | 'superseded'
  agent_id?: string | null
  last_error?: string | null
}

export function createAgentEnrollment(clientName: string, agentPort: number) {
  return agentRequest('/api/agents/enrollments', {
    client_name: clientName,
    agent_port: agentPort,
  }) as Promise<AgentEnrollment>
}

export interface ManualAgentInstallCommands {
  install_command: string
  pairing_command: string
  uninstall_command: string
}

export function createManualAgentInstall(clientName: string, agentPort: number) {
  return agentRequest('/api/agents/manual-install', {
    client_name: clientName,
    agent_port: agentPort,
  }) as Promise<ManualAgentInstallCommands>
}

export async function getAgentEnrollment(enrollmentId: string) {
  const res = await authFetch(`/api/agents/enrollments/${encodeURIComponent(enrollmentId)}`)
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail || 'Could not read enrollment status')
  }
  return res.json() as Promise<AgentEnrollmentStatus>
}

export function testAgent(clientName: string) {
  return agentRequest(`/api/agents/${encodeURIComponent(clientName)}/test`)
}

export function requestAgentUpdate(clientName: string) {
  return agentRequest(`/api/agents/${encodeURIComponent(clientName)}/update`, {})
}

export function setAgentAutoUpdate(clientName: string, enabled: boolean) {
  return agentRequest(`/api/agents/${encodeURIComponent(clientName)}/auto-update`, { enabled })
}

export function shutdownAgent(clientName: string, confirmation: string) {
  return agentRequest(`/api/agents/${encodeURIComponent(clientName)}/shutdown`, { confirmation })
}

export function unpairAgent(clientName: string, confirmation: string, forceLocal = false) {
  return agentRequest(`/api/agents/${encodeURIComponent(clientName)}/unpair`, {
    confirmation,
    force_local: forceLocal,
  })
}

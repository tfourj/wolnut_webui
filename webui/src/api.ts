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
}

export interface WolnutConfig {
  log_level: string
  poll_interval: number
  status_file: string
  nut: NutConfig
  wake_on: WakeOnConfig
  clients: ClientConfig[]
}

export async function fetchConfig(): Promise<WolnutConfig> {
  const res = await fetch('/api/config')
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function saveConfig(cfg: WolnutConfig) {
  const res = await fetch('/api/config', {
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
  const res = await fetch('/api/status')
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function sendWol(mac: string) {
  const res = await fetch('/api/wol', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mac }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function sendWolClient(name: string) {
  const res = await fetch(`/api/wol/client/${encodeURIComponent(name)}`, { method: 'POST' })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function resolveMac(host: string) {
  const res = await fetch('/api/resolve-mac', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ host }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json() as Promise<{ host: string; mac: string }>
}

export async function pingHost(host: string) {
  const res = await fetch('/api/ping', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ host }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json() as Promise<{ host: string; online: boolean }>
}

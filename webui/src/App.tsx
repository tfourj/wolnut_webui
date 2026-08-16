import { useEffect, useState } from 'react'
import { fetchConfig, saveConfig, fetchStatus, sendWolClient, resolveMac, pingHost, WolnutConfig, getAuthStatus, login as apiLogin, getMe, clearToken, getToken } from './api'

type Tab = 'dashboard' | 'config' | 'clients'

const DEFAULT_CFG: WolnutConfig = {
  log_level: 'INFO',
  poll_interval: 15,
  status_file: '/config/wolnut_state.json',
  nut: { ups: 'ups@localhost', username: '', password: '' },
  wake_on: { restore_delay_sec: 30, min_battery_percent: 25, client_timeout_sec: 600, reattempt_delay: 30 },
  clients: [],
}

export default function App() {
  const [tab, setTab] = useState<Tab>('dashboard')
  const [cfg, setCfg] = useState<WolnutConfig | null>(null)
  const [originalCfg, setOriginalCfg] = useState<WolnutConfig | null>(null)
  const [status, setStatus] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [authEnabled, setAuthEnabled] = useState<boolean | null>(null)
  const [authUser, setAuthUser] = useState<string | null>(null)
  const [authChecking, setAuthChecking] = useState(true)

  const showToast = (msg: string) => {
    setToast(msg)
    setTimeout(() => setToast(null), 3000)
  }

  const isDirty = !!cfg && !!originalCfg && JSON.stringify(cfg) !== JSON.stringify(originalCfg)

  const checkAuth = async () => {
    try {
      const st = await getAuthStatus()
      setAuthEnabled(st.auth_enabled)
      if (!st.auth_enabled) {
        setAuthUser(null)
        setAuthChecking(false)
        return true
      }
      const token = getToken()
      if (!token) {
        setAuthChecking(false)
        return false
      }
      try {
        const me = await getMe()
        setAuthUser(me.user)
        setAuthChecking(false)
        return true
      } catch {
        clearToken()
        setAuthUser(null)
        setAuthChecking(false)
        return false
      }
    } catch {
      setAuthEnabled(false)
      setAuthChecking(false)
      return true
    }
  }

  const load = async () => {
    setLoading(true)
    setErr(null)
    try {
      const c = await fetchConfig()
      setCfg(c)
      setOriginalCfg(JSON.parse(JSON.stringify(c)))
    } catch (e: any) {
      const msg = String(e.message || e)
      if (msg.includes('401') || msg.toLowerCase().includes('not authenticated') || msg.toLowerCase().includes('invalid token')) {
        clearToken()
        setAuthUser(null)
      }
      setErr(msg)
    } finally {
      setLoading(false)
    }
    try {
      const s = await fetchStatus()
      setStatus(s)
    } catch (e: any) {
      const msg = String(e.message || e)
      if (msg.includes('401') || msg.toLowerCase().includes('not authenticated')) {
        clearToken()
        setAuthUser(null)
      }
    }
  }

  useEffect(() => {
    checkAuth().then(ok => {
      if (ok) load()
      else setLoading(false)
    })
  }, [])

  useEffect(() => {
    if (authChecking) return
    if (authEnabled && !authUser) return
    const id = setInterval(async () => {
      try {
        const s = await fetchStatus()
        setStatus(s)
      } catch {}
    }, 5000)
    return () => clearInterval(id)
  }, [authChecking, authEnabled, authUser])

  // re-check after login
  const handleLoggedIn = async (user: string) => {
    setAuthUser(user)
    setLoading(true)
    await load()
  }

  const handleLogout = async () => {
    clearToken()
    setAuthUser(null)
    setCfg(null)
    setStatus(null)
    setAuthEnabled(null)
    setAuthChecking(true)
    await checkAuth()
  }

  const onSave = async () => {
    if (!cfg) return
    setSaving(true)
    setErr(null)
    try {
      await saveConfig(cfg)
      setOriginalCfg(JSON.parse(JSON.stringify(cfg)))
      showToast('Configuration saved — restart container to apply')
    } catch (e: any) {
      setErr(String(e.message || e))
    } finally {
      setSaving(false)
    }
  }

  if (authChecking) {
    return (
      <div className="container">
        <p style={{ color: '#9aa0ae' }}>Checking authentication...</p>
      </div>
    )
  }
  if (authEnabled && !authUser) {
    return (
      <div className="container">
        <div className="header">
          <h1>🥜 Wolnut<span>UPS Wake-on-LAN</span></h1>
        </div>
        <Login onLoggedIn={handleLoggedIn} />
      </div>
    )
  }

  if (loading) {
    return (
      <div className="container">
        <p style={{ color: '#9aa0ae' }}>Loading Wolnut...</p>
      </div>
    )
  }

  return (
    <div className="container">
      <div className="header">
        <h1>
          🥜 Wolnut
          <span>UPS Wake-on-LAN</span>
        </h1>
        <div className="header-actions">
          {authEnabled && authUser && <span style={{ color: '#9aa0ae', fontSize: 13, marginRight: 4 }}>{authUser}</span>}
          {authEnabled && authUser && <button className="btn btn-ghost" onClick={handleLogout}>Logout</button>}
          <button className="btn btn-ghost" onClick={load}>Refresh</button>
          {isDirty && (
            <button className="btn btn-primary" onClick={onSave} disabled={saving}>
              {saving ? 'Saving...' : 'Save config'}
            </button>
          )}
        </div>
      </div>

      {err && (
        <div className="card" style={{ borderColor: '#4d1f1f', background: '#1e1313' }}>
          <strong style={{ color: '#e74c3c' }}>Error</strong>
          <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, color: '#e6e8ec' }}>{err}</pre>
        </div>
      )}

      <div className="tabs">
        <button className={`tab ${tab === 'dashboard' ? 'active' : ''}`} onClick={() => setTab('dashboard')}>Dashboard</button>
        <button className={`tab ${tab === 'config' ? 'active' : ''}`} onClick={() => setTab('config')}>Configuration</button>
        <button className={`tab ${tab === 'clients' ? 'active' : ''}`} onClick={() => setTab('clients')}>
          Clients ({cfg?.clients.length ?? 0})
        </button>
      </div>

      {tab === 'dashboard' && <Dashboard cfg={cfg} status={status} showToast={showToast} />}
      {tab === 'config' && cfg && <ConfigForm cfg={cfg} setCfg={setCfg} />}
      {tab === 'clients' && cfg && <ClientsTab cfg={cfg} setCfg={setCfg} status={status} showToast={showToast} />}

      {toast && <div className="toast">{toast}</div>}

      <p style={{ color: '#9aa0ae', fontSize: 12, marginTop: 24, textAlign: 'center' }}>
        Config file: <code>{status?.config_path || '/config/config.yaml'}</code> · Status file: <code>{status?.status_path || cfg?.status_file}</code>
      </p>
    </div>
  )
}

function Login({ onLoggedIn }: { onLoggedIn: (user: string) => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setErr(null)
    setBusy(true)
    try {
      const j = await apiLogin(username, password)
      onLoggedIn(username)
    } catch (e: any) {
      setErr(String(e.message || e).replace('{"detail":"', '').replace('"}', ''))
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="card" style={{ maxWidth: 420, margin: '40px auto' }}>
      <h2>Sign in</h2>
      <p className="desc">Enter ADMIN_USERNAME / ADMIN_PASSWORD to access Wolnut</p>
      <form onSubmit={submit}>
        <div className="field">
          <label>Username</label>
          <input value={username} onChange={e => setUsername(e.target.value)} placeholder="admin" autoFocus />
        </div>
        <div className="field">
          <label>Password</label>
          <input type="password" value={password} onChange={e => setPassword(e.target.value)} placeholder="••••••••" />
        </div>
        {err && <div style={{ color: '#e74c3c', fontSize: 13, marginBottom: 10, whiteSpace: 'pre-wrap' }}>{err}</div>}
        <button className="btn btn-primary" type="submit" disabled={busy} style={{ width: '100%' }}>
          {busy ? 'Signing in...' : 'Sign in'}
        </button>
      </form>
    </div>
  )
}

function Dashboard({ cfg, status, showToast }: { cfg: WolnutConfig | null; status: any; showToast: (m: string) => void }) {
  const ups = status?.ups || {}
  const upsError: string | null = status?.ups_error || null
  const upscAvailable: boolean | null = status?.upsc_available ?? null
  const state = status?.state || {}
  const battery = ups['battery.charge'] ?? '—'
  const power = ups['ups.status'] ?? 'Unknown'
  const isOnline = String(power).includes('OL')
  const isOnBattery = String(power).includes('OB')
  const isUpscMissing = upscAvailable === false || (upsError && upsError.includes("'upsc'"))

  return (
    <div className="status-grid">
      <div className="card">
        <h2>UPS Status</h2>
        <p className="desc">
          {cfg?.nut.ups || 'ups@localhost'} · poll every {cfg?.poll_interval ?? 15}s
        </p>

        <div style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
          <span className={`badge ${isOnline ? 'online' : isOnBattery ? 'offline' : ''}`}>
            <span className="badge-dot" style={{ background: isOnline ? '#2ecc71' : isOnBattery ? '#e74c3c' : '#9aa0ae' }} />
            {power}
          </span>
          <span className="badge">🔋 {battery}%</span>
        </div>

        {isUpscMissing && (
          <div
            style={{
              background: '#2a1515',
              border: '1px solid #4d1f1f',
              borderRadius: 8,
              padding: '12px 14px',
              marginBottom: 12,
              fontSize: 13,
              lineHeight: 1.5,
            }}
          >
            <strong style={{ color: '#ff6b6b', display: 'block', marginBottom: 4 }}>
              ⚠ upsc not found
            </strong>
            <span style={{ color: '#e6e8ec' }}>
              Failed to get UPS status: [Errno 2] No such file or directory: 'upsc' is not installed.
            </span>
            <pre
              style={{
                background: '#0f1115',
                border: '1px solid #2a2e3a',
                borderRadius: 6,
                padding: 8,
                fontSize: 11,
                marginTop: 8,
                whiteSpace: 'pre-wrap',
                color: '#9aa0ae',
              }}
            >
              {upsError || "upsc binary missing — install nut-client (apt install nut-client)."}
            </pre>
            <span style={{ color: '#9aa0ae', fontSize: 12 }}>
              <code>nut-client</code> needs to be installed to query UPS status.
            </span>
          </div>
        )}

        {!isUpscMissing && upsError && (
          <div
            style={{
              background: '#2a2015',
              border: '1px solid #4d3a1f',
              borderRadius: 8,
              padding: '12px 14px',
              marginBottom: 12,
              fontSize: 13,
            }}
          >
            <strong style={{ color: '#f1c40f', display: 'block', marginBottom: 4 }}>UPS error</strong>
            <span style={{ color: '#e6e8ec' }}>{upsError}</span>
          </div>
        )}

        {Object.keys(ups).length === 0 ? (
          !isUpscMissing && !upsError ? <p className="inline-help">No UPS data — check NUT connection / ups name.</p> : null
        ) : (
          <div>
            <div className="kv"><span>UPS name</span><span>{cfg?.nut.ups}</span></div>
            <div className="kv"><span>Battery</span><span>{battery}%</span></div>
            <div className="kv"><span>Status</span><span>{power}</span></div>
            {ups['ups.model'] && <div className="kv"><span>Model</span><span>{ups['ups.model']}</span></div>}
            {ups['battery.runtime'] && <div className="kv"><span>Runtime</span><span>{ups['battery.runtime']} sec</span></div>}
            {ups['input.voltage'] && <div className="kv"><span>Input V</span><span>{ups['input.voltage']}</span></div>}
          </div>
        )}

        <details style={{ marginTop: 12 }}>
          <summary style={{ cursor: 'pointer', color: '#9aa0ae', fontSize: 13 }}>Raw UPS vars</summary>
          <pre
            style={{
              background: '#0f1115',
              border: '1px solid #2a2e3a',
              borderRadius: 8,
              padding: 12,
              fontSize: 11,
              overflowX: 'auto',
              maxHeight: 220,
            }}
          >
            {JSON.stringify(ups, null, 2)}
          </pre>
          {upsError && (
            <pre
              style={{
                background: '#1a1212',
                border: '1px solid #4d1f1f',
                borderRadius: 8,
                padding: 12,
                fontSize: 11,
                overflowX: 'auto',
                marginTop: 8,
                color: '#ff9999',
              }}
            >
              Error: {upsError}
            </pre>
          )}
        </details>
      </div>

      <div className="card">
        <h2>System State</h2>
        <p className="desc">Persisted recovery state</p>
        {Object.keys(state).length === 0 ? (
          <p className="inline-help">No state file yet — will be created on first battery event.</p>
        ) : (
          <pre
            style={{
              background: '#0f1115',
              border: '1px solid #2a2e3a',
              borderRadius: 8,
              padding: 12,
              fontSize: 11,
              overflowX: 'auto',
              maxHeight: 340,
            }}
          >
            {JSON.stringify(state, null, 2)}
          </pre>
        )}
      </div>

      <div className="card" style={{ gridColumn: '1 / -1' }}>
        <h2>Clients overview</h2>
        <p className="desc">Live ping status (refreshes every 5s)</p>
        {!status?.clients || status.clients.length === 0 ? (
          <div className="empty">No clients configured — add them in Clients tab.</div>
        ) : (
          <div>
            {status.clients.map((c: any) => (
              <div key={c.name} className="client-row">
                <div className="client-main">
                  <strong>{c.name}</strong>
                  <span>{c.host} · {c.mac}</span>
                </div>
                <span className={`badge ${c.online ? 'online' : 'offline'}`}>
                  <span className="badge-dot" style={{ background: c.online ? '#2ecc71' : '#e74c3c' }} />
                  {c.online ? 'Online' : 'Offline'}
                </span>
                <button
                  className="btn btn-ghost btn-small"
                  onClick={async () => {
                    try {
                      await sendWolClient(c.name)
                      showToast(`WOL sent to ${c.name}`)
                    } catch (e: any) {
                      showToast(`Failed: ${String(e.message || e)}`)
                    }
                  }}
                >
                  Wake
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function ConfigForm({ cfg, setCfg }: { cfg: WolnutConfig; setCfg: (c: WolnutConfig) => void }) {
  const set = (patch: Partial<WolnutConfig>) => setCfg({ ...cfg, ...patch })
  const setNut = (patch: Partial<WolnutConfig['nut']>) => setCfg({ ...cfg, nut: { ...cfg.nut, ...patch } })
  const setWake = (patch: Partial<WolnutConfig['wake_on']>) => setCfg({ ...cfg, wake_on: { ...cfg.wake_on, ...patch } })

  return (
    <>
      <div className="card">
        <h2>NUT Server</h2>
        <p className="desc">Network UPS Tools connection (upsc)</p>
        <div className="grid2">
          <div className="field">
            <label>UPS name</label>
            <input
              value={cfg.nut.ups}
              onChange={e => setNut({ ups: e.target.value })}
              placeholder="ups@localhost"
            />
            <span className="inline-help">Format: &lt;ups-name&gt;@&lt;host&gt;</span>
          </div>
          <div className="field">
            <label>Log level</label>
            <select value={cfg.log_level} onChange={e => set({ log_level: e.target.value })}>
              <option>DEBUG</option>
              <option>INFO</option>
              <option>WARNING</option>
              <option>ERROR</option>
              <option>CRITICAL</option>
            </select>
          </div>
          <div className="field">
            <label>Username (optional)</label>
            <input value={cfg.nut.username || ''} onChange={e => setNut({ username: e.target.value })} placeholder="upsmon" />
          </div>
          <div className="field">
            <label>Password (optional)</label>
            <input
              type="password"
              value={cfg.nut.password || ''}
              onChange={e => setNut({ password: e.target.value })}
              placeholder="••••••••"
            />
          </div>
        </div>
      </div>

      <div className="card">
        <h2>Global</h2>
        <p className="desc">Polling and file locations</p>
        <div className="grid2">
          <div className="field">
            <label>Poll interval (seconds)</label>
            <input
              type="number"
              value={cfg.poll_interval}
              onChange={e => set({ poll_interval: parseInt(e.target.value) || 0 })}
              min={1}
            />
            <span className="inline-help">Should be shorter than NUT shutdown delay</span>
          </div>
          <div className="field">
            <label>Status file</label>
            <input value={cfg.status_file} onChange={e => set({ status_file: e.target.value })} />
            <span className="inline-help">Path inside container, e.g. /config/wolnut_state.json</span>
          </div>
        </div>
      </div>

      <div className="card">
        <h2>Wake-on-LAN behavior</h2>
        <p className="desc">Timing after power restoration</p>
        <div className="grid2">
          <div className="field">
            <label>Restore delay (sec)</label>
            <input
              type="number"
              value={cfg.wake_on.restore_delay_sec}
              onChange={e => setWake({ restore_delay_sec: parseInt(e.target.value) || 0 })}
            />
            <span className="inline-help">Wait after OL before sending WOL</span>
          </div>
          <div className="field">
            <label>Min battery %</label>
            <input
              type="number"
              value={cfg.wake_on.min_battery_percent}
              onChange={e => setWake({ min_battery_percent: parseInt(e.target.value) || 0 })}
              min={0}
              max={100}
            />
          </div>
          <div className="field">
            <label>Client timeout (sec)</label>
            <input
              type="number"
              value={cfg.wake_on.client_timeout_sec}
              onChange={e => setWake({ client_timeout_sec: parseInt(e.target.value) || 0 })}
            />
          </div>
          <div className="field">
            <label>Reattempt delay (sec)</label>
            <input
              type="number"
              value={cfg.wake_on.reattempt_delay}
              onChange={e => setWake({ reattempt_delay: parseInt(e.target.value) || 0 })}
            />
          </div>
        </div>
      </div>
    </>
  )
}

function ClientsTab({
  cfg,
  setCfg,
  status,
  showToast,
}: {
  cfg: WolnutConfig
  setCfg: (c: WolnutConfig) => void
  status: any
  showToast: (m: string) => void
}) {
  const updateClient = (idx: number, patch: Partial<WolnutConfig['clients'][number]>) => {
    const next = [...cfg.clients]
    next[idx] = { ...next[idx], ...patch }
    setCfg({ ...cfg, clients: next })
  }
  const removeClient = (idx: number) => {
    setCfg({ ...cfg, clients: cfg.clients.filter((_, i) => i !== idx) })
  }
  const addClient = () => {
    setCfg({
      ...cfg,
      clients: [...cfg.clients, { name: `client ${cfg.clients.length + 1}`, host: '192.168.0.100', mac: 'auto' }],
    })
  }

  const liveMap = new Map<string, boolean>()
  for (const c of status?.clients || []) liveMap.set(c.name, c.online)

  return (
    <div className="card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <div>
          <h2 style={{ margin: 0 }}>Clients</h2>
          <p className="desc" style={{ margin: 0 }}>Machines to wake after outage. MAC can be "auto" to resolve via ARP.</p>
        </div>
        <button className="btn btn-primary" onClick={addClient}>+ Add client</button>
      </div>

      {cfg.clients.length === 0 && <div className="empty">No clients yet.</div>}

      {cfg.clients.map((c, idx) => {
        const online = liveMap.get(c.name)
        return (
          <div key={idx} className="card" style={{ background: '#0f1115', padding: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
              <strong>#{idx + 1} — {c.name || 'Unnamed'}</strong>
              <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                {online !== undefined && (
                  <span className={`badge ${online ? 'online' : 'offline'}`} style={{ fontSize: 11 }}>
                    {online ? 'Online' : 'Offline'}
                  </span>
                )}
                <button className="btn btn-danger btn-small" onClick={() => removeClient(idx)}>Remove</button>
              </div>
            </div>

            <div className="grid2">
              <div className="field">
                <label>Name</label>
                <input value={c.name} onChange={e => updateClient(idx, { name: e.target.value })} placeholder="client 1" />
              </div>
              <div className="field">
                <label>Host (IP or hostname)</label>
                <input value={c.host} onChange={e => updateClient(idx, { host: e.target.value })} placeholder="192.168.0.100" />
              </div>
              <div className="field">
                <label>MAC address</label>
                <input value={c.mac} onChange={e => updateClient(idx, { mac: e.target.value })} placeholder="38:f7:cd:c5:87:6b or auto" />
                <span className="inline-help">Use "auto" to resolve via ARP at runtime</span>
              </div>
              <div className="field">
                <label>Actions</label>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  <button
                    className="btn btn-ghost btn-small"
                    onClick={async () => {
                      try {
                        const j = await pingHost(c.host)
                        showToast(j.online ? `${c.host} is online` : `${c.host} is offline`)
                      } catch (e: any) {
                        showToast(String(e.message || e))
                      }
                    }}
                  >
                    Ping
                  </button>
                  <button
                    className="btn btn-ghost btn-small"
                    onClick={async () => {
                      try {
                        const j = await resolveMac(c.host)
                        updateClient(idx, { mac: j.mac })
                        showToast(`Resolved ${j.mac}`)
                      } catch (e: any) {
                        showToast(String(e.message || e))
                      }
                    }}
                  >
                    Resolve MAC
                  </button>
                  <button
                    className="btn btn-ghost btn-small"
                    onClick={async () => {
                      try {
                        await sendWolClient(c.name)
                        showToast(`WOL sent to ${c.name}`)
                      } catch (e: any) {
                        // fallback to direct MAC
                        try {
                          const { sendWol } = await import('./api')
                          await sendWol(c.mac)
                          showToast(`WOL sent to ${c.mac}`)
                        } catch (e2: any) {
                          showToast(String(e2.message || e.message || e2))
                        }
                      }
                    }}
                  >
                    Wake now
                  </button>
                </div>
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}

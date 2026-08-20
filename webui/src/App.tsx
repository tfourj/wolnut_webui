import { useEffect, useState } from 'react'
import {
  clearToken,
  fetchConfig,
  fetchStatus,
  getAuthStatus,
  getMe,
  getToken,
  login as apiLogin,
  pingHost,
  resolveMac,
  saveConfig,
  sendWol,
  sendWolClient,
  testNotification,
  NotificationProvider,
  WolnutConfig,
} from './api'

type Tab = 'dashboard' | 'config' | 'clients' | 'notifications'

const DEFAULT_CFG: WolnutConfig = {
  log_level: 'INFO',
  poll_interval: 15,
  status_file: '/config/wolnut_state.json',
  nut: { ups: 'ups@localhost', username: '', password: '' },
  wake_on: { restore_delay_sec: 30, min_battery_percent: 25, client_timeout_sec: 600, reattempt_delay: 30 },
  clients: [],
  webui: { suppress_mac_warnings: false },
  notifications: {
    discord: { enabled: false, webhook_url: '' },
    gotify: { enabled: false, url: '', token: '', priority: 5 },
    ntfy: { enabled: false, url: 'https://ntfy.sh', topic: '', token: '', priority: 3 },
    events: {
      power_loss: true,
      power_restored: true,
      wake_sent: true,
      client_recovered: true,
      errors: true,
    },
  },
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
  const [saveWarnings, setSaveWarnings] = useState<any[]>([])

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
      if (!c.webui) c.webui = { suppress_mac_warnings: false }
      if (!c.notifications) {
        c.notifications = JSON.parse(JSON.stringify(DEFAULT_CFG.notifications))
      } else if (!c.notifications.ntfy) {
        c.notifications.ntfy = { ...DEFAULT_CFG.notifications.ntfy }
      }
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
    setSaveWarnings([])
    try {
      const res: any = await saveConfig(cfg)
      setOriginalCfg(JSON.parse(JSON.stringify(cfg)))
      if (res.warnings && res.warnings.length > 0) {
        setSaveWarnings(res.warnings)
        showToast(`Configuration saved — ${res.warnings.length} MAC warning(s)`)
      } else {
        showToast('Configuration saved — applied dynamically')
      }
      // auto-switch to clients tab if warnings
      if (res.warnings && res.warnings.length > 0) setTab('clients')
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
        <button
          className={`tab ${tab === 'notifications' ? 'active' : ''}`}
          onClick={() => setTab('notifications')}
        >
          Notifications
        </button>
      </div>

      {tab === 'dashboard' && <Dashboard cfg={cfg} status={status} showToast={showToast} />}
      {tab === 'config' && cfg && <ConfigForm cfg={cfg} setCfg={setCfg} />}
      {tab === 'clients' && cfg && <ClientsTab cfg={cfg} setCfg={setCfg} status={status} showToast={showToast} warnings={saveWarnings} />}
      {tab === 'notifications' && cfg && (
        <NotificationsTab cfg={cfg} setCfg={setCfg} showToast={showToast} />
      )}

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
  const isUpscMissing = upscAvailable === false

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
              <div key={c.name} className="client-row" style={{ opacity: c.enabled === false ? 0.6 : 1 }}>
                <div className="client-main">
                  <strong>{c.name}</strong>
                  <span>{c.host} · {c.mac}</span>
                  <div style={{ display: 'flex', gap: 6, marginTop: 4, flexWrap: 'wrap' }}>
                    {c.enabled === false && (
                      <span style={{ background: '#2a2e3a', color: '#9aa0ae', padding: '2px 8px', borderRadius: 999, fontSize: 11 }}>Disabled</span>
                    )}
                    {c.always_wake && (
                      <span style={{ background: '#2a2015', border: '1px solid #f1c40f', color: '#f1c40f', padding: '2px 8px', borderRadius: 999, fontSize: 11 }}>⚠ Always</span>
                    )}
                  </div>
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
  const setWebUI = (patch: Partial<WolnutConfig['webui']>) => setCfg({ ...cfg, webui: { ...(cfg.webui || { suppress_mac_warnings: false }), ...patch } })

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

      <div className="card">
        <h2>WebUI Settings</h2>
        <p className="desc">Frontend display preferences</p>
        <div className="field" style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
          <input
            type="checkbox"
            checked={!!cfg.webui?.suppress_mac_warnings}
            onChange={e => setWebUI({ suppress_mac_warnings: e.target.checked })}
            style={{ width: 18, height: 18 }}
            id="suppress_mac_warnings"
          />
          <label htmlFor="suppress_mac_warnings" style={{ textTransform: 'none', letterSpacing: 0, fontWeight: 500, color: '#e6e8ec', fontSize: 14 }}>
            Suppress MAC warnings
          </label>
        </div>
        <span className="inline-help">Hide orange warnings when MAC "auto" cannot be resolved on save</span>
      </div>
    </>
  )
}

function NotificationsTab({
  cfg,
  setCfg,
  showToast,
}: {
  cfg: WolnutConfig
  setCfg: (c: WolnutConfig) => void
  showToast: (message: string) => void
}) {
  const [testing, setTesting] = useState<NotificationProvider | null>(null)
  const notifications = cfg.notifications

  const setDiscord = (patch: Partial<typeof notifications.discord>) => {
    setCfg({
      ...cfg,
      notifications: {
        ...notifications,
        discord: { ...notifications.discord, ...patch },
      },
    })
  }
  const setGotify = (patch: Partial<typeof notifications.gotify>) => {
    setCfg({
      ...cfg,
      notifications: {
        ...notifications,
        gotify: { ...notifications.gotify, ...patch },
      },
    })
  }
  const setNtfy = (patch: Partial<typeof notifications.ntfy>) => {
    setCfg({
      ...cfg,
      notifications: {
        ...notifications,
        ntfy: { ...notifications.ntfy, ...patch },
      },
    })
  }
  const setEvent = (event: keyof typeof notifications.events, enabled: boolean) => {
    setCfg({
      ...cfg,
      notifications: {
        ...notifications,
        events: { ...notifications.events, [event]: enabled },
      },
    })
  }
  const runTest = async (provider: NotificationProvider) => {
    setTesting(provider)
    try {
      await testNotification(provider, notifications)
      const providerName = provider === 'ntfy' ? 'ntfy' : provider[0].toUpperCase() + provider.slice(1)
      showToast(`${providerName} test notification sent`)
    } catch (error: any) {
      showToast(`Test failed: ${String(error.message || error)}`)
    } finally {
      setTesting(null)
    }
  }

  return (
    <>
      <div className="card">
        <h2>Notification events</h2>
        <p className="desc">Choose which Wolnut events are sent to enabled providers.</p>
        <div className="notification-events">
          <NotificationEventToggle
            label="Power loss"
            description="UPS switches to battery power"
            checked={notifications.events.power_loss}
            onChange={enabled => setEvent('power_loss', enabled)}
          />
          <NotificationEventToggle
            label="Power restored"
            description="Utility power returns"
            checked={notifications.events.power_restored}
            onChange={enabled => setEvent('power_restored', enabled)}
          />
          <NotificationEventToggle
            label="Wake packet sent"
            description="A Wake-on-LAN packet is sent"
            checked={notifications.events.wake_sent}
            onChange={enabled => setEvent('wake_sent', enabled)}
          />
          <NotificationEventToggle
            label="Client recovered"
            description="A client becomes reachable after waking"
            checked={notifications.events.client_recovered}
            onChange={enabled => setEvent('client_recovered', enabled)}
          />
          <NotificationEventToggle
            label="Errors"
            description="Wake failures and recovery timeouts"
            checked={notifications.events.errors}
            onChange={enabled => setEvent('errors', enabled)}
          />
        </div>
      </div>

      <div className="card">
        <div className="provider-heading">
          <div>
            <h2>Discord webhook</h2>
            <p className="desc">Send event embeds to a Discord channel.</p>
          </div>
          <label className="switch-label">
            <input
              type="checkbox"
              checked={notifications.discord.enabled}
              onChange={event => setDiscord({ enabled: event.target.checked })}
            />
            Enabled
          </label>
        </div>
        <div className="field">
          <label>Webhook URL</label>
          <input
            type="password"
            value={notifications.discord.webhook_url}
            onChange={event => setDiscord({ webhook_url: event.target.value })}
            placeholder="https://discord.com/api/webhooks/..."
            autoComplete="off"
          />
          <span className="inline-help">Create this in Discord channel settings under Integrations.</span>
        </div>
        <button
          className="btn btn-ghost"
          onClick={() => runTest('discord')}
          disabled={
            testing !== null
            || !notifications.discord.enabled
            || !notifications.discord.webhook_url.trim()
          }
        >
          {testing === 'discord' ? 'Sending...' : 'Send Discord test'}
        </button>
      </div>

      <div className="card">
        <div className="provider-heading">
          <div>
            <h2>Gotify</h2>
            <p className="desc">Send messages through your self-hosted Gotify server.</p>
          </div>
          <label className="switch-label">
            <input
              type="checkbox"
              checked={notifications.gotify.enabled}
              onChange={event => setGotify({ enabled: event.target.checked })}
            />
            Enabled
          </label>
        </div>
        <div className="grid2">
          <div className="field">
            <label>Server URL</label>
            <input
              value={notifications.gotify.url}
              onChange={event => setGotify({ url: event.target.value })}
              placeholder="https://gotify.example.com"
            />
          </div>
          <div className="field">
            <label>App token</label>
            <input
              type="password"
              value={notifications.gotify.token}
              onChange={event => setGotify({ token: event.target.value })}
              placeholder="Gotify application token"
              autoComplete="off"
            />
          </div>
          <div className="field">
            <label>Priority</label>
            <input
              type="number"
              min={0}
              max={10}
              value={notifications.gotify.priority}
              onChange={event => setGotify({ priority: Number(event.target.value) })}
            />
            <span className="inline-help">0 is lowest, 10 is highest.</span>
          </div>
        </div>
        <button
          className="btn btn-ghost"
          onClick={() => runTest('gotify')}
          disabled={
            testing !== null
            || !notifications.gotify.enabled
            || !notifications.gotify.url.trim()
            || !notifications.gotify.token.trim()
          }
        >
          {testing === 'gotify' ? 'Sending...' : 'Send Gotify test'}
        </button>
      </div>

      <div className="card">
        <div className="provider-heading">
          <div>
            <h2>ntfy</h2>
            <p className="desc">Publish notifications to ntfy.sh or a self-hosted ntfy server.</p>
          </div>
          <label className="switch-label">
            <input
              type="checkbox"
              checked={notifications.ntfy.enabled}
              onChange={event => setNtfy({ enabled: event.target.checked })}
            />
            Enabled
          </label>
        </div>
        <div className="grid2">
          <div className="field">
            <label>Server URL</label>
            <input
              value={notifications.ntfy.url}
              onChange={event => setNtfy({ url: event.target.value })}
              placeholder="https://ntfy.sh"
            />
          </div>
          <div className="field">
            <label>Topic</label>
            <input
              value={notifications.ntfy.topic}
              onChange={event => setNtfy({ topic: event.target.value })}
              placeholder="wolnut-alerts"
              autoComplete="off"
            />
          </div>
          <div className="field">
            <label>Access token</label>
            <input
              type="password"
              value={notifications.ntfy.token}
              onChange={event => setNtfy({ token: event.target.value })}
              placeholder="Optional for public topics"
              autoComplete="off"
            />
          </div>
          <div className="field">
            <label>Priority</label>
            <input
              type="number"
              min={1}
              max={5}
              value={notifications.ntfy.priority}
              onChange={event => setNtfy({ priority: Number(event.target.value) })}
            />
            <span className="inline-help">1 is lowest, 3 is default, 5 is highest.</span>
          </div>
        </div>
        <button
          className="btn btn-ghost"
          onClick={() => runTest('ntfy')}
          disabled={
            testing !== null
            || !notifications.ntfy.enabled
            || !notifications.ntfy.url.trim()
            || !notifications.ntfy.topic.trim()
          }
        >
          {testing === 'ntfy' ? 'Sending...' : 'Send ntfy test'}
        </button>
      </div>
    </>
  )
}

function NotificationEventToggle({
  label,
  description,
  checked,
  onChange,
}: {
  label: string
  description: string
  checked: boolean
  onChange: (enabled: boolean) => void
}) {
  return (
    <label className="notification-event">
      <input
        type="checkbox"
        checked={checked}
        onChange={event => onChange(event.target.checked)}
      />
      <span>
        <strong>{label}</strong>
        <small>{description}</small>
      </span>
    </label>
  )
}

function ClientsTab({
  cfg,
  setCfg,
  status,
  showToast,
  warnings = [],
}: {
  cfg: WolnutConfig
  setCfg: (c: WolnutConfig) => void
  status: any
  showToast: (m: string) => void
  warnings?: any[]
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
      clients: [
        ...cfg.clients,
        { name: `client ${cfg.clients.length + 1}`, host: '192.168.0.100', mac: 'auto', always_wake: false, enabled: true },
      ],
    })
  }

  const liveMap = new Map<string, boolean>()
  for (const c of status?.clients || []) liveMap.set(c.name, c.online)
  const isSuppressed = !!cfg.webui?.suppress_mac_warnings
  const effectiveWarnings = isSuppressed ? [] : (warnings || [])
  const warningMap = new Map<string, any>()
  for (const w of effectiveWarnings || []) if (w.client) warningMap.set(w.client, w)

  return (
    <div className="card">
      {effectiveWarnings && effectiveWarnings.length > 0 && (
        <div style={{ background: '#2a2015', border: '1px solid #f1c40f', color: '#f1c40f', borderRadius: 8, padding: '10px 12px', marginBottom: 12, fontSize: 13 }}>
          <strong style={{ display: 'block', marginBottom: 4 }}>⚠ MAC resolution warning</strong>
          <span style={{ color: '#e6e8ec' }}>{effectiveWarnings.length} client(s) with MAC "auto" could not be resolved. They will be retried at runtime, but WOL may fail if the host is unreachable.</span>
        </div>
      )}
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
        const w = warningMap.get(c.name)
        const isDisabled = c.enabled === false
        return (
          <div
            key={idx}
            className="card"
            style={{ background: '#0f1115', padding: 16, borderColor: w ? '#f1c40f' : undefined, opacity: isDisabled ? 0.6 : 1 }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10, flexWrap: 'wrap', gap: 8 }}>
              <strong>#{idx + 1} — {c.name || 'Unnamed'}</strong>
              <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                {w && (
                  <span style={{ background: '#2a2015', border: '1px solid #f1c40f', color: '#f1c40f', padding: '2px 8px', borderRadius: 999, fontSize: 11, display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                    ⚠ MAC unresolved
                  </span>
                )}
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
                <input
                  value={c.mac}
                  onChange={e => updateClient(idx, { mac: e.target.value })}
                  placeholder="38:f7:cd:c5:87:6b or auto"
                  style={warningMap.get(c.name) ? { borderColor: '#f1c40f', background: '#2a2015' } : undefined}
                />
                <span className="inline-help">Use "auto" to resolve via ARP at runtime</span>
                {warningMap.get(c.name) && (
                  <div style={{ background: '#2a2015', border: '1px solid #f1c40f', color: '#f1c40f', borderRadius: 6, padding: '6px 8px', fontSize: 12, marginTop: 6, display: 'flex', gap: 6, alignItems: 'center' }}>
                    <span>⚠</span>
                    <span style={{ color: '#e6e8ec' }}>{warningMap.get(c.name).message} — WOL may fail until host is reachable.</span>
                  </div>
                )}
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
            <div style={{ display: 'flex', gap: 16, marginTop: 12, flexWrap: 'wrap', alignItems: 'center', borderTop: '1px solid #2a2e3a', paddingTop: 12 }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 13 }}>
                <input
                  type="checkbox"
                  checked={!!c.always_wake}
                  onChange={e => updateClient(idx, { always_wake: e.target.checked })}
                  style={{ width: 16, height: 16 }}
                />
                <span style={{ color: c.always_wake ? '#f1c40f' : '#e6e8ec' }}>Always wake</span>
                <span style={{ color: '#9aa0ae', fontSize: 12 }}>(even if offline before outage)</span>
              </label>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 13 }}>
                <input
                  type="checkbox"
                  checked={c.enabled ?? true}
                  onChange={e => updateClient(idx, { enabled: e.target.checked })}
                  style={{ width: 16, height: 16 }}
                />
                <span style={{ color: c.enabled === false ? '#9aa0ae' : '#e6e8ec' }}>Enabled</span>
              </label>
              {c.always_wake && (
                <span style={{ background: '#2a2015', border: '1px solid #f1c40f', color: '#f1c40f', padding: '2px 8px', borderRadius: 999, fontSize: 11 }}>
                  ⚠ Always
                </span>
              )}
              {c.enabled === false && (
                <span style={{ background: '#2a2e3a', color: '#9aa0ae', padding: '2px 8px', borderRadius: 999, fontSize: 11 }}>
                  Disabled
                </span>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}

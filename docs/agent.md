# Secure shutdown agent setup

Wolnut's Linux agent exposes a deliberately small API: status, shutdown, and
unpair. It never accepts an executable path, shell command, argument list, or
environment variable from the controller.

## Requirements

- A Linux amd64 or arm64 device running systemd
- TCP reachability from the Wolnut host to the device, port `8184` by default
- Administrator access on the device
- HTTPS access to the Wolnut WebUI
- `ADMIN_USERNAME`, `ADMIN_PASSWORD`, and an unpredictable
  `WOLNUT_JWT_SECRET` of at least 32 characters

Restrict the agent port with the device firewall so only the Wolnut controller
can reach it. Do not expose the agent or Wolnut's port `8183` directly to the
internet.

## Put the WebUI behind HTTPS

Terminate TLS in a trusted reverse proxy. For example, a Caddy instance on the
Wolnut host can use:

```caddyfile
wolnut.example.com {
    reverse_proxy 127.0.0.1:8183
}
```

Keep direct access to port `8183` firewalled. Wolnut trusts forwarded scheme
headers only from loopback by default. If the proxy runs elsewhere, set its
exact IP address:

```yaml
environment:
  - WOLNUT_FORWARDED_ALLOW_IPS=192.168.1.5
```

Never configure this setting as `*` on an untrusted network.

## Install the standalone agent

Download the binary and matching checksum from the desired GitHub release.
Replace `VERSION` and `ARCH` (`amd64` or `arm64`) below:

```bash
curl -LO https://github.com/tfourj/wolnut_webui/releases/download/VERSION/wolnut-agent-linux-ARCH
curl -LO https://github.com/tfourj/wolnut_webui/releases/download/VERSION/wolnut-agent-linux-ARCH.sha256
sha256sum -c wolnut-agent-linux-ARCH.sha256
chmod 0755 wolnut-agent-linux-ARCH
sudo ./wolnut-agent-linux-ARCH install-service
```

`install-service` copies the binary to `/usr/local/bin/wolnut-agent`, creates a
hardened root systemd service, and starts it on `0.0.0.0:8184`. Agent state and
private keys are stored in `/var/lib/wolnut-agent/state.json` with root-only
permissions. The local listen configuration is stored in
`/etc/wolnut-agent/agent.env`; edit it only as root, then restart
`wolnut-agent`.

To use another address or port:

```bash
sudo ./wolnut-agent-linux-ARCH install-service --listen 192.168.1.20:9191
```

Allow that port through the firewall only from the Wolnut controller.

## Pair a client

1. Add or edit the client in Wolnut. Disable **Wake on restore** if the device
   is shutdown-only, then save the configuration.
2. On the device, generate fresh enrollment values:

   ```bash
   sudo wolnut-agent pairing-code
   ```

3. Within 10 minutes, choose **Pair agent** in the client's Secure shutdown
   panel and enter the port, pairing code, and complete SHA-256 fingerprint.
4. Pairing verifies the displayed fingerprint before sending the one-time code.
   Wolnut then issues role-restricted certificates and all subsequent traffic
   uses TLS 1.3 mutual authentication.
5. Test the connection. Enable **Automatic shutdown**, select a battery
   threshold from 1–100%, and save.

The pairing code is 128 bits, expires after 10 minutes, is single-use, and
locks after five failed attempts. Run `pairing-code` again to rotate it.

## Operation and recovery

- Wolnut acts only on explicit, valid NUT `OB` status and battery charge data.
- A device receives one stable command ID per outage. Retries cannot schedule
  duplicate shutdowns.
- Delivery retries continue at the configured polling interval until accepted
  or NUT explicitly reports `OL`.
- The agent replies before scheduling the fixed `/usr/bin/systemctl poweroff`
  action. "Accepted" does not claim the machine finished powering off.
- Certificate expiry is shown after pairing or a successful connection test.
  Re-pair the agent to rotate its five-year leaf certificate.

Normal **Unpair** resets both sides. If the device is unreachable, the WebUI
can forget it locally; afterward reset the device before pairing again:

```bash
sudo wolnut-agent reset-pairing --confirm
sudo systemctl restart wolnut-agent
sudo wolnut-agent pairing-code
```

Back up `/config/security` together with Wolnut's configuration. Possession of
that directory authorizes control of paired agents, so store backups as
secrets. Restoring only part of the controller identity is intentionally
rejected; restore the complete directory or reset and re-pair every agent.

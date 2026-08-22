# Secure shutdown agent setup

Wolnut's Linux agent exposes a deliberately small API: status, shutdown, fixed
self-update and update-policy actions, and unpair. It never accepts an
executable path, download URL, shell command, argument list, or environment
variable from the controller.

## Requirements

- A Linux amd64 or arm64 device running systemd
- TCP reachability from the Wolnut host to the device, port `8184` by default
- Outbound HTTPS from the device to Wolnut and the configured release host for
  one-line installation
- Administrator access on the device
- `curl` and `sha256sum` for one-line installation
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

Set the public HTTPS address used by devices when enrolling:

```yaml
environment:
  - WOLNUT_PUBLIC_URL=https://wolnut.example.com
```

The agent validates this address with the device's normal system trust store.
For a private certificate authority, install that CA on the device. Do not
disable TLS verification. If binaries are hosted somewhere other than the
project's latest GitHub release, configure an HTTPS directory containing
`install.sh`, `uninstall.sh`, `agent-release.json`, both agent architectures,
and every matching `.sha256` file:

```yaml
environment:
  - WOLNUT_AGENT_DOWNLOAD_BASE_URL=https://downloads.example.com/wolnut
```

## One-line installation and pairing

1. Add the client in Wolnut and save the configuration.
2. In the client's **Secure shutdown** section, choose **Quick install**.
3. Confirm the agent port and generate the command.
4. Copy the short `curl ... | sh` command and run it on the Linux device. It
   fetches a customized `install.sh` from Wolnut over HTTPS; the installer then
   detects amd64 or arm64, verifies the agent binary, enrolls it, and starts the
   hardened systemd service.
5. Keep the dialog open to see the live enrollment result. Test the connection,
   then enable **Automatic shutdown**, choose a threshold, and save.

The installer runs directly when the current account is root, including on a
default Proxmox host where `sudo` is not installed. For an unprivileged account
it uses `sudo` when available. If neither condition applies, it explains how to
log in as root with `su -` and rerun the same command.

The command sends a 256-bit enrollment token in the HTTPS Authorization header,
not in the URL, and pipes the returned installer to `/bin/sh`. The token expires
after 10 minutes. Wolnut stores only its SHA-256 hash, binds it to the first
agent identity and certificate request, and invalidates older commands for the
same client. Do not share the command while it is valid. A retry from that same
agent is allowed if the HTTPS response was interrupted.

The installer is delivered by the trusted Wolnut HTTPS origin. It downloads
only the matching agent architecture from the configured release directory and
verifies the separately downloaded SHA-256 file before running or installing
the binary. Automatic enrollment does not enable battery shutdown by itself.

GitHub's bare `/releases/latest/download` address is an asset prefix and may
return 404 by itself. The installer appends an exact asset name such as
`wolnut-agent-linux-amd64`; that complete URL is the one that must exist in the
latest release.

## Manual installation

Choose **Manual install** in the client's Secure shutdown section, select the
port, and click **Show manual commands**. Wolnut provides separate commands to:

1. Fetch and run `install.sh` from Wolnut without an enrollment secret; the
   script verifies the downloaded agent binary.
2. Generate the one-time pairing code and certificate fingerprint on the
   device.
3. Return to Wolnut's certificate-pinned manual pairing dialog.

The installer stores the service binary at
`/var/lib/wolnut-agent/wolnut-agent`, links
`/usr/local/bin/wolnut-agent` to it, creates a hardened root systemd service,
and starts it on the selected port. Agent state and private keys are stored in
`/var/lib/wolnut-agent/state.json` with root-only permissions. The local listen
and release-source configuration is stored in
`/etc/wolnut-agent/agent.env`; edit it only as root, then restart
`wolnut-agent`.

Allow that port through the firewall only from the Wolnut controller.

## Manual certificate-pinned pairing

1. Add or edit the client in Wolnut. Disable **Wake on restore** if the device
   is shutdown-only, then save the configuration.
2. On the device, generate fresh enrollment values as root (directly or with
   `sudo` when it is installed):

   ```bash
   wolnut-agent pairing-code
   ```

3. Within 10 minutes, choose **Pair manually** in the client's Secure shutdown
   panel and enter the port, pairing code, and complete SHA-256 fingerprint.
4. Pairing verifies the displayed fingerprint before sending the one-time code.
   Wolnut then issues role-restricted certificates and all subsequent traffic
   uses TLS 1.3 mutual authentication.
5. Test the connection. Enable **Automatic shutdown**, select a battery
   threshold from 1–100%, and save.

The pairing code is 128 bits, expires after 10 minutes, is single-use, and
locks after five failed attempts. Run `pairing-code` again to rotate it.

## Uninstalling

Expand **Uninstall command** in the Manual install dialog to copy a verified
command for the matching release. `uninstall.sh` stops and disables the
systemd service and removes the installed binary. By default it preserves
`/var/lib/wolnut-agent` and `/etc/wolnut-agent` so pairing state and
configuration can be recovered. Run the downloaded script with `--purge` only
when you also want to permanently remove that data.

Like the installer, the uninstaller runs directly as root and falls back to
`sudo` only when needed and available.

## Version display and updates

After pairing or a successful **Test connection**, each client card displays
the installed agent version. Once the agent has checked its configured release
source, the card also shows the latest version, update status, and any safe,
redacted error.

Choose **Check for update** for an immediate check, or enable **Automatic agent
updates** for that device. Enabling the toggle performs a check immediately;
the agent then checks every six hours while the policy remains enabled. Policy
changes and manual checks require the same authenticated HTTPS administration
controls as pairing and shutdown.

Updates are deliberately narrow:

- Wolnut sends no URL, path, command, arguments, script, or environment value.
- The agent uses only the HTTPS release directory stored locally during
  installation.
- It downloads `agent-release.json` and its checksum, accepts only stable
  `x.y.z` versions with the matching protocol, and refuses downgrades.
- It downloads only `wolnut-agent-linux-amd64` or
  `wolnut-agent-linux-arm64`, verifies the matching SHA-256 file, runs the
  downloaded binary only with the fixed `version` argument, and confirms the
  embedded version before an atomic replacement.
- The agent restarts only `wolnut-agent.service` with a static `systemctl`
  invocation. Update state survives that restart and is reported afterward.

Agents released before self-update support must be upgraded once with
**Reinstall agent** in the paired device card. Pairing state is retained when
the installer is rerun. Tagged releases must use `vX.Y.Z`; the release workflow
publishes the manifest and checksums automatically.

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
- Unpairing or resetting pairing disables the agent's automatic update policy.

Normal **Unpair** resets both sides. If the device is unreachable, the WebUI
can forget it locally; afterward run these commands as root (directly or with
`sudo`) before pairing again:

```bash
wolnut-agent reset-pairing --confirm
systemctl restart wolnut-agent
wolnut-agent pairing-code
```

Back up `/config/security` together with Wolnut's configuration. Possession of
that directory authorizes control of paired agents, so store backups as
secrets. Restoring only part of the controller identity is intentionally
rejected; restore the complete directory or reset and re-pair every agent.

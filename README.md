# WOLNUT

**WOLNUT** is a lightweight service that works alongside [NUT (Network UPS Tools)](https://networkupstools.org/) to safely shut down Linux devices as UPS charge falls and wake selected systems after power returns.

wolnut... get it?

## What It Does

When a UPS switches to battery power, WOLNUT:

1. Detects the power event via `upsc`
2. Delivers mutually authenticated shutdown requests at each device's battery threshold
3. Retries unreachable agents safely using idempotent command IDs
4. Tracks which clients were online before the outage
5. Waits for power restoration and a safe battery charge
6. Sends WOL packets to bring selected systems back

This helps reboot systems automatically after a controlled shutdown caused by a power loss — especially useful for homelabs, small servers, and media boxes.

---

## Features

- Auto-detect MAC addresses with ARP
- Tracks online status of clients via ping
- Supports NUT with or without authentication
- Persistent state file for post-reboot recovery
- Runs as a standalone Python service or Docker container
- WebUI always running on port 8183 for configuration
- Discord, Gotify, and ntfy notifications with per-event controls
- Test notifications from the WebUI before saving provider settings
- Per-device UPS battery shutdown thresholds
- Standalone Linux amd64 and arm64 shutdown agents
- Certificate-pinned enrollment followed by TLS 1.3 mutual authentication
- No remote shell or user-supplied command execution

## Secure shutdown agents

Shutdown management is opt-in per client. After saving a client, choose
**Quick install** to generate a short-lived one-line command. Run it on the
Linux systemd device to verify and run `install.sh`, download the matching
checksum-verified agent, install its hardened service, and enroll it
automatically over HTTPS. The installer runs directly for root users such as a
default Proxmox login, and uses `sudo` only when required and available.

Choose **Manual install** to install the daemon without an enrollment secret,
then finish certificate-pinned pairing using the device's one-time code and
fingerprint. The same dialog provides a verified `uninstall.sh` command.
Pairing does not enable automatic shutdown; choose a threshold and save the
configuration afterward.

Each paired device card shows the installed agent version and the latest
version discovered from verified release metadata. **Check for update** starts
a checksum-verified update immediately. **Automatic agent updates** is opt-in
per device and checks when enabled and every six hours while the agent runs.
The controller can request only this fixed update operation; it cannot provide
a download URL, executable path, command, or arguments.

The agent accepts only status, shutdown, fixed self-update, update-policy, and
unpair requests. A shutdown acknowledgement means the agent accepted and
scheduled `systemctl poweroff`; Wolnut cannot prove final power-off after the
device goes offline.

See [Secure shutdown agent setup](docs/agent.md) for installation, HTTPS,
firewall, pairing, reset, and recovery instructions.

## Notifications

Open the **Notifications** tab in the WebUI to configure Discord, Gotify, or
ntfy.
Each provider can be enabled independently, and event switches control alerts
for power loss, restoration, wake packets, recovered clients, shutdown
acknowledgements, and delivery failures.

The test buttons use the values currently in the form, so you can verify a
webhook URL, Gotify app token, or ntfy topic before saving. ntfy supports both
ntfy.sh and self-hosted servers, with an optional access token for protected
topics. Provider credentials are stored in the YAML config file; protect that
file as you would any other secret.

---

## Quickstart

See the [Quickstart](docs/quickstart.md) guide.

### Docker Compose

See [docker-compose.yml](docker-compose.yml) for an example docker compose file

Pull latest image:

```bash
docker pull ghcr.io/tfourj/wolnut_webui:latest
```

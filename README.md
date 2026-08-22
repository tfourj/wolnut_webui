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

Shutdown management is opt-in per client. Install the standalone agent on a
Linux systemd device, display a short-lived pairing code locally, and pair it
from the client's **Secure shutdown** panel. Pairing does not enable automatic
shutdown; choose a threshold and save the configuration afterward.

The agent accepts only status, shutdown, and unpair requests. A shutdown
acknowledgement means the agent accepted and scheduled `systemctl poweroff`;
Wolnut cannot prove final power-off after the device goes offline.

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

# WOLNUT

**WOLNUT** is a lightweight Python service designed to work alongside [NUT (Network UPS Tools)](https://networkupstools.org/) to automatically send Wake-on-LAN (WOL) packets to client systems after a power outage.

wolnut... get it?

## What It Does

When a UPS (connected to NUT) switches to battery power, WOLNUT:

1. Detects the power event via `upsc`
2. Tracks which clients were online before the outage
3. Waits for power to be restored and the battery to reach a safe threshold
4. Sends WOL packets to bring back any systems that powered down

This helps reboot systems automatically after a controlled shutdown caused by a power loss — especially useful for homelabs, small servers, and media boxes.

---

## Features

- Auto-detect MAC addresses with ARP
- Tracks online status of clients via ping
- Supports NUT with or without authentication
- Persistent state file for post-reboot recovery
- Runs as a standalone Python service or Docker container
- WebUI always running on port 8183 for configuration
- Discord webhook and Gotify notifications with per-event controls
- Test notifications from the WebUI before saving provider settings

## Notifications

Open the **Notifications** tab in the WebUI to configure Discord or Gotify.
Each provider can be enabled independently, and event switches control alerts
for power loss, power restoration, wake packets, recovered clients, and errors.

The test buttons use the values currently in the form, so you can verify a
webhook URL or Gotify app token before saving. Provider credentials are stored
in the YAML config file; protect that file as you would any other secret.

---

## Quickstart

See the [Quickstart](docs/quickstart.md) guide.

### Docker Compose

See [docker-compose.yml](docker-compose.yml) for an example docker compose file

Pull latest image:

```bash
docker pull ghcr.io/tfourj/wolnut_webui:latest
```

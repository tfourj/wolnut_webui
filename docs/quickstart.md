# Quickstart Guide

This guide will help you get `wolnut` running in a few simple steps using Docker.

## Prerequisites

- Docker installed and running.
- A NUT (Network UPS Tools) server monitoring your UPS. See [Techno Tim's](https://technotim.live/posts/NUT-server-guide/) guide
- The IP address or hostname of your NUT server, probably localhost.
- The name of the UPS as configured in NUT (e.g., `ups`).

## 1. Make dir

```bash
mkdir wolnut && cd wolnut
```

## 2. Download compose

With command:

```bash
curl -O https://raw.githubusercontent.com/tfourj/wolnut_webui/main/docker-compose.yml
# or
wget https://raw.githubusercontent.com/tfourj/wolnut_webui/main/docker-compose.yml
```

Or manually download `docker-compose.yml` from the repo and place it in `wolnut`.

## 3. Change .envs

Edit `docker-compose.yml` and set:

```yaml
environment:
  - ADMIN_USERNAME=admin
  - ADMIN_PASSWORD=changeme
  - WOLNUT_JWT_SECRET=change-this-to-a-long-random-secret
  - WOLNUT_PUBLIC_URL=https://wolnut.example.com
```

Change all three values. `WOLNUT_JWT_SECRET` must be an unpredictable value of
at least 32 characters. Secure shutdown administration remains disabled when
these settings are absent or weak.

Set `WOLNUT_PUBLIC_URL` to the HTTPS address devices can use to reach Wolnut.
This avoids embedding an internal proxy address in generated agent install
commands.

## 4. Run compose

```bash
docker compose up -d
```

Requires `network_mode: host` for Wake-on-LAN — do not add `ports`. WebUI is always running on port 8183.

## 5. Setup in WebUI

Open the WebUI, log in with `ADMIN_USERNAME` / `ADMIN_PASSWORD`, then
configure NUT and clients under **Configuration** and **Clients**. All settings
are hot-reloaded, so no restart is needed.

HTTP is sufficient for initial Wake-on-LAN configuration, but agent pairing,
automatic shutdown settings, manual shutdown, and unpairing require HTTPS.
For an unpaired client, choose **Quick install**, copy the generated command,
and run it on the Linux device. The installer works directly as root on
Proxmox and other systems without `sudo`, or uses `sudo` for an unprivileged
account when it is available. Choose **Manual install** instead to install the
daemon first and then complete certificate-pinned pairing with a code and
fingerprint. Wolnut displays the live enrollment status and refreshes the
client after pairing. Follow
[Secure shutdown agent setup](agent.md) for firewall and recovery guidance.

After pairing, use **Test connection** to refresh the installed agent version.
The client card can check for a newer verified release immediately or enable
automatic checks for that individual device.

Optional notifications can be configured under **Notifications**. Enable a
Discord webhook, Gotify, or ntfy provider, enter its settings, and use the
matching test button before saving. For ntfy, enter the server root URL and
topic; an access token is optional for public topics and required when your
server protects the topic. Choose which power, wake, recovery, and error events
each enabled provider receives. Notification credentials are stored in the
YAML config file, so keep that file private.

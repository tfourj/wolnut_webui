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
```

Change to secure values. `ADMIN_USERNAME`/`ADMIN_PASSWORD` are the WebUI login.

## 4. Run compose

```bash
docker compose up -d
```

Requires `network_mode: host` for Wake-on-LAN — do not add `ports`. WebUI is always running on port 8183.

## 5. Setup in WebUI

Open `http://<host>:8183`, log in with `ADMIN_USERNAME` / `ADMIN_PASSWORD`, then configure NUT and clients under **Configuration** and **Clients**. All settings are hot-reloaded — no restart needed.

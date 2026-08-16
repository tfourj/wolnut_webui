# WOLNUT

**WOLNUT** is a lightweight service for NUT (Network UPS Tools) that sends Wake-on-LAN to clients after power is restored.

## What It Does

1. Detects `OB`/`OL` via `upsc`
2. Snapshots online clients at `OB`
3. Waits `restore_delay_sec` + `min_battery_percent`
4. Sends WOL to clients that were online before outage

For homelabs / servers after controlled NUT shutdown.

## Quickstart

See [Quickstart](docs/quickstart.md):

```bash
mkdir -p ~/wolnut/config && cd ~/wolnut
curl -O https://raw.githubusercontent.com/tfourj/wolnut_webui/main/docker-compose.yml
# edit ADMIN_USERNAME / ADMIN_PASSWORD / WOLNUT_JWT_SECRET in docker-compose.yml
docker pull ghcr.io/tfourj/wolnut_webui:latest
docker compose up -d
# open http://<host>:8183 -> setup NUT + clients in WebUI
```

WebUI is always on. No manual `config.yaml` needed.

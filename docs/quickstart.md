# Quickstart

WebUI is always running — all configuration is done there. No manual `config.yaml` editing needed.

## 1. Make dir

```bash
mkdir -p ~/wolnut/config && cd ~/wolnut
```

## 2. Download compose

With command:

```bash
curl -O https://raw.githubusercontent.com/tfourj/wolnut_webui/main/docker-compose.yml
# or
wget https://raw.githubusercontent.com/tfourj/wolnut_webui/main/docker-compose.yml
```

Or manually download `docker-compose.yml` from the repo and place it in `~/wolnut/`.

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
docker pull ghcr.io/tfourj/wolnut_webui:latest
docker compose up -d
```

Requires `network_mode: host` for Wake-on-LAN — do not add `ports`.

## 5. Setup in WebUI

Open `http://<host>:8183`, log in with `ADMIN_USERNAME` / `ADMIN_PASSWORD`, then configure NUT and clients under **Configuration** and **Clients**. All settings are hot-reloaded — no restart needed.

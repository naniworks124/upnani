# Smart Upload Engine — plain VPS deployment (no Docker required)

This variant runs on **any Linux VPS** — Docker or not — because it's just
a Python process managed by `systemd`, fronted by `nginx` so you can point
any domain at it with free SSL. Works identically on DigitalOcean,
Hetzner, Linode, Contabo, Vultr, a spare home server, literally anything
that gives you SSH access to Ubuntu/Debian.

## Security note

This version has **no login** and **no database** — it's built for
running on your own VPS for personal use, controlled entirely through
your GitHub-hosted `down.json` file rather than a public dashboard.
Since there's no auth on the API, restrict access at the network level:
either keep the VPS's firewall closed to everything except SSH (don't
expose port 7860 publicly), or only reach it through the Cloudflare
Tunnel setup below rather than a raw public IP.

## What's in this folder

```
vps-deploy/
├── run.py                          # Entry point (reads PORT env var, defaults to 7860)
├── .env.example                    # Copy to .env and fill in your real credentials
├── backend/                        # Unchanged FastAPI app
├── frontend/                       # Dashboard (static files)
└── deploy/
    ├── setup.sh                        # One-shot installer script
    ├── smart-upload-engine.service     # systemd unit (keeps it running, auto-restart)
    └── nginx-smart-upload-engine.conf  # Reverse proxy template for your domain
```

## Requirements

- A VPS running Ubuntu or Debian (any provider, any size — this app is
  lightweight; 1 vCPU / 512MB–1GB RAM is enough for personal use)
- Nothing else — no database, no login setup. Task state is stored in a
  plain local JSON file on the VPS itself.
- Optional but recommended: a domain name, with its DNS **A record**
  pointed at your VPS's IP address (needed for step 4 below)

## Setup

**1. Get the project onto your VPS.**
```bash
scp -r vps-deploy/ user@YOUR_VPS_IP:/opt/smart-upload-engine
ssh user@YOUR_VPS_IP
cd /opt/smart-upload-engine
```

**2. Run the installer.**
```bash
sudo bash deploy/setup.sh
```
This installs Python, creates a virtual environment, installs
dependencies, copies `.env.example` to `.env`, and registers the app as
a systemd service (auto-restarts on crash or VPS reboot).

**3. Fill in your real credentials.**
```bash
sudo nano /opt/smart-upload-engine/.env
```
At minimum set: your `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`/
`GOOGLE_REFRESH_TOKEN` and/or `GOFILE_API_TOKEN` (whichever destination
you're using), and `REMOTE_QUEUE_URL` pointing at your GitHub-hosted
`down.json`.

**4. Start it.**
```bash
sudo systemctl start smart-upload-engine
sudo journalctl -u smart-upload-engine -f   # watch the logs
```
Look for `Smart Upload Engine worker started.` — that confirms it's up.
At this point it's already reachable at `http://YOUR_VPS_IP:7860`.

## Pointing a domain at it — IMPORTANT if using a free VPS

Free VPS providers often assign a **new IP address every time the VPS
restarts**. If you use the normal nginx + DNS "A record" method below,
you'd have to manually update your DNS every single restart — annoying
and easy to forget.

**If your VPS has a stable/static IP** (most paid VPS plans): use the
nginx + certbot method in the next section.

**If your VPS is free/ephemeral (IP changes on restart)**: use
**Cloudflare Tunnel** instead — it needs a free Cloudflare account with
your domain added (Cloudflare manages the DNS; you can keep the domain
registered anywhere and just switch its nameservers to Cloudflare's).

```bash
sudo bash deploy/cloudflare-tunnel-setup.sh yourdomain.com upload.yourdomain.com
```

You'll be asked to open a URL and log into Cloudflare once — after that,
it's fully automated: your subdomain (`upload.yourdomain.com` in the
example) always points at this app, **even after the VPS gets a new IP**,
because the tunnel is an outbound connection that survives IP changes
and auto-reconnects on its own. No DNS updates, no port forwarding, free
SSL included.

From then on, starting everything after a VPS restart is just:
```bash
sudo systemctl start smart-upload-engine
```
(`cloudflared` is installed as its own systemd service too, so it
auto-starts and auto-reconnects on every boot — you don't need to redo
the tunnel setup again.)

### Alternative: nginx + certbot (only if your VPS has a static IP)

```bash
# Edit the domain name inside this file first:
sudo nano /opt/smart-upload-engine/deploy/nginx-smart-upload-engine.conf

sudo cp deploy/nginx-smart-upload-engine.conf /etc/nginx/sites-available/smart-upload-engine
sudo ln -s /etc/nginx/sites-available/smart-upload-engine /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# Free SSL certificate, auto-renewing:
sudo certbot --nginx -d yourdomain.com
```

After that, `https://yourdomain.com` serves the dashboard directly — no
`:7860` port needed, and traffic is encrypted.

**No domain yet?** You can still use the app at `http://YOUR_VPS_IP:7860`
right away; add the domain + nginx step whenever you get one.

## Managing the service

```bash
sudo systemctl status smart-upload-engine    # is it running?
sudo systemctl restart smart-upload-engine   # after editing .env or code
sudo systemctl stop smart-upload-engine
sudo journalctl -u smart-upload-engine -f    # live logs
```

It's set to auto-start on every VPS reboot (`systemctl enable`, already
done by the installer) and auto-restart itself if it ever crashes
(`Restart=always` in the service file).

## Why this is a better fit than Wispbyte

- **Real disk** — even the smallest VPS plans typically give 10-25GB+,
  versus the 1GB that caused the `pip install` to fail on Wispbyte.
- **Full OS access** — you control Python, disk paths, and processes
  directly instead of working around a game-server-style panel.
- **Any domain** — nginx + certbot is the standard way to point any
  domain you own at any VPS, with free auto-renewing SSL.
- **Docker-optional** — since this setup doesn't need Docker at all, it
  works even on the cheapest/most restricted VPS plans.

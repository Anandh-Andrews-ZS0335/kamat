# Complete Deployment & Remote Access Guide

This guide explains how to deploy and run **Last Mile (Agentic Prescriptive Analytics)** so your teammates, judges, and clients can access and interact with the live application securely from anywhere.

---

## 🚀 Deployment Options Overview

| Option | Best For | Setup Time | Cost | Persistent HTTPS |
|---|---|---|---|---|
| **1. Cloudflare Tunnel / ngrok** | **Instant Remote Sharing & Demos** | **30 seconds** | **Free** | ✅ Yes |
| **2. Docker Compose (Cloud VPS / Server)** | **AWS EC2, DigitalOcean, Hetzner, Local Server** | **2 minutes** | **Low / Free Tier** | ✅ Yes |
| **3. Cloud PaaS (Render / Railway / Fly.io)** | **Zero-Ops Managed Cloud Hosting** | **3 minutes** | **Free / Low** | ✅ Yes |
| **4. Linux Background Service (systemd)** | **Dedicated On-Premise / Always-On Machine** | **2 minutes** | **Free** | ✅ Local / VPN |

---

## ⚡ Option 1: Instant Remote HTTPS Access (Fastest for Teams & Demos)

You can run the application on your computer or team machine and give teammates instant, secure HTTPS access without opening any firewall ports.

### Method A: Cloudflare Tunnel (Recommended - Free, Permanent HTTPS, Built-in Auth)
Cloudflare provides free tunneling with enterprise-grade SSL and optional Google/Email access control:

```bash
# 1. Start the application locally
make up

# 2. In a separate terminal, install and run cloudflared:
# On macOS: brew install cloudflared
# On Linux: curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb && sudo dpkg -i cloudflared.deb

cloudflared tunnel --url http://localhost:8000
```
**Result:** Cloudflare will output a public HTTPS URL:
`https://random-subdomain.trycloudflare.com`

Share this URL with your teammates. They will have full access to:
- **Demo Showcase:** `https://your-tunnel.trycloudflare.com/demo`
- **Manager Approval Console:** `https://your-tunnel.trycloudflare.com/manager`
- **Admin Trace:** `https://your-tunnel.trycloudflare.com/admin`

---

### Method B: Ngrok (Quick 10-Second Share)
```bash
# 1. Start the application
make up

# 2. In another terminal:
ngrok http 8000
```
Share the generated `https://xxxx.ngrok-free.app` URL with your team.

---

## 🐳 Option 2: Docker & Docker Compose (Any Cloud VPS or Server)

Deploy on any Linux server (AWS EC2, DigitalOcean Droplet, Google Compute Engine, Hetzner, or a local server).

### Prerequisites
- Docker & Docker Compose installed on the server.

### 1-Command Deployment
```bash
# Clone the repository on your server
git clone <your-repo-url>
cd kamat

# (Optional) Create .env file with your GEMINI_API_KEY if using LLM drafting
cp .env.example .env

# Start with Docker Compose
docker compose up -d --build
```

### Checking Status & Logs
```bash
docker compose ps
docker compose logs -f lastmile
```

The application will be running at `http://<your-server-ip>:8000/demo`.

---

## ☁️ Option 3: Managed Cloud One-Click (Render / Railway / Fly.io)

### Deploying to Render (Free / Low-Cost with Automated HTTPS)
1. Push this repository to GitHub or GitLab.
2. Go to [Render.com](https://render.com) and click **New → Blueprint**.
3. Select your repository. Render will automatically read `render.yaml` and configure:
   - Python 3.11 container with `coinor-cbc` solver
   - Web service on port `8000`
   - 1GB Persistent SSD disk for the bank & audit database
4. Click **Apply**. Render will build and launch your live application with a free `https://your-app.onrender.com` domain.

---

### Deploying to Fly.io
```bash
# Install flyctl
curl -L https://fly.io/install.sh | sh

# Login and deploy
fly auth login
fly launch --config fly.toml --now
```

---

## 🖥️ Option 4: Always-On Background Daemon on Linux (systemd)

To keep the application running 24/7 on a dedicated team server:

1. Create a systemd service file:
```bash
sudo nano /etc/systemd/system/lastmile.service
```

2. Paste the following configuration (replace `/path/to/kamat` and `ubuntu` with your actual user and path):
```ini
[Unit]
Description=Last Mile Agentic Prescriptive Analytics
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/kamat
ExecStart=/home/ubuntu/kamat/.venv/bin/python /home/ubuntu/kamat/scripts/start_prod.py
Restart=always
RestartSec=5
Environment=PORT=8000
Environment=BANK_API_PORT=8001
Environment=BANK_API_URL=http://127.0.0.1:8001

[Install]
WantedBy=multi-user.target
```

3. Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable lastmile
sudo systemctl start lastmile
sudo systemctl status lastmile
```

---

## 🔒 Security Best Practices for Remote Sharing

1. **HMAC PII Tokenization (Built-in):**
   The application already redacts real customer names, SSNs, and identities before any processing using HMAC-SHA256 tokens (`tokenise.py`). Only the approval script temporarily rejoins first names from the isolated identity vault.

2. **Tamper-Proof Audit Chain (Built-in):**
   Every action, run, and human approval creates an append-only cryptographic block verified by SHA-256.

3. **Cloudflare Zero-Trust Access (Recommended for teams):**
   If deploying via Cloudflare Tunnel, you can enable **Cloudflare Access Applications** in the Cloudflare Dashboard to require teammates to sign in with their corporate email or Google workspace before seeing the dashboard.

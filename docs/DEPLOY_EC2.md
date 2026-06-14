# Single-Server EC2 Deployment with HTTPS
# Amazon Linux 2023 (kernel 6.1)

All 9 services run on one EC2 instance behind Nginx + Let's Encrypt.
Estimated cost: **~$30–40/month** (t3.medium on-demand).

> **CI:** Every push to `main` or `dev` automatically runs all 80 unit tests via GitHub Actions (`.github/workflows/test.yml`). Never deploy a commit that hasn't passed CI.

---

## 1. Launch EC2 instance

**Recommended spec:**

| Field | Value |
|---|---|
| AMI | **Amazon Linux 2023 AMI** (kernel 6.1) |
| Instance type | `t3.medium` (2 vCPU, 4 GB RAM) |
| Storage | 30 GB gp3 |
| Key pair | Create or use existing `.pem` |

**Security group — inbound rules:**

| Port | Source | Purpose |
|---|---|---|
| 22 | Your IP only | SSH |
| 80 | 0.0.0.0/0 | HTTP (Let's Encrypt challenge + redirect) |
| 443 | 0.0.0.0/0 | HTTPS |

Do **not** open 3000, 8000–8007, 5432, or 6379 publicly.

**Assign an Elastic IP** — free while the instance is running, prevents the IP changing on reboot.

---

## 2. Point your domain to the server

In your DNS provider, add an **A record**:

```
stockai.yourdomain.com  →  <Elastic IP>
```

Wait for DNS to propagate (usually < 5 min) before running Certbot.

---

## 3. SSH in and install dependencies

Amazon Linux 2023 uses `dnf` (not `apt`) and the default user is `ec2-user`.

```bash
ssh -i your-key.pem ec2-user@<Elastic IP>
```

### Docker

```bash
sudo dnf install -y docker
sudo systemctl enable --now docker
sudo usermod -aG docker ec2-user

# Log out and back in for the group change to take effect
exit
ssh -i your-key.pem ec2-user@<Elastic IP>

docker --version
```

### Docker Compose V2 plugin

AL2023 doesn't bundle Docker Compose — install it manually:

```bash
COMPOSE_VERSION=$(curl -s https://api.github.com/repos/docker/compose/releases/latest \
  | grep '"tag_name"' | cut -d'"' -f4)

sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo curl -SL \
  "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-x86_64" \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

docker compose version
```

### Docker Buildx plugin

AL2023 ships with an old Buildx that is too old for `docker compose build`.
Install the latest version the same way:

```bash
BUILDX_VERSION=$(curl -s https://api.github.com/repos/docker/buildx/releases/latest \
  | grep '"tag_name"' | cut -d'"' -f4)

sudo curl -SL \
  "https://github.com/docker/buildx/releases/download/${BUILDX_VERSION}/buildx-${BUILDX_VERSION}.linux-amd64" \
  -o /usr/local/lib/docker/cli-plugins/docker-buildx
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-buildx

docker buildx version
```

### Nginx

```bash
sudo dnf install -y nginx
sudo systemctl enable --now nginx
```

### Certbot (via pip virtualenv — snapd is not in AL2023 repos)

AL2023's system Python is "externally managed" — do NOT run `python3 -m pip` directly.
Use the venv's own pip instead:

```bash
sudo dnf install -y python3 python3-pip augeas-libs

# Create an isolated virtualenv for certbot
sudo python3 -m venv /opt/certbot/

# Use the venv's pip — NOT the system pip
sudo /opt/certbot/bin/pip install --upgrade pip
sudo /opt/certbot/bin/pip install certbot certbot-nginx

# Make certbot available system-wide
sudo ln -s /opt/certbot/bin/certbot /usr/bin/certbot

certbot --version
```

### SELinux — allow Nginx to proxy to localhost ports

Amazon Linux 2023 ships with SELinux in **enforcing** mode. Without this, Nginx will get a `Permission denied` error when trying to proxy to Docker containers:

```bash
sudo setsebool -P httpd_can_network_connect 1
```

### Git

```bash
sudo dnf install -y git
```

---

## 4. Clone the repo

```bash
git clone <your-repo-url> ~/stockai
cd ~/stockai
```

---

## 5. Configure .env for production

```bash
cp .env .env.production
nano .env.production
```

Change these values:

```env
# --- Database ---
POSTGRES_PASSWORD=<strong-random-password>
DATABASE_URL=postgresql+psycopg2://stockai:<strong-random-password>@postgres:5432/stockai

# --- Auth ---
JWT_SECRET=<64-char-random-string>
# Generate one: openssl rand -hex 32

# --- Service URLs — keep as-is (docker internal network) ---
MARKET_DATA_URL=http://market-data:8001
TECHNICAL_ANALYSIS_URL=http://technical-analysis:8002
ML_PREDICTION_URL=http://ml-prediction:8003
RANKING_ENGINE_URL=http://ranking-engine:8004
SIGNAL_ENGINE_URL=http://signal-engine:8005
STRATEGY_ENGINE_URL=http://strategy-engine:8006
PORTFOLIO_OPTIMIZER_URL=http://portfolio-optimizer:8007
API_GATEWAY_URL=http://api-gateway:8000

# --- Frontend ---
NEXT_PUBLIC_API_URL=https://stockai.yourdomain.com

# --- Email alerts ---
EMAIL_PROVIDER=smtp
EMAIL_FROM=your@gmail.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your@gmail.com
SMTP_PASSWORD=<gmail-app-password>
```

Then symlink it as the active env file:

```bash
ln -sf .env.production .env
```

---

## 6. Lock down docker-compose for production

In `docker/docker-compose.yml`, bind only the api-gateway and frontend to localhost.
All other service ports should have their `ports:` lines removed entirely.

**Remove** these lines:
```yaml
#   ports: ["5432:5432"]   ← postgres — remove in prod
#   ports: ["6379:6379"]   ← redis
#   ports: ["8001:8001"]   ← market-data
#   ports: ["8002:8002"]   ← technical-analysis
#   ports: ["8003:8003"]   ← ml-prediction
#   ports: ["8004:8004"]   ← ranking-engine
#   ports: ["8005:8005"]   ← signal-engine
#   ports: ["8006:8006"]   ← strategy-engine
#   ports: ["8007:8007"]   ← portfolio-optimizer
```

**Change** api-gateway and frontend to localhost-only:
```yaml
  api-gateway:
    ports: ["127.0.0.1:8000:8000"]

  frontend:
    ports: ["127.0.0.1:3000:3000"]
```

The `127.0.0.1:` prefix means the port is only reachable from the host itself — Nginx proxies to it.

---

## 7. Build images on the server

The frontend image bakes `NEXT_PUBLIC_API_URL` into the JS bundle at build time.
It **must** be passed as a build argument, not just an env var:

```bash
cd ~/stockai

# Build backend services
docker compose -f docker/docker-compose.yml build \
  market-data technical-analysis ml-prediction ranking-engine \
  signal-engine strategy-engine portfolio-optimizer api-gateway

# Build frontend with HTTPS domain baked in
docker compose -f docker/docker-compose.yml build \
  --build-arg NEXT_PUBLIC_API_URL=https://stockai.yourdomain.com \
  frontend
```

This takes 5–15 minutes on first run.

---

## 8. Get SSL certificate (Let's Encrypt)

> ⚠️ **AL2023 has no `sites-available` or `sites-enabled` directories** — that is a
> Debian/Ubuntu convention. All Nginx config on AL2023 goes in `/etc/nginx/conf.d/`.
> Do not run `ln -s /etc/nginx/sites-enabled/...` — it will fail.

First create a temporary HTTP-only config so Certbot can verify domain ownership:

```bash
sudo tee /etc/nginx/conf.d/stockai.conf > /dev/null <<'EOF'
server {
    listen 80;
    server_name stockai.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:3000;
    }
}
EOF

sudo nginx -t && sudo systemctl reload nginx
```

Start the services so port 3000 is listening (Certbot needs a live server on 80):

```bash
cd ~/stockai
docker compose -f docker/docker-compose.yml --env-file .env up -d
```

Now get the certificate:

```bash
sudo certbot --nginx -d stockai.yourdomain.com
```

Certbot will ask for your email address (for renewal notices) and automatically patch `/etc/nginx/conf.d/stockai.conf` with HTTPS settings.

Verify:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

---

## 9. Final Nginx config with HTTPS + API proxy

Replace the Certbot-generated config with the full production version:

```bash
sudo tee /etc/nginx/conf.d/stockai.conf > /dev/null <<'EOF'
# Redirect HTTP → HTTPS
server {
    listen 80;
    server_name stockai.yourdomain.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name stockai.yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/stockai.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/stockai.yourdomain.com/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    # Research report generation calls Claude (up to 90s) — must come before /api/ catch-all
    location /api/research/ {
        rewrite ^/api/(.*) /$1 break;
        proxy_pass         http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_read_timeout 200s;
        proxy_send_timeout 200s;
    }

    # AI endpoints can take up to 120s — must be listed before the catch-all
    location /api/ai/ {
        proxy_pass         http://127.0.0.1:3000;
        proxy_read_timeout 180s;
        proxy_send_timeout 180s;
    }

    # Ingest endpoint can take up to 120s
    location /api/admin/ingest {
        proxy_pass         http://127.0.0.1:3000;
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
    }

    # Frontend (Next.js) — keep default short; AI/ingest handled above
    location / {
        proxy_pass         http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade $http_upgrade;
        proxy_set_header   Connection 'upgrade';
        proxy_set_header   Host $host;
        proxy_cache_bypass $http_upgrade;
        proxy_read_timeout 30s;
    }

    # API gateway — direct browser API calls
    location /api/ {
        rewrite ^/api/(.*) /$1 break;
        proxy_pass         http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_read_timeout 150s;
    }
}
EOF

sudo nginx -t && sudo systemctl reload nginx
```

---

## 10. Seed the database (first run only)

```bash
# Wait for all containers to be healthy
docker compose -f docker/docker-compose.yml ps

# Seed stock universe
curl -s -X POST http://localhost:8000/admin/seed | python3 -m json.tool

# Ingest initial price data
curl -s -X POST http://localhost:8000/admin/ingest \
  -H "Content-Type: application/json" \
  -d '{"symbols": ["AAPL","TSLA","NVDA","0700.HK"]}' | python3 -m json.tool
```

### Run DB migrations

Run once after first deploy (and after any upgrade of an existing instance):

```bash
# From project root on the server
bash scripts/migrations/run_migrations.sh
```

Migrations are idempotent — safe to re-run. On a fresh instance, migrations 001 and
002 are no-ops (columns already created by `create_all()`). Migration 003 (portfolio
config fix) must run on every instance.

> **What each migration does:**
> - `001` — adds `sector` column to `paper_trades` (back-fills from stocks table)
> - `002` — adds `signal_at_exit_id`/`signal_at_exit_type` to `paper_trades`
> - `003` — corrects paper portfolio config scale values: `risk_per_trade_pct=0.01`,
>   `max_position_pct=0.10`, `max_hold_days=60`

Visit `https://stockai.yourdomain.com` — it should load over HTTPS.

---

## 11. Auto-renew SSL certificate

With the pip install, add a cron job for auto-renewal:

```bash
echo "0 0,12 * * * root /opt/certbot/bin/certbot renew -q" \
  | sudo tee /etc/cron.d/certbot-renew

# Test renewal dry-run
sudo certbot renew --dry-run
```

Certificates renew automatically every 60 days.

---

## 12. Auto-start on reboot

```bash
sudo tee /etc/systemd/system/stockai.service > /dev/null <<'EOF'
[Unit]
Description=StockAI Docker Compose
Requires=docker.service
After=docker.service network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/home/ec2-user/stockai
ExecStart=docker compose -f docker/docker-compose.yml --env-file .env up -d
ExecStop=docker compose -f docker/docker-compose.yml down
TimeoutStartSec=300
User=ec2-user

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable stockai
```

Test it:

```bash
sudo systemctl start stockai
sudo systemctl status stockai
```

---

## 13. Add swap space (prevent OOM on builds)

The t3.medium has 4 GB RAM. Docker image builds and Next.js compilation can push past that limit, causing the instance to freeze or become unreachable. A 2 GB swap file acts as a safety valve.

```bash
bash ~/stockai/scripts/setup_swap.sh
```

The script is idempotent — run it once after provisioning and it will persist across reboots. It also sets `vm.swappiness=10` so the kernel only uses swap under real memory pressure, not as a first resort.

Verify swap is active:

```bash
free -h
# Swap: 2.0Gi should show up
```

---

## 14. Automated database backups

The script at `scripts/backup_db.sh` dumps the PostgreSQL database from the running Docker container, compresses it with gzip, uploads to S3, and automatically prunes copies older than 30 days.

### One-time setup

**1. Create an S3 bucket for backups** (in the AWS console or CLI):
```bash
aws s3 mb s3://your-stockai-backups --region ap-east-1
```

**2. Attach an IAM role to the EC2 instance** with this policy (replace `your-stockai-backups`):
```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject", "s3:ListBucket"],
    "Resource": [
      "arn:aws:s3:::your-stockai-backups",
      "arn:aws:s3:::your-stockai-backups/*"
    ]
  }]
}
```

Using an IAM role means no credentials file on disk — the instance automatically gets temporary tokens.

**3. Install AWS CLI on the instance:**
```bash
sudo dnf install -y awscli
```

**4. Set your bucket name in the script:**
```bash
sed -i 's/your-s3-backup-bucket/your-stockai-backups/' ~/stockai/scripts/backup_db.sh
```

**5. Test it manually:**
```bash
bash ~/stockai/scripts/backup_db.sh
# Should print: Backup complete.
```

**6. Schedule nightly at 02:00 UTC:**
```bash
echo "0 2 * * * ec2-user /home/ec2-user/stockai/scripts/backup_db.sh >> /var/log/stockai-backup.log 2>&1" \
  | sudo tee /etc/cron.d/stockai-backup
```

### Restoring from a backup

```bash
# Download a backup
aws s3 cp s3://your-stockai-backups/backups/stockai_db_20260531_020000.sql.gz /tmp/restore.sql.gz

# Decompress and restore into the running postgres container
gunzip -c /tmp/restore.sql.gz \
  | docker exec -i stockai-postgres-1 psql -U stockai stockai
```

---

## 15. Useful maintenance commands

```bash
# View all service logs
docker compose -f docker/docker-compose.yml logs -f

# View one service
docker compose -f docker/docker-compose.yml logs -f market-data

# Restart a single service
docker compose -f docker/docker-compose.yml restart market-data

# Rebuild and redeploy frontend after code changes
# (compose handles stop + remove + restart automatically)
docker compose -f docker/docker-compose.yml build \
  --build-arg NEXT_PUBLIC_API_URL=https://stockai.yourdomain.com frontend
docker compose -f docker/docker-compose.yml up -d frontend

# If you get "container name already in use" (happens when container was started
# manually with docker run instead of via compose):
docker stop stockai-frontend-1 && docker rm stockai-frontend-1
docker compose -f docker/docker-compose.yml up -d frontend

# Check disk usage
docker system df

# Free up unused images/containers/build cache
docker system prune -f

# Check SELinux denials (if Nginx shows 502)
sudo ausearch -m avc -ts recent | grep nginx
sudo setsebool -P httpd_can_network_connect 1   # fix if denied
```

---

## Common issues on Amazon Linux 2023

| Symptom | Cause | Fix |
|---|---|---|
| Nginx returns `502 Bad Gateway` | SELinux blocks proxy | `sudo setsebool -P httpd_can_network_connect 1` |
| `docker compose` not found | Compose plugin not installed | Re-run step 3 compose install |
| `compose build requires buildx 0.17.0 or later` | AL2023 ships old Buildx | Install latest Buildx — see step 3 |
| `certbot: command not found` | symlink missing | `sudo ln -s /opt/certbot/bin/certbot /usr/bin/certbot` |
| `ln: failed to create symbolic link '/etc/nginx/sites-enabled/'` | Ubuntu-style path doesn't exist on AL2023 | Use `/etc/nginx/conf.d/stockai.conf` instead — see step 8 |
| `pip install --upgrade` error "must give at least one requirement" | Command was truncated | Full command: `sudo /opt/certbot/bin/pip install --upgrade pip` |
| `python3 -m pip` fails with "externally-managed-environment" | AL2023 system Python is managed by dnf | Use `/opt/certbot/bin/pip` inside the venv only |
| Certbot fails "no valid ACME challenge" | Domain not pointing to server yet | Check DNS A record, wait propagation |
| Container exits on startup | `.env` not found | Ensure `ln -sf .env.production .env` was run |
| `Failed at step CHDIR: No such file or directory` | Wrong `WorkingDirectory` in systemd service | Set `WorkingDirectory=/home/ec2-user/stockai` (not `/home/ubuntu/...`) |

---

## Cost estimate

| Resource | Monthly cost |
|---|---|
| t3.medium on-demand | ~$30 |
| 30 GB gp3 EBS | ~$2.40 |
| Elastic IP (while running) | Free |
| Data transfer out (~10 GB) | ~$0.90 |
| Let's Encrypt SSL | **Free** |
| **Total** | **~$33/mo** |

Switch to a **t3.medium Reserved Instance (1-year)** to cut to ~$20/mo.

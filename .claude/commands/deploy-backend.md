# Deploy Backend Service

Deploy a changed Python file to a running container and restart it.

## Usage
`/project:deploy-backend` — then specify which service and file in your message, or Claude will ask.

## What this does

1. Commits any staged changes to `prod` branch
2. Pushes to remote
3. SSHs to EC2 and `git pull origin prod`
4. `docker cp` the changed file(s) to the correct container path
5. `docker restart <container>`
6. Tails logs briefly to confirm a clean start

## Container names and paths

| Service | Container | Source dir | Container path |
|---|---|---|---|
| market-data | `stockai-market-data-1` | `services/market-data/src/` | `/app/src/` |
| signal-engine | `stockai-signal-engine-1` | `services/signal-engine/src/` | `/app/src/` |
| decision-engine | `stockai-decision-engine-1` | `services/decision-engine/src/` | `/app/src/` |
| ml-prediction | `stockai-ml-prediction-1` | `services/ml-prediction/src/` | `/app/src/` |
| research-engine | `stockai-research-engine-1` | `services/research-engine/src/` | `/app/src/` |
| api-gateway | `stockai-api-gateway-1` | `services/api-gateway/src/` | `/app/src/` |
| ranking-engine | `stockai-ranking-engine-1` | `services/ranking-engine/src/` | `/app/src/` |
| signal-engine | `stockai-signal-engine-1` | `services/signal-engine/src/` | `/app/src/` |
| strategy-engine | `stockai-strategy-engine-1` | `services/strategy-engine/src/` | `/app/src/` |
| technical-analysis | `stockai-technical-analysis-1` | `services/technical-analysis/src/` | `/app/src/` |
| portfolio-optimizer | `stockai-portfolio-optimizer-1` | `services/portfolio-optimizer/src/` | `/app/src/` |

**Shared modules** (used by multiple services):
- `shared/db/models.py` → `/app/shared/db/models.py` (NOT `/app/src/db/`)
- `shared/common/*.py` → `/app/shared/common/`

## EC2 connection
- Host: `18.205.121.71`, key: `~/Documents/Stock_AI/lausing.pem`, user: `ec2-user`
- Project root on EC2: `/home/ec2-user/Stock_Trading_App`

## Template SSH commands

```bash
# Pull latest
ssh -i ~/Documents/Stock_AI/lausing.pem ec2-user@18.205.121.71 \
  "cd /home/ec2-user/Stock_Trading_App && git pull origin prod"

# Copy file + restart (example: market-data scheduler)
ssh -i ~/Documents/Stock_AI/lausing.pem ec2-user@18.205.121.71 \
  "docker cp /home/ec2-user/Stock_Trading_App/services/market-data/src/services/scheduler.py \
   stockai-market-data-1:/app/src/services/scheduler.py && \
   docker restart stockai-market-data-1"

# Tail logs to confirm clean start
ssh -i ~/Documents/Stock_AI/lausing.pem ec2-user@18.205.121.71 \
  "docker logs stockai-market-data-1 --since 30s -f" 
```

## Security invariants
- Never commit `.env.production` — it is gitignored
- Never embed real credential values in SSH command strings
- JWT secret and DB credentials live in EC2 `.env` only

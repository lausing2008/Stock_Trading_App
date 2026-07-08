# Deploy Frontend

Rebuild and redeploy the Next.js frontend. This is the most error-prone deployment step — follow exactly.

## Usage
`/project:deploy-frontend` — rebuilds and recreates the frontend container on EC2.

## Critical rules (from CLAUDE.md)

- **ALWAYS use `DOCKER_BUILDKIT=0`** — BuildKit silently serves cached layers even with `--no-cache`, producing a stale image. Never use `docker compose build`.
- **`frontend/.env.production` must exist on EC2** before building. It is gitignored and must not be committed. Contains: `API_GATEWAY_URL=http://api-gateway:8000`
- Run the build **synchronously** (no `run_in_background: true`) — an SSH timeout on a background build leaves the container in an unknown state.

## Steps

1. Commit frontend changes on `prod`, push to remote
2. SSH to EC2 and `git pull origin prod`
3. Verify `frontend/.env.production` exists on EC2
4. Run legacy build (synchronously):
   ```bash
   ssh -i ~/Documents/Stock_AI/lausing.pem ec2-user@18.205.121.71 \
     "cd /home/ec2-user/Stock_Trading_App && \
      DOCKER_BUILDKIT=0 docker build --no-cache -f frontend/Dockerfile -t stockai-frontend:latest . 2>&1 | tail -8"
   ```
5. Recreate the container:
   ```bash
   ssh -i ~/Documents/Stock_AI/lausing.pem ec2-user@18.205.121.71 \
     "docker compose -f /home/ec2-user/Stock_Trading_App/docker/docker-compose.yml up -d --force-recreate frontend"
   ```
6. Confirm the container started: look for `stockai-frontend-1 Started` in output.

## Common failure: improvements.tsx new tiers not showing

If a new tier was added to `improvements.tsx` but items don't appear after rebuild:
- The render loop is now automatic (driven by `TIER_LABEL` keys) — no hardcoded array to update.
- Check that the new tier number was added to the `type Tier` union, `TIER_LABEL`, AND `TIER_COLOR`.
- All three `Record<Tier, ...>` objects must include every union member or TypeScript will error.

## Post-deploy check

```bash
# Confirm frontend is up and serving
curl -s -o /dev/null -w "%{http_code}" https://lausing.com/
# Should return 200
```

# Deployment Guide — AWS ECS Fargate

Target: Each microservice runs as its own ECS Fargate service behind a shared
Application Load Balancer. Postgres lives on RDS, Redis on ElastiCache, ML
model artifacts on S3 (next iteration) or EFS.

## Prerequisites

- AWS account with admin creds for Terraform apply
- Terraform ≥ 1.6
- Docker + AWS CLI v2
- An S3 bucket `stockai-tfstate` + DynamoDB table `stockai-tflock` for TF state locking (create once manually)

## 1. Provision infrastructure

```bash
cd infra/terraform/envs/dev
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars — set db_password

terraform init
terraform apply
```

Terraform creates:
- VPC with public + private subnets across 2 AZs, NAT gateway
- ECR repos for each of the 8 services
- ECS cluster (`stockai-dev`) with one Fargate service per repo
- Application Load Balancer routing path-prefixes to each service
- RDS Postgres 16 (t4g.micro) in private subnets
- ElastiCache Redis 7 (t4g.micro) in private subnets
- CloudWatch log groups per service (14-day retention)
- IAM task execution role

Capture the ALB DNS from `terraform output alb_dns`.

## 2. Build and push images

```bash
# Login to ECR
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <account>.dkr.ecr.us-east-1.amazonaws.com

# Build + push every service
for svc in market-data technical-analysis ml-prediction ranking-engine signal-engine strategy-engine portfolio-optimizer api-gateway; do
  docker build -f services/$svc/Dockerfile -t stockai/$svc:latest .
  docker tag stockai/$svc:latest <account>.dkr.ecr.us-east-1.amazonaws.com/stockai/$svc:latest
  docker push <account>.dkr.ecr.us-east-1.amazonaws.com/stockai/$svc:latest
done
```

Services pull `:latest` on next task start. Force a rolling deploy:
```bash
aws ecs update-service --cluster stockai-dev --service api-gateway --force-new-deployment
```

## 3. Bootstrap the database

One-shot: run a task override that seeds the universe.
```bash
aws ecs run-task --cluster stockai-dev --launch-type FARGATE \
  --task-definition stockai-dev-market-data \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-xxx],securityGroups=[sg-xxx],assignPublicIp=DISABLED}" \
  --overrides '{"containerOverrides":[{"name":"market-data","command":["python","-m","src.services.seed_universe"]}]}'
```

## 4. Frontend

The frontend ships as the same Docker image. For prod you have three options:
1. Run it as a 9th ECS service behind the same ALB (simplest — add to Terraform).
2. Static-export + CloudFront + S3 (cheapest).
3. Vercel (zero-ops).

Set `NEXT_PUBLIC_API_URL` to the ALB DNS (or your Route53 alias).

## 5. Observability

- CloudWatch Logs — each service streams JSON-structured logs.
- Container Insights — enabled on the ECS cluster.
- Health checks — ALB + ECS both hit `/health`; unhealthy tasks are replaced automatically.

## 6. Secrets

Move `db_password` and API keys to AWS Secrets Manager or SSM Parameter Store:

```hcl
# modules/ecs — add to container_definitions
secrets = [
  { name = "ALPHA_VANTAGE_API_KEY", valueFrom = aws_ssm_parameter.alpha_vantage.arn }
]
```

Grant `secretsmanager:GetSecretValue` or `ssm:GetParameters` to the task role.

## 7. Scaling

```hcl
resource "aws_appautoscaling_target" "svc" {
  max_capacity       = 4
  min_capacity       = 1
  resource_id        = "service/${aws_ecs_cluster.this.name}/${each.key}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}
```

ML and market-data typically want 2+ tasks; lightweight services stay at 1.

## 8. Teardown

```bash
terraform destroy
```

RDS has `skip_final_snapshot = true` and `deletion_protection = false` for dev convenience — flip both for staging/prod.

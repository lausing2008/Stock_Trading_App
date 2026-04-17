terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }
  backend "s3" {
    bucket         = "stockai-tfstate"  # create manually before first apply
    key            = "dev/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "stockai-tflock"
    encrypt        = true
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project     = "stockai"
      Environment = "dev"
      ManagedBy   = "terraform"
    }
  }
}

module "vpc" {
  source     = "../../modules/vpc"
  name       = "stockai-dev"
  cidr_block = "10.40.0.0/16"
}

module "ecr" {
  source   = "../../modules/ecr"
  services = local.services
}

module "rds" {
  source       = "../../modules/rds"
  name         = "stockai-dev"
  vpc_id       = module.vpc.vpc_id
  subnet_ids   = module.vpc.private_subnet_ids
  db_username  = var.db_username
  db_password  = var.db_password
  db_name      = var.db_name
  instance_class = "db.t4g.micro"
  ingress_sg_ids = [module.ecs.services_sg_id]
}

module "redis" {
  source     = "../../modules/redis"
  name       = "stockai-dev"
  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnet_ids
  ingress_sg_ids = [module.ecs.services_sg_id]
}

module "alb" {
  source            = "../../modules/alb"
  name              = "stockai-dev"
  vpc_id            = module.vpc.vpc_id
  public_subnet_ids = module.vpc.public_subnet_ids
}

module "ecs" {
  source             = "../../modules/ecs"
  name               = "stockai-dev"
  vpc_id             = module.vpc.vpc_id
  private_subnet_ids = module.vpc.private_subnet_ids
  services           = local.services
  ecr_urls           = module.ecr.repository_urls
  alb_listener_arn   = module.alb.listener_arn
  alb_sg_id          = module.alb.alb_sg_id
  database_url       = "postgresql+psycopg2://${var.db_username}:${var.db_password}@${module.rds.endpoint}/${var.db_name}"
  redis_url          = "redis://${module.redis.endpoint}:6379/0"
}

locals {
  services = {
    "api-gateway"         = { port = 8000, cpu = 256, memory = 512, path = "/*",               priority = 100 }
    "market-data"         = { port = 8001, cpu = 512, memory = 1024, path = "/market-data/*",  priority = 110 }
    "technical-analysis"  = { port = 8002, cpu = 512, memory = 1024, path = "/ta-svc/*",       priority = 120 }
    "ml-prediction"       = { port = 8003, cpu = 1024, memory = 2048, path = "/ml-svc/*",      priority = 130 }
    "ranking-engine"      = { port = 8004, cpu = 256, memory = 512, path = "/ranking-svc/*",   priority = 140 }
    "signal-engine"       = { port = 8005, cpu = 256, memory = 512, path = "/signal-svc/*",    priority = 150 }
    "strategy-engine"     = { port = 8006, cpu = 512, memory = 1024, path = "/strategy-svc/*", priority = 160 }
    "portfolio-optimizer" = { port = 8007, cpu = 512, memory = 1024, path = "/portfolio-svc/*",priority = 170 }
  }
}

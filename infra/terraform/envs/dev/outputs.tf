output "alb_dns" {
  value       = module.alb.alb_dns_name
  description = "Public entry point — point frontend NEXT_PUBLIC_API_URL at this"
}

output "ecr_repos" {
  value = module.ecr.repository_urls
}

output "pipeline_bucket" {
  description = "S3 bucket name shared by all pipelines in this workspace."
  value       = module.workspace.pipeline_bucket
}

output "runner_image" {
  description = "ECR URI for the transform runner image (use as runner_image in pipeline modules)."
  value       = "${aws_ecr_repository.runner.repository_url}:latest"
}

output "system_catalog" {
  description = "Workspace-owned observability catalog (ADR-016). Pipelines read this via remote_state to point runs/node_runs/tables writes at the workspace's system Glue DB."
  value       = module.workspace.system_catalog
}

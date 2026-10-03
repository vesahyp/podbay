terraform {
  required_providers {
    aws = { source = "hashicorp/aws" }
  }
  backend "local" {}
}

provider "aws" {
  # Workspace-wide default tags. Every AWS resource created by every
  # module under this workspace gets these — Cost Explorer can then
  # group spend by clavesa:workspace once the keys are activated as
  # cost-allocation tags in Billing. Per-resource tags merge on top.
  default_tags {
    tags = {
      "clavesa:workspace"  = var.workspace_name
      "clavesa:managed-by" = "clavesa"
    }
  }
}

module "workspace" {
  # Terraform 1.x rejects bare module paths without a leading "./"
  # prefix as "ambiguous registry / local" — v1.1.6 fix. Older
  # workspaces still parse the bare form via the embedded-form
  # heuristic in hclparser, and clavesa workspace upgrade rewrites it
  # on next run.
  source         = "./.clavesa/modules/v2.20.0/workspace/aws"
  workspace_name = var.workspace_name
  system_catalog = var.system_catalog
}

resource "aws_ecr_repository" "runner" {
  name         = "clavesa-${var.workspace_name}/transform-runner"
  force_delete = true
  tags         = { "clavesa:workspace" = var.workspace_name }
}

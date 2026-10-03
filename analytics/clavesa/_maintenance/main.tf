# clavesa maintenance pipeline (opt-in, GH #53).
#
# OPTIMIZEs and VACUUMs the workspace system bookkeeping tables under
# _system/pipelines (node_runs, runs, tables, column_stats) so their Delta
# transaction log and small-file count stay bounded. Compaction is a
# scheduled, observable pipeline instead of hidden on the per-node write path.
#
# This pipeline is scaffolded but NOT deployed. Enable it with
# 'clavesa pipeline deploy _maintenance' (it runs on the daily schedule
# below), or delete this directory to opt out. The runner already has S3 +
# Glue write to the workspace-owned system catalog, so no extra IAM is needed;
# it touches only the system tables, never another pipeline's outputs.
terraform {
  required_providers {
    aws = { source = "hashicorp/aws" }
  }
}

data "terraform_remote_state" "workspace" {
  backend = "local"
  config  = { path = "${path.module}/../terraform.tfstate" }
}

module "compact" {
  source         = "../.clavesa/modules/v2.20.0/transform/aws"
  pipeline_name  = var.pipeline_name
  name           = "compact"
  bucket         = data.terraform_remote_state.workspace.outputs.pipeline_bucket
  catalog        = "clavesa_podbay_traffic"
  schema         = var.schema
  system_catalog = "clavesa_podbay_traffic_system"

  language           = "python"
  python             = file("transforms/compact.py")
  inputs             = {}
  output_definitions = {}
}

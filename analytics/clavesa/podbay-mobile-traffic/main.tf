# clavesa pipeline
terraform {
  required_providers {
    aws = { source = "hashicorp/aws" }
  }
}

data "terraform_remote_state" "workspace" {
  backend = "local"
  config  = { path = "${path.module}/../terraform.tfstate" }
}
module "events" {
  source        = "../.clavesa/modules/v2.21.1/transform/aws"
  name          = "events"
  pipeline_name = var.pipeline_name

  bucket = data.terraform_remote_state.workspace.outputs.pipeline_bucket

  catalog = "clavesa_podbay_traffic"
  schema  = var.schema

  system_catalog     = "clavesa_podbay_traffic_system"
  language           = "python"
  output_definitions = { default = {} }

  python = file("transforms/events.py")

  # The phone app's CloudFront logs: same bucket as the landing page, its
  # own prefix (infra/mobile.tf logging_config). The bucket and prefix that
  # count are the ones in .clavesa/sources/mobile_logs.json: clavesa takes
  # them from the registry spec spec_name names and ignores the ones here
  # (clavesa-dev TODO.md, "inline source_inputs ... ignored"). With
  # podbay-traffic's "logs" this pipeline read the landing page's logs.
  source_inputs = { logs = { bucket = "podbay-cloudfront-logs", format = "tsv", prefix = "mobile/", spec_name = "mobile_logs" } }

}

module "app_json" {
  source        = "../.clavesa/modules/v2.21.1/transform/aws"
  name          = "app_json"
  pipeline_name = var.pipeline_name

  bucket = data.terraform_remote_state.workspace.outputs.pipeline_bucket

  catalog = "clavesa_podbay_traffic"
  schema  = var.schema

  system_catalog     = "clavesa_podbay_traffic_system"
  language           = "sql"
  # Private: the state bucket, read by the app through GET /api/stats behind
  # the sign-in.
  output_definitions = { default = { format = "json", path = "s3://podbay-mobile-state/stats/analytics.json", content_type = "application/json", cache_control = "no-cache" } }

  sql = file("sql/app_json.sql")

  inputs = { ev = module.events.outputs["default"] }
}


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
  source        = "../.clavesa/modules/v2.20.0/transform/aws"
  name          = "events"
  pipeline_name = var.pipeline_name

  bucket = data.terraform_remote_state.workspace.outputs.pipeline_bucket

  catalog = "clavesa_podbay_traffic"
  schema  = var.schema

  system_catalog     = "clavesa_podbay_traffic_system"
  language           = "python"
  output_definitions = { default = {} }

  python = file("transforms/events.py")

  source_inputs = { logs = { bucket = "podbay-cloudfront-logs", format = "tsv", prefix = "cloudfront/", spec_name = "logs" } }


}

module "daily" {
  source        = "../.clavesa/modules/v2.20.0/transform/aws"
  name          = "daily"
  pipeline_name = var.pipeline_name

  bucket = data.terraform_remote_state.workspace.outputs.pipeline_bucket

  catalog = "clavesa_podbay_traffic"
  schema  = var.schema

  system_catalog     = "clavesa_podbay_traffic_system"
  language           = "sql"
  output_definitions = { default = {} }

  sql = file("sql/daily.sql")

  inputs = { ev = module.events.outputs["default"] }

}

module "breakdowns" {
  source        = "../.clavesa/modules/v2.20.0/transform/aws"
  name          = "breakdowns"
  pipeline_name = var.pipeline_name

  bucket = data.terraform_remote_state.workspace.outputs.pipeline_bucket

  catalog = "clavesa_podbay_traffic"
  schema  = var.schema

  system_catalog     = "clavesa_podbay_traffic_system"
  language           = "sql"
  output_definitions = { default = {} }

  sql = file("sql/breakdowns.sql")

  inputs = { ev = module.events.outputs["default"] }

}

module "rollup" {
  source        = "../.clavesa/modules/v2.20.0/transform/aws"
  name          = "rollup"
  pipeline_name = var.pipeline_name

  bucket = data.terraform_remote_state.workspace.outputs.pipeline_bucket

  catalog = "clavesa_podbay_traffic"
  schema  = var.schema

  system_catalog     = "clavesa_podbay_traffic_system"
  language           = "sql"
  output_definitions = { default = {} }

  sql = file("sql/rollup.sql")

  inputs = { d = module.daily.outputs["default"] }

}

module "stats_json" {
  source        = "../.clavesa/modules/v2.20.0/transform/aws"
  name          = "stats_json"
  pipeline_name = var.pipeline_name

  bucket = data.terraform_remote_state.workspace.outputs.pipeline_bucket

  catalog = "clavesa_podbay_traffic"
  schema  = var.schema

  system_catalog     = "clavesa_podbay_traffic_system"
  language           = "sql"
  output_definitions = { default = { format = "json", path = "s3://podbay-site-content/data/analytics.json", content_type = "application/json", cache_control = "public, max-age=300" } }

  sql = file("sql/stats_json.sql")

  inputs = {
    bd = module.breakdowns.outputs["default"]
    d  = module.daily.outputs["default"]
    r  = module.rollup.outputs["default"]
  }



}


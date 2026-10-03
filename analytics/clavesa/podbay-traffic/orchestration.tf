# clavesa orchestration — managed by clavesa, do not edit by hand.
# Re-generated automatically when nodes or edges change.
#
# To detach from clavesa: delete this header, remove the
# _clavesa_sidecar/ directory's clavesa-only Python lambdas if you
# replace them, and own the file as standard Terraform.

module "src_logs" {
  source = "../.clavesa/modules/v2.20.0/source/aws"

  pipeline_name = var.pipeline_name
  name          = "logs"
  bucket        = "podbay-cloudfront-logs"
  prefix        = "cloudfront/"
  format        = "tsv"
}

# Shared resource tags — every clavesa-owned AWS resource carries the
# pipeline name + a `clavesa:type` label so tag-cost reporting can
# attribute spend per pipeline.
locals {
  clavesa_tags = {
    "clavesa:pipeline" = var.pipeline_name
    "clavesa:type"     = "orchestration"
  }
}

data "aws_caller_identity" "clavesa_owner" {}

# Glue catalog database — per-pipeline output namespace (ADR-016).
# location_uri is required for Spark's Glue Hive Client to resolve
# the DB's warehouse path on saveAsTable. Without it Hive trips
# `IllegalArgumentException: Can not create a Path from an empty string`.
resource "aws_glue_catalog_database" "pipeline" {
  name         = "clavesa_podbay_traffic__${replace(var.schema, "-", "_")}"
  description  = "Clavesa pipeline output tables — ${var.pipeline_name}"
  location_uri = "s3://${data.terraform_remote_state.workspace.outputs.pipeline_bucket}/${var.pipeline_name}/_warehouse/clavesa_podbay_traffic__${replace(var.schema, "-", "_")}.db"
  tags         = local.clavesa_tags
}

# Lake Formation grants — pipeline runner role on this DB and its tables.
# Mirrors the IAM grants in aws_iam_role_policy.pipeline_runner so LF
# doesn't override IAM on LF-gated accounts (GH #1).
resource "aws_lakeformation_permissions" "pipeline_runner_db" {
  principal   = aws_iam_role.pipeline_runner.arn
  permissions = ["DESCRIBE", "CREATE_TABLE", "ALTER", "DROP"]
  database {
    name = aws_glue_catalog_database.pipeline.name
  }
}

resource "aws_lakeformation_permissions" "pipeline_runner_tables" {
  principal   = aws_iam_role.pipeline_runner.arn
  permissions = ["SELECT", "INSERT", "DELETE", "ALTER", "DROP", "DESCRIBE"]
  table {
    database_name = aws_glue_catalog_database.pipeline.name
    wildcard      = true
  }
}

resource "aws_lakeformation_permissions" "owner_db" {
  principal   = data.aws_caller_identity.clavesa_owner.arn
  permissions = ["DESCRIBE"]
  database {
    name = aws_glue_catalog_database.pipeline.name
  }
}

resource "aws_lakeformation_permissions" "owner_tables" {
  principal   = data.aws_caller_identity.clavesa_owner.arn
  permissions = ["SELECT", "DESCRIBE"]
  table {
    database_name = aws_glue_catalog_database.pipeline.name
    wildcard      = true
  }
}

# CloudWatch log group — Step Functions execution logging (90-day retention).
resource "aws_cloudwatch_log_group" "sfn_logs" {
  name              = "/clavesa/${var.pipeline_name}/sfn"
  retention_in_days = 90
  tags              = local.clavesa_tags
}

# IAM execution role for the Step Functions state machine.
data "aws_iam_policy_document" "sfn_assume" {
  statement {
    sid     = "StepFunctionsTrust"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sfn_exec" {
  name               = "clavesa-${var.pipeline_name}-orchestration"
  assume_role_policy = data.aws_iam_policy_document.sfn_assume.json
  tags               = local.clavesa_tags
}

resource "aws_iam_role_policy" "sfn_exec_policy" {
  name = "clavesa-${var.pipeline_name}-orchestration"
  role = aws_iam_role.sfn_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Sid = "LambdaInvoke", Effect = "Allow", Action = ["lambda:InvokeFunction"], Resource = [aws_lambda_function.pipeline_runner.arn] },
      { Sid = "CloudWatchLogsDelivery", Effect = "Allow", Action = [
          "logs:CreateLogDelivery", "logs:GetLogDelivery", "logs:UpdateLogDelivery",
          "logs:DeleteLogDelivery", "logs:ListLogDeliveries", "logs:PutResourcePolicy",
          "logs:DescribeResourcePolicies", "logs:DescribeLogGroups",
      ], Resource = "*" },
    ]
  })
}

# Per-pipeline runner Lambda — image-based, hosts runner.pipeline_handler.
# v2.2.0: one Lambda per pipeline runs every transform sequentially in
# one Spark session via the runner's `_SPARK` singleton, mirroring the
# local bundle execution Phase A landed (ADR-014 local-cloud parity).
locals {
  pipeline_runner_image_match = regex("^([^:]+):(.+)$", data.terraform_remote_state.workspace.outputs.runner_image)
  pipeline_runner_repo_uri    = local.pipeline_runner_image_match[0]
  pipeline_runner_tag         = local.pipeline_runner_image_match[1]
  pipeline_runner_repo_name   = regex("^[^/]+/(.+)$", local.pipeline_runner_repo_uri)[0]
}

data "aws_ecr_image" "pipeline_runner" {
  repository_name = local.pipeline_runner_repo_name
  image_tag       = local.pipeline_runner_tag
}

data "aws_iam_policy_document" "pipeline_runner_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "pipeline_runner" {
  name               = "clavesa-${var.pipeline_name}-runner"
  assume_role_policy = data.aws_iam_policy_document.pipeline_runner_assume.json
  tags               = local.clavesa_tags
}

resource "aws_iam_role_policy" "pipeline_runner" {
  name = "clavesa-${var.pipeline_name}-runner"
  role = aws_iam_role.pipeline_runner.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Sid = "S3Read", Effect = "Allow", Action = ["s3:GetObject", "s3:ListBucket", "s3:GetBucketLocation"], Resource = [
          "arn:aws:s3:::${data.terraform_remote_state.workspace.outputs.pipeline_bucket}",
          "arn:aws:s3:::${data.terraform_remote_state.workspace.outputs.pipeline_bucket}/*",
      ]},
      { Sid = "S3ReadExternal", Effect = "Allow", Action = ["s3:GetObject", "s3:ListBucket", "s3:GetBucketLocation"], Resource = [
          "arn:aws:s3:::podbay-cloudfront-logs",
          "arn:aws:s3:::podbay-cloudfront-logs/*",
      ]},
      { Sid = "S3Write", Effect = "Allow", Action = [
          "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts",
      ], Resource = [
          "arn:aws:s3:::${data.terraform_remote_state.workspace.outputs.pipeline_bucket}/${var.pipeline_name}/_warehouse/*",
          "arn:aws:s3:::${data.terraform_remote_state.workspace.outputs.pipeline_bucket}/${var.pipeline_name}/_watermarks/*",
          "arn:aws:s3:::${data.terraform_remote_state.workspace.outputs.pipeline_bucket}/${var.pipeline_name}/*/*",
          "arn:aws:s3:::${data.terraform_remote_state.workspace.outputs.pipeline_bucket}/_system/pipelines/*",
          "arn:aws:s3:::${data.terraform_remote_state.workspace.outputs.pipeline_bucket}/_progress/*",
      ]},
      { Sid = "GlueCatalogRead", Effect = "Allow", Action = [
          "glue:GetDatabase", "glue:GetDatabases", "glue:GetTable", "glue:GetTables",
          "glue:GetPartition", "glue:GetPartitions",
      ], Resource = [
          "arn:aws:glue:*:*:catalog",
          "arn:aws:glue:*:*:database/default",
          "arn:aws:glue:*:*:database/clavesa_podbay_traffic__*",
          "arn:aws:glue:*:*:table/clavesa_podbay_traffic__*/*",
          "arn:aws:glue:*:*:database/clavesa_podbay_traffic_system__*",
          "arn:aws:glue:*:*:table/clavesa_podbay_traffic_system__*/*",
      ]},
      { Sid = "GlueCatalogWrite", Effect = "Allow", Action = [
          "glue:CreateTable", "glue:UpdateTable", "glue:DeleteTable",
          "glue:CreatePartition", "glue:UpdatePartition", "glue:DeletePartition",
          "glue:BatchCreatePartition", "glue:BatchDeletePartition",
      ], Resource = [
          "arn:aws:glue:*:*:catalog",
          "arn:aws:glue:*:*:database/clavesa_podbay_traffic__${replace(var.schema, "-", "_")}",
          "arn:aws:glue:*:*:table/clavesa_podbay_traffic__${replace(var.schema, "-", "_")}/*",
          "arn:aws:glue:*:*:database/clavesa_podbay_traffic_system__pipelines",
          "arn:aws:glue:*:*:table/clavesa_podbay_traffic_system__pipelines/*",
      ]},
      { Sid = "GlueDatabaseCreate", Effect = "Allow", Action = ["glue:CreateDatabase"], Resource = [
          "arn:aws:glue:*:*:catalog",
          "arn:aws:glue:*:*:database/clavesa_podbay_traffic__${replace(var.schema, "-", "_")}",
          "arn:aws:glue:*:*:database/clavesa_podbay_traffic_system__pipelines",
      ]},
      { Sid = "Logs", Effect = "Allow", Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"], Resource = ["arn:aws:logs:*:*:*"] },
      { Sid = "SQSDrain", Effect = "Allow", Action = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"], Resource = [module.src_logs.trigger_queue_arn] },
    ]
  })
}

resource "aws_lambda_function" "pipeline_runner" {
  function_name = "clavesa-${var.pipeline_name}-runner"
  role          = aws_iam_role.pipeline_runner.arn
  package_type  = "Image"
  image_uri     = "${local.pipeline_runner_repo_uri}@${data.aws_ecr_image.pipeline_runner.image_digest}"
  timeout       = 900   # 15min — the Lambda max; one container handles every transform
  memory_size   = 3008  # New AWS accounts default to a 3008MB per-function quota; bump via Service Quotas + raise this if you need more headroom for Spark broadcast tables

  ephemeral_storage {
    size = 10240  # 10 GB — Spark shuffle/spill space; marginal cost ~$0.00026/run vs 512 MB default (GH #43)
  }

  image_config {
    command = ["runner.pipeline_handler"]
  }

  environment {
    variables = {
      CLAVESA_PIPELINE            = var.pipeline_name
      CLAVESA_CATALOG             = "clavesa_podbay_traffic"
      CLAVESA_SCHEMA              = var.schema
      CLAVESA_SYSTEM_CATALOG      = "clavesa_podbay_traffic_system"
      CLAVESA_WAREHOUSE           = "s3://${data.terraform_remote_state.workspace.outputs.pipeline_bucket}/${var.pipeline_name}/_warehouse/"
      CLAVESA_SYSTEM_WAREHOUSE    = "s3://${data.terraform_remote_state.workspace.outputs.pipeline_bucket}/_system/pipelines/"
      CLAVESA_WATERMARKS          = "s3://${data.terraform_remote_state.workspace.outputs.pipeline_bucket}/${var.pipeline_name}/_watermarks/"
      CLAVESA_RUNNER_IMAGE_DIGEST = data.aws_ecr_image.pipeline_runner.image_digest
      CLAVESA_MODULE_VERSION      = "v2.20.0"
    }
  }

  tags = local.clavesa_tags
}

# Step Functions state machine — single Task that hands the full
# ordered transform list to the per-pipeline runner Lambda.
# v2.2.0: was multi-state (one Task per transform); collapsed because
# pipeline_handler now loops transforms inside one Spark session.
resource "aws_sfn_state_machine" "pipeline" {
  name     = "clavesa-${var.pipeline_name}"
  role_arn = aws_iam_role.sfn_exec.arn
  type     = "STANDARD"

  definition = jsonencode({
    Comment = "Clavesa pipeline: ${var.pipeline_name}"
    StartAt = "RunPipeline"
    States = {
      RunPipeline = {
        Type           = "Task"
        Resource       = "arn:aws:states:::lambda:invoke"
        TimeoutSeconds = 900
        Parameters = {
          FunctionName = aws_lambda_function.pipeline_runner.arn
          Payload = {
            _pipeline_run = true
            pipeline      = var.pipeline_name
            transforms = [
              {
                node       = "events"
                language   = "python"
                logic_path = "s3://${data.terraform_remote_state.workspace.outputs.pipeline_bucket}/${var.pipeline_name}/events/_runtime/logic.py"
                inputs     = { logs = { kind = "s3", bucket = "podbay-cloudfront-logs", prefix = "cloudfront/", format = "tsv", queue_url = module.src_logs.trigger_queue_url, read_options = { "comment" = "#", "header" = "false" } } }
                outputs    = { default = "" }
                parents    = []
              },
              {
                node       = "breakdowns"
                language   = "sql"
                logic_path = "s3://${data.terraform_remote_state.workspace.outputs.pipeline_bucket}/${var.pipeline_name}/breakdowns/_runtime/logic.sql"
                inputs     = { ev = "${module.events.outputs["default"].catalog_db}.${module.events.outputs["default"].catalog_table}" }
                outputs    = { default = "" }
                parents    = ["events"]
              },
              {
                node       = "daily"
                language   = "sql"
                logic_path = "s3://${data.terraform_remote_state.workspace.outputs.pipeline_bucket}/${var.pipeline_name}/daily/_runtime/logic.sql"
                inputs     = { ev = "${module.events.outputs["default"].catalog_db}.${module.events.outputs["default"].catalog_table}" }
                outputs    = { default = "" }
                parents    = ["events"]
              },
              {
                node       = "rollup"
                language   = "sql"
                logic_path = "s3://${data.terraform_remote_state.workspace.outputs.pipeline_bucket}/${var.pipeline_name}/rollup/_runtime/logic.sql"
                inputs     = { d = "${module.daily.outputs["default"].catalog_db}.${module.daily.outputs["default"].catalog_table}" }
                outputs    = { default = "" }
                parents    = ["daily"]
              },
              {
                node       = "stats_json"
                language   = "sql"
                logic_path = "s3://${data.terraform_remote_state.workspace.outputs.pipeline_bucket}/${var.pipeline_name}/stats_json/_runtime/logic.sql"
                inputs     = { bd = "${module.breakdowns.outputs["default"].catalog_db}.${module.breakdowns.outputs["default"].catalog_table}", d = "${module.daily.outputs["default"].catalog_db}.${module.daily.outputs["default"].catalog_table}", r = "${module.rollup.outputs["default"].catalog_db}.${module.rollup.outputs["default"].catalog_table}" }
                outputs    = { default = "" }
                parents    = ["breakdowns", "daily", "rollup"]
              },
            ]
            "_sf_execution_arn.$"        = "$$.Execution.Id"
            "_sf_execution_started_at.$" = "$$.Execution.StartTime"
            "_execution_input.$"         = "$$.Execution.Input"
          }
        }
        End = true
      }
    }
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.sfn_logs.arn}:*"
    include_execution_data = true
    level                  = "ERROR"
  }

  tags       = local.clavesa_tags
  depends_on = [aws_iam_role_policy.sfn_exec_policy]
}

# Schedule trigger — EventBridge rule + IAM role to start the state
# machine on a cron / rate cadence. Created only when var.trigger_schedule
# is non-null (count pattern).
resource "aws_cloudwatch_event_rule" "schedule" {
  count = var.trigger_schedule != null ? 1 : 0

  name                = "clavesa-${var.pipeline_name}-schedule"
  description         = "Scheduled trigger for Clavesa pipeline: ${var.pipeline_name}"
  schedule_expression = var.trigger_schedule
  tags                = local.clavesa_tags
}

data "aws_iam_policy_document" "events_assume" {
  count = var.trigger_schedule != null ? 1 : 0

  statement {
    sid     = "EventBridgeTrust"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "events_trigger" {
  count = var.trigger_schedule != null ? 1 : 0

  name               = "clavesa-${var.pipeline_name}-trigger"
  assume_role_policy = data.aws_iam_policy_document.events_assume[0].json
  tags               = local.clavesa_tags
}

resource "aws_iam_role_policy" "events_trigger_policy" {
  count = var.trigger_schedule != null ? 1 : 0

  name = "clavesa-${var.pipeline_name}-trigger"
  role = aws_iam_role.events_trigger[0].id

  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Sid = "StartExecution", Effect = "Allow", Action = ["states:StartExecution"], Resource = [aws_sfn_state_machine.pipeline.arn] }]
  })
}

resource "aws_cloudwatch_event_target" "schedule" {
  count = var.trigger_schedule != null ? 1 : 0

  rule     = aws_cloudwatch_event_rule.schedule[0].name
  arn      = aws_sfn_state_machine.pipeline.arn
  role_arn = aws_iam_role.events_trigger[0].arn

  # _trigger gets read by runs_writer (see runner/runner.py:_RUNS_TRIGGER_VALUES)
  # to label the row in `<system_catalog>__pipelines.runs.trigger`.
  input = jsonencode({
    pipeline = var.pipeline_name
    _trigger = "scheduled"
  })
}

# SQS poller — Lambda that checks source queues on a schedule and starts
# the state machine when any queue has messages.
locals {
  _poller_queue_arns = [module.src_logs.trigger_queue_arn]
  _poller_enabled    = length(local._poller_queue_arns) > 0 && var.trigger_batch_window != null
}

data "archive_file" "poller" {
  count       = local._poller_enabled ? 1 : 0
  type        = "zip"
  source_file = "${path.module}/_clavesa_sidecar/poller.py"
  output_path = "${path.module}/_clavesa_sidecar/poller.zip"
}

data "aws_iam_policy_document" "poller_assume" {
  count = local._poller_enabled ? 1 : 0
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "poller" {
  count              = local._poller_enabled ? 1 : 0
  name               = "clavesa-${var.pipeline_name}-poller"
  assume_role_policy = data.aws_iam_policy_document.poller_assume[0].json
  tags               = local.clavesa_tags
}

resource "aws_iam_role_policy" "poller" {
  count = local._poller_enabled ? 1 : 0
  name  = "clavesa-${var.pipeline_name}-poller"
  role  = aws_iam_role.poller[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Sid = "SQSPoll",  Effect = "Allow", Action = ["sqs:GetQueueAttributes"], Resource = local._poller_queue_arns },
      { Sid = "SFNStart", Effect = "Allow", Action = ["states:StartExecution"], Resource = [aws_sfn_state_machine.pipeline.arn] },
      { Sid = "Logs",     Effect = "Allow", Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"], Resource = ["arn:aws:logs:*:*:*"] },
    ]
  })
}

resource "aws_lambda_function" "poller" {
  count            = local._poller_enabled ? 1 : 0
  function_name    = "clavesa-${var.pipeline_name}-poller"
  role             = aws_iam_role.poller[0].arn
  runtime          = "python3.12"
  handler          = "poller.handler"
  filename         = data.archive_file.poller[0].output_path
  source_code_hash = data.archive_file.poller[0].output_base64sha256
  timeout          = 30

  environment {
    variables = {
      QUEUE_ARNS        = jsonencode(local._poller_queue_arns)
      STATE_MACHINE_ARN = aws_sfn_state_machine.pipeline.arn
    }
  }

  tags = local.clavesa_tags
}

resource "aws_cloudwatch_event_rule" "poller" {
  count               = local._poller_enabled ? 1 : 0
  name                = "clavesa-${var.pipeline_name}-poller"
  description         = "Polls source queues for ${var.pipeline_name} at ${var.trigger_batch_window}"
  schedule_expression = var.trigger_batch_window
  tags                = local.clavesa_tags
}

resource "aws_cloudwatch_event_target" "poller" {
  count = local._poller_enabled ? 1 : 0
  rule  = aws_cloudwatch_event_rule.poller[0].name
  arn   = aws_lambda_function.poller[0].arn
}

resource "aws_lambda_permission" "poller" {
  count         = local._poller_enabled ? 1 : 0
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.poller[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.poller[0].arn
}

# runs_writer — image-based Lambda that appends one row per
# terminal SFN execution to <system_catalog>__pipelines.runs (Delta
# via the runner image's Spark session, ADR-018). Pairs with the
# runner-populated node_runs table; joining on sf_execution_arn
# answers "which nodes ran in this execution?".
locals {
  runs_writer_image_match = regex("^([^:]+):(.+)$", data.terraform_remote_state.workspace.outputs.runner_image)
  runs_writer_repo_uri    = local.runs_writer_image_match[0]
  runs_writer_tag         = local.runs_writer_image_match[1]
  runs_writer_repo_name   = regex("^[^/]+/(.+)$", local.runs_writer_repo_uri)[0]
}

data "aws_ecr_image" "runs_writer" {
  repository_name = local.runs_writer_repo_name
  image_tag       = local.runs_writer_tag
}

data "aws_iam_policy_document" "runs_writer_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "runs_writer" {
  name               = "clavesa-${var.pipeline_name}-runs-writer"
  assume_role_policy = data.aws_iam_policy_document.runs_writer_assume.json
  tags               = local.clavesa_tags
}

resource "aws_iam_role_policy" "runs_writer" {
  name = "clavesa-${var.pipeline_name}-runs-writer"
  role = aws_iam_role.runs_writer.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Sid = "GlueCatalog", Effect = "Allow", Action = ["glue:GetDatabase", "glue:CreateDatabase", "glue:GetTable", "glue:GetTables", "glue:CreateTable", "glue:UpdateTable", "glue:GetPartition", "glue:GetPartitions"], Resource = [
          "arn:aws:glue:*:*:catalog",
          "arn:aws:glue:*:*:database/default",
          "arn:aws:glue:*:*:database/clavesa_podbay_traffic_system__pipelines",
          "arn:aws:glue:*:*:table/clavesa_podbay_traffic_system__pipelines/*",
      ]},
      { Sid = "S3Warehouse", Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket", "s3:GetBucketLocation"], Resource = [
          "arn:aws:s3:::${data.terraform_remote_state.workspace.outputs.pipeline_bucket}",
          "arn:aws:s3:::${data.terraform_remote_state.workspace.outputs.pipeline_bucket}/*",
      ]},
      { Sid = "Logs", Effect = "Allow", Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"], Resource = ["arn:aws:logs:*:*:*"] },
    ]
  })
}

# Lake Formation grants on the workspace system catalog (GH #1).
# References the system DB by name string since it's created in the
# workspace module's state, not this pipeline's.
resource "aws_lakeformation_permissions" "runs_writer_system_db" {
  principal   = aws_iam_role.runs_writer.arn
  permissions = ["DESCRIBE", "CREATE_TABLE", "ALTER"]
  database {
    name = "clavesa_podbay_traffic_system__pipelines"
  }
}

resource "aws_lakeformation_permissions" "runs_writer_system_tables" {
  principal   = aws_iam_role.runs_writer.arn
  permissions = ["SELECT", "INSERT", "ALTER", "DESCRIBE"]
  table {
    database_name = "clavesa_podbay_traffic_system__pipelines"
    wildcard      = true
  }
}

resource "aws_lakeformation_permissions" "pipeline_runner_system_db" {
  principal   = aws_iam_role.pipeline_runner.arn
  permissions = ["DESCRIBE", "CREATE_TABLE", "ALTER"]
  database {
    name = "clavesa_podbay_traffic_system__pipelines"
  }
}

resource "aws_lakeformation_permissions" "pipeline_runner_system_tables" {
  principal   = aws_iam_role.pipeline_runner.arn
  permissions = ["SELECT", "INSERT", "ALTER", "DESCRIBE"]
  table {
    database_name = "clavesa_podbay_traffic_system__pipelines"
    wildcard      = true
  }
}

resource "aws_lakeformation_permissions" "owner_system_db" {
  principal   = data.aws_caller_identity.clavesa_owner.arn
  permissions = ["DESCRIBE"]
  database {
    name = "clavesa_podbay_traffic_system__pipelines"
  }
}

resource "aws_lakeformation_permissions" "owner_system_tables" {
  principal   = data.aws_caller_identity.clavesa_owner.arn
  permissions = ["SELECT", "DESCRIBE"]
  table {
    database_name = "clavesa_podbay_traffic_system__pipelines"
    wildcard      = true
  }
}

resource "aws_lambda_function" "runs_writer" {
  function_name = "clavesa-${var.pipeline_name}-runs-writer"
  role          = aws_iam_role.runs_writer.arn
  package_type  = "Image"
  image_uri     = "${local.runs_writer_repo_uri}@${data.aws_ecr_image.runs_writer.image_digest}"
  timeout       = 120  # cold start ~5s + first Delta write ~30s on a fresh DB
  memory_size   = 1536 # PySpark needs the headroom even for one-row writes

  image_config {
    # Lambda invokes runner.runs_writer_handler — a thin handler in
    # runner.py that builds a runs-table row from the EventBridge
    # `detail` payload and calls _record_run() (Spark + Delta append).
    command = ["runner.runs_writer_handler"]
  }

  environment {
    variables = {
      CLAVESA_PIPELINE         = var.pipeline_name
      CLAVESA_SYSTEM_CATALOG   = "clavesa_podbay_traffic_system"
      CLAVESA_SYSTEM_WAREHOUSE = "s3://${data.terraform_remote_state.workspace.outputs.pipeline_bucket}/_system/pipelines/"
      CLAVESA_WAREHOUSE        = "s3://${data.terraform_remote_state.workspace.outputs.pipeline_bucket}/${var.pipeline_name}/_warehouse/"
    }
  }

  tags = local.clavesa_tags
}

resource "aws_cloudwatch_event_rule" "runs" {
  name        = "clavesa-${var.pipeline_name}-runs"
  description = "Captures terminal Step Functions execution events for ${var.pipeline_name} into the runs Delta table"
  event_pattern = jsonencode({
    source        = ["aws.states"]
    "detail-type" = ["Step Functions Execution Status Change"]
    detail = {
      stateMachineArn = [aws_sfn_state_machine.pipeline.arn]
      status          = ["SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"]
    }
  })
  tags = local.clavesa_tags
}

resource "aws_cloudwatch_event_target" "runs" {
  rule = aws_cloudwatch_event_rule.runs.name
  arn  = aws_lambda_function.runs_writer.arn
}

resource "aws_lambda_permission" "runs" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.runs_writer.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.runs.arn
}


########################################################################
# Today so far: the live counter behind the first line of /stats/.
#
#   CloudFront log file lands in the log bucket
#     ── S3 event ──> Lambda (analytics/live/handler.py)
#                       ├─> DynamoDB: one row per Helsinki day, sets of
#                       │   session ids (visits, copies, github); one
#                       │   marker row per file, so a retry counts nothing
#                       └─> data/live.json in the site bucket, which the
#                           board reads
#
# The nightly pipeline in analytics/ stays the record; this is the
# provisional number for the day it has not seen yet. The code and its
# dependencies are zipped from infra/.build/live, which `make live-build`
# fills; `make plan` runs it first.
########################################################################

resource "aws_dynamodb_table" "live" {
  name         = "podbay-live"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"

  attribute {
    name = "pk"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }
}

data "archive_file" "live" {
  type        = "zip"
  source_dir  = "${path.module}/.build/live"
  output_path = "${path.module}/.build/live.zip"
}

data "aws_iam_policy_document" "live_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "live" {
  name               = "podbay-live"
  assume_role_policy = data.aws_iam_policy_document.live_assume.json
}

data "aws_iam_policy_document" "live" {
  statement {
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:*:*:*"]
  }
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.site_logs.arn}/cloudfront/*"]
  }
  statement {
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.site.arn}/data/live.json"]
  }
  statement {
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.live.arn]
  }
}

resource "aws_iam_role_policy" "live" {
  name   = "podbay-live"
  role   = aws_iam_role.live.id
  policy = data.aws_iam_policy_document.live.json
}

resource "aws_cloudwatch_log_group" "live" {
  name              = "/aws/lambda/podbay-live"
  retention_in_days = 30
}

resource "aws_lambda_function" "live" {
  function_name    = "podbay-live"
  role             = aws_iam_role.live.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "handler.handler"
  filename         = data.archive_file.live.output_path
  source_code_hash = data.archive_file.live.output_base64sha256
  timeout          = 60
  memory_size      = 256

  environment {
    variables = {
      TABLE       = aws_dynamodb_table.live.name
      SITE_BUCKET = aws_s3_bucket.site.bucket
    }
  }

  depends_on = [aws_cloudwatch_log_group.live, aws_iam_role_policy.live]
}

resource "aws_lambda_permission" "live_from_logs" {
  statement_id  = "AllowLogBucket"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.live.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.site_logs.arn
}

resource "aws_s3_bucket_notification" "site_logs" {
  bucket = aws_s3_bucket.site_logs.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.live.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "cloudfront/"
    filter_suffix       = ".gz"
  }

  depends_on = [aws_lambda_permission.live_from_logs]
}

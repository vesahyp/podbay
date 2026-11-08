########################################################################
# podbay mobile: the phone app at app.podbay.tienoo.com
# (docs/adr/0001-podbay-mobile.md).
#
#   phone ── CloudFront (app.podbay.tienoo.com) ──┬── S3 podbay-mobile-app (the shell)
#                                                 └── /api/* ── HTTP API (JWT: Cognito)
#                                                                  └── Lambda podbay-mobile-api
#                                                                        ├── S3 podbay-mobile-state (the Mac's snapshot)
#                                                                        └── DynamoDB podbay-mobile
#   Mac (podbay screen) ── PutObject state ── Query/Update messages   (outbound only)
#
# The owner's address is not in this public repo: it is the SSM parameter
# /podbay/mobile/owner-email, created once by hand
# (aws ssm put-parameter --name /podbay/mobile/owner-email --type String --value ...).
########################################################################

variable "mobile_domain" {
  description = "The phone app's host."
  type        = string
  default     = "app.podbay.tienoo.com"
}

data "aws_ssm_parameter" "owner_email" {
  name = "/podbay/mobile/owner-email"
}

data "aws_caller_identity" "current" {}

locals {
  owner_email = nonsensitive(data.aws_ssm_parameter.owner_email.value)
  # The Playwright user. Admin-created, never mailed, in the test group:
  # its messages are marked test and never typed to Head Jeeves.
  test_email = "test@${var.mobile_domain}"
}

########################################################################
# Buckets: the app shell (CloudFront reads it) and the state (only the
# API Lambda reads it; the Mac writes it).
########################################################################

resource "aws_s3_bucket" "mobile_app" {
  bucket = "podbay-mobile-app"
}

resource "aws_s3_bucket" "mobile_state" {
  bucket = "podbay-mobile-state"
}

resource "aws_s3_bucket_public_access_block" "mobile_app" {
  bucket                  = aws_s3_bucket.mobile_app.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_public_access_block" "mobile_state" {
  bucket                  = aws_s3_bucket.mobile_state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "mobile_app" {
  bucket = aws_s3_bucket.mobile_app.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_ownership_controls" "mobile_state" {
  bucket = aws_s3_bucket.mobile_state.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "mobile_app" {
  bucket = aws_s3_bucket.mobile_app.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "mobile_state" {
  bucket = aws_s3_bucket.mobile_state.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_policy" "mobile_app" {
  bucket = aws_s3_bucket.mobile_app.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowCloudFrontOAC"
      Effect    = "Allow"
      Principal = { Service = "cloudfront.amazonaws.com" }
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.mobile_app.arn}/*"
      Condition = { StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.mobile.arn } }
    }]
  })
}

########################################################################
# DynamoDB: messages, audit, rate counters, passkeys, grants. Layout in
# mobile/api/handler.py.
########################################################################

resource "aws_dynamodb_table" "mobile" {
  name         = "podbay-mobile"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  point_in_time_recovery { enabled = true }
}

########################################################################
# SES for the sign-in codes: app.podbay.tienoo.com, DKIM, MAIL FROM,
# DMARC (jeeves practices/auth.md). The Cognito email service-linked role
# is account-global and owned by omatienoo's Terraform.
########################################################################

resource "aws_ses_domain_identity" "mobile" {
  domain = var.mobile_domain
}

# Cognito refuses an identity SES has not verified yet, so the pool waits
# for this.
resource "aws_route53_record" "mobile_ses_verification" {
  zone_id = data.aws_route53_zone.primary.zone_id
  name    = "_amazonses.${var.mobile_domain}"
  type    = "TXT"
  ttl     = 600
  records = [aws_ses_domain_identity.mobile.verification_token]
}

resource "aws_ses_domain_identity_verification" "mobile" {
  domain     = aws_ses_domain_identity.mobile.id
  depends_on = [aws_route53_record.mobile_ses_verification]
}

resource "aws_ses_domain_dkim" "mobile" {
  domain = aws_ses_domain_identity.mobile.domain
}

resource "aws_route53_record" "mobile_dkim" {
  count   = 3
  zone_id = data.aws_route53_zone.primary.zone_id
  name    = "${aws_ses_domain_dkim.mobile.dkim_tokens[count.index]}._domainkey.${var.mobile_domain}"
  type    = "CNAME"
  ttl     = 600
  records = ["${aws_ses_domain_dkim.mobile.dkim_tokens[count.index]}.dkim.amazonses.com"]
}

resource "aws_ses_domain_mail_from" "mobile" {
  domain           = aws_ses_domain_identity.mobile.domain
  mail_from_domain = "mail.${var.mobile_domain}"
}

resource "aws_route53_record" "mobile_mail_from_mx" {
  zone_id = data.aws_route53_zone.primary.zone_id
  name    = "mail.${var.mobile_domain}"
  type    = "MX"
  ttl     = 600
  records = ["10 feedback-smtp.${var.region}.amazonses.com"]
}

resource "aws_route53_record" "mobile_mail_from_spf" {
  zone_id = data.aws_route53_zone.primary.zone_id
  name    = "mail.${var.mobile_domain}"
  type    = "TXT"
  ttl     = 600
  records = ["v=spf1 include:amazonses.com -all"]
}

resource "aws_route53_record" "mobile_dmarc" {
  zone_id = data.aws_route53_zone.primary.zone_id
  name    = "_dmarc.${var.mobile_domain}"
  type    = "TXT"
  ttl     = 600
  records = ["v=DMARC1; p=none;"]
}

########################################################################
# Cognito: email one-time codes, no password UI, one user plus the test
# user. Self sign-up is refused by the pre-signup allowlist.
########################################################################

data "archive_file" "mobile_presignup" {
  type        = "zip"
  source_file = "${path.module}/../mobile/api/presignup.py"
  output_path = "${path.module}/.build/mobile-presignup.zip"
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "mobile_presignup" {
  name               = "podbay-mobile-presignup"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "mobile_presignup" {
  name = "podbay-mobile-presignup"
  role = aws_iam_role.mobile_presignup.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "arn:aws:logs:*:*:*" },
      {
        Effect   = "Allow"
        Action   = ["cognito-idp:ListUsers", "cognito-idp:AdminLinkProviderForUser"]
        Resource = "arn:aws:cognito-idp:${var.region}:${data.aws_caller_identity.current.account_id}:userpool/*"
      },
    ]
  })
}

resource "aws_cloudwatch_log_group" "mobile_presignup" {
  name              = "/aws/lambda/podbay-mobile-presignup"
  retention_in_days = 30
}

resource "aws_lambda_function" "mobile_presignup" {
  function_name    = "podbay-mobile-presignup"
  role             = aws_iam_role.mobile_presignup.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "presignup.handler"
  filename         = data.archive_file.mobile_presignup.output_path
  source_code_hash = data.archive_file.mobile_presignup.output_base64sha256
  timeout          = 10

  environment {
    variables = { ALLOWED_EMAILS = join(",", [lower(local.owner_email), local.test_email]) }
  }

  depends_on = [aws_cloudwatch_log_group.mobile_presignup]
}

resource "aws_lambda_permission" "mobile_presignup" {
  statement_id  = "AllowCognito"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.mobile_presignup.function_name
  principal     = "cognito-idp.amazonaws.com"
  source_arn    = aws_cognito_user_pool.mobile.arn
}

resource "aws_cognito_user_pool" "mobile" {
  name                = "podbay-mobile"
  user_pool_tier      = "ESSENTIALS"
  deletion_protection = "ACTIVE"

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  admin_create_user_config {
    allow_admin_create_user_only = false # Google federation creates users; the allowlist is the gate
  }

  lambda_config {
    pre_sign_up = aws_lambda_function.mobile_presignup.arn
  }

  # Email codes are the sign-in. PASSWORD is listed only because Cognito
  # will not remove it; the app client allows no password flow.
  sign_in_policy {
    allowed_first_auth_factors = ["EMAIL_OTP", "PASSWORD"]
  }

  email_configuration {
    email_sending_account = "DEVELOPER"
    source_arn            = aws_ses_domain_identity_verification.mobile.arn
    from_email_address    = "podbay <no-reply@${var.mobile_domain}>"
  }

  password_policy {
    minimum_length                   = 16
    require_lowercase                = true
    require_numbers                  = true
    require_symbols                  = false
    require_uppercase                = true
    temporary_password_validity_days = 1
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }
}

resource "aws_cognito_user_group" "test" {
  name         = "test"
  user_pool_id = aws_cognito_user_pool.mobile.id
  description  = "Playwright: messages are marked test and never typed to Head Jeeves"
}

# The phone app: email codes and refresh only. 15 minute access tokens,
# 30 day sign-ins, revocation on so `podbay mobile signout` ends them.
resource "aws_cognito_user_pool_client" "mobile" {
  name                          = "podbay-mobile-app"
  user_pool_id                  = aws_cognito_user_pool.mobile.id
  explicit_auth_flows           = ["ALLOW_USER_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
  generate_secret               = false
  prevent_user_existence_errors = "ENABLED"
  enable_token_revocation       = true

  access_token_validity  = 15
  id_token_validity      = 15
  refresh_token_validity = 30
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
}

# Tests only: admin password sign-in, which needs AWS credentials.
resource "aws_cognito_user_pool_client" "mobile_test" {
  name                    = "podbay-mobile-test"
  user_pool_id            = aws_cognito_user_pool.mobile.id
  explicit_auth_flows     = ["ALLOW_ADMIN_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
  generate_secret         = false
  enable_token_revocation = true

  access_token_validity  = 15
  id_token_validity      = 15
  refresh_token_validity = 1
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
}

resource "random_password" "mobile_owner" {
  length  = 32
  special = false
}

resource "random_password" "mobile_test" {
  length  = 32
  special = false
}

resource "aws_cognito_user" "mobile_owner" {
  user_pool_id   = aws_cognito_user_pool.mobile.id
  username       = local.owner_email
  password       = random_password.mobile_owner.result # CONFIRMED and OTP-capable; the password opens nothing
  message_action = "SUPPRESS"
  attributes = {
    email          = local.owner_email
    email_verified = "true"
  }
  lifecycle { ignore_changes = [attributes] }
  depends_on = [aws_lambda_permission.mobile_presignup]
}

resource "aws_cognito_user" "mobile_test" {
  user_pool_id   = aws_cognito_user_pool.mobile.id
  username       = local.test_email
  password       = random_password.mobile_test.result
  message_action = "SUPPRESS"
  attributes = {
    email          = local.test_email
    email_verified = "true"
  }
  depends_on = [aws_lambda_permission.mobile_presignup]
}

resource "aws_cognito_user_in_group" "mobile_test" {
  user_pool_id = aws_cognito_user_pool.mobile.id
  group_name   = aws_cognito_user_group.test.name
  username     = aws_cognito_user.mobile_test.sub
}

########################################################################
# The API Lambda and the HTTP API.
########################################################################

resource "aws_iam_role" "mobile_api" {
  name               = "podbay-mobile-api"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "mobile_api" {
  name = "podbay-mobile-api"
  role = aws_iam_role.mobile_api.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "arn:aws:logs:*:*:*" },
      { Effect = "Allow", Action = ["s3:GetObject"], Resource = "${aws_s3_bucket.mobile_state.arn}/state/*" },
      { Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.mobile_state.arn },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:Query"]
        Resource = aws_dynamodb_table.mobile.arn
      },
    ]
  })
}

resource "aws_cloudwatch_log_group" "mobile_api" {
  name              = "/aws/lambda/podbay-mobile-api"
  retention_in_days = 90
}

data "archive_file" "mobile_api" {
  type        = "zip"
  source_dir  = "${path.module}/.build/mobile-api"
  output_path = "${path.module}/.build/mobile-api.zip"
}

resource "aws_lambda_function" "mobile_api" {
  function_name    = "podbay-mobile-api"
  role             = aws_iam_role.mobile_api.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "handler.handler"
  filename         = data.archive_file.mobile_api.output_path
  source_code_hash = data.archive_file.mobile_api.output_base64sha256
  timeout          = 10
  memory_size      = 512

  environment {
    variables = {
      TABLE        = aws_dynamodb_table.mobile.name
      STATE_BUCKET = aws_s3_bucket.mobile_state.bucket
      STATE_KEY    = "state/state.json.gz"
      RP_ID        = var.mobile_domain
      ORIGIN       = "https://${var.mobile_domain}"
    }
  }

  depends_on = [aws_cloudwatch_log_group.mobile_api, aws_iam_role_policy.mobile_api]
}

resource "aws_apigatewayv2_api" "mobile" {
  name          = "podbay-mobile"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_authorizer" "mobile" {
  api_id           = aws_apigatewayv2_api.mobile.id
  name             = "cognito"
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  jwt_configuration {
    issuer   = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.mobile.id}"
    audience = [aws_cognito_user_pool_client.mobile.id, aws_cognito_user_pool_client.mobile_test.id]
  }
}

resource "aws_apigatewayv2_integration" "mobile" {
  api_id                 = aws_apigatewayv2_api.mobile.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.mobile_api.invoke_arn
  payload_format_version = "2.0"
}

locals {
  mobile_routes = [
    "GET /api/state", "GET /api/me", "GET /api/messages", "POST /api/messages",
    "POST /api/passkey/register/options", "POST /api/passkey/register/verify",
    "POST /api/passkey/auth/options", "POST /api/passkey/auth/verify",
  ]
}

# Cognito access tokens have no `aud` claim; the authorizer checks
# client_id against the audience list instead.
resource "aws_apigatewayv2_route" "mobile" {
  for_each           = toset(local.mobile_routes)
  api_id             = aws_apigatewayv2_api.mobile.id
  route_key          = each.value
  target             = "integrations/${aws_apigatewayv2_integration.mobile.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.mobile.id
}

resource "aws_cloudwatch_log_group" "mobile_access" {
  name              = "/aws/apigateway/podbay-mobile"
  retention_in_days = 90
}

resource "aws_apigatewayv2_stage" "mobile" {
  api_id      = aws_apigatewayv2_api.mobile.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = 10
    throttling_rate_limit  = 2
  }

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.mobile_access.arn
    format = jsonencode({
      id = "$context.requestId", at = "$context.requestTime", ip = "$context.identity.sourceIp", route = "$context.routeKey",
      status = "$context.status", sub = "$context.authorizer.claims.sub", err = "$context.authorizer.error",
      ms = "$context.responseLatency"
    })
  }
}

resource "aws_lambda_permission" "mobile_api" {
  statement_id  = "AllowHttpApi"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.mobile_api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.mobile.execution_arn}/*/*"
}

########################################################################
# Certificate, CloudFront and DNS for app.podbay.tienoo.com.
########################################################################

resource "aws_acm_certificate" "mobile" {
  provider          = aws.us_east_1
  domain_name       = var.mobile_domain
  validation_method = "DNS"
  lifecycle { create_before_destroy = true }
}

resource "aws_route53_record" "mobile_cert_validation" {
  for_each = {
    for dvo in aws_acm_certificate.mobile.domain_validation_options :
    dvo.domain_name => { name = dvo.resource_record_name, type = dvo.resource_record_type, record = dvo.resource_record_value }
  }
  zone_id         = data.aws_route53_zone.primary.zone_id
  name            = each.value.name
  type            = each.value.type
  records         = [each.value.record]
  ttl             = 60
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "mobile" {
  provider                = aws.us_east_1
  certificate_arn         = aws_acm_certificate.mobile.arn
  validation_record_fqdns = [for r in aws_route53_record.mobile_cert_validation : r.fqdn]
}

resource "aws_cloudfront_origin_access_control" "mobile" {
  name                              = "podbay-mobile-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# The page may talk to itself (/api/, t.gif) and to Cognito, nothing else.
resource "aws_cloudfront_response_headers_policy" "mobile" {
  name = "podbay-mobile"
  security_headers_config {
    content_security_policy {
      content_security_policy = join("; ", [
        "default-src 'self'",
        "connect-src 'self' https://cognito-idp.${var.region}.amazonaws.com",
        "img-src 'self' data:",
        "style-src 'self'",
        "script-src 'self'",
        "manifest-src 'self'",
        "worker-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "form-action 'none'",
      ])
      override = true
    }
    strict_transport_security {
      access_control_max_age_sec = 63072000
      include_subdomains         = false
      override                   = true
    }
    content_type_options { override = true }
    frame_options {
      frame_option = "DENY"
      override     = true
    }
    referrer_policy {
      referrer_policy = "same-origin"
      override        = true
    }
  }
}

resource "aws_cloudfront_distribution" "mobile" {
  enabled             = true
  is_ipv6_enabled     = true
  default_root_object = "index.html"
  price_class         = "PriceClass_100"
  comment             = "podbay mobile app"
  aliases             = [var.mobile_domain]

  # The tracker's datastore, beside the landing page's logs in its own prefix.
  logging_config {
    bucket          = aws_s3_bucket.site_logs.bucket_domain_name
    prefix          = "mobile/"
    include_cookies = false
  }

  origin {
    domain_name              = aws_s3_bucket.mobile_app.bucket_regional_domain_name
    origin_id                = "s3-podbay-mobile"
    origin_access_control_id = aws_cloudfront_origin_access_control.mobile.id
  }

  origin {
    domain_name = replace(aws_apigatewayv2_api.mobile.api_endpoint, "https://", "")
    origin_id   = "api-podbay-mobile"
    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id           = "s3-podbay-mobile"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = "658327ea-f89d-4fab-a63d-7e88639e58f6" # CachingOptimized
    response_headers_policy_id = aws_cloudfront_response_headers_policy.mobile.id
  }

  ordered_cache_behavior {
    path_pattern               = "/api/*"
    target_origin_id           = "api-podbay-mobile"
    viewer_protocol_policy     = "https-only"
    allowed_methods            = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = false
    cache_policy_id            = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad" # CachingDisabled
    origin_request_policy_id   = "b689b0a8-53d0-40ab-baf2-68738e2966ac" # AllViewerExceptHostHeader
    response_headers_policy_id = aws_cloudfront_response_headers_policy.mobile.id
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }

  viewer_certificate {
    acm_certificate_arn      = aws_acm_certificate_validation.mobile.certificate_arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }
}

resource "aws_route53_record" "mobile_a" {
  zone_id = data.aws_route53_zone.primary.zone_id
  name    = var.mobile_domain
  type    = "A"
  alias {
    name                   = aws_cloudfront_distribution.mobile.domain_name
    zone_id                = local.cloudfront_zone_id
    evaluate_target_health = false
  }
}

resource "aws_route53_record" "mobile_aaaa" {
  zone_id = data.aws_route53_zone.primary.zone_id
  name    = var.mobile_domain
  type    = "AAAA"
  alias {
    name                   = aws_cloudfront_distribution.mobile.domain_name
    zone_id                = local.cloudfront_zone_id
    evaluate_target_health = false
  }
}

########################################################################
# Outputs the Makefile and the tests read.
########################################################################

output "mobile_url" {
  value = "https://${var.mobile_domain}"
}

output "mobile_bucket" {
  value = aws_s3_bucket.mobile_app.bucket
}

output "mobile_distribution_id" {
  value = aws_cloudfront_distribution.mobile.id
}

output "mobile_state_bucket" {
  value = aws_s3_bucket.mobile_state.bucket
}

output "mobile_table" {
  value = aws_dynamodb_table.mobile.name
}

output "mobile_pool_id" {
  value = aws_cognito_user_pool.mobile.id
}

output "mobile_client_id" {
  value = aws_cognito_user_pool_client.mobile.id
}

output "mobile_test_client_id" {
  value = aws_cognito_user_pool_client.mobile_test.id
}

output "mobile_test_email" {
  value = local.test_email
}

output "mobile_test_password" {
  value     = random_password.mobile_test.result
  sensitive = true
}

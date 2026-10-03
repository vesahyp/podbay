########################################################################
# podbay.tienoo.com: the project page (kivikko's pattern).
#
#   S3 (private) ── OAC ──> CloudFront <── ACM cert (us-east-1)
#                              ▲
#                  Route53 alias records (podbay.tienoo.com)
#
# The tienoo.com hosted zone was created by the Route53 domain
# registration and is not owned by any repo's Terraform state. It is
# only referenced here.
#
# The same bucket also holds data/analytics.json, which the nightly
# pipeline in analytics/ writes. `make deploy` excludes data/ so a site
# deploy cannot remove it.
########################################################################

locals {
  # CloudFront's fixed hosted-zone ID (constant for all distributions).
  cloudfront_zone_id = "Z2FDTNDATAQYW2"
}

data "aws_route53_zone" "primary" {
  name         = "${var.primary_domain}."
  private_zone = false
}

########################################################################
# S3 content bucket (private; only CloudFront reads it via OAC).
########################################################################

resource "aws_s3_bucket" "site" {
  bucket = var.bucket_name
}

resource "aws_s3_bucket_public_access_block" "site" {
  bucket                  = aws_s3_bucket.site.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "site" {
  bucket = aws_s3_bucket.site.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "site" {
  bucket = aws_s3_bucket.site.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_policy" "site" {
  bucket = aws_s3_bucket.site.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowCloudFrontOAC"
      Effect    = "Allow"
      Principal = { Service = "cloudfront.amazonaws.com" }
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.site.arn}/*"
      Condition = {
        StringEquals = {
          "AWS:SourceArn" = aws_cloudfront_distribution.site.arn
        }
      }
    }]
  })
}

########################################################################
# Access-log bucket (the tracker datastore; private, 90-day expiry).
# ACLs stay enabled (BucketOwnerPreferred): CloudFront standard logging
# grants the log-delivery account through the bucket ACL.
########################################################################

resource "aws_s3_bucket" "site_logs" {
  bucket = var.logs_bucket_name
}

resource "aws_s3_bucket_ownership_controls" "site_logs" {
  bucket = aws_s3_bucket.site_logs.id
  rule { object_ownership = "BucketOwnerPreferred" }
}

resource "aws_s3_bucket_public_access_block" "site_logs" {
  bucket                  = aws_s3_bucket.site_logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Raw logs carry client IPs; 90 days is the window they exist for.
resource "aws_s3_bucket_lifecycle_configuration" "site_logs" {
  bucket = aws_s3_bucket.site_logs.id
  rule {
    id     = "expire-old-logs"
    status = "Enabled"
    filter { prefix = "cloudfront/" }
    expiration { days = 90 }
  }
}

########################################################################
# ACM certificate (us-east-1), DNS-validated into the tienoo.com zone.
########################################################################

resource "aws_acm_certificate" "site" {
  provider          = aws.us_east_1
  domain_name       = var.site_domain
  validation_method = "DNS"

  lifecycle { create_before_destroy = true }
}

resource "aws_route53_record" "cert_validation" {
  for_each = {
    for dvo in aws_acm_certificate.site.domain_validation_options :
    dvo.domain_name => {
      name   = dvo.resource_record_name
      type   = dvo.resource_record_type
      record = dvo.resource_record_value
    }
  }

  zone_id         = data.aws_route53_zone.primary.zone_id
  name            = each.value.name
  type            = each.value.type
  records         = [each.value.record]
  ttl             = 60
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "site" {
  provider                = aws.us_east_1
  certificate_arn         = aws_acm_certificate.site.arn
  validation_record_fqdns = [for r in aws_route53_record.cert_validation : r.fqdn]
}

########################################################################
# CloudFront: OAC + distribution.
########################################################################

resource "aws_cloudfront_origin_access_control" "site" {
  name                              = "podbay-site-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_function" "index" {
  name    = "podbay-site-index"
  runtime = "cloudfront-js-2.0"
  comment = "directory addresses serve their index.html"
  publish = true
  code    = file("${path.module}/index.js")
}

resource "aws_cloudfront_distribution" "site" {
  enabled             = true
  is_ipv6_enabled     = true
  default_root_object = "index.html"
  price_class         = "PriceClass_100"
  comment             = "podbay.tienoo.com site"
  aliases             = [var.site_domain]

  # Standard access logging: the request record and the tracker's
  # datastore, from the first deploy.
  logging_config {
    bucket          = aws_s3_bucket.site_logs.bucket_domain_name
    prefix          = "cloudfront/"
    include_cookies = false
  }

  origin {
    domain_name              = aws_s3_bucket.site.bucket_regional_domain_name
    origin_id                = "s3-podbay-site"
    origin_access_control_id = aws_cloudfront_origin_access_control.site.id
  }

  default_cache_behavior {
    target_origin_id       = "s3-podbay-site"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    # AWS-managed "CachingOptimized" policy.
    cache_policy_id = "658327ea-f89d-4fab-a63d-7e88639e58f6"

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.index.arn
    }
  }

  # A missing key answers with the site's own 404 page and a 404 status.
  # This is not an SPA, so no 200 fallback to index.html. S3 with OAC
  # denies ListBucket, so a missing key arrives as 403 too.
  custom_error_response {
    error_code            = 403
    response_code         = 404
    response_page_path    = "/404.html"
    error_caching_min_ttl = 10
  }
  custom_error_response {
    error_code            = 404
    response_code         = 404
    response_page_path    = "/404.html"
    error_caching_min_ttl = 10
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }

  viewer_certificate {
    acm_certificate_arn      = aws_acm_certificate_validation.site.certificate_arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }
}

########################################################################
# Route53 alias records → CloudFront.
########################################################################

resource "aws_route53_record" "alias_a" {
  zone_id = data.aws_route53_zone.primary.zone_id
  name    = var.site_domain
  type    = "A"
  alias {
    name                   = aws_cloudfront_distribution.site.domain_name
    zone_id                = local.cloudfront_zone_id
    evaluate_target_health = false
  }
}

resource "aws_route53_record" "alias_aaaa" {
  zone_id = data.aws_route53_zone.primary.zone_id
  name    = var.site_domain
  type    = "AAAA"
  alias {
    name                   = aws_cloudfront_distribution.site.domain_name
    zone_id                = local.cloudfront_zone_id
    evaluate_target_health = false
  }
}

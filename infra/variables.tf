variable "aws_profile" {
  description = "AWS CLI profile; null uses the default credential chain."
  type        = string
  default     = null
}

variable "region" {
  description = "Home region for everything except the CloudFront ACM cert."
  type        = string
  default     = "eu-north-1"
}

variable "primary_domain" {
  description = "Existing Route53 zone the site domain hangs under. The zone is owned by no repo and is only read here."
  type        = string
  default     = "tienoo.com"
}

variable "site_domain" {
  description = "Fully qualified site domain."
  type        = string
  default     = "podbay.tienoo.com"
}

variable "bucket_name" {
  description = "S3 bucket for the site and the analytics JSON under data/."
  type        = string
  default     = "podbay-site-content"
}

variable "logs_bucket_name" {
  description = "S3 bucket for CloudFront access logs (the tracker datastore)."
  type        = string
  default     = "podbay-cloudfront-logs"
}

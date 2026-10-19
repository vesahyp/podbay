output "bucket_name" {
  value = aws_s3_bucket.site.bucket
}

output "distribution_id" {
  value = aws_cloudfront_distribution.site.id
}

output "site_url" {
  value = "https://${var.site_domain}"
}

output "live_function" {
  value = aws_lambda_function.live.function_name
}

output "live_table" {
  value = aws_dynamodb_table.live.name
}

output "logs_bucket_name" {
  value = aws_s3_bucket.site_logs.bucket
}

variable "pipeline_name" {
  description = "Human-readable name for this pipeline"
  default     = "podbay-traffic"
}

variable "schema" {
  description = "Pipeline schema identifier (ADR-016 middle level). Default is the sanitized pipeline name; override to share a schema across pipelines (only one pipeline may write into any given schema)."
  type        = string
  default     = "podbay_traffic"
}

variable "trigger_schedule" {
  description = "Optional EventBridge schedule expression to run the pipeline on a fixed interval, e.g. \"rate(1 day)\" or \"cron(0 2 * * ? *)\". Null to disable."
  type        = string
  default     = null
}

variable "trigger_batch_window" {
  description = "How often the poller checks source queues for new data, e.g. \"rate(1 minute)\" or \"rate(15 minutes)\". Only has effect when the pipeline has S3-event-driven sources (trigger_queue_arns non-empty in orchestration). Set to null to disable the poller; the source queues will then accumulate messages with nothing to drain them."
  type        = string
  default     = "rate(1 minute)"
}

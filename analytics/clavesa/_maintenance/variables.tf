variable "pipeline_name" {
  description = "Human-readable name for this pipeline."
  default     = "_maintenance"
}

variable "schema" {
  description = "Pipeline schema identifier (ADR-016). Unused for writes — the maintenance transform produces no user outputs — but required by the transform module."
  type        = string
  default     = "_maintenance"
}

variable "trigger_schedule" {
  description = "EventBridge schedule for the maintenance run. Daily by default; OPTIMIZE/VACUUM of the system tables is cheap. Null to disable the schedule."
  type        = string
  default     = "cron(0 3 * * ? *)"
}

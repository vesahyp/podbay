variable "workspace_name" {
  description = "Unique name for this workspace. Used as prefix for shared AWS resources."
  default     = "podbay-traffic"
}

variable "runner_version" {
  description = "Transform runner image version tag (must be built locally before apply)."
  default     = "v2.20.0"
}

variable "system_catalog" {
  description = "Workspace-owned observability catalog (ADR-016). Hosts runs/node_runs/tables under the 'pipelines' schema."
  default     = "clavesa_podbay_traffic_system"
}

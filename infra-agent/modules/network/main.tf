terraform {
  required_version = ">= 1.5.0"
}

variable "environment" {
  description = "Target environment name"
  type        = string
}

resource "null_resource" "network_baseline" {
  triggers = {
    environment = var.environment
    module      = "network"
  }
}

output "network_module_status" {
  value = "network baseline configured for ${var.environment}"
}


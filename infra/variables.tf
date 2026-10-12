variable "tenancy_ocid" {
  description = "Tenancy OCID (root compartment). Supplied via TF_VAR_tenancy_ocid; never committed."
  type        = string

  validation {
    condition     = startswith(var.tenancy_ocid, "ocid1.tenancy.")
    error_message = "tenancy_ocid must be a tenancy OCID (ocid1.tenancy...)."
  }
}

variable "oci_config_profile" {
  description = "Profile name in ~/.oci/config."
  type        = string
  default     = "CONFER"
}

variable "region" {
  description = "Must be the tenancy home region."
  type        = string
  default     = "us-sanjose-1"

  validation {
    condition     = var.region == "us-sanjose-1"
    error_message = "Region must be us-sanjose-1."
  }
}

variable "ssh_public_key_path" {
  description = "Public half of the dedicated VM SSH key."
  type        = string
  default     = "~/.ssh/confer_oci_ed25519.pub"
}

variable "ssh_allowed_cidr" {
  description = "Your public IPv4 as /32. Only this address may SSH to the VM."
  type        = string

  validation {
    condition     = can(cidrhost(var.ssh_allowed_cidr, 0)) && endswith(var.ssh_allowed_cidr, "/32")
    error_message = "ssh_allowed_cidr must be one IPv4 address in /32 form."
  }
}

variable "instance_ocpus" {
  description = "A1 OCPUs."
  type        = number
  default     = 2

  validation {
    condition     = var.instance_ocpus >= 1 && var.instance_ocpus <= 2
    error_message = "Instance must use between 1 and 2 OCPUs."
  }
}

variable "instance_memory_gbs" {
  description = "A1 memory in GB."
  type        = number
  default     = 12

  validation {
    condition     = var.instance_memory_gbs >= 6 && var.instance_memory_gbs <= 12
    error_message = "Instance memory must be between 6 and 12 GB."
  }
}

variable "boot_volume_gbs" {
  description = "Boot volume size in GB."
  type        = number
  default     = 50

  validation {
    condition     = var.boot_volume_gbs >= 50 && var.boot_volume_gbs <= 200
    error_message = "Boot volume must be between 50 and 200 GB."
  }
}

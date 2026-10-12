variable "region" {
  description = "AWS region. us-west-2 offers every Free Plan instance type below at the same price as us-east-1."
  type        = string
  default     = "us-west-2"
}

variable "aws_profile" {
  description = "AWS CLI profile created by `aws login --profile <name>`."
  type        = string
  default     = "newsroom"
}

variable "instance_type" {
  description = <<-EOT
    Free Plan eligible x86 instance type (accounts created on/after 2025-07-15).
    t3.small  (2 GiB)  ~ $0.0208/hr: recommended; the app uses ~60 MB.
    t3.micro  (1 GiB)  ~ $0.0104/hr
    m7i-flex.large (8 GiB) ~ $0.0958/hr: the assessment's suggestion; $100 of credit lasts ~6 weeks.
  EOT
  type        = string
  default     = "t3.small"

  validation {
    condition     = contains(["t3.micro", "t3.small", "c7i-flex.large", "m7i-flex.large"], var.instance_type)
    error_message = "Use a Free Plan eligible x86 type: t3.micro, t3.small, c7i-flex.large or m7i-flex.large."
  }
}

variable "root_volume_gbs" {
  description = "Root gp3 volume size in GB (encrypted)."
  type        = number
  default     = 20

  validation {
    condition     = var.root_volume_gbs >= 8 && var.root_volume_gbs <= 30
    error_message = "Root volume must be between 8 and 30 GB."
  }
}

variable "ssh_public_key_path" {
  description = "Public half of a dedicated SSH key (never the private key)."
  type        = string
  default     = "~/.ssh/confer_aws_ed25519.pub"
}

variable "ssh_allowed_cidr" {
  description = "Your public IPv4 as /32. Only this address may reach SSH."
  type        = string

  validation {
    condition     = can(cidrhost(var.ssh_allowed_cidr, 0)) && endswith(var.ssh_allowed_cidr, "/32")
    error_message = "ssh_allowed_cidr must be one IPv4 address in /32 form."
  }
}

variable "app_git_ref" {
  description = "Full commit SHA of the public repository to deploy. Pinned so the VM runs exactly the reviewed code."
  type        = string
  default     = "f3349a039d7a1046ec5505286be7ad906158ab71"

  validation {
    condition     = can(regex("^[0-9a-f]{40}$", var.app_git_ref))
    error_message = "app_git_ref must be a full 40-character commit SHA."
  }
}

variable "repo_url" {
  description = "Public HTTPS URL of the repository (no credentials)."
  type        = string
  default     = "https://github.com/newsroom-ak/autonomous-newsroom.git"
}

variable "public_hostname" {
  description = "Optional custom DNS name pointing at the Elastic IP. Default: <ip-with-dashes>.sslip.io (free, automatic HTTPS)."
  type        = string
  default     = null
}

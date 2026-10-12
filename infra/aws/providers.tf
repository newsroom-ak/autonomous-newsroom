provider "aws" {
  region = var.region

  # Credentials come from `aws login --profile <name>` (short-lived, refreshed by the CLI).
  # No access keys are configured here, in variables, or in state.
  profile = var.aws_profile

  default_tags {
    tags = {
      Project   = "confer-autonomous-newsroom"
      ManagedBy = "terraform"
    }
  }
}

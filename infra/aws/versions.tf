terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # 6.23+ reads `aws login` (console sign-in) credentials natively. Verify with `terraform init`.
      version = "~> 6.23"
    }
  }
}

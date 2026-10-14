terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.60"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.7"
    }
  }

  # Where the state goes is up to you: copy backend.hcl.example to backend.hcl and run
  # `terraform init -backend-config=backend.hcl`. The state holds the IoT device's private key, so keep it private.
  backend "s3" {}
}

# Credentials from the environment (AWS_PROFILE).
provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "desk-control"
      Component = "alexa"
      ManagedBy = "terraform:desk-control/alexa/terraform"
    }
  }
}

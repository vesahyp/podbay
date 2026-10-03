terraform {
  # 1.10 is the floor: use_lockfile below is the S3-native state lock,
  # and it does not exist before then. .mise.toml pins the exact version.
  required_version = ">= 1.10"

  # State lives in the owner's backup bucket, keyed
  # tfstate/<repo>/<stack>.tfstate. The bucket name is not in the repo:
  # `make plan` passes it (TFSTATE_BUCKET in the Makefile).
  backend "s3" {
    key          = "tfstate/podbay/infra.tfstate"
    region       = "eu-north-1"
    profile      = "personal"
    encrypt      = true
    use_lockfile = true
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

# Everything except the CloudFront cert lives here. The project tag is the
# cost-allocation split: Cost Explorer groups by tag:project.
provider "aws" {
  region  = var.region
  profile = var.aws_profile

  default_tags {
    tags = {
      project = "podbay"
    }
  }
}

# CloudFront requires its ACM certificate in us-east-1, regardless of the
# bucket's region.
provider "aws" {
  alias   = "us_east_1"
  region  = "us-east-1"
  profile = var.aws_profile

  default_tags {
    tags = {
      project = "podbay"
    }
  }
}

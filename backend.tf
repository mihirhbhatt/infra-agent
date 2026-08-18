terraform {
  backend "s3" {
    bucket = "infra-agent-terraform-state"
    key    = "terraform.tfstate"
    region = "us-east-1"
  }
}

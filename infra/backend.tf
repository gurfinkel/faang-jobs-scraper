terraform {
  backend "s3" {
    bucket         = "faang-jobs-scraper-tfstate"
    key            = "faang-jobs-scraper/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    use_lockfile   = true
  }
}

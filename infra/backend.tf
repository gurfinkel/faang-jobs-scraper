terraform {
  backend "s3" {
    bucket         = "faang-jobs-scraper-tfstate"
    key            = "faang-jobs-scraper/terraform.tfstate"
    region         = "us-east-1"
    use_lockfile   = "faang-jobs-scraper-tf-locks"
    encrypt        = true
  }
}

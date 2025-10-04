terraform {
  backend "s3" {
    bucket         = "faang-jobs-scraper-tfstate"
    key            = "faang-jobs-scraper/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "faang-jobs-scraper-tf-locks"
    encrypt        = true
  }
}

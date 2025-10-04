resource "aws_s3_bucket" "tf_state" {
  bucket = "faang-jobs-scraper-tfstate"
}

resource "aws_dynamodb_table" "tf_lock" {
  name         = "faang-jobs-scraper-tf-locks"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }
}

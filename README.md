### 0) Prereqs
#### - Docker Desktop is running
#### - AWS CLI is configured for the correct account
#### - Terraform has already created infra/ (so outputs exist)

# 1) Set region and pull the ECR repo URL from Terraform outputs
REGION=us-east-1
ECR=$(terraform -chdir=infra output -raw ecr_repo_url)
echo "$ECR"   # e.g. 3210....dkr.ecr.us-east-1.amazonaws.com/faang-scraper

# 2) Build the image for x86_64 (amd64) so it runs on Fargate
docker build --platform=linux/amd64 -t faang-scraper:latest .

# 3) Log in to ECR (note the explicit region)
aws ecr get-login-password --region "$REGION" \
| docker login --username AWS --password-stdin "${ECR%/*}"

# 4) Tag and push (use braces in zsh!)
echo "${ECR}:latest"
docker tag faang-scraper:latest "${ECR}:latest"
docker push "${ECR}:latest"

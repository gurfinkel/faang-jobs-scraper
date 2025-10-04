terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws     = { source = "hashicorp/aws", version = ">= 5.0" }
    archive = { source = "hashicorp/archive", version = ">= 2.4" }
  }
}

provider "aws" {
  region = var.region
}

# ---------- Variables ----------
variable "region" { default = "us-east-1" }
variable "ecr_repo_name" { default = "faang-scraper" }
variable "ddb_table_name" { default = "faang_jobs" }

# ---------- DynamoDB ----------
resource "aws_dynamodb_table" "jobs" {
  name         = var.ddb_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "company"
  range_key    = "url"

  # Primary key attributes
  attribute {
    name = "company"
    type = "S"
  }
  attribute {
    name = "url"
    type = "S"
  }

  # Attributes used by GSIs
  attribute {
    name = "posted_at"
    type = "N"
  }
  attribute {
    name = "category"
    type = "S"
  }
  attribute {
    name = "loc_country"
    type = "S"
  }

  # GSIs for efficient queries
  global_secondary_index {
    name            = "GSICompanyPosted"
    hash_key        = "company"
    range_key       = "posted_at"
    projection_type = "INCLUDE"
    non_key_attributes = [
      "url", "title", "description", "category",
      "loc_country", "loc_admin1", "loc_city", "remote", "last_seen_at"
    ]
  }

  global_secondary_index {
    name            = "GSICategoryPosted"
    hash_key        = "category"
    range_key       = "posted_at"
    projection_type = "INCLUDE"
    non_key_attributes = [
      "url", "title", "description", "company",
      "loc_country", "loc_admin1", "loc_city", "remote", "last_seen_at"
    ]
  }

  global_secondary_index {
    name            = "GSICountryPosted"
    hash_key        = "loc_country"
    range_key       = "posted_at"
    projection_type = "INCLUDE"
    non_key_attributes = [
      "url", "title", "description", "company", "category",
      "loc_admin1", "loc_city", "remote", "last_seen_at"
    ]
  }
}

# ---------- ECR ----------
resource "aws_ecr_repository" "scraper" {
  name = var.ecr_repo_name
  image_scanning_configuration { scan_on_push = true }
  force_delete = true
}

# ---------- Networking (create our own VPC) ----------
data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "faang" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags                 = { Name = "faang-vpc" }
}

resource "aws_internet_gateway" "igw" {
  vpc_id = aws_vpc.faang.id
  tags   = { Name = "faang-igw" }
}

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.faang.id
  cidr_block              = cidrsubnet(aws_vpc.faang.cidr_block, 8, count.index + 1) # 10.0.1.0/24, 10.0.2.0/24
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true
  tags                    = { Name = "faang-public-${count.index}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.faang.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.igw.id
  }
  tags = { Name = "faang-public-rt" }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# Security group for ECS tasks (egress only)
resource "aws_security_group" "ecs_tasks" {
  name        = "faang-scraper-sg"
  description = "Egress-only SG for ECS tasks"
  vpc_id      = aws_vpc.faang.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ---------- Logs & ECS ----------
resource "aws_cloudwatch_log_group" "ecs" {
  name              = "/ecs/faang-scraper"
  retention_in_days = 30
}

resource "aws_ecs_cluster" "this" {
  name = "faang-scraper-cluster"
}

# ---------- IAM (ECS) ----------
resource "aws_iam_role" "ecs_exec" {
  name = "faang-scraper-exec-role"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [{ Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}
resource "aws_iam_role_policy_attachment" "ecs_exec_attach" {
  role       = aws_iam_role.ecs_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "ecs_task" {
  name = "faang-scraper-task-role"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [{ Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_policy" "ddb_rw" {
  name = "faang-scraper-ddb-rw"
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Effect = "Allow",
      Action = [
        "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:BatchWriteItem",
        "dynamodb:Query", "dynamodb:DeleteItem", "dynamodb:DescribeTable", "dynamodb:GetItem", "dynamodb:Scan"
      ],
      Resource = [aws_dynamodb_table.jobs.arn, "${aws_dynamodb_table.jobs.arn}/index/*"]
    }]
  })
}
resource "aws_iam_role_policy_attachment" "ecs_task_ddb" {
  role       = aws_iam_role.ecs_task.name
  policy_arn = aws_iam_policy.ddb_rw.arn
}

# ---------- ECS Task Definition ----------
resource "aws_ecs_task_definition" "scrape" {
  family                   = "faang-scraper"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 1024
  memory                   = 2048
  execution_role_arn       = aws_iam_role.ecs_exec.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  # Pin to x86_64 (matches docker build --platform=linux/amd64)
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    {
      name      = "scraper",
      image     = "${aws_ecr_repository.scraper.repository_url}:latest",
      essential = true,
      environment = [
        { name = "DDB_TABLE", value = aws_dynamodb_table.jobs.name },
        { name = "MAX_NEW_PER_RUN", value = "300" },   # cap new detail fetches per company/run
        { name = "CHUNK_UPSERT_SIZE", value = "100" }, # flush to DynamoDB every 100 new items
        { name = "LOCK_TTL_SEC", value = "5400" }      # lock TTL (seconds) to avoid overlap
      ],
      logConfiguration = {
        logDriver = "awslogs",
        options = {
          awslogs-region        = var.region,
          awslogs-group         = aws_cloudwatch_log_group.ecs.name,
          awslogs-stream-prefix = "ecs"
        }
      },
      command = ["python", "main.py"]
    }
  ])
}

# ---------- EventBridge hourly schedule -> ECS task ----------
resource "aws_iam_role" "events_run_task" {
  name = "faang-scraper-events-role"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [{ Effect = "Allow", Principal = { Service = "events.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "events_run_task" {
  name = "faang-scraper-events-policy"
  role = aws_iam_role.events_run_task.id
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Effect   = "Allow",
      Action   = ["ecs:RunTask", "iam:PassRole"],
      Resource = [aws_ecs_task_definition.scrape.arn, aws_iam_role.ecs_exec.arn, aws_iam_role.ecs_task.arn]
    }]
  })
}

resource "aws_cloudwatch_event_rule" "hourly" {
  name                = "faang-scraper-hourly"
  schedule_expression = "rate(1 hour)"
}

resource "aws_cloudwatch_event_target" "ecs_target" {
  rule      = aws_cloudwatch_event_rule.hourly.name
  target_id = "scrape-ecs"
  arn       = aws_ecs_cluster.this.arn
  role_arn  = aws_iam_role.events_run_task.arn

  ecs_target {
    task_definition_arn = aws_ecs_task_definition.scrape.arn
    launch_type         = "FARGATE"
    network_configuration {
      subnets          = aws_subnet.public[*].id
      security_groups  = [aws_security_group.ecs_tasks.id]
      assign_public_ip = true
    }
  }
}

# ---------- Lambda + API Gateway ----------
data "archive_file" "lambda_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../api"
  output_path = "${path.module}/lambda.zip"
}

resource "aws_iam_role" "lambda_role" {
  name = "faang-api-lambda-role"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}
resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}
resource "aws_iam_role_policy" "lambda_ddb_read" {
  name = "faang-api-ddb-read"
  role = aws_iam_role.lambda_role.id
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Effect   = "Allow",
      Action   = ["dynamodb:Query", "dynamodb:Scan", "dynamodb:GetItem", "dynamodb:DescribeTable"],
      Resource = [aws_dynamodb_table.jobs.arn, "${aws_dynamodb_table.jobs.arn}/index/*"]
    }]
  })
}

resource "aws_lambda_function" "api" {
  function_name = "faang-jobs-api"
  role          = aws_iam_role.lambda_role.arn
  handler       = "handler.handler"
  runtime       = "python3.11"
  filename      = data.archive_file.lambda_zip.output_path
  timeout       = 10
  environment { variables = { DDB_TABLE = aws_dynamodb_table.jobs.name } }
}

resource "aws_apigatewayv2_api" "http" {
  name          = "faang-jobs-http"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "get_jobs" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "GET /jobs"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowInvokeByAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*"
}

resource "aws_apigatewayv2_stage" "prod" {
  api_id      = aws_apigatewayv2_api.http.id
  name        = "prod"
  auto_deploy = true
}

# ---------- Outputs ----------
output "api_url" { value = "${aws_apigatewayv2_api.http.api_endpoint}/prod/jobs" }
output "ecr_repo_url" { value = aws_ecr_repository.scraper.repository_url }
output "ecs_cluster" { value = aws_ecs_cluster.this.name }
output "task_def_arn" { value = aws_ecs_task_definition.scrape.arn }
output "task_sg_id" { value = aws_security_group.ecs_tasks.id }
output "subnet_ids" { value = aws_subnet.public[*].id }

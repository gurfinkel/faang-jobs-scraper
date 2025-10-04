terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = ">= 2.4"
    }
  }
}

####################
# Variables
####################

# Primary AWS region to deploy into.  Adjust this if you wish to run in a different region.
variable "region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us‑east‑1"
}

# A short prefix used when constructing resource names.  Set this to something
# unique (e.g. your project or repository name) to avoid naming collisions.
variable "project_name" {
  description = "Prefix for all resource names"
  type        = string
  default     = "faang‑jobs‑scraper"
}

# Name of the ECR repository for the scraping container.  Defaults to
# "<project_name>-scraper".
variable "ecr_repo_name" {
  description = "Name of the ECR repository used for the scraper image"
  type        = string
  default     = null
}

# Name of the DynamoDB table used to store job postings.  Defaults to
# "<project_name>_jobs".
variable "ddb_table_name" {
  description = "Name of the DynamoDB table used for job postings"
  type        = string
  default     = null
}

####################
# Providers
####################

provider "aws" {
  region = var.region
}

locals {
  # Resolve dynamic defaults based on the project_name.  Terraform allows
  # variables to remain null; we then fill them here.
  effective_ecr_repo_name = coalesce(var.ecr_repo_name, "${var.project_name}-scraper")
  effective_ddb_table_name = coalesce(var.ddb_table_name, "${replace(var.project_name, "-", "_")}_jobs")
}

####################
# DynamoDB
####################

# DynamoDB table storing job postings.  Each job is keyed by the company and
# a unique URL to avoid duplicates.  Additional attributes are defined to
# support secondary indexes used by the API.
resource "aws_dynamodb_table" "jobs" {
  name         = local.effective_ddb_table_name
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

  # Attributes used in secondary indexes
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

  # Global secondary index to query by company and posting date
  global_secondary_index {
    name               = "GSICompanyPosted"
    hash_key           = "company"
    range_key          = "posted_at"
    projection_type    = "INCLUDE"
    non_key_attributes = ["url", "title", "description", "category", "loc_country", "loc_admin1", "loc_city", "remote", "last_seen_at"]
  }

  # Global secondary index to query by category and posting date
  global_secondary_index {
    name               = "GSICategoryPosted"
    hash_key           = "category"
    range_key          = "posted_at"
    projection_type    = "INCLUDE"
    non_key_attributes = ["url", "title", "description", "company", "loc_country", "loc_admin1", "loc_city", "remote", "last_seen_at"]
  }

  # Global secondary index to query by country and posting date
  global_secondary_index {
    name               = "GSILocationPosted"
    hash_key           = "loc_country"
    range_key          = "posted_at"
    projection_type    = "INCLUDE"
    non_key_attributes = ["url", "title", "description", "company", "category", "loc_admin1", "loc_city", "remote", "last_seen_at"]
  }
}

####################
# ECR Repository
####################

resource "aws_ecr_repository" "scraper" {
  name                 = local.effective_ecr_repo_name
  image_scanning_configuration {
    scan_on_push = true
  }
  force_delete = true
}

####################
# CloudWatch Log Groups
####################

resource "aws_cloudwatch_log_group" "ecs" {
  name              = "/ecs/${var.project_name}-scraper"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_group" "lambda_api" {
  name              = "/aws/lambda/${var.project_name}-jobs-api"
  retention_in_days = 30
}

####################
# IAM Roles and Policies
####################

# ECS execution role used to allow the ECS agent to pull images and write logs
resource "aws_iam_role" "ecs_exec" {
  name_prefix = "${var.project_name}-scraper-exec-role-"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# ECS task role for the scraping task.  Grants permissions to write to DynamoDB
resource "aws_iam_role" "ecs_task" {
  name_prefix = "${var.project_name}-scraper-task-role-"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_policy" "ddb_rw" {
  name_prefix = "${var.project_name}-scraper-ddb-rw-"
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Effect   = "Allow",
      Action   = [
        "dynamodb:BatchWriteItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
        "dynamodb:DeleteItem",
        "dynamodb:GetItem",
        "dynamodb:Query",
        "dynamodb:Scan"
      ],
      Resource = [aws_dynamodb_table.jobs.arn, "${aws_dynamodb_table.jobs.arn}/*"]
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_task_attach" {
  role       = aws_iam_role.ecs_task.name
  policy_arn = aws_iam_policy.ddb_rw.arn
}

# EventBridge rule role for triggering ECS tasks
resource "aws_iam_role" "events_run_task" {
  name_prefix = "${var.project_name}-scraper-events-role-"
  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Effect    = "Allow",
      Principal = { Service = "events.amazonaws.com" },
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "events_run_task" {
  name = "${var.project_name}-scraper-events-policy"
  role = aws_iam_role.events_run_task.id
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect   = "Allow",
        Action   = ["ecs:RunTask"],
        Resource = "*"
      },
      {
        Effect   = "Allow",
        Action   = ["iam:PassRole"],
        Resource = aws_iam_role.ecs_task.arn
      }
    ]
  })
}

# Lambda role for API function
resource "aws_iam_role" "lambda_role" {
  name_prefix = "${var.project_name}-api-lambda-role-"
  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Effect    = "Allow",
      Principal = { Service = "lambda.amazonaws.com" },
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "lambda_ddb_read" {
  name = "${var.project_name}-api-lambda-ddb-read"
  role = aws_iam_role.lambda_role.id
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Effect   = "Allow",
      Action   = ["dynamodb:Query", "dynamodb:GetItem", "dynamodb:Scan"],
      Resource = [aws_dynamodb_table.jobs.arn, "${aws_dynamodb_table.jobs.arn}/*"]
    }]
  })
}

# Attach AWS managed basic execution role to the Lambda role so the function can
# write logs to CloudWatch.
resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

####################
# ECS Cluster and Fargate Task
####################

# ECS cluster for the scraping task
resource "aws_ecs_cluster" "this" {
  name = "${var.project_name}-scraper-cluster"
}

# Task definition for the scraping container.  You will need to build and push
# your scraper image to ECR before deploying.  The container definition
# includes environment variables pointing to the DynamoDB table and region.
data "aws_iam_policy_document" "ecs_task_execution_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ecs_task_execution" {
  name_prefix = "${var.project_name}-scraper-execution-role-"
  assume_role_policy = data.aws_iam_policy_document.ecs_task_execution_assume_role.json
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution_attach" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_ecs_task_definition" "scraper" {
  family                   = "${var.project_name}-scraper-task"
  cpu                      = 512
  memory                   = 1024
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([
    {
      name  = "scraper"
      image = aws_ecr_repository.scraper.repository_url
      essential = true
      environment = [
        { name = "TABLE_NAME", value = aws_dynamodb_table.jobs.name },
        { name = "AWS_REGION", value = var.region }
      ]
      logConfiguration = {
        logDriver = "awslogs",
        options = {
          awslogs-group         = aws_cloudwatch_log_group.ecs.name,
          awslogs-region        = var.region,
          awslogs-stream-prefix = "ecs"
        }
      }
    }
  ])
}

# Security group for the Fargate task.  No inbound rules needed since it
# makes outbound requests only.
resource "aws_security_group" "ecs" {
  name        = "${var.project_name}-scraper-sg"
  description = "Security group for FAANG scraper tasks"
  vpc_id      = data.aws_vpc.default.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# Use the default VPC and its subnets for Fargate tasks.  The AWS provider
# version 5.x no longer exposes a data source named `aws_default_vpc`; instead
# query the default VPC using `aws_vpc` with the `default` argument.
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

####################
# EventBridge Rule for Scraper
####################

# Schedule the Fargate task to run once per day.  Adjust the schedule_expression
# to control the frequency.  See https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-create-rule-schedule.html
resource "aws_cloudwatch_event_rule" "daily_scrape" {
  name                = "${var.project_name}-scraper-daily"
  schedule_expression = "rate(1 day)"
}

resource "aws_cloudwatch_event_target" "daily_scrape" {
  rule      = aws_cloudwatch_event_rule.daily_scrape.name
  target_id = "${var.project_name}-scraper-task"
  arn       = aws_ecs_cluster.this.arn
  ecs_target {
    task_definition_arn = aws_ecs_task_definition.scraper.arn
    task_count          = 1
    network_configuration {
      subnets         = data.aws_subnets.default.ids
      security_groups = [aws_security_group.ecs.id]
      assign_public_ip = true
    }
    launch_type = "FARGATE"
  }
  role_arn = aws_iam_role.events_run_task.arn
}

# Permission allowing EventBridge to run ECS tasks
resource "aws_iam_role_policy" "events_ecs_invoke" {
  name = "${var.project_name}-scraper-events-ecs-invoke"
  role = aws_iam_role.events_run_task.id
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Effect = "Allow",
      Action = ["iam:PassRole"],
      Resource = aws_iam_role.ecs_task.arn
    }, {
      Effect = "Allow",
      Action = ["ecs:RunTask"],
      Resource = aws_ecs_task_definition.scraper.arn,
      Condition = {
        ArnEquals = {
          "ecs:cluster" = aws_ecs_cluster.this.arn
        }
      }
    }]
  })
}

####################
# Lambda API and API Gateway
####################

# Package the API Lambda code.  Assumes the API code lives in the api/ directory
# of your repository and that it produces a lambda.zip artifact in infra/ when
# built.  Remove this archive packaging block if you handle packaging elsewhere.
data "archive_file" "api_zip" {
  type        = "zip"
  source_dir  = "../api"
  output_path = "${path.module}/lambda-api.zip"
}

resource "aws_lambda_function" "api" {
  function_name    = "${var.project_name}-jobs-api"
  handler          = "handler.lambda_handler"
  runtime          = "python3.11"
  role             = aws_iam_role.lambda_role.arn
  filename         = data.archive_file.api_zip.output_path
  source_code_hash = data.archive_file.api_zip.output_base64sha256
  timeout          = 30
  environment {
    variables = {
      TABLE_NAME = aws_dynamodb_table.jobs.name
    }
  }
  depends_on = [aws_cloudwatch_log_group.lambda_api]
}

# Create an HTTP API Gateway for the Lambda function
resource "aws_apigatewayv2_api" "http" {
  name          = "${var.project_name}-jobs-http"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "api_integration" {
  api_id             = aws_apigatewayv2_api.http.id
  integration_type   = "AWS_PROXY"
  integration_method = "POST"
  integration_uri    = aws_lambda_function.api.invoke_arn
}

resource "aws_apigatewayv2_route" "jobs_route" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "GET /jobs"
  target    = "integrations/${aws_apigatewayv2_integration.api_integration.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.http.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_lambda_permission" "api_invoke" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*"
}

####################
# Outputs
####################

output "table_name" {
  description = "DynamoDB table name"
  value       = aws_dynamodb_table.jobs.name
}

output "ecr_repository_url" {
  description = "ECR repository URL for the scraper image"
  value       = aws_ecr_repository.scraper.repository_url
}

output "api_endpoint" {
  description = "Invoke URL for the jobs API"
  value       = aws_apigatewayv2_stage.default.invoke_url
}

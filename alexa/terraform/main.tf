# The cloud half of Desk Control's Alexa skill (see alexa/README.md):
#
# - an AWS IoT Core device ("thing") with a certificate, which the app on the PC connects to over MQTT. The PC only
#   makes an outgoing connection, so nothing on it is reachable from the internet;
# - the smart home skill's Lambda function, which only Alexa, for this one skill, may call;
# - a kill switch that turns the function off if it's ever called far more than a person would.
#
# Cost: the Lambda, CloudWatch and SNS parts stay inside AWS's always-free tiers. IoT Core isn't always free, but one
# device connected all month with a handful of messages a day comes to well under a cent a month.

data "aws_caller_identity" "me" {}
data "aws_partition" "this" {}

data "aws_iot_endpoint" "data" {
  endpoint_type = "iot:Data-ATS"
}

locals {
  name   = "desk-control-alexa"
  prefix = "arn:${data.aws_partition.this.partition}:iot:${var.region}:${data.aws_caller_identity.me.account_id}"
  topic  = "desk-control/${var.thing_name}"
  lambda_trust = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

# The desk's stand-in on IoT Core, and what the app may do as it: listen for commands, and keep its two retained
# status messages up to date.

resource "aws_iot_thing" "desk" {
  name = var.thing_name
}

resource "aws_iot_certificate" "desk" {
  active = true
}

resource "aws_iot_thing_principal_attachment" "desk" {
  thing     = aws_iot_thing.desk.name
  principal = aws_iot_certificate.desk.arn
}

resource "aws_iot_policy" "desk" {
  name = local.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = "iot:Connect", Resource = "${local.prefix}:client/${var.thing_name}" },
      { Effect = "Allow", Action = "iot:Subscribe", Resource = "${local.prefix}:topicfilter/${local.topic}/command" },
      { Effect = "Allow", Action = "iot:Receive", Resource = "${local.prefix}:topic/${local.topic}/command" },
      {
        Effect   = "Allow"
        Action   = ["iot:Publish", "iot:RetainPublish"]
        Resource = ["${local.prefix}:topic/${local.topic}/online", "${local.prefix}:topic/${local.topic}/state"]
      },
    ]
  })
}

resource "aws_iot_policy_attachment" "desk" {
  policy = aws_iot_policy.desk.name
  target = aws_iot_certificate.desk.arn
}

# The skill's function.

data "archive_file" "skill" {
  type        = "zip"
  source_file = "${path.module}/../lambda/handler.py"
  output_path = "${path.module}/.build/skill.zip"
}

resource "aws_cloudwatch_log_group" "skill" {
  name              = "/aws/lambda/${local.name}"
  retention_in_days = 3
}

resource "aws_iam_role" "skill" {
  name               = local.name
  assume_role_policy = local.lambda_trust
}

resource "aws_iam_role_policy" "skill" {
  name = "logs-and-desk"
  role = aws_iam_role.skill.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.skill.arn}:*"
      },
      { Effect = "Allow", Action = "iot:Publish", Resource = "${local.prefix}:topic/${local.topic}/command" },
      {
        Effect   = "Allow"
        Action   = "iot:GetRetainedMessage"
        Resource = ["${local.prefix}:topic/${local.topic}/online", "${local.prefix}:topic/${local.topic}/state"]
      },
    ]
  })
}

resource "aws_lambda_function" "skill" {
  function_name    = local.name
  description      = "Desk Control's Alexa smart home skill (desk-control/alexa)"
  role             = aws_iam_role.skill.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "handler.lambda_handler"
  filename         = data.archive_file.skill.output_path
  source_code_hash = data.archive_file.skill.output_base64sha256
  memory_size      = 128
  # Alexa gives up after 8 seconds.
  timeout = 7
  # A couple at a time is plenty for one person; the kill switch sets this to 0, `terraform apply` sets it back.
  reserved_concurrent_executions = 2

  environment {
    variables = {
      IOT_ENDPOINT = data.aws_iot_endpoint.data.endpoint_address
      THING_NAME   = var.thing_name
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.skill.name
  }

  depends_on = [aws_iam_role_policy.skill]
}

# Made once the skill exists: its ID comes from the Alexa developer console.
resource "aws_lambda_permission" "alexa" {
  count              = var.skill_id == "" ? 0 : 1
  statement_id       = "AllowAlexaSmartHome"
  action             = "lambda:InvokeFunction"
  function_name      = aws_lambda_function.skill.function_name
  principal          = "alexa-connectedhome.amazon.com"
  event_source_token = var.skill_id
}

# The kill switch: an alarm on the invocation count, which tells a small function (and you, by email) to turn the
# skill's function off.

resource "aws_sns_topic" "killswitch" {
  name = "${local.name}-killswitch"
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.killswitch.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_cloudwatch_metric_alarm" "flood" {
  alarm_name          = "${local.name}-flood"
  alarm_description   = "Far more Alexa calls than a person makes: the kill switch turns the function off. Run terraform apply in desk-control/alexa/terraform to turn it back on."
  namespace           = "AWS/Lambda"
  metric_name         = "Invocations"
  dimensions          = { FunctionName = aws_lambda_function.skill.function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = var.max_invocations_per_5_minutes
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.killswitch.arn]
}

data "archive_file" "killswitch" {
  type        = "zip"
  source_file = "${path.module}/../lambda/killswitch.py"
  output_path = "${path.module}/.build/killswitch.zip"
}

resource "aws_cloudwatch_log_group" "killswitch" {
  name              = "/aws/lambda/${local.name}-killswitch"
  retention_in_days = 3
}

resource "aws_iam_role" "killswitch" {
  name               = "${local.name}-killswitch"
  assume_role_policy = local.lambda_trust
}

resource "aws_iam_role_policy" "killswitch" {
  name = "logs-and-turn-off"
  role = aws_iam_role.killswitch.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.killswitch.arn}:*"
      },
      { Effect = "Allow", Action = "lambda:PutFunctionConcurrency", Resource = aws_lambda_function.skill.arn },
    ]
  })
}

resource "aws_lambda_function" "killswitch" {
  function_name    = "${local.name}-killswitch"
  description      = "Turns the Alexa skill's function off when it's called too often (desk-control/alexa)"
  role             = aws_iam_role.killswitch.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "killswitch.lambda_handler"
  filename         = data.archive_file.killswitch.output_path
  source_code_hash = data.archive_file.killswitch.output_base64sha256
  memory_size      = 128
  timeout          = 10

  environment {
    variables = { FUNCTION_NAME = aws_lambda_function.skill.function_name }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.killswitch.name
  }

  depends_on = [aws_iam_role_policy.killswitch]
}

resource "aws_lambda_permission" "killswitch_from_sns" {
  statement_id  = "AllowSNS"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.killswitch.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.killswitch.arn
}

resource "aws_sns_topic_subscription" "killswitch" {
  topic_arn  = aws_sns_topic.killswitch.arn
  protocol   = "lambda"
  endpoint   = aws_lambda_function.killswitch.arn
  depends_on = [aws_lambda_permission.killswitch_from_sns]
}

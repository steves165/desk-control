output "lambda_arn" {
  description = "Paste into the skill's Default endpoint in the Alexa developer console."
  value       = aws_lambda_function.skill.arn
}

output "iot_endpoint" {
  value = data.aws_iot_endpoint.data.endpoint_address
}

output "thing_name" {
  value = aws_iot_thing.desk.name
}

output "certificate_pem" {
  value     = aws_iot_certificate.desk.certificate_pem
  sensitive = true
}

output "private_key" {
  value     = aws_iot_certificate.desk.private_key
  sensitive = true
}

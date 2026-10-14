variable "region" {
  description = <<-EOT
    Alexa only calls smart home skills' functions in its own regions: eu-west-1 for English (UK) and the rest of
    Europe and India, us-east-1 for North America, us-west-2 for Japan and Australia.
  EOT
  type        = string
  default     = "eu-west-1"
}

variable "skill_id" {
  description = "The skill's ID from the Alexa developer console (amzn1.ask.skill.…): only that skill may call the function. Until it's set, nothing can."
  type        = string
  default     = ""
}

variable "thing_name" {
  description = "The IoT device standing in for the desk; also its MQTT client ID and Alexa endpoint ID."
  type        = string
  default     = "desk-control-desk"
}

variable "max_invocations_per_5_minutes" {
  description = <<-EOT
    The kill switch: more calls than this in 5 minutes turns the function off (concurrency 0) until the next
    `terraform apply`. Talking to the desk is a few calls at a time; Alexa's own polling adds a few an hour.
  EOT
  type        = number
  default     = 60
}

variable "alert_email" {
  description = "Told when the kill switch turns the function off (confirm the subscription email from AWS first)."
  type        = string
}

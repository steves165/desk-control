"""The skill function's kill switch (alexa/terraform/main.tf): when the alarm on its invocation count goes off, sets
its concurrency to 0, so nothing more can run it until `terraform apply` sets it back."""
import json
import os

import boto3


def lambda_handler(event, context):
    for record in event.get("Records", []):
        alarm = json.loads(record.get("Sns", {}).get("Message", "{}"))
        if alarm.get("NewStateValue") != "ALARM":
            continue
        boto3.client("lambda").put_function_concurrency(
            FunctionName=os.environ["FUNCTION_NAME"], ReservedConcurrentExecutions=0)
        print(f"Turned {os.environ['FUNCTION_NAME']} off: {alarm.get('NewStateReason')}")

# Alexa

Control the desk by voice through an Alexa smart home skill that you host yourself. Once it's set up:

- “Alexa, set the desk to Stand” (any preset name)
- “Alexa, set the desk height to 100”: any height, in centimetres
- “Alexa, raise the desk” / “Alexa, lower the desk”: to your highest / lowest preset (needs at least two)
- “Alexa, turn up the desk height”: 10 cm up, or by as much as you say
- “Alexa, what's the desk height?”

Desk Control has to be running on the PC and connected to the desk. When it isn't, Alexa says the desk isn't
responding.

> [!CAUTION]
> Alexa can't tell the skill whether a command came from someone speaking or from a routine, so think twice before
> adding the desk to scheduled routines. Saying “Alexa, stop” doesn't stop the desk: use the handset, the app or the
> tray.

## How it works

```
"Alexa, set the desk to Stand"
  → Alexa → the skill's AWS Lambda function (lambda/handler.py)
  → AWS IoT Core (MQTT)
  → Desk Control on your PC (alexa_link.py), over a connection the PC opens → the desk
```

- **No incoming connections.** The PC only opens an outgoing connection to IoT Core. It needs no open ports,
  tunnels or port forwarding.
- **What the app shares.** It keeps two retained messages up to date: whether it's online, and the desk's status,
  height, range and presets. The Lambda function reads these to answer Alexa.
- **What the skill can do.** It can move or stop the desk, nothing else.
- **No surprise moves.** The app ignores commands more than 30 seconds old, so a slow network can't move the desk
  long after you asked.
- **Who can use it.** Only Alexa, for your skill, can call the function. While the skill stays in development (it
  never needs to be published), only your Amazon account can enable it.

**Cost.** The Lambda, CloudWatch and SNS parts stay inside AWS's always-free tiers. IoT Core isn't always free, but
one device connected all month with a handful of messages a day comes to well under a cent a month. A kill switch
turns the function off, and emails you, if it's called more than 60 times in 5 minutes.

## Set up

You need an AWS account, the AWS CLI, [Terraform](https://developer.hashicorp.com/terraform/install) 1.6 or newer,
and an [Amazon developer account](https://developer.amazon.com/) using the same Amazon login as your Echo.

### 1. Create the skill

1. In the [Alexa developer console](https://developer.amazon.com/alexa/console/ask), click **Create skill**.
2. Name it **Desk Control**, choose your language (for example English (UK)), choose **Smart Home** and
   **Provision your own**, then create it.
3. Copy the **Skill ID** (`amzn1.ask.skill.…`) from the skill's Smart Home page.

### 2. Deploy the AWS side

From `alexa/terraform`, with your AWS credentials in the environment (for example `AWS_PROFILE=...`):

```sh
cp backend.hcl.example backend.hcl            # point it at an S3 bucket of yours, for Terraform's state
cp terraform.tfvars.example terraform.tfvars  # fill in skill_id and alert_email
terraform init -backend-config=backend.hcl
terraform apply
```

- **Region.** Alexa only calls smart home functions in its own regions, so the default `region` is `eu-west-1`
  (English (UK), Europe and India). Use `us-east-1` for North America, or `us-west-2` for Japan and Australia.
- **Alert email.** AWS sends a confirmation email to `alert_email`; confirm it so the kill switch can reach you.
- **Private key.** Terraform's state holds the IoT device's private key, so keep the state bucket private.

### 3. Give Desk Control the device's certificate

```sh
terraform -chdir=alexa/terraform output -json | python alexa/export.py
```

This writes the certificate, key and endpoint to `%APPDATA%\DeskControl\alexa`; from WSL, pass that folder as
`/mnt/c/Users/<you>/AppData/Roaming/DeskControl/alexa`. Then restart Desk Control (tray → Quit, and open it again).
Once it's connected, the log (`%LOCALAPPDATA%\desk-control\desk-control.log`) says `alexa: connected`, and
**Settings → Alexa** turns the link off and on.

### 4. Finish the skill

Back in the developer console:

1. **Smart Home → Default endpoint**: paste `terraform output lambda_arn`, then click **Save**.
2. **Account linking.** Smart home skills need it, and Login with Amazon is enough for a skill only you use:
   1. In the [Login with Amazon console](https://developer.amazon.com/loginwithamazon/console/site/lwa/overview.html),
      create a security profile (any name, description and privacy URL, such as this repository's URL), then
      copy its **Client ID** and **Client Secret** from **Web Settings**.
   2. In the skill's **Account linking** page, choose **Auth Code Grant** and fill in:

      | Field | Value |
      |---|---|
      | Web Authorization URI | `https://www.amazon.com/ap/oa` |
      | Access Token URI | `https://api.amazon.com/auth/o2/token` |
      | Client ID / Client Secret | from the security profile |
      | Client Authentication Scheme | HTTP Basic |
      | Scope | `profile:user_id` |

   3. Save, then copy the **Alexa Redirect URLs** shown on that page into the security profile's **Allowed
      Return URLs** (under Web Settings → Edit).

### 5. Turn it on

In the Alexa app, go to **More → Skills & Games → Your Skills → Dev**, open **Desk Control**, click **Enable to
use** and sign in with your Amazon account. Alexa then finds a device called **Desk**; you can rename it in the
Alexa app.

**After adding, renaming or deleting a preset**, say “Alexa, discover devices” so Alexa learns the new names.

## Troubleshooting

- **Alexa says the desk isn't responding.** Desk Control isn't running, isn't connected to the desk, or can't
  reach IoT Core. Look for `alexa:` lines in the log.
- **Check what Alexa is sent.** The function's log group is `/aws/lambda/desk-control-alexa` in your region.
- **Every command fails.** If you got the kill switch email, run `terraform apply` again to turn the function
  back on.
- **Test without Alexa.** The function can be called directly, for example:

  ```sh
  aws lambda invoke --function-name desk-control-alexa --cli-binary-format raw-in-base64-out --payload \
    '{"directive":{"header":{"namespace":"Alexa","name":"ReportState","messageId":"1","payloadVersion":"3"},"endpoint":{"endpointId":"desk-control-desk"},"payload":{}}}' \
    /dev/stdout
  ```

- **Run the function's tests.** `python -m pytest alexa/lambda` (needs `boto3` and `pytest`).

## Removing it

Run `terraform destroy` in `alexa/terraform`, delete `%APPDATA%\DeskControl\alexa`, and delete the skill in the
developer console.

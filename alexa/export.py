"""Gives Desk Control the IoT device made by alexa/terraform: its certificate, key and endpoint.

Usage, after `terraform apply` in alexa/terraform:

    terraform -chdir=alexa/terraform output -json | python alexa/export.py [folder]

The folder defaults to %APPDATA%\\DeskControl\\alexa, where the app looks. Then restart Desk Control (tray → Quit, and
open it again).
"""
import json
import os
import shutil
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
if len(sys.argv) > 1:
    folder = Path(sys.argv[1])
elif "APPDATA" in os.environ:
    folder = Path(os.environ["APPDATA"]) / "DeskControl" / "alexa"
else:
    sys.exit("Give the folder to write to, e.g. /mnt/c/Users/<you>/AppData/Roaming/DeskControl/alexa")

outputs = {name: output["value"] for name, output in json.load(sys.stdin).items()}
folder.mkdir(parents=True, exist_ok=True)
(folder / "alexa.json").write_text(json.dumps({"endpoint": outputs["iot_endpoint"], "thing": outputs["thing_name"]},
                                              indent=2))
(folder / "certificate.pem").write_text(outputs["certificate_pem"])
(folder / "private.key").write_text(outputs["private_key"])
shutil.copy(here / "AmazonRootCA1.pem", folder / "ca.pem")
print(f"Wrote the Alexa device settings to {folder}")

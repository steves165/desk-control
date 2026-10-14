# Desk Control

This add-on holds your desk's Bluetooth connection from your Home Assistant box. The
[Desk Control](https://github.com/steves165/desk-control) app on your PC then drives the desk through the
add-on, and so does the Alexa skill if you've set one up. Your presets and calibration live here, so the app,
the widget and Alexa all agree.

The desk accepts **one Bluetooth connection at a time**. Don't also set up Home Assistant's own *IKEA Idåsen
Desk* integration, and close the desk's phone app.

## Setting up

1. **Bluetooth.** Home Assistant must see a Bluetooth adapter: check **Settings → Devices & services →
   Bluetooth**. The box has to be within Bluetooth range of the desk (about 10 m, less through walls). If it
   isn't, an [ESPHome Bluetooth proxy](https://esphome.io/components/bluetooth_proxy.html) near the desk
   doesn't help this add-on, which needs a local adapter; use a USB Bluetooth adapter on an extension lead
   instead.
2. **Start the add-on** and open its **Log** tab. It shows the **pairing key** the app needs, for example:

   ```
   listening on port 8574; pairing key 1A2B-3C4D-5E6F-7A8B
   ```

3. **Connect the app.** In Desk Control on your PC, open **Settings (⚙) → Desk Control box…**, enter the
   box's address (such as `homeassistant.local` or its IP address) and the pairing key, then click
   **Connect**. If the add-on has no presets yet, the app's presets and calibration are copied over.
4. **Choose the desk.** Put the desk in pairing mode (on an Idåsen, hold the Bluetooth button until the light
   flashes blue), then in the app click **Choose desk**, or use **Settings → Change desk**. The list shows
   what the box can see, and the box pairs with the desk itself.

To go back to connecting from the PC, open **Settings → Desk Control box…** and click **Use this PC**.

## Alexa

If you've set up the Alexa skill (see
[alexa/README.md](https://github.com/steves165/desk-control/blob/main/alexa/README.md)), move its device
certificate to the box, so Alexa works while the PC is off:

1. Copy the `alexa` folder from `%APPDATA%\DeskControl` on your PC into this add-on's configuration folder:
   with the **Samba share** add-on, that's `addon_configs\<something>_desk_control\` on the box.
2. Restart the add-on. Its log says `alexa: connected`.

The app stops its own Alexa link while it uses the box, so the two don't take turns.

## Files

Everything is in the add-on's configuration folder (`addon_configs/<something>_desk_control`), which is
included in Home Assistant backups:

| File | What it is |
|---|---|
| `settings.json` | The desk's address and name, calibration and presets. |
| `agent.json` | The pairing key. Delete it and restart the add-on to make a new one. |
| `alexa/` | The Alexa device certificate, if you use Alexa. |

## Security

The app proves it knows the pairing key without sending it, but the connection isn't encrypted: keep port 8574
to your home network, and don't forward it from your router.

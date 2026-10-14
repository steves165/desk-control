"""Developer tool: pair with a desk and list its GATT services and characteristics. Doesn't move the desk.

Usage: python probe.py [name prefix, default "Desk"]   (put the desk in pairing mode first)
"""
import asyncio, sys
from bleak import BleakScanner, BleakClient

NAME = sys.argv[1] if len(sys.argv) > 1 else "Desk"

async def main():
    dev = await BleakScanner.find_device_by_filter(lambda d, a: (d.name or "").startswith(NAME), timeout=15)
    if not dev:
        print("not found"); return
    print("found", dev.name, dev.address)
    async with BleakClient(dev, timeout=45, pair=True) as c:
        for s in c.services:
            print(f"[svc] {s.uuid} {s.description}")
            for ch in s.characteristics:
                val = ""
                if "read" in ch.properties:
                    try: val = (await c.read_gatt_char(ch)).hex()
                    except Exception as e: val = f"<{type(e).__name__}: {e}>"
                print(f"   {ch.uuid} {ch.properties} {ch.description} {val}")

asyncio.run(main())

"""Check that a virtual Xbox 360 pad can be created (ViGEmBus), with a few retries."""
import sys
import time

import vgamepad

print("python", sys.version.split()[0], sys.executable)
for i in range(5):
    try:
        g = vgamepad.VX360Gamepad()
        print(f"try {i + 1}: X360 OK")
        time.sleep(1)
        del g
        break
    except RuntimeError as e:
        print(f"try {i + 1}: {e}")
        time.sleep(1)

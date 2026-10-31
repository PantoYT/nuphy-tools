# nuphy-tools

Analog keys for the NuPhy Field75 HE V2 on Windows: the keyboard's magnetic switches report how
far each key is pressed, and `pad` turns that into a virtual Xbox 360 controller, following a key
map you set up. Smooth steering in racing games and Rocket League, no controller needed.

NuPhyIO has a "Gamepad Simulation" for the original Field75 HE, but it is disabled for the V2.
This tool reads the same key travel data NuPhyIO's Key Travel page shows, over USB HID.

Unofficial, not affiliated with NuPhy. A small novelty tool for one keyboard: for analog input
from many hall effect keyboards (Wooting, NuPhy, DrunkDeer, Razer, Keychron, ...), see
[AnalogSense](https://github.com/AnalogSense) and its Universal Analog Plugin for the Wooting
Analog SDK. Their Field75 HE V2 support uses the protocol notes from here
(calamity-inc/Soup#192, AnalogSense/JavaScript-SDK#14).

## Key map

Any key can drive any part of the pad:

- a **stick half** (left/right stick up, down, left, right): the stick moves as far as the key is
  pressed; opposite halves cancel out;
- a **trigger** (LT, RT): pressed as far as the key is;
- a **button** (A, B, X, Y, LB, RB, Back, Start, stick clicks, d-pad): pressed once the key is
  past the dead zone.

One key can drive several things. A stick binding can also **hold a button** while its key is
down; on that stick axis it then wins over the other keys. Presets:

| Preset | Keys |
|---|---|
| `rocket-league` (default) | A/D left stick X; W/S RT/LT and left stick up/down (air pitch, flips); Q/E LB held + left stick X (analog free air roll) |
| `racing` | A/D left stick X, W RT, S LT |
| `left-stick` | WASD = left stick in four directions |

Each key is scaled from the dead zone (default 5 % of its travel) to the full point (90 %), so
resting noise is ignored and full output is reached just before the bottom.

## Use

```
pip install vgamepad textual         # vgamepad installs the ViGEmBus driver (click through it)
powershell -ExecutionPolicy Bypass -File tools\add-to-path.ps1   # once: `nuphy` in any terminal
nuphy                                # the board
```

The board has four tabs, on the function keys:

1. **F1 live** — every mapped key's travel as you press it, and what the pad sends for it: both
   sticks, the triggers, the buttons. Plain keys do nothing here, so you can press anything;
   Ctrl+Q quits.
2. **F2 keys** — the key map. `a` adds a binding: press the key on the keyboard (read from the
   keyboard itself, so Fn and modifiers work too), then pick what it drives. Enter changes one,
   `d` deletes one, `p` loads a preset.
3. **F3 settings** — dead zone, full point, and the games the pad works for. With games listed,
   the pad only moves while one of them is the window in front; anywhere else it stays plugged in
   but sends nothing, so typing in a browser or chat never steers. Empty = always on.
4. **F4 background** — the pad as a hidden program that starts at logon (Task Scheduler,
   `\nuphy-tools\pad`, run with `pythonw`): install, start/stop, on/off at logon, remove, and its
   log.

Everything lives in `state/config.json`; a running pad reloads it within a second.

The same without the board (`nuphy <command>` = `python nuphyhid.py <command>`):

```
nuphy pad                                  # in the foreground, Ctrl+C to stop
nuphy pad --only RocketLeague.exe,acs.exe  # override the games list for this run
nuphy config dead=0.05 full=0.9 only=RocketLeague.exe map=racing
nuphy task install|start|stop|enable|disable|remove|status
nuphy watch                                # live travel bars for W A S D
nuphy dump                                 # raw input reports
nuphy selftest
python tools/padtest.py                    # can a virtual pad be created at all?
```

`nuphyhid.py` is one file, standard library only; `pad` needs vgamepad and the board needs
textual. A pad that cannot start yet (keyboard unplugged, driver not up at logon) retries every
5 s. If it fails with `VIGEM_ERROR_TARGET_NOT_PLUGGED_IN` right after installing the driver, try
again a bit later or restart Windows.

### Rocket League setup

With the `rocket-league` preset. The keys still send W/A/S/D/Q/E as normal key presses, so take
them off the keyboard side:

- **Keyboard/Mouse column:** unbind Drive Forward/Backwards, Steer Left/Right, Air Steer
  Left/Right, Air Pitch Up/Down, Air Roll Left/Right.
- **Gamepad column:** Air Roll (free) on **LB**; nothing on Air Roll Left/Right (if they sit on
  the left stick, air steer and roll happen at the same time).
- Controller deadzone low (0.05–0.10), since the tool already has one.

Rocket League keeps a single keyboard binding set, so W/A/S/D do nothing in game while the tool
is not running.

## Anti-cheat

Rocket League uses Easy Anti-Cheat for online play since April 2026. A virtual pad gives no more
than a real controller does (no macros, no automation), and the same ViGEmBus driver is what
DS4Windows uses, but neither Psyonix nor Epic has said anything about virtual controllers. Use it
at your own risk; trying it offline first is a good idea. Check the rules of any other game.

## Safety

The keyboard is only read. The one request ever sent is the start of the key status stream,
byte for byte as NuPhyIO sends it, checked against an allowlist; nothing writes the keyboard's
settings. Command numbers on this firmware do not match the enum in the NuPhyIO web bundle, so
nothing is sent from guesswork.

NuPhyIO's Key Travel page also sets a flag in each mode's config that seems to persist. This tool
relies on it being set (open that page once if the pad gets no data) and does not change it.

## Protocol

Documented in the docstring of `nuphyhid.py`. To learn more of it, `tools/capture.ps1` records
USB traffic with [USBPcap](https://desowin.org/usbpcap/) (admin PowerShell) while you use
drive.nuphy.io, and `tools/pcap.py` prints the request/reply pairs.

## Status

Experimental. Tested on one Field75 HE V2 (USB `19F5:6132`, firmware built June 2026), Windows 11.
Other NuPhy HE keyboards probably use the same framing, but their product IDs are not handled yet.

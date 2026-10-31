"""nuphyhid — analog keys of a NuPhy HE keyboard over USB HID (Field75 HE V2, 19F5:6132), Windows.

Everything here was learned from USBPcap captures of NuPhyIO, not guessed. The keyboard is only
read: the one request ever sent is the start of the key status stream, exactly as NuPhyIO sends
it (a strict allowlist). Command numbers on this firmware differ from the NuPhyIO bundle's enum
(241 there is "RestoreFactory", here it reads mode names), so nothing comes from guesswork.

Requests are 64-byte output reports on the interface with 65-byte in/out reports:
  [0] 0x55 request (replies start 0xAA and echo the header)   [1] command
  [2] XOR key (0 = plain)   [3] checksum = sum(bytes 4..63) & 0xFF
  [4] length   [5..6] address, little endian   [7] 0   [8..] data

  command 169     start the key status stream (NuPhyIO's Key Travel page, startKeyStatusUpload).
                  NuPhyIO also sets bit 0x08 in each mode's config (command 6, addresses
                  4/68/132); that flag persists, and this tool does not touch it.

Key status stream: 64-byte input reports, one per changed key, ~10 ms apart:
  [0] 0xA0  [1] 0x10  [3] HID usage of the key (W 1A, A 04, S 16, D 07)
  [4..5] travel, fine units (big endian)       [6..7] travel in 0.01 mm (big endian)
  [9] 01 = actuated, FF = released             [14..15] full travel in 0.01 mm (0x0141 = 3.21)
  [16..17] raw sensor reading                  [18..19] its resting value

    python nuphyhid.py pad              # virtual Xbox pad: A/D steer, W/S throttle/brake, Q/E analog air roll
    python nuphyhid.py pad --only RocketLeague.exe   # override the config's program list
    python nuphyhid.py config [dead=0.05] [full=0.9] [only=RocketLeague.exe,acs.exe]
    python nuphyhid.py task install|remove|start|stop|enable|disable|status   # pad at logon, hidden
    python nuphyhid.py watch            # live travel bars for W A S D
    python nuphyhid.py dump             # every input report as hex
    python nuphyhid.py selftest
    python nuphy_tui.py                 # the board (needs textual)

`pad` needs vgamepad (pip install vgamepad, which runs the ViGEmBus driver installer). The keys
still type W/A/S/D as well, so unbind them on the keyboard side of the game.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

VID, PID = 0x19F5, 0x6132
KEY_STATUS_START = 169
ALLOWED = {(KEY_STATUS_START, 0, 0)}  # (command, address, length) — nothing else is ever sent
REPORT = 0xA0
WASD = {0x1A: "W", 0x04: "A", 0x16: "S", 0x07: "D"}
STATE = Path(__file__).resolve().parent / "state"


# ─── packets ─────────────────────────────────────────────────────────────────

def request_packet(cmd: int, addr: int, length: int, data: bytes = b"") -> bytes:
    if (cmd, addr, length) not in ALLOWED:
        raise ValueError(f"refusing command {cmd} at {addr}/{length}: not in the captured, known-safe set")
    p = bytearray(64)
    p[0], p[1], p[2], p[4], p[5], p[6] = 0x55, cmd, 0, length, addr & 0xFF, addr >> 8
    p[8:8 + len(data)] = data
    p[3] = sum(p[4:64]) & 0xFF
    return bytes(p)


def parse_status(d: bytes) -> dict | None:
    if len(d) < 20 or d[0] != REPORT:
        return None
    travel = d[6] << 8 | d[7]
    full = d[14] << 8 | d[15] or 1
    return {"key": d[3], "travel": travel, "full": full, "fine": d[4] << 8 | d[5],
            "down": d[9] == 0x01, "raw": d[16] << 8 | d[17], "rest": d[18] << 8 | d[19],
            "value": min(travel / full, 1.0)}


# ─── device ──────────────────────────────────────────────────────────────────

def _cached_path() -> str | None:
    try:
        return (STATE / "path.txt").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _remember_path(path: str) -> None:
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        (STATE / "path.txt").write_text(path, encoding="utf-8")
    except OSError:
        pass


def _enumerate() -> list[str]:
    import subprocess
    ps = (f"Get-PnpDevice -PresentOnly -Class HIDClass | ? {{ $_.InstanceId -match "
          f"'^HID\\\\VID_{VID:04X}&PID_{PID:04X}' }} | % InstanceId")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                         timeout=30, creationflags=0x08000000).stdout  # CREATE_NO_WINDOW
    return ["\\\\?\\" + line.strip().replace("\\", "#").lower() + "#{4d1e55b2-f16f-11cf-88cb-001111000030}"
            for line in out.splitlines() if line.strip()]


class Keyboard:
    """The configuration interface of a NuPhy HE keyboard (Windows HID, ctypes)."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ctypes, self.wintypes = ctypes, wintypes
        self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        hid = ctypes.WinDLL("hid")
        self.k32.CreateFileW.restype = wintypes.HANDLE
        # Enumeration costs a PowerShell start (~1-2 s), so the interface path is cached in state/.
        cached = [p for p in [_cached_path()] if p]

        class Caps(ctypes.Structure):
            _fields_ = [("Usage", ctypes.c_ushort), ("UsagePage", ctypes.c_ushort), ("In", ctypes.c_ushort),
                        ("Out", ctypes.c_ushort), ("r", ctypes.c_ushort * 28)]

        self.h = None
        for path in cached + [p for p in _enumerate_lazy(cached)]:
            h = self.k32.CreateFileW(path, 0xC0000000, 3, None, 3, 0, None)
            if h in (None, wintypes.HANDLE(-1).value):
                continue
            pp, caps = ctypes.c_void_p(), Caps()
            hid.HidD_GetPreparsedData(h, ctypes.byref(pp))
            hid.HidP_GetCaps(pp, ctypes.byref(caps))
            hid.HidD_FreePreparsedData(pp)
            if caps.In == 65 and caps.Out == 65:
                self.h = h
                _remember_path(path)
                break
            self.k32.CloseHandle(h)
        if self.h is None:
            raise RuntimeError(f"NuPhy keyboard ({VID:04X}:{PID:04X}) configuration interface not found — is it on USB?")

    def write(self, pkt: bytes) -> None:
        ctypes, wintypes = self.ctypes, self.wintypes
        buf, n = ctypes.create_string_buffer(b"\0" + pkt, 65), wintypes.DWORD()
        if not self.k32.WriteFile(self.h, buf, 65, ctypes.byref(n), None):
            raise OSError(ctypes.get_last_error(), "HID write failed")

    def read(self) -> bytes:
        ctypes, wintypes = self.ctypes, self.wintypes
        rb, n = ctypes.create_string_buffer(65), wintypes.DWORD()
        if not self.k32.ReadFile(self.h, rb, 65, ctypes.byref(n), None):
            raise OSError(ctypes.get_last_error(), "HID read failed")
        return rb.raw[1:n.value]

    def transact(self, pkt: bytes, timeout: float = 1.0) -> bytes:
        import threading
        self.write(pkt)
        for _ in range(64):  # skip unrelated reports (e.g. the key status stream) until our echo
            res = {}
            t = threading.Thread(target=lambda: res.setdefault("d", self.read()), daemon=True)
            t.start()
            t.join(timeout)
            d = res.get("d")
            if d is None:
                break
            if len(d) >= 8 and d[0] == 0xAA and d[1] == pkt[1] and d[5:7] == pkt[5:7]:
                return d
        raise TimeoutError(f"no reply to command {pkt[1]}")

    def reports(self):
        while True:
            yield self.read()

    def start_key_status(self) -> None:
        self.transact(request_packet(KEY_STATUS_START, 0, 0))

    def close(self) -> None:
        self.k32.CloseHandle(self.h)


def _enumerate_lazy(skip: list[str]):
    yield from (p for p in _enumerate() if p not in skip)


# ─── mapping: analog keys to a virtual Xbox pad ─────────────────────────────

IDLE_RESTART = 2.0  # seconds without a report while a key is held: send start again
CONFIG = STATE / "config.json"

# What a key can drive. Stick halves take the key's travel as a 0..1 deflection; buttons are
# pressed as soon as the key is past the dead zone.
STICKS = {"lx-": "left stick left", "lx+": "left stick right", "ly+": "left stick up", "ly-": "left stick down",
          "rx-": "right stick left", "rx+": "right stick right", "ry+": "right stick up",
          "ry-": "right stick down"}
TRIGGERS = {"lt": "left trigger (LT)", "rt": "right trigger (RT)"}
BUTTONS = {"a": "A", "b": "B", "x": "X", "y": "Y", "lb": "LB", "rb": "RB", "back": "Back", "start": "Start",
           "ls": "left stick click", "rs": "right stick click", "up": "d-pad up", "down": "d-pad down",
           "left": "d-pad left", "right": "d-pad right"}
TARGETS = STICKS | TRIGGERS | BUTTONS

# HID usage -> key name, for the keys a keyboard reports (modifiers as the NuPhy stream sends them)
KEY_NAMES = ({0x04 + i: chr(ord("A") + i) for i in range(26)} | {0x1E + i: str((i + 1) % 10) for i in range(10)}
             | {0x28: "Enter", 0x29: "Esc", 0x2A: "Backspace", 0x2B: "Tab", 0x2C: "Space", 0x2D: "-", 0x2E: "=",
                0x2F: "[", 0x30: "]", 0x31: "\\", 0x33: ";", 0x34: "'", 0x35: "`", 0x36: ",", 0x37: ".",
                0x38: "/", 0x39: "Caps Lock", 0x46: "Print Screen", 0x49: "Insert", 0x4A: "Home",
                0x4B: "Page Up", 0x4C: "Delete", 0x4D: "End", 0x4E: "Page Down", 0x4F: "Right", 0x50: "Left",
                0x51: "Down", 0x52: "Up", 0x100: "Left Ctrl", 0x200: "Left Shift", 0x400: "Left Alt",
                0x800: "Left Win", 0x1000: "Right Ctrl", 0x2000: "Right Shift", 0x4000: "Right Alt",
                0x8000: "Right Win", 0xFF05: "Fn"}
             | {0x3A + i: f"F{i + 1}" for i in range(12)})
KEY_CODES = {v.lower(): k for k, v in KEY_NAMES.items()}


def key_name(code: int) -> str:
    return KEY_NAMES.get(code, f"0x{code:02X}")


def key_code(name: str | int) -> int:
    """'W', 'space', 'f1', 26 or '0x1a' -> HID usage."""
    if isinstance(name, int):
        return name
    s = str(name).strip()
    if s.lower() in KEY_CODES:
        return KEY_CODES[s.lower()]
    try:
        return int(s, 0)
    except ValueError:
        raise ValueError(f"unknown key: {name}") from None


def bind(key: str, to: str, hold: str | None = None) -> dict:
    return {"key": key_code(key), "to": to} | ({"hold": hold} if hold else {})


# A binding with "hold" presses that button while its key is down, and on its stick axis it wins
# over the other bindings: Rocket League's free air roll turns stick X into roll while LB is held,
# so Q/E become analog roll without a Shift key, and A/D steering does not mix into it.
PRESETS = {
    "rocket-league": [bind("A", "lx-"), bind("D", "lx+"), bind("W", "rt"), bind("W", "ly+"), bind("S", "lt"),
                      bind("S", "ly-"), bind("Q", "lx-", "lb"), bind("E", "lx+", "lb")],
    "racing": [bind("A", "lx-"), bind("D", "lx+"), bind("W", "rt"), bind("S", "lt")],
    "left-stick": [bind("A", "lx-"), bind("D", "lx+"), bind("W", "ly+"), bind("S", "ly-")],
}
PRESET_NOTES = {
    "rocket-league": "A/D steer, W/S triggers + stick up/down (pitch, flips), Q/E analog air roll on LB",
    "racing": "A/D steer, W throttle (RT), S brake (LT)",
    "left-stick": "WASD = left stick in four directions (walking, flying)",
}

# dead: resting noise below it is ignored; full: output reaches 1.0 here, before the bottom (~0.97);
# only: programs (exe names) the pad works for while they are in front, empty = always;
# map: the bindings
DEFAULTS = {"dead": 0.05, "full": 0.90, "only": [], "map": PRESETS["rocket-league"]}


def check_binding(b: dict) -> dict:
    to = str(b.get("to", "")).lower()
    if to not in TARGETS:
        raise ValueError(f"unknown target: {b.get('to')!r} (one of {', '.join(TARGETS)})")
    hold = b.get("hold")
    hold = str(hold).lower() if hold else None
    if hold and (hold not in BUTTONS or to not in STICKS):
        raise ValueError(f"hold must be a button and only goes with a stick target (got {hold!r} for {to})")
    code = key_code(b.get("key"))
    if not 0 < code <= 0xFFFF:
        raise ValueError(f"key out of range: {code}")
    return {"key": code, "to": to} | ({"hold": hold} if hold else {})


def check_config(cfg: dict) -> dict:
    """Defaults filled in, values checked; raises ValueError on anything that would not work."""
    out = dict(DEFAULTS)
    out.update({k: cfg[k] for k in DEFAULTS if k in cfg})
    dead, full = float(out["dead"]), float(out["full"])
    if not 0.0 <= dead < 0.5 or not 0.5 <= full <= 1.0:
        raise ValueError(f"dead must be 0..0.5 and full 0.5..1 (got {dead}, {full})")
    only = out["only"]
    if isinstance(only, str):
        only = [o for o in only.replace(" ", ",").split(",") if o]
    if isinstance(out["map"], str):
        if out["map"] not in PRESETS:
            raise ValueError(f"unknown preset: {out['map']} (one of {', '.join(PRESETS)})")
        out["map"] = PRESETS[out["map"]]
    seen, bindings = set(), []
    for b in out["map"]:
        b = check_binding(b)
        if (b["key"], b["to"]) not in seen:  # the same key twice on one target adds nothing
            seen.add((b["key"], b["to"]))
            bindings.append(b)
    out.update(dead=dead, full=full, only=sorted({str(o).strip().lower() for o in only if str(o).strip()}),
               map=bindings)
    return out


def load_config() -> dict:
    try:
        return check_config(json.loads(CONFIG.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError, AttributeError):
        return check_config({})


def save_config(cfg: dict) -> dict:
    cfg = check_config(cfg)
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    tmp.replace(CONFIG)
    return cfg


def mapped_keys(cfg: dict) -> list[int]:
    """The keys the map uses, in map order, each once."""
    return list(dict.fromkeys(b["key"] for b in cfg["map"]))


def shape(v: float, cfg: dict = DEFAULTS) -> float:
    dead, full = cfg["dead"], cfg["full"]
    return min(max((v - dead) / (full - dead), 0.0), 1.0)


def outputs(state: dict[int, float], cfg: dict = DEFAULTS) -> dict:
    """Key travel (0..1 per HID usage) -> pad state: lx ly rx ry -1..1, lt rt 0..1, buttons (a set).

    Each stick half and trigger takes the strongest key bound to it; opposite halves subtract.
    On a stick axis where a key bound with "hold" is down, only such keys count (see PRESETS)."""
    halves: dict[str, list[tuple[float, bool]]] = {}
    buttons: set[str] = set()
    held_axes: set[str] = set()
    for b in cfg["map"]:
        v = shape(state.get(b["key"], 0.0), cfg)
        if v <= 0:
            continue
        to = b["to"]
        if to in BUTTONS:
            buttons.add(to)
            continue
        halves.setdefault(to, []).append((v, "hold" in b))
        if "hold" in b:
            buttons.add(b["hold"])
            held_axes.add(to[:2])

    def half(name: str) -> float:
        vals = [v for v, held in halves.get(name, []) if held or name[:2] not in held_axes]
        return max(vals, default=0.0)

    out = {ax: half(ax + "+") - half(ax + "-") for ax in ("lx", "ly", "rx", "ry")}
    out.update(lt=half("lt"), rt=half("rt"), buttons=buttons)
    return out


def bar(v: float, width: int = 12) -> str:
    k = round(v * width)
    return "#" * k + "." * (width - k)


def watch() -> None:
    kb = Keyboard()
    kb.start_key_status()
    state = {k: 0.0 for k in WASD}
    print("press W A S D (Ctrl+C to stop)")
    last = 0.0
    try:
        for d in kb.reports():
            r = parse_status(d)
            if not r or r["key"] not in WASD:
                continue
            state[r["key"]] = r["value"]
            if time.monotonic() - last > 0.03:
                last = time.monotonic()
                print("\r" + "  ".join(f"{WASD[k]} {bar(state[k])} {state[k] * 100:3.0f}%" for k in WASD),
                      end="", flush=True)
    except KeyboardInterrupt:
        print()
    finally:
        kb.close()


def foreground_exe() -> str:
    """File name of the process that owns the foreground window ('' if unknown)."""
    import ctypes
    from ctypes import wintypes
    u32, k32 = ctypes.WinDLL("user32"), ctypes.WinDLL("kernel32")
    pid = wintypes.DWORD()
    u32.GetWindowThreadProcessId(u32.GetForegroundWindow(), ctypes.byref(pid))
    k32.OpenProcess.restype = wintypes.HANDLE
    h = k32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf, n = ctypes.create_unicode_buffer(1024), wintypes.DWORD(1024)
        if not k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return ""
        return Path(buf.value).name
    finally:
        k32.CloseHandle(h)


def is_target(exe: str, only: set[str]) -> bool:
    """No list = always on; otherwise only while one of these programs is in the foreground."""
    return not only or exe.lower() in only


STATUS = STATE / "status.json"


def write_status(**kw) -> None:
    """What the running pad is doing, for the board (it cannot ask the hidden process)."""
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        STATUS.write_text(json.dumps({"pid": os.getpid(), "time": time.time(), **kw}), encoding="utf-8")
    except OSError:
        pass


def read_status() -> dict:
    try:
        return json.loads(STATUS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def pad(only: set[str] | None = None) -> None:
    """only=None: the program list (and dead/full) come from state/config.json and are re-read
    when it changes, so the board can retune a running pad. A given set overrides the list."""
    import threading
    import vgamepad
    override = None if only is None else sorted(o.lower() for o in only)
    conf = [load_config()]
    if override is not None:
        conf[0]["only"] = override
    g = vgamepad.VX360Gamepad()
    kb = Keyboard()
    kb.start_key_status()
    state: dict[int, float] = {}  # every key the stream reports; the map picks what it needs
    xb = vgamepad.XUSB_BUTTON
    vbuttons = {"a": xb.XUSB_GAMEPAD_A, "b": xb.XUSB_GAMEPAD_B, "x": xb.XUSB_GAMEPAD_X, "y": xb.XUSB_GAMEPAD_Y,
                "lb": xb.XUSB_GAMEPAD_LEFT_SHOULDER, "rb": xb.XUSB_GAMEPAD_RIGHT_SHOULDER,
                "back": xb.XUSB_GAMEPAD_BACK, "start": xb.XUSB_GAMEPAD_START,
                "ls": xb.XUSB_GAMEPAD_LEFT_THUMB, "rs": xb.XUSB_GAMEPAD_RIGHT_THUMB,
                "up": xb.XUSB_GAMEPAD_DPAD_UP, "down": xb.XUSB_GAMEPAD_DPAD_DOWN,
                "left": xb.XUSB_GAMEPAD_DPAD_LEFT, "right": xb.XUSB_GAMEPAD_DPAD_RIGHT}
    neutral = {"lx": 0.0, "ly": 0.0, "rx": 0.0, "ry": 0.0, "lt": 0.0, "rt": 0.0, "buttons": set()}
    seen = [time.monotonic()]
    active = [is_target(foreground_exe(), set(conf[0]["only"]))]
    lock = threading.Lock()
    stop = threading.Event()  # ends the helper threads when this run ends (replug, Ctrl+C)
    live = sys.stdout is not None and sys.stdout.isatty()  # the \r status line only in a console

    def send():
        o = outputs(state, conf[0]) if active[0] else neutral
        with lock:
            for name, button in vbuttons.items():
                (g.press_button if name in o["buttons"] else g.release_button)(button=button)
            g.left_joystick_float(x_value_float=o["lx"], y_value_float=o["ly"])
            g.right_joystick_float(x_value_float=o["rx"], y_value_float=o["ry"])
            g.right_trigger_float(value_float=o["rt"])
            g.left_trigger_float(value_float=o["lt"])
            g.update()
        return o

    def describe():
        only = conf[0]["only"]
        return ("active" if active[0] else "paused") + (f" (only: {', '.join(only)})" if only else " (always on)")

    def focus():  # outside the chosen programs the pad stays plugged in but neutral
        mtime, n = None, 0
        while not stop.wait(0.25):
            n += 1
            if override is None and n % 4 == 0:  # config changed (e.g. from the board): reload
                try:
                    m = CONFIG.stat().st_mtime
                except OSError:
                    m = None
                if m != mtime:
                    if mtime is not None or m is not None:
                        conf[0] = load_config()
                        print(f"\nconfig: dead {conf[0]['dead']:.2f}, full {conf[0]['full']:.2f}, "
                              f"only {conf[0]['only'] or 'everything'}, {len(conf[0]['map'])} bindings",
                              flush=True)
                        send()  # a binding may have been removed while its key was down
                    mtime = m
            now = is_target(foreground_exe(), set(conf[0]["only"]))
            if now != active[0]:
                active[0] = now
                send()
                print(f"\n{describe()}", flush=True)
                write_status(state=describe())

    threading.Thread(target=focus, daemon=True).start()
    print(describe(), flush=True)
    write_status(state=describe())

    def watchdog():  # if the stream stops while a key is held, send the captured start again
        while not stop.wait(0.5):
            if any(state.values()) and time.monotonic() - seen[0] > IDLE_RESTART:
                try:
                    kb.write(request_packet(KEY_STATUS_START, 0, 0))  # the echo is skipped below
                except OSError as e:
                    print("\nrestart failed:", e)
                seen[0] = time.monotonic()

    threading.Thread(target=watchdog, daemon=True).start()
    print("virtual Xbox pad on:", ", ".join(f"{key_name(b['key'])} -> {b['to']}"
                                            + (f" (+{b['hold']})" if "hold" in b else "") for b in conf[0]["map"]),
          "- Ctrl+C to stop", flush=True)
    try:
        for d in kb.reports():
            seen[0] = time.monotonic()
            r = parse_status(d)
            if not r:
                continue
            state[r["key"]] = r["value"]
            if not active[0] or all(b["key"] != r["key"] for b in conf[0]["map"]):
                continue
            o = send()
            if live:
                print(f"\rL {o['lx']:+.2f} {o['ly']:+.2f}  R {o['rx']:+.2f} {o['ry']:+.2f}  LT {o['lt']:.2f}  "
                      f"RT {o['rt']:.2f}  {' '.join(sorted(o['buttons'])):12}", end="", flush=True)
    finally:
        stop.set()
        write_status(state="stopped")
        with lock:
            g.reset()
            g.update()
        kb.close()


def pad_forever(only: set[str] | None = None, retry: float = 5.0) -> None:
    """pad() that survives a missing or replugged keyboard and a driver that is not up yet
    (at logon). Without a console (pythonw), output goes to state/pad.log."""
    if sys.stdout is None:
        STATE.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(STATE / "pad.log", "w", encoding="utf-8", buffering=1)
    while True:
        try:
            pad(only)
        except KeyboardInterrupt:
            print()
            return
        except (OSError, RuntimeError, TimeoutError) as e:
            print(f"\n{time.strftime('%H:%M:%S')} {e} — retrying in {retry:.0f} s", flush=True)
            write_status(state=f"waiting: {e}")
        time.sleep(retry)


# ─── background pad (Task Scheduler) ─────────────────────────────────────────

TASK_PATH, TASK_NAME = "\\nuphy-tools\\", "pad"


def _ps(script: str, timeout: float = 30) -> str:
    import subprocess
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                       text=True, timeout=timeout, creationflags=0x08000000)  # CREATE_NO_WINDOW
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout).strip()
                           else f"powershell exit {r.returncode}")
    return r.stdout.strip()


def _task() -> str:
    return f"-TaskPath '{TASK_PATH}' -TaskName '{TASK_NAME}'"


def task_status() -> dict:
    """{'installed': bool, 'state': 'Ready'/'Running'/'Disabled'/..., 'args': ...}"""
    out = _ps(f"$t = Get-ScheduledTask {_task()} -ErrorAction SilentlyContinue; if ($t) {{ "
              f"$t.State.ToString(); $t.Actions[0].Arguments }}")
    lines = out.splitlines()
    return {"installed": bool(lines), "state": lines[0] if lines else "", "args": lines[1] if len(lines) > 1 else ""}


def task_install(delay_s: int = 20) -> None:
    """Run `pad` hidden (pythonw) at logon. Paths come from this Python and this file, nothing fixed."""
    pyw = Path(sys.executable).with_name("pythonw.exe")
    if not pyw.exists():
        raise RuntimeError(f"pythonw.exe not found next to {sys.executable}")
    script, here = Path(__file__).resolve(), Path(__file__).resolve().parent
    _ps(f"$a = New-ScheduledTaskAction -Execute '{pyw}' -Argument '\"{script}\" pad' -WorkingDirectory '{here}'; "
        f"$t = New-ScheduledTaskTrigger -AtLogOn -User \"$env:USERDOMAIN\\$env:USERNAME\"; $t.Delay = 'PT{delay_s}S'; "
        f"$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
        f"-ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew; "
        f"Register-ScheduledTask {_task()} -Action $a -Trigger $t -Settings $s -Force "
        f"-Description 'nuphy-tools: analog keys to a virtual Xbox pad' | Out-Null")


def task_remove() -> None:
    _ps(f"Unregister-ScheduledTask {_task()} -Confirm:$false")


def task_run(on: bool) -> None:
    _ps(f"{'Start' if on else 'Stop'}-ScheduledTask {_task()}")
    if not on:
        write_status(state="stopped")


def task_enable(on: bool) -> None:
    _ps(f"{'Enable' if on else 'Disable'}-ScheduledTask {_task()} | Out-Null")


def dump() -> None:
    kb = Keyboard()
    kb.start_key_status()
    t0 = time.monotonic()
    try:
        for d in kb.reports():
            print(f"{time.monotonic() - t0:8.3f} {d.hex(' ')}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        kb.close()


# ─── selftest ────────────────────────────────────────────────────────────────

def selftest() -> int:
    ok = fail = 0

    def check(c, m):
        nonlocal ok, fail
        ok, fail = (ok + 1, fail) if c else (ok, fail + 1)
        if not c:
            print("  FAIL:", m)

    def raises(f, m):
        try:
            f()
            check(False, m)
        except ValueError:
            check(True, m)

    check(is_target("Whatever.exe", set()), "no --only list = always active")
    check(is_target("RocketLeague.exe", {"rocketleague.exe"}), "listed program matches, any case")
    check(not is_target("Discord.exe", {"rocketleague.exe"}), "other programs pause the pad")
    st = request_packet(KEY_STATUS_START, 0, 0)
    check(st == bytes.fromhex("55a9") + bytes(62), "key status start = captured command 169")
    raises(lambda: request_packet(241, 0, 56), "unknown commands are refused")
    raises(lambda: request_packet(6, 4, 4, b"\x01\x06\x00\x0e"), "config writes are refused (read-only tool)")
    raises(lambda: request_packet(KEY_STATUS_START, 0, 1, b"\x01"), "169 only in its captured form")

    # config
    c = check_config({"only": "RocketLeague.exe, ACS.exe,", "dead": "0.1"})
    check(c["only"] == ["acs.exe", "rocketleague.exe"] and c["dead"] == 0.1 and c["full"] == 0.9,
          "config: comma list normalized, numbers parsed, defaults filled")
    raises(lambda: check_config({"dead": 0.6}), "config: dead >= 0.5 refused")
    raises(lambda: check_config({"full": 1.5}), "config: full > 1 refused")
    check(check_config({"x": 1}) == check_config({}), "config: unknown keys dropped")
    soft = check_config({"dead": 0.0, "full": 0.5})
    check(outputs({0x1A: 0.25}, soft)["rt"] == 0.5, "outputs use the given dead/full")
    c = check_config({"map": [{"key": "space", "to": "A"}, {"key": "0x2c", "to": "a"}, {"key": 4, "to": "rx-"}]})
    check(c["map"] == [{"key": 0x2C, "to": "a"}, {"key": 4, "to": "rx-"}], "map: names/hex/ints, duplicates dropped")
    check(check_config({"map": "racing"})["map"] == PRESETS["racing"], "map: a preset name")
    raises(lambda: check_config({"map": [{"key": "W", "to": "turbo"}]}), "map: unknown target refused")
    raises(lambda: check_config({"map": [{"key": "W", "to": "rt", "hold": "lb"}]}), "map: hold only on sticks")
    raises(lambda: check_config({"map": [{"key": "nokey", "to": "rt"}]}), "map: unknown key name refused")
    check(key_name(0x1A) == "W" and key_code("f1") == 0x3A and key_name(0x200) == "Left Shift", "key names")

    # stream: real reports from the capture (W at the bottom, S released)
    w = bytes.fromhex("a0 10 00 1a 1e e5 01 39 0a 01 ff 00 04 04 01 41 08 f1 0c 45 1e dc 01 39") + bytes(40)
    s = bytes.fromhex("a0 10 00 16 00 00 00 00 7f ff ff 00 04 04 01 41 0c e6 0c d4 00 00 00 00") + bytes(40)
    r = parse_status(w)
    check(r["key"] == 0x1A and r["travel"] == 313 and r["full"] == 321, "W at the bottom: 3.13 of 3.21 mm")
    check(r["down"] and abs(r["value"] - 313 / 321) < 1e-9, "W actuated")
    check(r["raw"] == 0x08F1 and r["rest"] == 0x0C45, "raw sensor and resting value")
    r = parse_status(s)
    check(r["key"] == 0x16 and r["travel"] == 0 and not r["down"] and r["value"] == 0, "S released")
    check(parse_status(bytes.fromhex("aa06") + bytes(62)) is None, "config replies are not status reports")

    # outputs with the default (rocket-league) map
    o = outputs({0x1A: 0.975, 0x04: 0.0, 0x16: 0.02, 0x07: 0.475})
    check(not o["buttons"], "no air roll without Q/E")
    check(o["rt"] == 1.0 and o["lt"] == 0.0, "W at the bottom = full throttle, S noise = no brake")
    check(abs(o["lx"] - 0.5) < 1e-9, "D half way = steer +0.5")
    check(o["ly"] == 1.0, "W = stick up (nose down in the air, front flip)")
    o = outputs({0x07: 1.0, 0x14: 0.475})
    check(o["buttons"] == {"lb"} and abs(o["lx"] + 0.5) < 1e-9, "Q half way = LB held, stick X -0.5 (overrides D)")
    check(not outputs({0x14: 0.02, 0x08: 0.02})["buttons"], "Q/E resting noise does not hold the air roll button")
    check(outputs({0x16: 1.0})["ly"] == -1.0, "S = stick down")
    check(outputs({0x04: 1.0, 0x07: 1.0})["lx"] == 0.0, "A and D together cancel out")
    # other maps
    m = check_config({"map": [{"key": "space", "to": "a"}, {"key": "I", "to": "ry+"}, {"key": "K", "to": "ry-"}]})
    o = outputs({0x2C: 0.3, 0x0C: 0.475}, m)
    check(o["buttons"] == {"a"} and abs(o["ry"] - 0.5) < 1e-9 and o["lx"] == 0, "space = A button, I = right stick up")
    check(outputs({0x0C: 0.02}, m)["ry"] == 0.0, "keys not in the map and resting noise give nothing")
    check(mapped_keys(check_config({})) == [0x04, 0x07, 0x1A, 0x16, 0x14, 0x08], "mapped keys, each once, in order")

    print(f"selftest: {ok}/{ok + fail} OK")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    a = sys.argv[1:]
    cmd = a[0] if a else ""
    if cmd == "selftest":
        sys.exit(selftest())
    elif cmd == "pad":
        only = set(a[a.index("--only") + 1].split(",")) if "--only" in a and a.index("--only") + 1 < len(a) else None
        pad_forever(only)
    elif cmd in ("watch", "dump"):
        {"watch": watch, "dump": dump}[cmd]()
    elif cmd == "config":
        cfg = load_config()
        if a[1:]:
            cfg = save_config(cfg | dict(kv.split("=", 1) for kv in a[1:] if "=" in kv))
        print(json.dumps(cfg, indent=2))
    elif cmd == "task" and len(a) > 1 and a[1] in ("install", "remove", "start", "stop", "enable", "disable",
                                                    "status"):
        {"install": task_install, "remove": task_remove, "start": lambda: task_run(True),
         "stop": lambda: task_run(False), "enable": lambda: task_enable(True),
         "disable": lambda: task_enable(False), "status": lambda: None}[a[1]]()
        print(task_status())
    else:
        print(__doc__)
        sys.exit(2)

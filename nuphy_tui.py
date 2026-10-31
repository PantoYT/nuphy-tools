"""nuphy board — the analog keys and the virtual pad in the terminal (Textual).

    python nuphy_tui.py        (or `nuphy` with no arguments)

Four tabs, on the function keys: F1 live (key travel and what the pad would send), F2 keys (which
key drives which stick, trigger or button — press a key to bind it), F3 settings (dead zone,
full point, the games the pad works for), F4 background (the hidden pad that starts at logon,
and its log). Each tab shows its own keys in the footer; F1 then ? explains it all.

The board never reacts to plain keys on the live tab — that is where you press them — and quits
only with Ctrl+Q, so testing a key can never close it.

Everything goes through nuphyhid.py, so the board, the CLI and the background pad can never
disagree. The board only reads the keyboard; settings are saved to state/config.json and the
running background pad picks them up within a second.
"""

from __future__ import annotations

import threading
import time

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import DataTable, Footer, Input, Label, OptionList, RichLog, Static, TabbedContent, TabPane
from textual.widgets.option_list import Option

import nuphyhid as nh

ACCENT, ERROR = "#e8a04a", "#e06070"
THEME = Theme(name="nuphy", primary=ACCENT, secondary="#6fb7a8", accent=ACCENT, foreground="#d8d4cc",
              background="#141414", surface="#1c1c1c", panel="#262626", success="#6fb7a8", warning="#e0b050",
              error=ERROR, dark=True)
LEARN_AT = 0.5  # a key pressed past half its travel is the one being bound


def meter(v: float, width: int, mark: float | None = None) -> Text:
    """A horizontal bar 0..1; `mark` draws a thin tick (the full point)."""
    v = min(max(v, 0.0), 1.0)
    n = round(v * width)
    out = Text()
    for i in range(width):
        if mark is not None and i == round(mark * width) and i >= n:
            out.append("|", style="dim")
        else:
            out.append("█" if i < n else "·", style="bold " + ACCENT if i < n else "dim")
    return out


def stick(x: float, y: float, w: int = 15, h: int = 7) -> list[Text]:
    """A stick seen from above, as lines: a box with a dot where the stick is."""
    cx, cy = round((x + 1) / 2 * (w - 1)), round((1 - (y + 1) / 2) * (h - 1))
    lines = []
    for r in range(h):
        line = Text()
        for c in range(w):
            if (r, c) == (cy, cx):
                line.append("●", style="bold " + ACCENT if (x or y) else "bold")
            elif r == h // 2 and c == w // 2:
                line.append("+", style="dim")
            elif r in (0, h - 1) or c in (0, w - 1):
                line.append("·", style="dim")
            else:
                line.append(" ")
        lines.append(line)
    return lines


def describe(b: dict) -> str:
    return nh.TARGETS[b["to"]] + (f", holding {nh.BUTTONS[b['hold']]}" if "hold" in b else "")


# ─── dialogs ─────────────────────────────────────────────────────────────────

class Prompt(ModalScreen[str | None]):
    """One-line question. Enter = answer, Esc = cancel."""

    BINDINGS = [Binding("escape", "cancel", "cancel")]

    def __init__(self, question: str, value: str = "", title: str = "nuphy"):
        super().__init__()
        self.question, self.value, self.box_title = question, value, title

    def compose(self) -> ComposeResult:
        with Vertical(id="box", classes="modal"):
            yield Label(self.question, id="q")
            yield Input(value=self.value)

    def on_mount(self) -> None:
        self.query_one("#box").border_title = self.box_title
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)


class Choice(ModalScreen[str | None]):
    """Pick one option from a list. Enter = pick, Esc = cancel."""

    BINDINGS = [Binding("escape", "cancel", "cancel")]

    def __init__(self, question: str, options: list[tuple[str, str]], title: str = "nuphy"):
        super().__init__()
        self.question, self.options, self.box_title = question, options, title

    def compose(self) -> ComposeResult:
        with Vertical(id="cbox", classes="modal"):
            yield Label(self.question, id="cq")
            yield OptionList(*[Option(label, id=oid) for oid, label in self.options])

    def on_mount(self) -> None:
        self.query_one("#cbox").border_title = self.box_title
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class Learn(ModalScreen[int | None]):
    """Wait for a key to be pressed past half its travel, read from the keyboard's own stream
    (not from the terminal), so any key works, Fn and modifiers included. Esc = cancel."""

    BINDINGS = [Binding("escape", "cancel", "cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="lbox", classes="modal"):
            yield Static(id="lmsg")

    def on_mount(self) -> None:
        self.query_one("#lbox").border_title = "bind a key"
        self.held = {k for k, r in self.app.keys.items() if r["value"] >= LEARN_AT}  # already down: not it
        self.set_interval(0.05, self.poll)
        self.poll()

    def poll(self) -> None:
        keys = self.app.keys
        self.held &= {k for k, r in keys.items() if r["value"] >= LEARN_AT}
        down = [k for k, r in keys.items() if r["value"] >= LEARN_AT and k not in self.held]
        if down:
            self.dismiss(max(down, key=lambda k: keys[k]["value"]))
            return
        moving = [k for k, r in keys.items() if r["value"] > 0.02]
        t = Text("Press the key you want to bind, at least half way down.\n\n")
        if self.app.kb_state != "connected":
            t.append(f"keyboard: {self.app.kb_state}", style="bold " + ERROR)
        elif moving:
            k = max(moving, key=lambda k: keys[k]["value"])
            t.append(f"{nh.key_name(k)}  ", style="bold")
            t.append_text(meter(keys[k]["value"] / LEARN_AT, 24))
        else:
            t.append("waiting...", style="dim")
        t.append("\n\nEsc cancels.", style="dim")
        self.query_one("#lmsg", Static).update(t)

    def action_cancel(self) -> None:
        self.dismiss(None)


HELP = """HOW IT WORKS

The keyboard's magnetic switches report how far each key is pressed. A small hidden program
(the background pad) turns that into a virtual Xbox controller, following your key map.

TABS (function keys)
  F1 live        key travel as you press, and what the pad sends for it. No other keys work
                 here, so you can press anything
  F2 keys        the key map: a adds a binding (press the key, then pick what it drives),
                 Enter changes one, d deletes one, p loads a preset
  F3 settings    dead zone, full point, the games the pad works for. Enter changes a row,
                 + / - nudge a number
  F4 background  the hidden pad that starts at logon: i install, s start/stop, e on/off at
                 logon, x remove. Its log is below

Everything is saved at once; the background pad reloads it within a second.

THE KEY MAP
A key on a stick half (e.g. left stick right) moves the stick as far as the key is pressed;
opposite halves cancel out. A key on a trigger presses it as far; a key on a button presses it
as soon as the key is past the dead zone. One key can drive several things (W: throttle and
stick up). "Holding" a button: while that key is down the button is held, and on its stick axis
it wins over the other keys — Rocket League's free air roll on Q/E works that way.

THE GAMES LIST
With games listed, the pad only moves while one of them is the window in front; anywhere else
it stays plugged in but sends nothing, so typing never steers. Empty list = always on.

IN THE GAME
Unbind the mapped keys on the keyboard side of the game. They still type normally elsewhere.

  ?  this help (not on the live tab)     Ctrl+Q  quit
"""


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "close", "close"), Binding("question_mark", "close", show=False)]

    def compose(self) -> ComposeResult:
        with Vertical(id="helpbox", classes="modal"):
            yield Static(Text(HELP))

    def on_mount(self) -> None:
        self.query_one("#helpbox").border_title = "help  ·  Esc closes"

    def action_close(self) -> None:
        self.dismiss(None)


# ─── tabs ────────────────────────────────────────────────────────────────────

class LivePane(Horizontal):
    def compose(self) -> ComposeResult:
        with Vertical(id="keysp", classes="panel"):
            yield Static(id="keys")
        with Vertical(id="padp", classes="panel"):
            yield Static(id="pad")


class MapPane(Vertical):
    BINDINGS = [
        Binding("a", "app.map_add", "add"),
        Binding("enter", "app.map_change", "change"),
        Binding("d,delete", "app.map_delete", "delete"),
        Binding("p", "app.map_preset", "preset"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="mapp", classes="panel"):
            yield DataTable(id="maptable", cursor_type="row")
        yield Static(id="mapnote")


class SettingsPane(Vertical):
    BINDINGS = [
        Binding("enter", "app.setting_edit", "change"),
        Binding("plus,equals_sign", "app.setting_nudge(1)", "more"),
        Binding("minus", "app.setting_nudge(-1)", "less"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="setp", classes="panel"):
            yield DataTable(id="settable", cursor_type="row")
        yield Static(id="setnote")


class BackgroundPane(Vertical):
    BINDINGS = [
        Binding("i", "app.task('install')", "install"),
        Binding("s", "app.task('toggle')", "start / stop"),
        Binding("e", "app.task('enable')", "on at logon"),
        Binding("x", "app.task('remove')", "remove"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="taskp", classes="panel"):
            yield Static(id="task")
        with Vertical(id="padlogp", classes="panel"):
            yield RichLog(id="padlog", markup=False, wrap=True)


class NuphyApp(App):
    TITLE = "nuphy"
    CSS = """
    Screen { layout: vertical; }
    #top { height: 2; padding: 1 2 0 2; background: $panel; color: $foreground; }
    TabbedContent { height: 1fr; }
    TabbedContent ContentSwitcher { height: 1fr; }
    TabPane { padding: 0 1; height: 1fr; }
    Tabs { background: $panel; }
    LivePane, MapPane, SettingsPane, BackgroundPane { height: 1fr; }
    .panel { border: round $foreground 30%; background: $surface; padding: 0 1; }
    .panel:focus-within { border: round $accent; }
    .panel { border-title-color: $foreground 70%; border-title-style: bold; }
    .panel:focus-within { border-title-color: $accent; }
    #keysp { width: 1fr; min-width: 60; height: auto; margin: 1 1 0 0; padding: 1 1; }
    #padp { width: 40; height: auto; margin: 1 0 0 0; padding: 1 1; }
    #mapp, #setp, #taskp { height: auto; margin: 1 0 0 0; }
    #taskp { padding: 1 1; }
    #padlogp { height: 1fr; margin: 1 0 0 0; }
    #padlog { background: $surface; }
    DataTable { height: auto; max-height: 24; background: $surface; }
    DataTable > .datatable--cursor { background: $accent 30%; color: $foreground; text-style: bold; }
    DataTable > .datatable--header { background: $surface; color: $foreground 60%; text-style: none; }
    #mapnote, #setnote { height: auto; color: $foreground 60%; padding: 1 1 0 1; }
    #log { height: 6; margin: 0 1 0 1; border: round $foreground 20%; background: $surface; }
    .modal { width: 84; height: auto; max-height: 80%; border: round $accent; background: $panel; padding: 1 2; }
    .modal { border-title-color: $accent; border-title-style: bold; }
    Prompt, Choice, Learn { align: center middle; }
    #q, #cq { margin-bottom: 1; }
    .modal OptionList { height: auto; max-height: 24; background: $panel; border: none; }
    .footer-key--key { color: $accent; text-style: bold; }
    HelpScreen { align: center middle; }
    #helpbox { width: 100; max-width: 100%; }
    """
    BINDINGS = [
        Binding("f1", "tab('live')", "live"),
        Binding("f2", "tab('map')", "keys"),
        Binding("f3", "tab('settings')", "settings"),
        Binding("f4", "tab('background')", "background"),
        Binding("question_mark", "help", "help"),
        Binding("ctrl+r", "refresh", "refresh", show=False),
        Binding("ctrl+q", "quit", "quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.keys: dict[int, dict] = {}  # every key the stream reported, by HID usage
        self.kb_state = "looking for the keyboard"
        self.cfg = nh.load_config()
        self.bg: dict = {}
        self._log_pos = 0
        self._stop = threading.Event()

    def compose(self) -> ComposeResult:
        yield Static(id="top")
        with TabbedContent(id="tabs", initial="live"):
            with TabPane("F1 live", id="live"):
                yield LivePane()
            with TabPane("F2 keys", id="map"):
                yield MapPane()
            with TabPane("F3 settings", id="settings"):
                yield SettingsPane()
            with TabPane("F4 background", id="background"):
                yield BackgroundPane()
        yield RichLog(id="log", markup=False, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        self.register_theme(THEME)
        self.theme = "nuphy"
        for sel, title in {"#keysp": "keys — travel as you press", "#padp": "what the pad sends",
                           "#mapp": "key map", "#setp": "settings", "#taskp": "background pad",
                           "#padlogp": "its log — state/pad.log", "#log": "log"}.items():
            self.query_one(sel).border_title = title
        self.query_one("#maptable", DataTable).add_columns("key", "drives", "")
        self.query_one("#settable", DataTable).add_columns("setting", "now", "")
        threading.Thread(target=self.reader, daemon=True).start()
        self.set_interval(1 / 30, self.paint_live)
        self.set_interval(3.0, self.refresh_task)
        self.set_interval(1.0, self.tail_padlog)
        self.refresh_map()
        self.refresh_settings()
        self.refresh_task()
        self.tail_padlog()
        self.log_line("F1 live · F2 keys · F3 settings · F4 background   ? help · Ctrl+Q quit")

    def on_unmount(self) -> None:
        self._stop.set()

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        """On the live tab the keys are being pressed for real: no plain-key actions there."""
        if action == "help" and self.active_tab() == "live":
            return False
        return True

    def active_tab(self) -> str:
        try:
            return self.query_one("#tabs", TabbedContent).active
        except Exception:
            return ""

    def log_line(self, line: str) -> None:
        self.query_one("#log", RichLog).write(Text(f"{time.strftime('%H:%M:%S')}  {line}"))

    # ─── keyboard (a thread: HID reads block) ─────────────────────────────

    def reader(self) -> None:
        while not self._stop.is_set():
            try:
                kb = nh.Keyboard()
            except Exception as e:
                self.kb_state = f"not found ({e.__class__.__name__}) — retrying"
                self._stop.wait(3)
                continue
            try:
                kb.start_key_status()
                self.kb_state = "connected"
                for d in kb.reports():
                    if self._stop.is_set():
                        break
                    r = nh.parse_status(d)
                    if r:
                        self.keys[r["key"]] = r
            except Exception as e:
                self.kb_state = f"lost ({e.__class__.__name__}) — retrying"
                self._stop.wait(3)
            finally:
                kb.close()

    def travel(self) -> dict[int, float]:
        return {k: r["value"] for k, r in self.keys.items()}

    # ─── live ─────────────────────────────────────────────────────────────

    def paint_live(self) -> None:
        cfg = self.cfg
        t = Text()
        keys = nh.mapped_keys(cfg)
        if not keys:
            t.append(" No keys mapped. F2 → a to add one.", style="dim")
        for i, k in enumerate(keys):
            r = self.keys.get(k, {"value": 0.0, "travel": 0, "full": 0})
            out = nh.shape(r["value"], cfg)
            mm = f"{r['travel'] / 100:4.2f} mm" if r.get("full") else "   -   "
            t.append(f" {nh.key_name(k):>5} ", style="bold")
            t.append_text(meter(r["value"], 26, mark=cfg["full"]))
            t.append(f"  {mm}  {r['value'] * 100:3.0f}%")
            t.append(f"  → {out * 100:3.0f}%", style="bold" if out else "dim")
            t.append("  " + " · ".join(describe(b) for b in cfg["map"] if b["key"] == k), style="dim")
            if i < len(keys) - 1:
                t.append("\n")
        t.append(f"\n\n dead zone {cfg['dead'] * 100:.0f}%  ·  full at {cfg['full'] * 100:.0f}% (the | mark)"
                 f"  ·  any key works here, Ctrl+Q quits", style="dim")
        self.query_one("#keys", Static).update(t)

        o = nh.outputs(self.travel(), cfg)
        p = Text()
        p.append("     left stick      right stick\n", style="dim")
        for a, b in zip(stick(o["lx"], o["ly"]), stick(o["rx"], o["ry"])):
            p.append(" ")
            p.append_text(a)
            p.append("  ")
            p.append_text(b)
            p.append("\n")
        p.append(f" {o['lx']:+.2f} {o['ly']:+.2f}       {o['rx']:+.2f} {o['ry']:+.2f}\n\n")
        for name in ("lt", "rt"):
            p.append(f" {name.upper()} ", style="bold")
            p.append_text(meter(o[name], 26))
            p.append(f" {o[name] * 100:3.0f}%\n")
        p.append("\n buttons ", style="bold")
        pressed = [nh.BUTTONS[b] for b in nh.BUTTONS if b in o["buttons"]]
        p.append(", ".join(pressed) if pressed else "-", style="bold " + ACCENT if pressed else "dim")
        p.append("\n\n preview from this board; the background pad\n only sends it while a listed game is in front",
                 style="dim")
        self.query_one("#pad", Static).update(p)
        self.paint_top()

    def paint_top(self) -> None:
        st = nh.read_status()
        bg = self.bg.get("state") or "not installed"
        if self.bg.get("state") == "Running" and st.get("state"):
            bg = st["state"]
        only = ", ".join(self.cfg["only"]) or "every program"
        t = Text()
        t.append(" keyboard ", style="dim")
        t.append(self.kb_state, style="bold" if self.kb_state == "connected" else "bold " + ERROR)
        t.append("    background pad ", style="dim")
        t.append(bg, style="bold")
        t.append("    for ", style="dim")
        t.append(only, style="bold")
        self.query_one("#top", Static).update(t)

    # ─── saving ───────────────────────────────────────────────────────────

    def save(self, change: dict, what: str) -> bool:
        try:
            self.cfg = nh.save_config(self.cfg | change)
            self.log_line(f"saved: {what}")
            ok = True
        except (ValueError, OSError) as e:
            self.log_line(f"error: {e}")
            ok = False
        self.refresh_map()
        self.refresh_settings()
        return ok

    # ─── key map ──────────────────────────────────────────────────────────

    def refresh_map(self) -> None:
        tbl = self.query_one("#maptable", DataTable)
        row = tbl.cursor_row
        tbl.clear()
        for i, b in enumerate(self.cfg["map"]):
            tbl.add_row(nh.key_name(b["key"]), describe(b), b["to"] + (f" +{b['hold']}" if "hold" in b else ""),
                        key=str(i))
        if self.cfg["map"]:
            tbl.move_cursor(row=min(row, len(self.cfg["map"]) - 1))
        self.query_one("#mapnote", Static).update(
            "a adds a binding: press the key on the keyboard, then pick what it drives. One key can drive\n"
            "several things. p loads a preset: " + "; ".join(f"{n} — {d}" for n, d in nh.PRESET_NOTES.items()))

    def _binding(self) -> int | None:
        tbl = self.query_one("#maptable", DataTable)
        if not self.cfg["map"] or tbl.row_count == 0:
            return None
        return int(tbl.coordinate_to_cell_key(tbl.cursor_coordinate).row_key.value)

    def pick_target(self, key: int, done) -> None:
        """Target, then (for a stick half) whether to hold a button with it."""
        opts = [(t, f"{d}  ({t})") for t, d in nh.TARGETS.items()]

        def got_target(to: str | None) -> None:
            if to is None:
                return
            if to not in nh.STICKS:
                done({"key": key, "to": to})
                return
            holds = [("none", "nothing else")] + [(b, f"hold {d} while this key is down") for b, d in nh.BUTTONS.items()]
            self.push_screen(Choice(f"{nh.key_name(key)} → {nh.TARGETS[to]}. Hold a button with it too? "
                                    "(Rocket League air roll: LB)", holds, title="key map"),
                             lambda h: None if h is None else done({"key": key, "to": to}
                                                                       | ({"hold": h} if h != "none" else {})))

        self.push_screen(Choice(f"What should {nh.key_name(key)} drive?", opts, title="key map"), got_target)

    def action_map_add(self) -> None:
        def learned(key: int | None) -> None:
            if key is not None:
                self.pick_target(key, lambda b: self.save({"map": self.cfg["map"] + [b]},
                                                          f"{nh.key_name(key)} → {describe(b)}"))

        self.push_screen(Learn(), learned)

    def action_map_change(self) -> None:
        i = self._binding()
        if i is None:
            return self.action_map_add()
        old = self.cfg["map"][i]

        def changed(b: dict) -> None:
            m = list(self.cfg["map"])
            m[i] = b
            self.save({"map": m}, f"{nh.key_name(b['key'])} → {describe(b)}")

        self.pick_target(old["key"], changed)

    def action_map_delete(self) -> None:
        i = self._binding()
        if i is None:
            return
        b = self.cfg["map"][i]
        self.save({"map": self.cfg["map"][:i] + self.cfg["map"][i + 1:]},
                  f"removed {nh.key_name(b['key'])} → {describe(b)}")

    def action_map_preset(self) -> None:
        opts = [(n, f"{n}  —  {d}") for n, d in nh.PRESET_NOTES.items()]
        self.push_screen(Choice("Replace the whole key map with a preset:", opts, title="key map"),
                         lambda n: n and self.save({"map": nh.PRESETS[n]}, f"preset {n}"))

    # ─── settings ─────────────────────────────────────────────────────────

    def refresh_settings(self) -> None:
        tbl = self.query_one("#settable", DataTable)
        row = tbl.cursor_row
        tbl.clear()
        c = self.cfg
        tbl.add_row("dead zone", f"{c['dead'] * 100:.0f}%", "travel below this is ignored (resting noise)", key="dead")
        tbl.add_row("full at", f"{c['full'] * 100:.0f}%", "output reaches 100% here, a bit before the bottom",
                    key="full")
        tbl.add_row("games", ", ".join(c["only"]) or "every program",
                    "the pad only moves while one of these is in front", key="only")
        tbl.move_cursor(row=min(row, 2))
        self.query_one("#setnote", Static).update(
            "Games are exe names, comma separated (Task Manager → Details shows them). Empty = always on.")

    def _setting(self) -> str:
        tbl = self.query_one("#settable", DataTable)
        return tbl.coordinate_to_cell_key(tbl.cursor_coordinate).row_key.value

    def action_setting_nudge(self, step: int) -> None:
        key = self._setting()
        if key in ("dead", "full"):
            v = round(self.cfg[key] + step * 0.01, 2)
            self.save({key: v}, f"{'dead zone' if key == 'dead' else 'full at'} {v * 100:.0f}%")

    def action_setting_edit(self) -> None:
        key = self._setting()
        if key == "only":
            q, val = "Games (exe names, comma separated; empty = every program):", ", ".join(self.cfg["only"])
        else:
            q, val = f"{'Dead zone' if key == 'dead' else 'Full at'} in percent:", f"{self.cfg[key] * 100:.0f}"

        def done(ans: str | None) -> None:
            if ans is None:
                return
            if key == "only":
                self.save({"only": ans}, f"games: {ans or 'every program'}")
            else:
                try:
                    self.save({key: float(ans.rstrip("%")) / 100}, f"{key} {ans}%")
                except ValueError:
                    self.log_line(f"error: not a number: {ans}")

        self.push_screen(Prompt(q, val, title="settings"), done)

    # ─── background pad ───────────────────────────────────────────────────

    @work(thread=True, exclusive=True, group="task")
    def refresh_task(self) -> None:
        try:
            self.bg = nh.task_status()
        except Exception as e:
            self.bg = {"installed": False, "state": f"unknown ({e})", "args": ""}
        self.call_from_thread(self.paint_task)

    def paint_task(self) -> None:
        t, st = self.bg, nh.read_status()
        out = Text()
        if not t.get("installed"):
            out.append("Not installed. ", style="bold")
            out.append("Press i to run the pad hidden at every logon (Task Scheduler, \\nuphy-tools\\pad).",
                       style="dim")
        else:
            out.append("task        ", style="dim")
            out.append(f"{t['state']}\n", style="bold")
            out.append("pad         ", style="dim")
            out.append(f"{st.get('state', '-') if t['state'] == 'Running' else 'not running'}\n", style="bold")
            out.append("runs        ", style="dim")
            out.append(f"pythonw {t['args']}\n")
            out.append("at logon    ", style="dim")
            out.append("off (disabled)" if t["state"] == "Disabled" else "on, 20 s after logon")
        self.query_one("#task", Static).update(out)
        self.paint_top()

    @work(thread=True, group="task")
    def action_task(self, what: str) -> None:
        t = self.bg
        try:
            if what == "install":
                nh.task_install()
                msg = "installed: the pad starts hidden at every logon"
            elif not t.get("installed"):
                msg = "not installed yet — press i"
            elif what == "toggle":
                on = t["state"] != "Running"
                nh.task_run(on)
                msg = "background pad started" if on else "background pad stopped"
            elif what == "enable":
                on = t["state"] == "Disabled"
                nh.task_enable(on)
                msg = "starts at logon again" if on else "won't start at logon (still installed)"
            elif what == "remove":
                if t["state"] == "Running":
                    nh.task_run(False)
                nh.task_remove()
                msg = "removed from Task Scheduler"
            else:
                msg = f"unknown: {what}"
        except Exception as e:
            msg = f"error: {e}"
        self.call_from_thread(self.log_line, msg)
        time.sleep(1.5)  # let the task change state before reading it
        self.refresh_task()

    def tail_padlog(self) -> None:
        log = self.query_one("#padlog", RichLog)
        try:
            data = (nh.STATE / "pad.log").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        if len(data) < self._log_pos:  # the pad restarted and began a new log
            log.clear()
            self._log_pos = 0
        for line in data[self._log_pos:].splitlines():
            if line.strip():
                log.write(Text(line))
        self._log_pos = len(data)

    # ─── app ──────────────────────────────────────────────────────────────

    def action_tab(self, name: str) -> None:
        self.query_one("#tabs", TabbedContent).active = name
        self.refresh_bindings()

    def on_tabbed_content_tab_activated(self, event) -> None:
        self.refresh_bindings()  # the help key is off on the live tab

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_refresh(self) -> None:
        self.cfg = nh.load_config()
        self.refresh_map()
        self.refresh_settings()
        self.refresh_task()


if __name__ == "__main__":
    NuphyApp().run()

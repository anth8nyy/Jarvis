"""Mac system control and status — volume, battery, disk, Wi-Fi, uptime, screen.

All local, all free. Volume is the only state change here and it's harmless, so
nothing needs a confirmation gate.
"""

from __future__ import annotations

import re
import subprocess

from jarvis.registry import Registry, Tool


def _sh(cmd: list, timeout: float = 10.0) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except Exception:
        return ""


def _osa(script: str) -> str:
    return _sh(["osascript", "-e", script])


# --- volume -----------------------------------------------------------------

def set_volume(level: int) -> str:
    level = max(0, min(100, int(level)))
    _osa(f"set volume output volume {level}")
    if level == 0:
        return "Muted, sir."
    return f"Volume set to {level} percent, sir."


def get_volume() -> str:
    vol = _osa("output volume of (get volume settings)")
    muted = _osa("output muted of (get volume settings)")
    if muted == "true":
        return "Sound is muted, sir."
    return f"Volume is at {vol} percent, sir." if vol else "I couldn't read the volume, sir."


def set_mute(mute: bool = True) -> str:
    _osa(f"set volume {'with' if mute else 'without'} output muted")
    return "Muted, sir." if mute else "Unmuted, sir."


# --- status readouts --------------------------------------------------------

def battery() -> str:
    out = _sh(["pmset", "-g", "batt"])
    m = re.search(r"(\d+)%", out)
    if not m:
        return "I couldn't read the battery, sir — this may be a desktop Mac."
    pct = int(m.group(1))
    charging = "charging" in out and "discharging" not in out
    ac = "AC Power" in out or "charged" in out and "discharging" not in out
    tm = re.search(r"(\d+:\d\d) remaining", out)
    if pct >= 95 and (ac or "charged" in out):
        return "Battery is essentially full, sir."
    state = "charging" if charging else ("plugged in" if ac else "on battery")
    msg = f"Battery is at {pct} percent, {state}"
    if tm and not charging and not ac:
        msg += f", about {tm.group(1)} remaining"
    return msg + ", sir."


def disk() -> str:
    out = _sh(["df", "-Hl", "/"])
    lines = out.splitlines()
    if len(lines) < 2:
        return "I couldn't read the disk, sir."
    parts = lines[1].split()
    # size used avail capacity ... — GNU/BSD df: fields 1,2,3,4
    size, used, avail, pct = parts[1], parts[2], parts[3], parts[4]
    return f"Your disk is {pct} full — {avail}B free of {size}B, sir."


def wifi() -> str:
    ssid = ""
    summary = _sh(["ipconfig", "getsummary", "en0"])
    m = re.search(r"\bSSID\s*:\s*(.+)", summary)
    if m:
        ssid = m.group(1).strip()
    ip = _sh(["ipconfig", "getifaddr", "en0"])
    if not ssid and not ip:
        return "You don't appear to be on Wi-Fi, sir."
    if ssid and ip:
        return f"You're on {ssid}, sir, at {ip}."
    if ip:
        return f"You're connected at {ip}, sir."
    return f"You're on {ssid}, sir."


def uptime() -> str:
    out = _sh(["uptime"])
    m = re.search(r"up\s+(.+?),\s+\d+\s+user", out)
    if m:
        return f"This Mac has been up {m.group(1).strip()}, sir."
    return "I couldn't read the uptime, sir."


def screens() -> str:
    out = _sh(["system_profiler", "SPDisplaysDataType"], timeout=15)
    res = re.findall(r"Resolution:\s*(.+)", out)
    if not res:
        return "I couldn't read the display, sir."
    if len(res) == 1:
        return f"You have one display at {res[0].strip()}, sir."
    joined = "; ".join(r.strip() for r in res)
    return f"You have {len(res)} displays, sir: {joined}."


def register(registry: Registry) -> None:
    registry.register(Tool(
        name="set_volume",
        description="Set the Mac's system output volume, 0 to 100. Use for 'set volume to 50', 'turn it up/down' (pick a sensible level), 'volume 30 percent'.",
        input_schema={"type": "object", "properties": {
            "level": {"type": "integer", "description": "0–100."}}, "required": ["level"]},
        handler=set_volume,
    ))
    registry.register(Tool(
        name="get_volume",
        description="Report the current system volume / whether sound is muted.",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=get_volume,
    ))
    registry.register(Tool(
        name="set_mute",
        description="Mute or unmute the Mac's sound. mute=true to silence, false to restore.",
        input_schema={"type": "object", "properties": {
            "mute": {"type": "boolean", "description": "True to mute, false to unmute."}},
            "required": ["mute"]},
        handler=set_mute,
    ))
    registry.register(Tool(
        name="battery_status",
        description="Report battery percentage and whether it's charging or on battery. Use for 'how much battery', 'am I charging'.",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=battery,
    ))
    registry.register(Tool(
        name="disk_space",
        description="Report free disk space on the Mac. Use for 'how much storage/disk is left'.",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=disk,
    ))
    registry.register(Tool(
        name="wifi_info",
        description="Report the Wi-Fi network name and local IP address. Use for 'what wifi am I on', 'what's my IP'.",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=wifi,
    ))
    registry.register(Tool(
        name="uptime",
        description="Report how long the Mac has been running since last boot.",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=uptime,
    ))
    registry.register(Tool(
        name="screen_info",
        description="Report the display(s) and their resolution. Use for 'what's my screen resolution', 'how many monitors'.",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=screens,
    ))

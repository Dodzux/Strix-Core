"""Four-zone lighting illustrations. Pure math; never opens an RGB device."""
import colorsys
from functools import lru_cache
import math
import re

EFFECTS = ("Static", "Breathing", "Spectrum", "Wallpaper sync", "Color cycle",
           "Wave", "Pulse", "Comet", "Starlight", "Gradient")
ANIMATED = set(EFFECTS) - {"Static", "Wallpaper sync", "Gradient"}


@lru_cache(maxsize=128)
def rgb(color):
    return tuple(int(color.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4))


def validate_lighting(value):
    if not isinstance(value, dict) or value.get("effect") not in EFFECTS:
        raise ValueError("Choose an available lighting effect.")
    colors = value.get("zones", [value.get("color")] * 4)
    if not isinstance(colors, list) or len(colors) != 4 or any(
        not isinstance(c, str) or not re.fullmatch(r"#[a-fA-F0-9]{6}", c) for c in colors
    ):
        raise ValueError("Each of the four zones needs a six-digit hex color.")
    speed = value.get("speed", 1)
    if type(speed) not in (int, float) or not math.isfinite(speed) or not .25 <= speed <= 3:
        raise ValueError("Animation speed must be between 0.25× and 3×.")
    paused = value.get('paused', False)
    if type(paused) is not bool:
        raise ValueError('Animation pause must be true or false.')
    intensity = value.get('intensity', 100)
    if type(intensity) not in (int, float) or not math.isfinite(intensity) or not 0 <= intensity <= 100:
        raise ValueError('Effect brightness must be between 0 and 100%.')
    return {"effect": value["effect"], "color": colors[0], "zones": list(colors), "speed": speed,
            'paused': paused, 'intensity': intensity}


def sample(effect, zones, position, seconds=0, speed=1, row=0):
    """Return RGB in [0,1] for one key at normalized horizontal position."""
    x = max(0, min(.999999, position))
    phase = seconds * speed
    color = rgb(zones[min(3, int(x * 4))])
    if effect in ("Spectrum", "Color cycle"):
        return colorsys.hsv_to_rgb((phase * .12 + (x if effect == "Spectrum" else 0)) % 1, .65, 1)
    if effect in ("Gradient", "Wave"):
        if effect == "Wave":
            # Close the color ring: crossing the fourth zone must blend back
            # into the first rather than jump abruptly at every cycle.
            place = ((x + phase * .14) % 1) * 4
            index = int(place); blend = place-index
            a, b = rgb(zones[index]), rgb(zones[(index+1)%4])
            return tuple(v*(1-blend)+w*blend for v,w in zip(a,b))
        place = x * 3
        index = min(2, int(place)); blend = place - index
        a, b = rgb(zones[index]), rgb(zones[index + 1])
        return tuple(v * (1 - blend) + w * blend for v, w in zip(a, b))
    brightness = 1
    if effect == "Breathing":
        brightness = .18 + .82 * (.5 + .5 * math.sin(phase * math.pi))
    elif effect == "Pulse":
        brightness = .15 + .85 * max(0, math.sin(phase * 4 - x * 5)) ** 3
    elif effect == "Comet":
        distance = ((phase * .25) % 1 - x) % 1
        brightness = .12 + .88 * max(0, 1 - distance / .3) ** 2
    elif effect == "Starlight":
        brightness = .12 + .88 * max(0, math.sin(phase * 1.6 + int(x * 14) * 17 + row * 11)) ** 12
    return tuple(c * brightness for c in color)

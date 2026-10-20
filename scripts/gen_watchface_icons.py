#!/usr/bin/env python3
"""Generate Pebble resource PNGs from the provided 16x16 icon pack.

Reads the original icons from ``watchface/resources/src/icons/`` (kept verbatim as
provenance, never referenced by Pebble directly) and writes the resource PNGs into
``watchface/resources/images/``.

The pack ships **white glyphs on opaque black tiles** (with transparent corners).
The watchface background is black, so recoloring keys on luminance — this is the
"invert" the mockup needs:

- bright pixels (the glyph) -> target color (visible)
- dark pixels (the tile)   -> transparent (invisible against the black watchface)

Nice consequence: the battery icons read correctly — ``batt_empty``'s black interior
becomes transparent (looks empty), ``batt_full``'s white interior stays filled.

Icons ship at **1.5x nearest-neighbor upscale (24x24)**: the luminance fix made the glyphs
readable at native size, and 24x24 splits the difference between too small (16x16) and too
dominant (32x32) on the 200x228 emery display. Note: non-integer nearest-neighbor scaling
gives slightly uneven pixel widths (some source pixels double, some stay single) — subtle at
real watch size, visible when magnified.

Each icon is emitted **twice**: a dark-theme set (``<name>.png``) and a light-theme set
(``<name>_light.png``) with high-contrast colors tuned for a light background (near-black
weather/battery glyphs, deep teal/red metric glyphs) — the watchface swaps sets when the
user picks a theme.

Deterministic: re-running produces byte-identical PNGs.

Usage (repo root):  uv run --with pillow python scripts/gen_watchface_icons.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "watchface" / "resources" / "src" / "icons"
OUT = ROOT / "watchface" / "resources" / "images"

TEAL = (54, 183, 158)
RED = (240, 72, 74)
WHITE = (235, 235, 235)
TEAL_LIGHT = (13, 106, 88)
RED_LIGHT = (176, 30, 36)
DARK_GREY = (28, 28, 30)
SCALE = 1.5  # 16x16 pack -> 24x24 resources

# source pack filename -> (resource base name, dark color, light color, rotate 90° clockwise)
MANIFEST: dict[str, tuple[str, tuple[int, int, int], tuple[int, int, int], bool]] = {
    # metric icons
    "Misc_Heartbeat_Line_2.png": ("icon_heartbeat", RED, RED_LIGHT, False),
    "Travel_Footstep_Tracks.png": ("icon_steps", TEAL, TEAL_LIGHT, False),
    "RPG_Skill_Dash_Dodge_Movement_Speed_Run_Sprint.png": ("icon_active", TEAL, TEAL_LIGHT, False),
    "Weather_Moon_Night_Crescent_Darkness_Mode_Twilight_Big.png": ("icon_sleep", TEAL, TEAL_LIGHT, False),
    # weather condition icons (panel slot)
    "Weather_Sun_Holy_Light_Rays_Summer_Season.png": ("wx_clear", TEAL, TEAL_LIGHT, False),
    "Weather_Sunny_Cloudy_Partial.png": ("wx_partly", TEAL, TEAL_LIGHT, False),
    "Weather_Cloudy_Overcast.png": ("wx_cloudy", TEAL, TEAL_LIGHT, False),
    "Weather_Umbrella_Rain_Parasol.png": ("wx_drizzle", TEAL, TEAL_LIGHT, False),
    "Weather_Raining_Cloud_Wet.png": ("wx_rain", TEAL, TEAL_LIGHT, False),
    "Weather_Snowflake_Freeze_Cold_Snowstorm_Frozen_Frost_Element_Winter_Season.png": ("wx_snow", TEAL, TEAL_LIGHT, False),
    "Weather_Thunderstorm_Cloud_Lightning_Zap.png": ("wx_storm", TEAL, TEAL_LIGHT, False),
    # battery icons (top row) — the pack ships them vertical; rotate to horizontal
    "Software_Battery_Power_Level_0_Empty.png": ("batt_empty", WHITE, DARK_GREY, True),
    "Software_Battery_Power_Level_1.png": ("batt_low", WHITE, DARK_GREY, True),
    "Software_Battery_Power_Level_2.png": ("batt_mid", WHITE, DARK_GREY, True),
    "Software_Battery_Power_Level_3_Full.png": ("batt_full", WHITE, DARK_GREY, True),
    "Software_Battery_Power_Level_Charging.png": ("batt_charging", WHITE, DARK_GREY, True),
}

# Unused pack icons stay in SRC only (provenance): Hiking, Sunny_Warm_Hot,
# Snowstorm_Cloud, Tornado.


def recolor(src: Path, dst: Path, color: tuple[int, int, int], rotate: bool = False) -> None:
    rgba = Image.open(src).convert("RGBA")
    w, h = rgba.size

    # palette: index 0 = transparent, index 1 = target color
    out = Image.new("P", (w, h))
    out.putpalette([0, 0, 0, color[0], color[1], color[2]] + [0, 0, 0] * 254)

    # luminance key: glyph (bright) -> target color; black tile -> transparent
    data = bytearray(w * h)
    for i, (r, g, b, a) in enumerate(rgba.getdata()):
        lum = (r + g + b) // 3
        data[i] = 1 if (a >= 128 and lum >= 128) else 0
    out.putdata(data)
    if rotate:
        # vertical pack battery -> horizontal (nub ends up on the right)
        out = out.transpose(Image.Transpose.ROTATE_270)
    out = out.resize((int(w * SCALE), int(h * SCALE)), Image.NEAREST)
    out.save(dst, transparency=0)


def main() -> None:
    if not SRC.is_dir():
        raise SystemExit(f"missing {SRC}; copy the provided icon pack there first")
    OUT.mkdir(parents=True, exist_ok=True)
    for src_name, (base, dark, light, rotate) in MANIFEST.items():
        src = SRC / src_name
        if not src.is_file():
            raise SystemExit(f"missing pack icon: {src_name}")
        recolor(src, OUT / f"{base}.png", dark, rotate)
        recolor(src, OUT / f"{base}_light.png", light, rotate)
        print(f"wrote {OUT / base}.png + _light.png")


if __name__ == "__main__":
    main()
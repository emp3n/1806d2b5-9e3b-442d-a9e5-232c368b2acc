"""Three curated palettes built on Radix 12-step scales.

Primitive values (raw hex) live ONLY here. Component CSS in theme.py uses
semantic tokens, so changing a role never means hunting through selectors.

Layers:
  primitive   --sand-2, --jade-11, ...        raw scales, copied from Radix
  semantic    --card, --ok, --crit-tint ...   purpose, mapped per palette+mode
  component   --lv, --knob, --shadow ...      consumed by the card selectors

Radix step contract: 1-2 backgrounds, 3-5 components, 6-8 borders,
9-10 solids, 11 low-contrast text, 12 high-contrast text.
"""
from __future__ import annotations

# Copied from @radix-ui/colors (light/dark), not imported at runtime.
SCALES: dict[str, dict[str, list[str]]] = {
    "sand": {
        "light": [
            "#FDFDFC",
            "#F9F9F8",
            "#F1F0EF",
            "#E9E8E6",
            "#E2E1DE",
            "#DAD9D6",
            "#CFCECA",
            "#BCBBB5",
            "#8D8D86",
            "#82827C",
            "#63635E",
            "#21201C"
        ],
        "dark": [
            "#111110",
            "#191918",
            "#222221",
            "#2A2A28",
            "#31312E",
            "#3B3A37",
            "#494844",
            "#62605B",
            "#6F6D66",
            "#7C7B74",
            "#B5B3AD",
            "#EEEEEC"
        ]
    },
    "slate": {
        "light": [
            "#FCFCFD",
            "#F9F9FB",
            "#F0F0F3",
            "#E8E8EC",
            "#E0E1E6",
            "#D9D9E0",
            "#CDCED6",
            "#B9BBC6",
            "#8B8D98",
            "#80838D",
            "#60646C",
            "#1C2024"
        ],
        "dark": [
            "#111113",
            "#18191B",
            "#212225",
            "#272A2D",
            "#2E3135",
            "#363A3F",
            "#43484E",
            "#5A6169",
            "#696E77",
            "#777B84",
            "#B0B4BA",
            "#EDEEF0"
        ]
    },
    "olive": {
        "light": [
            "#FCFDFC",
            "#F8FAF8",
            "#EFF1EF",
            "#E7E9E7",
            "#DFE2DF",
            "#D7DAD7",
            "#CCCFCC",
            "#B9BCB8",
            "#898E87",
            "#7F847D",
            "#60655F",
            "#1D211C"
        ],
        "dark": [
            "#111210",
            "#181917",
            "#212220",
            "#282A27",
            "#2F312E",
            "#383A36",
            "#454843",
            "#5C625B",
            "#687066",
            "#767D74",
            "#AFB5AD",
            "#ECEEEC"
        ]
    },
    "orange": {
        "light": [
            "#FEFCFB",
            "#FFF7ED",
            "#FFEFD6",
            "#FFDFB5",
            "#FFD19A",
            "#FFC182",
            "#F5AE73",
            "#EC9455",
            "#F76B15",
            "#EF5F00",
            "#CC4E00",
            "#582D1D"
        ],
        "dark": [
            "#17120E",
            "#1E160F",
            "#331E0B",
            "#462100",
            "#562800",
            "#66350C",
            "#7E451D",
            "#A35829",
            "#F76B15",
            "#FF801F",
            "#FFA057",
            "#FFE0C2"
        ]
    },
    "blue": {
        "light": [
            "#FBFDFF",
            "#F4FAFF",
            "#E6F4FE",
            "#D5EFFF",
            "#C2E5FF",
            "#ACD8FC",
            "#8EC8F6",
            "#5EB1EF",
            "#0090FF",
            "#0588F0",
            "#0D74CE",
            "#113264"
        ],
        "dark": [
            "#0D1520",
            "#111927",
            "#0D2847",
            "#003362",
            "#004074",
            "#104D87",
            "#205D9E",
            "#2870BD",
            "#0090FF",
            "#3B9EFF",
            "#70B8FF",
            "#C2E6FF"
        ]
    },
    "grass": {
        "light": [
            "#FBFEFB",
            "#F5FBF5",
            "#E9F6E9",
            "#DAF1DB",
            "#C9E8CA",
            "#B2DDB5",
            "#94CE9A",
            "#65BA74",
            "#46A758",
            "#3E9B4F",
            "#2A7E3B",
            "#203C25"
        ],
        "dark": [
            "#0E1511",
            "#141A15",
            "#1B2A1E",
            "#1D3A24",
            "#25482D",
            "#2D5736",
            "#366740",
            "#3E7949",
            "#46A758",
            "#53B365",
            "#71D083",
            "#C2F0C2"
        ]
    },
    "jade": {
        "light": [
            "#FBFEFD",
            "#F4FBF7",
            "#E6F7ED",
            "#D6F1E3",
            "#C3E9D7",
            "#ACDEC8",
            "#8BCEB6",
            "#56BA9F",
            "#29A383",
            "#26997B",
            "#208368",
            "#1D3B31"
        ],
        "dark": [
            "#0D1512",
            "#121C18",
            "#0F2E22",
            "#0B3B2C",
            "#114837",
            "#1B5745",
            "#246854",
            "#2A7E68",
            "#29A383",
            "#27B08B",
            "#1FD8A4",
            "#ADF0D4"
        ]
    },
    "teal": {
        "light": [
            "#FAFEFD",
            "#F3FBF9",
            "#E0F8F3",
            "#CCF3EA",
            "#B8EAE0",
            "#A1DED2",
            "#83CDC1",
            "#53B9AB",
            "#12A594",
            "#0D9B8A",
            "#008573",
            "#0D3D38"
        ],
        "dark": [
            "#0D1514",
            "#111C1B",
            "#0D2D2A",
            "#023B37",
            "#084843",
            "#145750",
            "#1C6961",
            "#207E73",
            "#12A594",
            "#0EB39E",
            "#0BD8B6",
            "#ADF0DD"
        ]
    },
    "amber": {
        "light": [
            "#FEFDFB",
            "#FEFBE9",
            "#FFF7C2",
            "#FFEE9C",
            "#FBE577",
            "#F3D673",
            "#E9C162",
            "#E2A336",
            "#FFC53D",
            "#FFBA18",
            "#AB6400",
            "#4F3422"
        ],
        "dark": [
            "#16120C",
            "#1D180F",
            "#302008",
            "#3F2700",
            "#4D3000",
            "#5C3D05",
            "#714F19",
            "#8F6424",
            "#FFC53D",
            "#FFD60A",
            "#FFCA16",
            "#FFE7B3"
        ]
    },
    "yellow": {
        "light": [
            "#FDFDF9",
            "#FEFCE9",
            "#FFFAB8",
            "#FFF394",
            "#FFE770",
            "#F3D768",
            "#E4C767",
            "#D5AE39",
            "#FFE629",
            "#FFDC00",
            "#9E6C00",
            "#473B1F"
        ],
        "dark": [
            "#14120B",
            "#1B180F",
            "#2D2305",
            "#362B00",
            "#433500",
            "#524202",
            "#665417",
            "#836A21",
            "#FFE629",
            "#FFFF57",
            "#F5E147",
            "#F6EEB4"
        ]
    },
    "red": {
        "light": [
            "#FFFCFC",
            "#FFF7F7",
            "#FEEBEC",
            "#FFDBDC",
            "#FFCDCE",
            "#FDBDBE",
            "#F4A9AA",
            "#EB8E90",
            "#E5484D",
            "#DC3E42",
            "#CE2C31",
            "#641723"
        ],
        "dark": [
            "#191111",
            "#201314",
            "#3B1219",
            "#500F1C",
            "#611623",
            "#72232D",
            "#8C333A",
            "#B54548",
            "#E5484D",
            "#EC5D5E",
            "#FF9592",
            "#FFD1D9"
        ]
    },
    "tomato": {
        "light": [
            "#FFFCFC",
            "#FFF8F7",
            "#FEEBE7",
            "#FFDCD3",
            "#FFCDC2",
            "#FDBDAF",
            "#F5A898",
            "#EC8E7B",
            "#E54D2E",
            "#DD4425",
            "#D13415",
            "#5C271F"
        ],
        "dark": [
            "#181111",
            "#1F1513",
            "#391714",
            "#4E1511",
            "#5E1C16",
            "#6E2920",
            "#853A2D",
            "#AC4D39",
            "#E54D2E",
            "#EC6142",
            "#FF977D",
            "#FBD3CB"
        ]
    },
    "violet": {
        "light": [
            "#FDFCFE",
            "#FAF8FF",
            "#F4F0FE",
            "#EBE4FF",
            "#E1D9FF",
            "#D4CAFE",
            "#C2B5F5",
            "#AA99EC",
            "#6E56CF",
            "#654DC4",
            "#6550B9",
            "#2F265F"
        ],
        "dark": [
            "#14121F",
            "#1B1525",
            "#291F43",
            "#33255B",
            "#3C2E69",
            "#473876",
            "#56468B",
            "#6958AD",
            "#6E56CF",
            "#7D66D9",
            "#BAA7FF",
            "#E2DDFE"
        ]
    }
}

# Three moods. Neutral carries the surface, accent only the focus/uptime tile,
# the four semantic scales carry ok -> warm -> hot -> crit. Brand stays the
# cloudflare orange in every palette: it identifies the product, not the theme.
PALETTES: dict[str, dict[str, str]] = {
    "linen": {
        "label": "Linen — warm sand, jade/amber/orange/red",
        "neutral": "sand", "accent": "orange",
        "ok": "jade", "warn": "amber", "hot": "orange", "crit": "red",
        "mem": "violet",
    },
    "slate": {
        "label": "Slate — cool blue-grey, teal/yellow/orange/tomato",
        "neutral": "slate", "accent": "blue",
        "ok": "teal", "warn": "yellow", "hot": "orange", "crit": "tomato",
        "mem": "violet",
    },
    "moss": {
        "label": "Moss — green-grey neutral, grass/amber/orange/red",
        "neutral": "olive", "accent": "grass",
        "ok": "grass", "warn": "amber", "hot": "orange", "crit": "red",
        "mem": "violet",
    },
}
DEFAULT_PALETTE = "linen"
# The cloudflare mark keeps its orange identity, but the shade is resolved
# per mode so the white/dark glyph on it clears 4.5:1 (the old #F6821F with a
# white cloud was only 2.58:1). Light uses orange 11, dark uses orange 9.
BRAND_LIGHT = "#CC4E00"
BRAND_DARK = "#F76B15"
ON_BRAND = "#FFFFFF"


def step(scale: str, n: int, mode: str) -> str:
    return SCALES[scale][mode][n - 1]


def _luminance(hex_color: str) -> float:
    c = hex_color.lstrip("#")
    rgb = [int(c[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in rgb]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def readable_on(background: str, first: str, second: str) -> str:
    """Pick whichever foreground has the higher contrast (theme-safe solid)."""
    return first if contrast(background, first) >= contrast(background, second) else second


def semantic_tokens(name: str, mode: str) -> dict[str, str]:
    """One palette + one mode -> the semantic layer the component CSS reads."""
    p = PALETTES[name]
    n = lambda i: step(p["neutral"], i, mode)
    a = lambda i: step(p["accent"], i, mode)
    def sem(key: str, i: int) -> str:
        return step(p[key], i, mode)

    # Radix step 11 on a step-2 tint lands at ~4.3:1 in LIGHT mode — just
    # below WCAG AA. Step 12 is the high-contrast text step and clears 11:1;
    # in DARK mode step 11 already clears 4.6:1 and keeps the hue vivid.
    text_step = 12 if mode == "light" else 11
    crit_solid = sem("crit", 11 if mode == "light" else 10)
    on_crit = readable_on(crit_solid, n(1), n(12))
    brand = BRAND_LIGHT if mode == "light" else BRAND_DARK
    on_brand = readable_on(brand, ON_BRAND, n(1))
    # The focus ring needs >=3:1 against the card (WCAG 1.4.11). Orange 9 is
    # only 2.82:1 on the light sand card, so light uses step 10.
    focus_step = 10 if mode == "light" else 9
    primary = a(11 if mode == "light" else 9)
    on_primary = readable_on(primary, ON_BRAND, n(1))
    return {
        "--surface": n(1),
        "--stage": n(1),
        "--card": n(2),
        "--raised": n(3),
        "--sunken": n(4),
        "--slab": n(3),
        "--knob": n(5),
        "--border": n(7),
        "--text": n(12),
        "--muted": n(11),
        "--focus": a(focus_step),
        "--primary": primary,
        "--on-primary": on_primary,
        "--brand": brand,
        "--on-brand": on_brand,
        "--accent": a(text_step),
        "--accent-solid": a(9),
        "--acc-tint": a(2),
        "--ok": sem("ok", text_step),
        "--ok-solid": sem("ok", 9),
        "--ok-tint": sem("ok", 2),
        # the CPU tile's own alias: keeping it separate from --ok-tint means a
        # future "accent tint" change cannot quietly repaint the status chips.
        "--cpu-tint": sem("ok", 2),
        "--warn": sem("warn", text_step),
        "--warn-solid": sem("warn", 9),
        "--warn-tint": sem("warn", 2),
        "--hot": sem("hot", text_step),
        "--hot-solid": sem("hot", 9),
        "--hot-tint": sem("hot", 2),
        "--crit": sem("crit", text_step),
        "--crit-solid": crit_solid,
        "--crit-tint": sem("crit", 2),
        "--on-crit": on_crit,
        "--mem": step(p["mem"], text_step, mode),
        "--mem-tint": step(p["mem"], 2, mode),
    }


def primitive_tokens(name: str, mode: str) -> dict[str, str]:
    """Raw scale aliases for a palette/mode, emitted once per selector."""
    p = PALETTES[name]
    out: dict[str, str] = {}
    for role in ("neutral", "accent", "ok", "warn", "hot", "crit", "mem"):
        scale = p[role]
        for i in (1, 2, 3, 4, 5, 6, 7, 9, 11, 12):
            out[f"--{role[:2] if role != 'neutral' else 'n'}-{i}"] = step(scale, i, mode)
    return out

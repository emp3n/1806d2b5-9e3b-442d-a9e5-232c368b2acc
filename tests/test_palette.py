"""Three palettes, one token contract, measured contrast in every mode."""
from __future__ import annotations

import re
from pathlib import Path

import palette
import theme

PAIRS = (
    ("text on card", "--text", "--card", 4.5),
    ("muted on card", "--muted", "--card", 4.5),
    ("text on raised", "--text", "--raised", 4.5),
    ("text on chip tint", "--text", "--chip-tint", 4.5),
    ("text on CPU tile", "--text", "--cpu-tint", 4.5),
    ("text on RAM tile", "--text", "--mem-tint", 4.5),
    ("warn text on card", "--warn", "--card", 4.5),
    ("hot text on card", "--hot", "--card", 4.5),
    ("crit text on card", "--crit", "--card", 4.5),
    ("text on accent tint", "--text", "--acc-tint", 4.5),
    ("text on crit solid", "--on-crit", "--crit-solid", 4.5),
    ("brand glyph", "--on-brand", "--brand", 4.5),
    ("primary solid", "--on-primary", "--primary", 4.5),
    ("focus ring", "--focus", "--card", 3.0),
)


def test_three_curated_palettes_exist():
    assert set(palette.PALETTES) == {"linen", "slate", "moss"}
    for name, spec in palette.PALETTES.items():
        for role in ("neutral", "accent", "ok", "warn", "hot", "crit", "mem"):
            assert spec[role] in palette.SCALES, f"{name}: unknown {role}"
        for mode in ("light", "dark"):
            for value in palette.semantic_tokens(name, mode).values():
                assert re.match(r"^#[0-9A-F]{6}$", value), (name, mode, value)


def test_every_text_pair_clears_wcag_aa_in_both_modes():
    for name in palette.PALETTES:
        for mode in ("light", "dark"):
            tokens = palette.semantic_tokens(name, mode)
            for label, fg, bg, floor in PAIRS:
                ratio = palette.contrast(tokens[fg], tokens[bg])
                assert ratio >= floor, f"{name}/{mode}: {label} {ratio:.2f} < {floor}"


def test_light_uses_high_contrast_step_and_dark_keeps_the_vivid_step():
    """The old 4.3:1 green-on-green came from step 11 on a light tint."""
    for name, spec in palette.PALETTES.items():
        light = palette.semantic_tokens(name, "light")
        dark = palette.semantic_tokens(name, "dark")
        assert light["--ok"] == palette.step(spec["ok"], 12, "light")
        assert dark["--ok"] == palette.step(spec["ok"], 11, "dark")


def test_generated_css_covers_every_palette_and_mode():
    css = theme.THEME_CSS
    for name in palette.PALETTES:
        assert f'.rd-stage[data-palette="{name}"] {{' in css
        assert f'.rd-stage[data-palette="{name}"][data-theme="dark"] {{' in css
        assert f'.rd-stage[data-palette="{name}"][data-theme="auto"] {{' in css
    assert "@media (prefers-color-scheme: dark)" in css
    # no raw colour left in the component layer; the token block owns them
    assert "#F4F3EF" not in css and "#1F2224" not in css


def test_static_structure_does_not_change_with_the_palette():
    """Spacing, radius, shadows and motion are not palette-owned."""
    stage = re.search(r"\.rd-stage \{(.*?)\}", theme.THEME_CSS, re.S).group(1)
    for token in ("--row: 32px", "--dock: 76px", "--fast: 140ms", "--slow: 260ms",
                  "--shadow:", "--shadow-tip:"):
        assert token in stage
    assert "#" not in stage


def test_the_card_and_the_app_accept_a_palette():
    card = ("\n".join(p.read_text() for p in (Path(__file__).resolve().parents[1] / "ui" / "card").glob("*.py")))
    app = (Path(__file__).resolve().parents[1] / "app.py").read_text()
    assert 'palette: str = "linen"' in card
    assert 'data-palette="{palette}"' in card
    assert 'params.get("palette"' in app
    assert "render(state, theme=theme, palette=palette" in app

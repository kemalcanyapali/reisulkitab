---
name: Reisülküttab
description: Quiet two-pane native settings shell and a floating dot-scope indicator, after anomalyco/hex.
colors:
  win: "#f4f5f6"          # the field behind everything (light scheme; dark twins in ui_theme.DARK)
  card: "#ffffff"         # content card and grouped cards
  inset: "#f7f8f9"        # a group nested inside a card
  border: "#e4e6e9"       # card edges; nested groups use border-soft #eceef0
  text: "#191b1f"
  text-2: "#454b54"
  muted: "#71767f"
  nav-sel: "#e8e9ec"      # the pill behind the open page; hover #ebecef
  btn: "#ffffff"          # on 1px btn-border #d9dce0; hover #f2f3f5, pressed #e8e9ec, disabled #f4f5f6
  focus: "#c2c6cd"        # focus ring gray
  rail-icon: "#7a7f89"    # one gray clearing 3:1 on both rails
  plate: "rgba(17,17,20,0.94)"  # Hex's near-black plate, rimmed white at a tenth
  pearl: "#dbd4c4"        # your voice; core #faf5eb
  agent-blue: "#b3c7e6"   # the agent working; core #ebf2ff
  coral: "#ff614f"        # failure; core #ffa899
  warn: "#f0b450"         # warning
typography:
  heading: {fontFamily: "system", fontSize: "14pt", fontWeight: 600}
  body: {fontFamily: "system"}
  label: {fontFamily: "system", fontSize: "10.5pt"}  # clock: monospace 10pt
rounded:
  card: "12px"
  group: "10px"
  inset: "8px"
  control: "7px"
spacing:
  shell: "9px"     # window edge to field, field to card; content margins 18/12
  stack: "8px"     # vertical rhythm inside the card
components:
  button: {backgroundColor: "{colors.btn}", textColor: "{colors.text}", rounded: "{rounded.control}", padding: "4px 14px"}
  nav-item: {backgroundColor: "transparent", textColor: "{colors.text-2}", rounded: "{rounded.control}", padding: "4px 10px"}
  nav-item-selected: {backgroundColor: "{colors.nav-sel}", textColor: "{colors.text}", rounded: "{rounded.control}", padding: "4px 10px"}
---

# Design System: Reisülküttab

## Overview

**Creative North Star: "The Quiet Field"**

A native Windows (PyQt6) shell with a flat navigation rail, one rounded content surface, fine borders and system typography. Custom shell surfaces are neutral; native inputs retain the Windows accent color. The recording overlay is a near-black pill with a dot waveform and semantic state colors.

**Key Characteristics:**
- Two-pane shell: rail of eight pages + one content card; hairline borders; zero shadows.
- Neutral custom shell surfaces; native controls keep the system accent.
- Follows the OS scheme (sheet rebuilt on ApplicationPaletteChange); input controls stay native.
- General includes installed version, update status, Check/Update controls and a save-before-restart note. Busy, unsupported and failed updates explain their limits without hiding the check.

## Colors

**The Dark Mirror Rule.** One neutral scale per scheme, measured from the Hex settings goldens; roles per the frontmatter (field → card → inset; `text`/`text-2`/`muted`; `nav-sel` pill; `btn` states). Every shell token has a dark twin in `ui_theme.DARK` (`win #0d0e10`, `card #17181b`, `text #ececee`); when the system palette is dark the twin is normative. Never hardcode one scheme.

**The Native Accent Rule.** Custom shell surfaces remain neutral. Native controls retain the system accent; the overlay uses pearl for voice, agent blue for processing, coral for failure and amber for warning.

## Typography

**The System Type Rule.** System font only (Segoe UI via Qt); the overlay clock is monospace. Never bundle or name a font; hierarchy is size and weight — heading 600/14pt once per card, group titles 600 in `text-2`, body at the system default, overlay label 10.5pt, clock 10pt mono.

## Layout

Rail ≤184px that yields width before the content does; rows ≈32px with drawn 16px stroke icons; card inset 9px; content margins 18/12; sections stack at 8px; groups pad 14px with a 12px title offset. Window opens 860×680 clamped to the screen; pages scroll and take the squeeze. The rail leads the tab order; Save sits at the card foot and keeps the window open.

## Elevation & Depth

**The Border-Not-Shadow Rule.** The shell has no shadows: depth is 1px borders plus the inset surface. The overlay plate floats on the product's only shadow — three soft strokes (7/4/1.5px wide at alpha 26/36/52) offset 2.5px down — rimmed white at a tenth.

## Shapes

Descending radius ladder: card 12 → group 10 → inset 8 → button/nav 7 → tab 6. Dictation's plate is Hex's 112×26px capsule; processing is 76×26px. Meeting and message plates remain 48px tall. Nav selection is a soft rounded fill, never an accent bar. Focus rings are pre-drawn transparent borders that turn `focus`, so focus moves nothing.

## Components

- **Buttons:** styled `QPushButton` — `btn` fill on 1px `btn-border`, radius 7, padding 4px 14px; hover/pressed one neutral step; focus swaps the border; disabled dims to `muted`.
- **Inputs:** native, untouched — comboboxes, spinboxes, checkboxes and text fields render as Windows draws them, as the Hex reference does.
- **Navigation:** rail rows transparent at rest in `text-2`, `hover` fill, `nav-sel` pill selected; labels elide, never scroll; the one visible tab bar repeats the pill at radius 6.
- **Cards:** `QGroupBox` as grouped card (radius 10, 1px `border`, 600 title); a model box inside a card is an inset (radius 8, `border-soft`), never card-on-card; message boxes repaint on `card`.
- **Dot scope overlay (signature):** frameless, focus-free, click-through dictation capsule, 112×26px plus 5px shadow margin. The 43×9 pearl dot field follows real level history from the centre outward; processing uses a blue flow in a 76×26px capsule. It carries no dictation clock, as in Hex. Top/bottom centre positions join the four retained corners; existing saved positions remain valid. Meetings keep their two 22×5 microphone/speaker fields and elapsed clock on the larger plate. Dismissable jobs and done/warning/error retain readable messages and their existing dismissal behavior. Motion uses the existing 33ms timer. Visual geometry reference: anomalyco/hex `src/rune_hud/dot_scope.rs`; recording/model state machines are unchanged.

## Do's and Don'ts

### Do:
- **Do** take every color and radius from `ui_theme` tokens; seat new surfaces on `card` or `inset` with `border`/`border-soft` edges, and keep inputs native.
- **Do** let the sheet reach message boxes (dialogs never half-native); elide long rail labels; let pages scroll under squeeze.

### Don't:
- **Don't** add custom shadows, accent colors, fonts or nested card decoration to the shell; preserve native control rendering.
- **Don't** treat this as web: it is a Qt stylesheet (QSS) plus painted widgets; no HTML/CSS components exist.
- **Don't** steal focus or animate the shell; motion belongs to the overlay's dots alone.

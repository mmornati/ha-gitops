---
name: HA GitOps
description: The ingress panel that shows whether the box and GitHub agree, as strips in bays, wearing the Home Assistant theme in use.
colors:
  accent: "#03a9f4"
  accent-ink: "#00202e"
  fresh: "#ffa600"
  fresh-ink: "#1f1300"
  review: "#039be5"
  review-ink: "#00192a"
  held: "#db4437"
  held-ink: "#1f0502"
  landed: "#43a047"
  landed-ink: "#04210a"
  add: "#d1e3d1"
  del: "#f0d4d2"
  hunk: "#e1e1e1"
  plus: "#367039"
  minus: "#a33a30"
  bay: "#fafafa"
  rail: "#ffffff"
  strip: "#f4f4f4"
  rail-edge: "#e0e0e0"
  on-bay: "#212121"
  on-bay-muted: "#6c6c6c"
  add-dark: "#2c3e2d"
  del-dark: "#472b29"
  hunk-dark: "#373737"
  plus-dark: "#7fb982"
  minus-dark: "#dd736a"
  bay-dark: "#111111"
  rail-dark: "#1c1c1c"
  strip-dark: "#262626"
  rail-edge-dark: "#343434"
  on-bay-dark: "#e1e1e1"
  on-bay-muted-dark: "#9e9e9e"
typography:
  summary:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif"
    fontSize: "25px"
    fontWeight: 650
    lineHeight: 1.25
    letterSpacing: "-0.02em"
  title:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif"
    fontSize: "18px"
    fontWeight: 700
    lineHeight: 1.25
  bay-title:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif"
    fontSize: "14px"
    fontWeight: 700
    lineHeight: 1.5
  body:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.5
  note:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.5
  key:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif"
    fontSize: "14px"
    fontWeight: 600
    lineHeight: 1
  field:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif"
    fontSize: "11px"
    fontWeight: 700
    lineHeight: 1
    letterSpacing: "0.05em"
  commit:
    fontFamily: "ui-monospace, 'SF Mono', 'Cascadia Mono', 'Roboto Mono', Menlo, Consolas, monospace"
    fontSize: "17px"
    fontWeight: 700
    lineHeight: 1.2
    fontFeature: "tnum"
  path:
    fontFamily: "ui-monospace, 'SF Mono', 'Cascadia Mono', 'Roboto Mono', Menlo, Consolas, monospace"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.35
  diff:
    fontFamily: "ui-monospace, 'SF Mono', 'Cascadia Mono', 'Roboto Mono', Menlo, Consolas, monospace"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.5
rounded:
  sm: "8px"
  card: "12px"
  pill: "11px"
spacing:
  strip-gap: "6px"
  key-gap: "8px"
  bay-pad: "12px"
  bay-gap: "16px"
  gutter: "20px"
  gutter-phone: "14px"
components:
  key:
    backgroundColor: "{colors.rail}"
    textColor: "{colors.on-bay}"
    typography: "{typography.key}"
    rounded: "{rounded.sm}"
    padding: "0 14px"
    height: "38px"
  key-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-ink}"
    typography: "{typography.key}"
    rounded: "{rounded.sm}"
    padding: "0 14px"
    height: "38px"
  key-danger:
    backgroundColor: "{colors.held}"
    textColor: "{colors.held-ink}"
    typography: "{typography.key}"
    rounded: "{rounded.sm}"
    padding: "0 14px"
    height: "38px"
  key-onpaper:
    backgroundColor: "transparent"
    textColor: "{colors.on-bay}"
    typography: "{typography.key}"
    rounded: "{rounded.sm}"
    padding: "0 14px"
    height: "34px"
  strip:
    backgroundColor: "{colors.strip}"
    textColor: "{colors.on-bay}"
    typography: "{typography.path}"
    rounded: "{rounded.sm}"
    height: "42px"
  field-fresh:
    backgroundColor: "{colors.fresh}"
    textColor: "{colors.fresh-ink}"
    typography: "{typography.field}"
    width: "62px"
  field-review:
    backgroundColor: "{colors.review}"
    textColor: "{colors.review-ink}"
    typography: "{typography.field}"
    width: "62px"
  field-held:
    backgroundColor: "{colors.held}"
    textColor: "{colors.held-ink}"
    typography: "{typography.field}"
    width: "62px"
  field-landed:
    backgroundColor: "{colors.landed}"
    textColor: "{colors.landed-ink}"
    width: "76px"
  bay:
    backgroundColor: "{colors.rail}"
    textColor: "{colors.on-bay}"
    rounded: "{rounded.card}"
    padding: "12px"
  attention-strip:
    backgroundColor: "{colors.rail}"
    textColor: "{colors.on-bay}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
  wire-word:
    backgroundColor: "#dcdcdc"
    textColor: "{colors.on-bay}"
    rounded: "{rounded.sm}"
    padding: "0 10px"
    height: "26px"
  wire-word-equal:
    backgroundColor: "{colors.landed}"
    textColor: "{colors.landed-ink}"
  wire-word-drift:
    backgroundColor: "{colors.fresh}"
    textColor: "{colors.fresh-ink}"
  wire-word-behind:
    backgroundColor: "{colors.review}"
    textColor: "{colors.review-ink}"
  switch-track:
    backgroundColor: "#ababab"
    rounded: "{rounded.pill}"
    width: "38px"
    height: "22px"
  switch-track-on:
    backgroundColor: "{colors.accent}"
  dialog:
    backgroundColor: "{colors.rail}"
    textColor: "{colors.on-bay}"
    rounded: "{rounded.card}"
    padding: "20px"
    width: "min(440px, calc(100vw - 32px))"
  toast:
    backgroundColor: "{colors.on-bay}"
    textColor: "{colors.bay}"
    rounded: "{rounded.sm}"
    padding: "10px 14px"
  toast-error:
    backgroundColor: "{colors.held}"
    textColor: "{colors.held-ink}"
  run-log:
    backgroundColor: "{colors.rail}"
    textColor: "{colors.on-bay-muted}"
    typography: "{typography.diff}"
    rounded: "{rounded.card}"
---

# Design System: HA GitOps

## Overview

**Creative North Star: "Strip Bay"**

The panel is a flight-progress strip board. Every changed file is a strip, and the bay it sits in says its state: not backed up, in review, held back, landed. Each strip ends on its leading side in a flat colour field, and that field plus the bay carries the state. The word inside the field names the change (new, edit, gone, moved, held) or, once landed, the commit.

The board wears Home Assistant's theme. The page is Home Assistant's canvas, a bay is a Home Assistant card (1px border, 12px corners), and a strip is one tonal step off that card with a 1px ring. Text, the accent and the four state colours are Home Assistant's own. The stylesheet carries Home Assistant's default light and dark values; inside Home Assistant a script reads the active theme from the parent page, custom themes included, and replaces them live. The layout and the components stay the panel's own.

Above the bays, a sticky head rule always names the box commit, a connector that reads as agreement or as one side being ahead, and the GitHub commit. Under it sits one plain sentence in the largest type on the page, then any attention strips, then a row of keys. Controls are rectangular keys; the key that matters is filled with the accent, the only coloured control on a rail.

The system is dense, quiet and literal.

**Key Characteristics:**
- Three surfaces from one theme: canvas (page, head rule), card (bays, attention strips, run log, dialog, default keys), and strip (one step off the card).
- State is a full-height flat colour field at the head of a strip, never a small badge on it.
- Four fixed bays in a fixed order; an empty bay stays and says what would stop there.
- Monospace only for machine values: paths, commits, branch names, diffs, line counts.
- Motion marks a state change and nothing else.
- Light, dark and custom themes are the same board; only the eight borrowed colours change.

## Colors

Adjusted after this record was derived (same build, 0.5.2): muted text is a 74% mix of text into card (at least 5:1 on every surface it sits on); lines are the 13% / 16% mixes in the CSS defaults too, not only under `syncTheme`; ink on a coloured field is `#141414` or white, whichever has the higher contrast ratio; solid error fields (Failed, Invalid and Offline flags, the danger key, the error toast) use the error colour darkened by 14% (`--held-fill`, `#bc3a2f` by default) with white ink, while the held hatch uses two lighter tints of it with dark ink; the off switch track is a 52% mix; the dialog backdrop is plain black at 55%.

Home Assistant's neutrals and its own primary, warning, info, error and success colours; nothing in the palette belongs to the panel. The values in the frontmatter are Home Assistant's defaults (light, and `-dark`) as the stylesheet ships them, with the derived colours resolved against those defaults.

### Primary
- **Accent** (`accent` on `accent-ink`): Home Assistant's primary colour. The primary key on a rail, the confirm key of a dialog that is not destructive, a switch that is on, and the form accent and caret.

### Secondary
- **Fresh** (`fresh` on `fresh-ink`): Home Assistant's warning colour. Not backed up. Also the "box has changes" connector, warning attention flags, pending checks, and text selection.
- **Review** (`review` on `review-ink`): Home Assistant's info colour. In review. Also "GitHub is ahead", a run in progress, the run sweep, and info attention flags.
- **Held** (`held` on `held-ink`): Home Assistant's error colour. Held back, when hatched; failure and destructive action, when solid.
- **Landed** (`landed` on `landed-ink`): Home Assistant's success colour. Landed commits, the "in agreement" connector, passing checks.

The state colours are the same in light and dark.

### Tertiary
- **Diff Add / Diff Delete / Diff Hunk** (`add`, `del`, `hunk`): line backgrounds inside an opened diff. They are the strip colour with 20% of `landed`, 18% of `held`, and 9% of `on-bay` mixed in.
- **Plus / Minus** (`plus`, `minus`): the two line counts on a strip, `landed` at 62% and `held` at 70% mixed into `on-bay`, so they stay readable on a strip.

### Neutral
- **Canvas** (`bay`, `bay-dark`): Home Assistant's primary background. The page, the sticky head rule, and the text of the toast.
- **Card** (`rail`, `rail-dark`): Home Assistant's card background. Bays, attention strips, the run log, the dialog, a default key.
- **Strip** (`strip`): the card with 5% of `on-bay` mixed in. Every file and commit strip.
- **Line** (`rail-edge`): every 1px border, ring and divider, the 2px connector line, the scrollbar thumb. Inside Home Assistant it is recomputed as a solid mix of `on-bay` into the card (13% on a light canvas, 16% on a dark one), whatever the theme's own divider is.
- **Text** (`on-bay`): Home Assistant's primary text, on every surface. Also the 2px focus ring, and the fill of the toast.
- **Muted Text** (`on-bay-muted`): `on-bay` at 66% mixed into the card. Counts, notes, empty-bay sentences, labels, the log, the directory part of a path, chevrons, and the border of an outline key.
- **Idle** (14% of `on-bay` in the canvas; 38% of `on-bay` in the card): the connector word when the relation is unknown; the track of a switch that is off.

The stylesheet still names some of these `--strip-ink`, `--strip-muted`, `--strip-rule` and `--focus`; they are aliases of `on-bay`, `on-bay-muted`, `rail-edge` and `on-bay`.

### Named Rules
**The Borrowed Eight Rule.** Eight colours come from Home Assistant: canvas, card, text, primary, warning, info, error, success. Every other surface, line and text colour is a mix of those eight, so a custom theme recolours the whole panel.

**The Ink By Contrast Rule.** Text on a coloured field is not a palette choice. Inside Home Assistant it is near-black (#141414) on a field whose relative luminance is above 0.18 and white on a darker one; the `-ink` values in the frontmatter are what the stylesheet uses when the panel is opened on its own.

**The One Accent Rule.** The accent fills the primary key, the confirm key and a switch that is on. It is never a state: a file's state uses `fresh`, `review`, `held` or `landed`.

**The Hatch Means Held Rule.** The 135 degree hatch (6px of `held`, 6px of `held` mixed 70% with white) appears on a held-back strip's field and on the "Held" attention flag, and nowhere else. Solid `held` means a failure or a destructive key.

**The Field Only Rule.** The four state colours are fills for fields, flags, the connector word, the checks dot, the danger key and the error toast. They are never text colours; the line counts use `plus` and `minus`, which are mixed into the text colour.

## Typography

**Display Font:** the platform sans stack (system-ui, with -apple-system, Segoe UI, Roboto, Helvetica Neue)
**Body Font:** the same stack
**Label/Mono Font:** the platform mono stack (ui-monospace, with SF Mono, Cascadia Mono, Roboto Mono, Menlo, Consolas), tabular figures

**Character:** One sans at a few close sizes, with weight doing the hierarchy, against a mono reserved for values a machine produced. The stacks are what the panel's no-external-requests constraint leaves; the durable part of the system is the ramp and the sans/mono split, not the face.

### Hierarchy
- **Summary** (650, 25px, 1.25, -0.02em; 21px at 560px and below): the one sentence that says what is waiting. One per screen, capped at 34em, balanced.
- **Title** (700, 18px, 1.25): the question in a confirmation dialog.
- **Commit** (mono 700, 17px, 1.2): the two commit ids on the head rule.
- **Body** (400, 15px, 1.5): attention sentences, dialog text; the product name on the head rule is 15px at 700.
- **Bay title** (700, 14px): bay headings and the run log summary; the count beside it is 13px at 600, tabular.
- **Key** (600, 14px, 1): every key; switches are 500.
- **Note** (400, 13px): last-run line, bay notes, "and N more"; empty-bay sentences are 14px; the footer is 12.5px.
- **Field** (700, 11px, 0.05em, uppercase): the change word in a strip's field. A landed field is the commit id instead (mono 600, 12px, no caps, no tracking).
- **Path** (mono, 13px, 1.35): directory in `on-bay-muted`, file name at 700; a "was" line for a move at 12px.
- **Diff** (mono, 12px, 1.5, tab size 2): opened diffs and the run log.

### Named Rules
**The Machine Values Rule.** Monospace is for paths, commit ids, branch names, diffs, the log and line counts. Sentences, headings, keys and field words are sans.

## Layout

One column of blocks inside a centred container (max 1280px, 20px gutters; 14px at 560px and below): head rule, summary, last-run line, attention strips, key row, bays, run log, footer. Blocks are separated by 16px to 30px.

The head rule is sticky at the top, bleeds to the container edge, and closes with a 1px `rail-edge` line. It is a two-column grid (name, then the span); the span is box commit, connector (at least 40px, flexible), GitHub commit right-aligned. At 560px and below the name stacks above the span.

Bays are a grid with 16px gaps: one column on a phone, two from 720px, three from 1080px. The three working bays (not backed up, in review, held back) take one column each; the landed bay spans the full row. Bays align to the top and grow with their strips; bay order never changes.

Inside a bay (12px padding): heading and count, an optional note, strips stacked with 6px gaps, then the bay's keys on a rail separated by a 1px `rail-edge` line with 12px above and below it. A strip row is a grid of field (62px; 54px on a phone; 76px landed), path, line counts, chevron, with a minimum height of 42px (48px on a phone). Long paths wrap anywhere; they are never truncated.

The key row wraps, with keys on the left and the two switches pushed right; on a phone keys share the row equally and each switch takes a full line with its track at the far edge. Keys are 38px high (outline keys 34px) and 44px at 560px and below.

## Elevation & Depth

Flat, with tonal layering. Depth at rest is three tones (canvas, card, strip) and a 1px line: bays, the run log and keys carry a 1px `rail-edge` border; strips and attention strips carry a 1px `rail-edge` ring. A shadow appears only on something that is open or floating over the page.

### Shadow Vocabulary
- **Ring** (`box-shadow: 0 0 0 1px` in `rail-edge`): strips and attention strips, at rest.
- **Open strip** (ring plus `0 12px 24px -14px rgb(0 0 0 / .45)`): a strip while its diff is open.
- **Toast** (`0 12px 30px -10px rgb(0 0 0 / .5)`): the toast.
- **Dialog** (`0 24px 60px -12px rgb(0 0 0 / .6)` over a `rgb(8 14 12 / .6)` backdrop): the confirmation dialog, which has no border.
- **Switch thumb** (`0 1px 2px rgb(0 0 0 / .35)`): the white thumb of a switch.

### Named Rules
**The Flat At Rest Rule.** A surface at rest is separated by tone and a 1px line, never by a shadow. Shadows belong to an open strip, the toast, the dialog and the switch thumb.

## Shapes

Gently rounded. Controls and strips take 8px corners: strips, attention strips, keys, the connector word, the toast. A field or flag takes the strip's two leading corners and runs the strip's full height; an opened diff takes its two bottom corners. Cards take 12px: bays, the run log, the dialog. The only round forms are the switch (22px track, 16px thumb) and the 9px checks dot.

Lines are 1px `rail-edge`; the border of an outline key is 1px `on-bay-muted`. Icons are 18px line icons drawn at 1.7 stroke with round caps and joins, inline SVG, coloured by the text they sit beside.

## Components

### Keys
- **Shape:** rectangular, 8px corners, 38px high, 14px side padding, optional 18px icon with an 8px gap.
- **Default:** card fill, `rail-edge` border, `on-bay` text.
- **Primary:** `accent` fill and border with `accent-ink` text, no shadow. One per rail: "Back up now" on the first bay, "Merge" on the review bay; "Sync now" in the key row becomes primary only while GitHub is ahead. The confirm key of a dialog that is not destructive takes the same dress.
- **Danger:** solid `held` fill with `held-ink`; the confirm key of a destructive dialog.
- **Outline:** transparent with an `on-bay-muted` border and `on-bay` text, 34px high; the key inside an attention strip, a landed strip, and the dialog's Cancel. On hover its border goes to `on-bay` and it tints with 7% `on-bay`.
- **Hover / active / disabled:** the border darkens to `on-bay-muted` on hover, the key drops 1px when pressed, and a disabled key is at 45% opacity. Transitions are 150ms.
- **Focus:** a 2px `on-bay` ring, 2px off the control, on every focusable element.
- A key that opens a confirmation ends its label with an ellipsis.

### Switches
A label followed by a 38px by 22px pill track (38% of `on-bay` in the card when off, `accent` when on) with a 16px white thumb that slides 16px in 200ms. Used for the two standing settings, not for actions.

### Bays
A Home Assistant card: card fill, 1px `rail-edge` border, 12px corners, 12px padding. Heading (14px, 700) with a muted count on the same baseline. An empty bay keeps its place and carries one muted sentence saying what would stop there. The bay's keys sit on a rail at its foot.

### Strips (signature)
- **Row:** strip fill, 8px corners, 1px ring; field, path, line counts, chevron. The whole row is one button; it tints with 7% `on-bay` on hover.
- **Field:** full height, 62px wide, the bay's state colour, the change word in 11px caps. Held fields are hatched and say "held"; their counts are replaced by a lock icon.
- **Open:** the chevron turns 90 degrees, the strip takes the open-strip shadow, and the diff unfolds below a `rail-edge` line (220ms, clipped from the top), up to 440px high and scrolling both ways. Added, removed and hunk lines take `add`, `del` and `hunk`. A held strip opens onto a sentence instead of content.
- **Travel:** when a file changes state its strip moves to the new bay with a view transition (250ms). Skipped above 80 files and under reduced motion.
- **Landed strip:** the field is the commit id as a link (76px, mono), followed by the subject at 600 and a muted author and age line, with an outline "Revert…" key at the end. It does not open.
- **Mark:** a strip reached from "Show them" takes a 2px `held` outline for 1.6s.

### Attention strips
A card-coloured strip on the canvas (8px corners, 1px ring) with a full-height flag (104px; 92px on a phone) carrying an icon and one word at 12px, 700: solid `held` for errors, `fresh` for warnings, `review` for information, hatched for held. Then the sentence, then at most a pair of outline keys. On a phone the keys drop under the sentence.

### Head rule
Two commit ids in mono at 17px with an 11px muted label over each ("The box", "GitHub · branch"), joined by a 2px `rail-edge` line with a word resting on it (12.5px, 700, 26px high, 8px corners). The word's fill says the relation: `landed` in agreement, `fresh` box has changes, `review` GitHub is ahead or a run in progress, the idle mix when unknown. While a run is on, a 36px by 4px `review` segment sweeps the line (1.4s, alternating); under reduced motion it is hidden.

### Dialog and toast
The dialog is a card without a border (12px corners, 20px padding, at most 440px wide) under the dialog shadow: an 18px question, a muted sentence, and Cancel plus the confirm key on a right-aligned rail. Focus starts on Cancel. The toast is the inverse of the page (`on-bay` fill, canvas-coloured text, 8px corners), centred at the bottom, rising 12px as it fades in (200ms); an error toast is solid `held`.

### Run log
A collapsed disclosure in card dress (card fill, 1px border, 12px corners) with a chevron; the log is 12px mono in `on-bay-muted`, up to 420px high.

## Do's and Don'ts

### Do:
- **Do** take canvas, card, text, accent and the four state colours from Home Assistant, and mix every other surface, line and text colour from them.
- **Do** show a file's state with the bay it is in and a full-height colour field at the head of its strip.
- **Do** keep all four bays on screen in the same order, and give an empty bay one sentence about what would stop there.
- **Do** put a bay's keys on that bay's rail, with one accent-filled primary key per rail.
- **Do** choose the ink on a coloured field by contrast with that field, not by hue.
- **Do** keep monospace for paths, commit ids, branch names, diffs, the log and line counts.
- **Do** keep keys at least 44px high at 560px and below.
- **Do** tie motion to a state change, 150ms to 250ms, with `cubic-bezier(.16, 1, .3, 1)`, and remove it under reduced motion.

### Don't:
- **Don't** give a surface, a line or text a colour of the panel's own; it comes from the Home Assistant theme or from a mix of it.
- **Don't** reduce a file's state to a chip or a small badge, or the files to a table; a bay is a Home Assistant card, and state stays a full-height field.
- **Don't** use a state colour as text colour or as decoration; it fills a field or it is absent.
- **Don't** use the accent to mean a state.
- **Don't** hatch anything that is not held back.
- **Don't** put a shadow on a surface at rest; tone and a 1px line separate it.
- **Don't** load fonts, scripts or images from outside the file.
- **Don't** show the content of a held-back file, in a diff or anywhere else.
- **Don't** truncate a path; let it wrap.

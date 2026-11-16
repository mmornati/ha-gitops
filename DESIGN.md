---
name: HA GitOps
description: The ingress panel that shows whether the box and GitHub agree, as paper strips in bays.
colors:
  strip: "#fbfaf4"
  strip-ink: "#1a211f"
  strip-muted: "#5a635d"
  strip-rule: "#dcd9cb"
  fresh: "#e5a31c"
  fresh-ink: "#2a1c00"
  review: "#4a8be0"
  review-ink: "#041b38"
  held: "#e5695e"
  held-ink: "#2b0703"
  landed: "#33a06d"
  landed-ink: "#03231a"
  add: "#d3ecd0"
  del: "#f6d6d0"
  hunk: "#e9e6da"
  bay: "#d7ddd8"
  rail: "#c6cec8"
  rail-edge: "#a9b4ad"
  on-bay: "#17201d"
  on-bay-muted: "#44504b"
  focus: "#0b4fb3"
  strip-dark: "#ebe6d8"
  strip-ink-dark: "#171e1c"
  strip-muted-dark: "#555e58"
  strip-rule-dark: "#cdc6b3"
  fresh-dark: "#f0b23a"
  review-dark: "#6aa6f0"
  held-dark: "#ee7468"
  landed-dark: "#4dbb84"
  add-dark: "#cfe6cb"
  del-dark: "#f1cfc8"
  hunk-dark: "#ddd7c6"
  bay-dark: "#121a18"
  rail-dark: "#1b2624"
  rail-edge-dark: "#2c3b38"
  on-bay-dark: "#e1e8e4"
  on-bay-muted-dark: "#98a8a2"
  focus-dark: "#f0b23a"
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
  sm: "3px"
  dialog: "6px"
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
    backgroundColor: "{colors.strip}"
    textColor: "{colors.strip-ink}"
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
    textColor: "{colors.strip-ink}"
    typography: "{typography.key}"
    rounded: "{rounded.sm}"
    padding: "0 14px"
    height: "34px"
  strip:
    backgroundColor: "{colors.strip}"
    textColor: "{colors.strip-ink}"
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
    padding: "10px 12px 12px"
  attention-strip:
    backgroundColor: "{colors.strip}"
    textColor: "{colors.strip-ink}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
  wire-word:
    backgroundColor: "{colors.rail-edge}"
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
    backgroundColor: "{colors.rail-edge}"
    rounded: "{rounded.pill}"
    width: "38px"
    height: "22px"
  switch-track-on:
    backgroundColor: "{colors.landed}"
  dialog:
    backgroundColor: "{colors.strip}"
    textColor: "{colors.strip-ink}"
    rounded: "{rounded.dialog}"
    padding: "20px"
    width: "min(440px, calc(100vw - 32px))"
  toast:
    backgroundColor: "{colors.strip}"
    textColor: "{colors.strip-ink}"
    rounded: "{rounded.sm}"
    padding: "10px 14px"
  toast-error:
    backgroundColor: "{colors.held}"
    textColor: "{colors.held-ink}"
---

# Design System: HA GitOps

## Overview

**Creative North Star: "Strip Bay"**

The panel is a flight-progress strip board. Every changed file is a paper strip, and the bay it sits in says its state: not backed up, in review, held back, landed. The surface is painted metal (sage-grey in light, green-black in dark); the strips are pale paper that keeps its paper colour in both schemes. Each strip ends on its leading side in a flat colour field, and that field plus the bay carries the state. The word inside the field names the change (new, edit, gone, moved, held) or, once landed, the commit.

Above the bays, a sticky head rule always names the box commit, a connector that reads as agreement or as one side being ahead, and the GitHub commit. Under it sits one plain sentence in the largest type on the page, then any attention strips, then a row of keys. Controls are rectangular keys that sit on the bay's rail; the key that matters is paper-coloured, so it reads as the one thing lifted off the metal.

The system is dense, quiet and literal. It has its own identity rather than imitating Home Assistant cards, while staying calm enough to live inside the Home Assistant frame. It refuses the add-on default of rounded status cards with chips and a file table.

**Key Characteristics:**
- Two materials only: painted metal (bay, rail) and paper (strips, attention strips, dialog, toast).
- State is a full-height flat colour field at the head of a strip, never a small badge on it.
- Four fixed bays in a fixed order; an empty bay stays and says what would stop there.
- Monospace only for machine values: paths, commits, branch names, diffs, line counts.
- Motion marks a state change and nothing else.
- Light and dark are the same board; the metal changes, the paper barely does.

## Colors

A muted green-grey metal carrying warm paper, with four saturated state colours that appear only as fields.

### Primary
- **Strip Paper** (`strip`, `strip-dark`): every strip, attention strip, the dialog, the toast and the primary key. It is the only surface that casts a shadow.
- **Strip Ink** (`strip-ink`): text on paper, and the focus ring for anything focused on paper.
- **Strip Muted** (`strip-muted`): directory part of a path, secondary lines, chevrons, the border of an on-paper key.
- **Strip Rule** (`strip-rule`): the hairline between a strip's row and its opened diff.

### Secondary
- **Fresh Amber** (`fresh` on `fresh-ink`): not backed up. Also the "box has changes" connector, warning attention flags, pending checks, and text selection.
- **Review Blue** (`review` on `review-ink`): in review. Also "GitHub is ahead", a run in progress, the run sweep, info attention flags, and form accent and caret.
- **Held Red** (`held` on `held-ink`): held back, when hatched; failure and destructive action, when solid.
- **Landed Green** (`landed` on `landed-ink`): landed commits, the "in agreement" connector, passing checks, a switch that is on.

The ink colours stay the same in dark; only the fields get one step lighter.

### Tertiary
- **Diff Add / Diff Delete / Diff Hunk** (`add`, `del`, `hunk`): line backgrounds inside an opened diff, on paper. The line counts on a strip use two darker inks of the same hues (#1c6b3a added, #a02a1f removed).

### Neutral
- **Bay Metal** (`bay`, `bay-dark`): the page and the sticky head rule.
- **Rail** (`rail`): the body of a bay, the run log, and a default key.
- **Rail Edge** (`rail-edge`): the 3px top edge of a bay, hairlines on metal, key borders, the connector line, an unknown connector word, a switch that is off.
- **On Bay / On Bay Muted** (`on-bay`, `on-bay-muted`): text on metal; muted for counts, notes, empty-bay sentences, labels and the log.
- **Focus** (`focus`, `focus-dark`): the 2px focus ring on metal. In dark it is the amber of `fresh-dark`.

### Named Rules
**The Two Inks Rule.** Metal takes `on-bay` inks; paper takes `strip` inks. Anything that sits on paper (a strip, an attention strip, the dialog, the toast) uses paper tokens for its text, borders and focus ring, in both schemes.

**The Hatch Means Held Rule.** The 135 degree hatch (6px of `held`, 6px of `held` mixed 72% with white) appears on a held-back strip's field and on the "Held" attention flag, and nowhere else. Solid `held` means a failure or a destructive key.

**The Field Only Rule.** The four state colours are fills for fields, flags, the connector word and state keys. They are never text colours on metal or on paper.

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
- **Path** (mono, 13px, 1.35): directory in `strip-muted`, file name at 700; a "was" line for a move at 12px.
- **Diff** (mono, 12px, 1.5, tab size 2): opened diffs and the run log.

### Named Rules
**The Machine Values Rule.** Monospace is for paths, commit ids, branch names, diffs, the log and line counts. Sentences, headings, keys and field words are sans.

## Layout

One column of blocks inside a centred container (max 1280px, 20px gutters; 14px at 560px and below): head rule, summary, last-run line, attention strips, key row, bays, run log, footer. Blocks are separated by 16px to 30px.

The head rule is sticky at the top, bleeds to the container edge, and closes with a 1px `rail-edge` line. It is a two-column grid (name, then the span); the span is box commit, connector (at least 40px, flexible), GitHub commit right-aligned. At 560px and below the name stacks above the span.

Bays are a grid with 16px gaps: one column on a phone, two from 720px, three from 1080px. The three working bays (not backed up, in review, held back) take one column each; the landed bay spans the full row. Bays align to the top and grow with their strips; bay order never changes.

Inside a bay: heading and count, an optional note, strips stacked with 6px gaps, then the bay's keys on a rail separated by a 1px `rail-edge` line with 12px above and below it. A strip row is a grid of field (62px; 54px on a phone; 76px landed), path, line counts, chevron, with a minimum height of 42px (48px on a phone). Long paths wrap anywhere; they are never truncated.

The key row wraps, with keys on the left and the two switches pushed right; on a phone keys share the row equally and each switch takes a full line with its track at the far edge. Keys are 38px high (on-paper keys 34px) and 44px at 560px and below.

## Elevation & Depth

Paper casts a shadow; metal does not. Bays are separated from the page by tone (`rail` on `bay`) and a 3px top edge, not by shadow. A strip's shadow is a 1px edge line plus a short, tight, soft drop, the shadow of paper in a holder rather than a floating card.

### Shadow Vocabulary
- **Strip** (`box-shadow: 0 1px 0 rgb(23 32 29 / .18), 0 3px 8px -3px rgb(23 32 29 / .35)`; dark: `0 1px 0 rgb(0 0 0 / .5), 0 4px 10px -4px rgb(0 0 0 / .7)`): strips, attention strips, the primary key.
- **Open strip** (strip shadow plus `0 14px 28px -14px rgb(0 0 0 / .55)`): a strip while its diff is open.
- **Toast** (strip shadow plus `0 12px 30px -10px rgb(0 0 0 / .5)`): the toast.
- **Dialog** (`0 24px 60px -12px rgb(0 0 0 / .6)` over a `rgb(8 14 12 / .6)` backdrop): the confirmation dialog.

### Named Rules
**The Paper Casts Rule.** Only paper surfaces carry a shadow. A bay, a rail, a default key and the head rule are flat.

## Shapes

Rectangular with barely eased corners (3px) on strips, fields, keys, the connector word and the toast. A field takes the strip's two leading corners and runs the strip's full height. A bay is square on top under its 3px edge and eased (3px) at the bottom. The dialog is slightly softer (6px). The only round forms are the switch (22px track, 16px thumb) and the 9px checks dot.

Borders are 1px `rail-edge` on metal and 1px `strip-muted` or `strip-rule` on paper. Icons are 18px line icons drawn at 1.7 stroke with round caps and joins, inline SVG, coloured by the text they sit beside.

## Components

### Keys
- **Shape:** rectangular, 3px corners, 38px high, 14px side padding, optional 18px icon with an 8px gap.
- **Default:** `rail` fill, `rail-edge` border, `on-bay` text. Sits on metal.
- **Primary:** `strip` fill and border with the strip shadow. One per rail: "Back up now" on the first bay, "Merge" on the review bay; "Sync now" in the key row becomes primary only while GitHub is ahead.
- **Danger:** solid `held` fill with `held-ink`; the confirm key of a destructive dialog.
- **On paper:** transparent with a `strip-muted` border and `strip-ink` text, 34px high; the key inside an attention strip, a landed strip, and the dialog's Cancel.
- **Hover / active / disabled:** the border darkens on hover, the key drops 1px when pressed, and a disabled key is at 45% opacity. Transitions are 150ms.
- A key that opens a confirmation ends its label with an ellipsis.

### Switches
A label followed by a 38px by 22px pill track (`rail-edge` off, `landed` on) with a 16px paper thumb that slides 16px in 200ms. Used for the two standing settings, not for actions.

### Bays
`rail` fill, 3px `rail-edge` top edge, padding 10px 12px 12px. Heading (14px, 700) with a muted count on the same baseline. An empty bay keeps its place and carries one muted sentence saying what would stop there. The bay's keys sit on a rail at its foot.

### Strips (signature)
- **Row:** paper, 3px corners, strip shadow; field, path, line counts, chevron. The whole row is one button; it tints with 7% `strip-ink` on hover.
- **Field:** full height, 62px wide, the bay's state colour, the change word in 11px caps. Held fields are hatched and say "held"; their counts are replaced by a lock icon.
- **Open:** the chevron turns 90 degrees, the strip takes the open-strip shadow, and the diff unfolds below a `strip-rule` line (220ms, clipped from the top), up to 440px high and scrolling both ways. Added, removed and hunk lines take `add`, `del` and `hunk`. A held strip opens onto a sentence instead of content.
- **Travel:** when a file changes state its strip moves to the new bay with a view transition (250ms). Skipped above 80 files and under reduced motion.
- **Landed strip:** the field is the commit id as a link (76px, mono), followed by the subject at 600 and a muted author and age line, with an on-paper "Revert…" key at the end. It does not open.

### Attention strips
A paper strip with a full-height flag (104px; 92px on a phone) carrying an icon and one word at 12px, 700: solid `held` for errors, `fresh` for warnings, `review` for information, hatched for held. Then the sentence, then at most a pair of on-paper keys. On a phone the keys drop under the sentence.

### Head rule
Two commit ids in mono at 17px with an 11px muted label over each ("The box", "GitHub · branch"), joined by a 2px `rail-edge` line with a word resting on it (12.5px, 700, 26px high, 3px corners). The word's fill says the relation: `landed` in agreement, `fresh` box has changes, `review` GitHub is ahead or a run in progress, `rail-edge` unknown. While a run is on, a 36px by 4px `review` segment sweeps the line (1.4s, alternating); under reduced motion it is hidden.

### Dialog and toast
The dialog is paper (6px corners, 20px padding, at most 440px wide): an 18px question, a muted sentence, and Cancel plus the confirm key on a right-aligned rail. Focus starts on Cancel. The toast is a paper strip centred at the bottom that rises 12px as it fades in (200ms); an error toast is solid `held`.

### Run log
A collapsed disclosure in bay dress (`rail`, 3px top edge) with a chevron; the log is 12px mono in `on-bay-muted`, up to 420px high.

## Do's and Don'ts

### Do:
- **Do** show a file's state with the bay it is in and a full-height colour field at the head of its strip.
- **Do** keep all four bays on screen in the same order, and give an empty bay one sentence about what would stop there.
- **Do** put a bay's keys on that bay's rail, with one paper-coloured primary key per rail.
- **Do** use paper tokens (`strip-ink`, `strip-muted`, `strip-rule`) for text, borders and focus rings on anything paper, in light and dark.
- **Do** keep monospace for paths, commit ids, branch names, diffs, the log and line counts.
- **Do** keep keys at least 44px high at 560px and below.
- **Do** tie motion to a state change, 150ms to 250ms, with `cubic-bezier(.16, 1, .3, 1)`, and remove it under reduced motion.

### Don't:
- **Don't** build rounded status cards with chips and a file table; that is the add-on default this system refuses.
- **Don't** use a state colour as text colour or as decoration; it fills a field or it is absent.
- **Don't** hatch anything that is not held back.
- **Don't** put a shadow on metal; only paper casts one.
- **Don't** load fonts, scripts or images from outside the file.
- **Don't** show the content of a held-back file, in a diff or anywhere else.
- **Don't** truncate a path; let it wrap.

---
version: 1
slug: "gitops-backup-rootfs-app-panel-index-html"
primary_target: "gitops_backup/rootfs/app/panel/index.html"
related_targets: []
---

# Surface: HA GitOps ingress panel

Scope: `gitops_backup/rootfs/app/panel/index.html`, the only screen of the app. Visitor mode: Operate.

Audience and job: the box's administrator, on a phone right after an alert or on a desktop while editing config. In ten seconds they must know whether the box and GitHub agree, what is waiting, and what needs them; then act once.

Constraints: one self-contained HTML file, no external requests, vanilla JS, light and dark, keyboard and screen reader, one-handed on a phone. Held-file content never shown.

Code-led build (owner's choice, recorded in `.impeccable/config.json`).

## Direction contract

THESIS: Strip Bay. Every changed file is a strip, and its bay says its state: not backed up, in review, held back, landed. Refuses the add-on default of rounded status cards with chips and a file table.

OWN-WORLD: a dark green-black (or sage-grey in light) metal bay; pale paper strips in holders with a soft offset shadow; each strip ends in a flat colour field: the colour and the bay carry the state (amber, blue, hatched red, green), the word in it names the change (new, edit, gone, moved) or, once landed, the commit. Monospace only for paths, commits and diffs. Controls are rectangular keys on the bay rail; the primary key is paper-coloured.

STORY: the head rule tells them the box and GitHub agree or not; one sentence says what is waiting; a bay holds the strips that need them; they press that bay's key.

FIRST VIEWPORT: head rule across the top with the box commit, a connector that reads equal or ahead, and the GitHub commit; under it one plain sentence and any attention strips with their action; then three bays side by side (stacked on a phone), each with its strips and its keys on the rail. The primary key sits on the first bay's rail and in the control row.

SIGNATURE INTERACTION: a strip travels from one bay to another when its state changes (view transition), and opens in place into its diff. MOTION GRAMMAR: state changes only, 150 to 250 ms, ease-out; a sweep along the head rule while a run is on.

FORM: flight progress strip board, third of seven grounded candidates. Seed key 51811922.

Raises: columns never move, a strip changes state in place (departures board); state is a flat colour field, never a small badge (typographic tiles); a head rule that always names where you are (lexicon page).

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

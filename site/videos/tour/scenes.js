/* Scenes of the HA GitOps tour (see ../engine/engine.js and the canvas-video skill).
 *
 * Each scene is fn(t, d, cues): a pure function of t, hooked onto the spoken
 * sentences with at(cues, i). The look is the panel's own (DESIGN.md): a
 * green-black bay, paper strips with a flat state field, a head rule naming
 * the box's commit and GitHub's. File names and commits are demo data.
 */
"use strict";

const P = {
  bay: "#121a18", rail: "#1b2624", edge: "#2c3b38", ink: "#e1e8e4", muted: "#98a8a2",
  strip: "#ebe6d8", sink: "#171e1c", smuted: "#555e58",
  fresh: "#f0b23a", review: "#6aa6f0", held: "#ee7468", landed: "#4dbb84",
};
const FIELD_INK = { fresh: "#2a1c00", review: "#041b38", held: "#2b0703", landed: "#03231a" };

const STR = {
  en: {
    box: "The box", hub: "GitHub · main", agree: "in agreement", drift: "box has changes", ahead: "GitHub is ahead",
    edit: "EDIT", fresh: "NEW", held: "HELD",
    h1: "Your config never stops changing.", onBox: "On the box", onHub: "On GitHub", unmoved: "main has not moved",
    h2: "A pull request in between.", pr: "PULL REQUEST", main: "main", never: "never pushed to main",
    h3: "Out for review.", notBacked: "Not backed up", backup: "Backup pull request #12", quiet: "quiet for two minutes", rolling: "one pull request, kept up to date",
    h4: "Checked before you merge.", c1: "Config validation", c1b: "against a pinned Home Assistant version", c2: "Smoke boot", c2b: "a real Home Assistant starts with it", passed: "checks passed", running: "checks running",
    h5: "Merged, pulled, applied.", landed: "Landed on main", merged: "merged", check: "config check: valid", reload: "automation.reload", only: "only what changed", restart: "restart: asked for, never forced",
    h6: "Secrets stay home.", heldBay: "Held back", neverSees: "GitHub never sees it", looks: "contains a password",
    h7: "HA GitOps", sub7: "Your config, one pull request at a time.", add: "Add to Home Assistant", demo: "demo data",
  },
  fr: {
    box: "La box", hub: "GitHub · main", agree: "en accord", drift: "la box a changé", ahead: "GitHub est en avance",
    edit: "MODIF", fresh: "NOUV", held: "RETENU",
    h1: "Votre config change sans arrêt.", onBox: "Sur la box", onHub: "Sur GitHub", unmoved: "main n'a pas bougé",
    h2: "Une pull request entre les deux.", pr: "PULL REQUEST", main: "main", never: "jamais poussé sur main",
    h3: "En relecture.", notBacked: "Non sauvegardé", backup: "Pull request de sauvegarde #12", quiet: "deux minutes sans changement", rolling: "une seule pull request, tenue à jour",
    h4: "Vérifié avant la fusion.", c1: "Validation de la config", c1b: "sur une version figée de Home Assistant", c2: "Démarrage de test", c2b: "un vrai Home Assistant démarre avec", passed: "vérifications réussies", running: "vérifications en cours",
    h5: "Fusionné, récupéré, appliqué.", landed: "Arrivé sur main", merged: "fusionnée", check: "vérification de la config : valide", reload: "automation.reload", only: "seulement ce qui a changé", restart: "redémarrage : proposé, jamais imposé",
    h6: "Les secrets restent à la maison.", heldBay: "Retenu", neverSees: "GitHub ne le voit jamais", looks: "contient un mot de passe",
    h7: "HA GitOps", sub7: "Votre config, une pull request à la fois.", add: "Ajouter à Home Assistant", demo: "données de démonstration",
  },
};

/* ---- the Strip Bay kit --------------------------------------------------- */

function withAlpha(a, fn) {
  if (a <= 0) return;
  ctx.save(); ctx.globalAlpha *= clamp(a); fn(); ctx.restore();
}

/* A paper strip: state field on the left, mono file name, optional right note. */
function strip(x, y, w, state, word, name, o = {}) {
  const h = o.h || 46, fw = o.fw || 74, a = o.alpha === undefined ? 1 : o.alpha;
  withAlpha(a, () => {
    ctx.fillStyle = "rgba(0,0,0,.55)"; rr(x, y + 3, w, h, 3); ctx.fill();          // paper casts
    ctx.fillStyle = P.strip; rr(x, y, w, h, 3); ctx.fill();
    ctx.save(); rr(x, y, fw, h, 3); ctx.clip();
    ctx.fillStyle = P[state]; ctx.fillRect(x, y, fw, h);
    if (state === "held") {                                                         // hatch means held
      ctx.strokeStyle = "rgba(255,255,255,.34)"; ctx.lineWidth = 6;
      for (let i = -h; i < fw + h; i += 17) { ctx.beginPath(); ctx.moveTo(x + i, y + h); ctx.lineTo(x + i + h, y); ctx.stroke(); }
    }
    ctx.restore();
    const mono = state === "landed";
    text(word, x + fw / 2, y + h / 2 + 1, { size: mono ? 14 : 12, weight: 700, font: mono ? MONO : INTER, color: FIELD_INK[state], align: "center", base: "middle", spacing: mono ? 0 : 0.6 });
    text(name, x + fw + 14, y + h / 2 + 1, { size: o.size || 16, weight: 700, font: o.sans ? INTER : MONO, color: P.sink, base: "middle" });
    if (o.right) text(o.right, x + w - 14, y + h / 2 + 1, { size: 13, weight: 600, font: MONO, color: P.smuted, align: "right", base: "middle" });
    if (o.lock) drawLock(x + w - 30, y + h / 2, P.smuted);
  });
}

function drawLock(cx, cy, color) {
  ctx.save(); ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.lineJoin = "round";
  rr(cx - 8, cy - 2, 16, 12, 2); ctx.stroke();
  ctx.beginPath(); ctx.arc(cx, cy - 2, 5, Math.PI, 0); ctx.stroke(); ctx.restore();
}

/* A bay: a tonal channel with a 3px top rail and a title. */
function bay(x, y, w, h, title, a = 1, note = "") {
  withAlpha(a, () => {
    ctx.fillStyle = P.rail; ctx.fillRect(x, y, w, h);
    ctx.fillStyle = P.edge; ctx.fillRect(x, y, w, 3);
    text(title, x + 14, y + 30, { size: 16, weight: 700, color: P.ink });
    if (note) text(note, x + w - 14, y + 30, { size: 14, color: P.muted, align: "right" });
  });
}

/* The head rule: box commit, state field on the wire, GitHub commit. */
function rule(y, boxSha, hubSha, state, word, a = 1) {
  withAlpha(a, () => {
    text(S.box, 80, y - 12, { size: 13, color: P.muted });
    text(boxSha, 80, y + 12, { size: 20, weight: 700, font: MONO, color: P.ink });
    text(S.hub, 1200, y - 12, { size: 13, color: P.muted, align: "right" });
    text(hubSha, 1200, y + 12, { size: 20, weight: 700, font: MONO, color: P.ink, align: "right" });
    ctx.fillStyle = P.edge; ctx.fillRect(190, y + 2, 900, 2);
    const fill = { equal: P.landed, drift: P.fresh, ahead: P.review }[state];
    const ink = { equal: FIELD_INK.landed, drift: FIELD_INK.fresh, ahead: FIELD_INK.review }[state];
    const w = measure(word, 15, 700, INTER) + 28;
    ctx.fillStyle = fill; rr(640 - w / 2, y - 13, w, 32, 3); ctx.fill();
    text(word, 640, y + 4, { size: 15, weight: 700, color: ink, align: "center", base: "middle" });
  });
}

/* The centre wire that separates the two sides. */
function wire(y1, y2, a = 1) {
  withAlpha(a, () => { ctx.fillStyle = P.edge; ctx.fillRect(639, y1, 2, y2 - y1); });
}

function key(label, cx, cy, k, o = {}) {
  const w = measure(label, o.size || 22, 700, INTER) + 56, h = o.h || 58, s = 0.9 + 0.1 * outBack(k);
  withAlpha(k, () => {
    ctx.save(); ctx.translate(cx, cy); ctx.scale(s, s);
    ctx.fillStyle = "rgba(0,0,0,.55)"; rr(-w / 2, -h / 2 + 4, w, h, 3); ctx.fill();
    ctx.fillStyle = P.strip; rr(-w / 2, -h / 2, w, h, 3); ctx.fill();
    text(label, 0, 2, { size: o.size || 22, weight: 700, color: P.sink, align: "center", base: "middle" });
    ctx.restore();
  });
}

const LX = 80, RX = 680, BW = 520;      // the two columns

/* ---- scenes -------------------------------------------------------------- */

function sDrift(t, d, cues) {
  headline(S.h1, seg(t, 0.15, 0.8), 118, 46);
  const a1 = at(cues, 1, 4.4), a2 = at(cues, 2, 7), a3 = at(cues, 3, 9.6);
  const drifted = t > a1 + 0.2;
  rule(196, "4c5a275", "4c5a275", drifted ? "drift" : "equal", drifted ? S.drift : S.agree, seg(t, 0.4, 1));
  wire(240, 590, seg(t, 0.5, 1.1));
  bay(LX, 240, BW, 330, S.onBox, seg(t, 0.5, 1.1));
  bay(RX, 240, BW, 330, S.onHub, seg(t, 0.6, 1.2));
  strip(RX + 12, 286, BW - 24, "landed", "4c5a275", "initial config import", { sans: true, alpha: seg(t, 0.9, 1.4) });
  const rows = [[a1, "fresh", S.edit, "automations.yaml", "+6 −0"], [a2, "fresh", S.edit, "custom_components/hacs/const.py", "+2 −2"], [a3, "fresh", S.edit, "configuration.yaml", "+1 −1"]];
  rows.forEach(([a, st, w, n, r], i) => {
    const k = outCubic(seg(t, a + 0.1, a + 0.6));
    strip(LX + 12, 286 + i * 54 + (1 - k) * 14, BW - 24, st, w, n, { right: r, alpha: k });
  });
  text(S.unmoved, RX + 14, 372, { size: 16, color: P.muted, alpha: seg(t, a2 + 0.4, a2 + 1) });
}

function sIdea(t, d, cues) {
  headline(S.h2, seg(t, 0.15, 0.8), 118, 46);
  const a1 = at(cues, 1, 5);
  const y = 430;
  // the two ends
  bay(LX, y - 70, 250, 140, S.box, seg(t, 0.3, 0.9));
  strip(LX + 12, y - 24, 226, "fresh", S.edit, "…yaml", { alpha: seg(t, 0.6, 1.1) });
  bay(950, y - 70, 250, 140, S.main, seg(t, 0.4, 1));
  strip(962, y - 24, 226, "landed", "4c5a275", "", { alpha: seg(t, 0.7, 1.2) });
  // the gate
  const g = seg(t, 1.2, 2);
  arrow(340, y, 500, y, seg(t, 1.0, 1.7), P.muted);
  key(S.pr, 640, y, g, { size: 20 });
  arrow(780, y, 940, y, seg(t, 2.0, 2.8), P.muted);
  // the road that does not exist
  const k = seg(t, a1 + 0.2, a1 + 1.1);
  if (k > 0) {
    const pts = []; for (let i = 0; i <= 40; i++) { const u = i / 40; pts.push([330 + u * 620, y - 100 - Math.sin(u * Math.PI) * 90]); }
    line(partial(pts, k), P.held, 3, 0.9, [10, 8]);
    cross(640, y - 190, 26, P.held, seg(t, a1 + 0.9, a1 + 1.4));
    text(S.never, 640, y - 232, { size: 20, weight: 700, color: P.held, align: "center", alpha: seg(t, a1 + 1.0, a1 + 1.5) });
  }
}

function sOut(t, d, cues) {
  headline(S.h3, seg(t, 0.15, 0.8), 118, 46);
  const a0 = at(cues, 0, 0.5), a1 = at(cues, 1, 4.5);
  rule(196, "4c5a275", "4c5a275", "drift", S.drift, seg(t, 0.2, 0.8));
  wire(240, 590);
  bay(LX, 240, BW, 330, S.notBacked);
  bay(RX, 240, BW, 330, S.backup, 1, t > a0 + 2.6 ? S.running : "");
  // first strip crosses
  const k1 = inOut(seg(t, a0 + 1.6, a0 + 2.8));
  strip(lerp(LX + 12, RX + 12, k1), 286, BW - 24, k1 < 0.5 ? "fresh" : "review", S.edit, "automations.yaml", { right: "+6 −0" });
  // the quiet timer, while it waits
  const q = seg(t, a0 + 0.2, a0 + 1.6);
  withAlpha(1 - seg(t, a0 + 1.6, a0 + 2.0), () => {
    ctx.fillStyle = P.edge; ctx.fillRect(LX + 14, 350, BW - 28, 4);
    ctx.fillStyle = P.fresh; ctx.fillRect(LX + 14, 350, (BW - 28) * q, 4);
    text(S.quiet, LX + 14, 380, { size: 15, color: P.muted });
  });
  // a second edit joins the same pull request
  const b = outCubic(seg(t, a1 + 0.1, a1 + 0.6)), k2 = inOut(seg(t, a1 + 1.2, a1 + 2.4));
  strip(lerp(LX + 12, RX + 12, k2), lerp(286, 340, k2), BW - 24, k2 < 0.5 ? "fresh" : "review", S.edit, "scripts.yaml", { right: "+5 −0", alpha: b });
  text(S.rolling, RX + 14, 420, { size: 16, color: P.muted, alpha: seg(t, a1 + 2.4, a1 + 3) });
}

function sChecked(t, d, cues) {
  headline(S.h4, seg(t, 0.15, 0.8), 118, 46);
  const a0 = at(cues, 0, 0.5), a1 = at(cues, 1, 3.6);
  const done = t > a1 + 1.5;
  rule(196, "4c5a275", "4c5a275", "drift", S.drift);
  wire(240, 590);
  bay(LX, 240, BW, 330, S.notBacked);
  text("—", LX + 14, 304, { size: 18, color: P.muted });
  bay(RX, 240, BW, 330, S.backup, 1, done ? S.passed : S.running);
  strip(RX + 12, 286, BW - 24, "review", S.edit, "automations.yaml", { right: "+6 −0" });
  strip(RX + 12, 340, BW - 24, "review", S.edit, "scripts.yaml", { right: "+5 −0" });
  const rows = [[a0 + 0.9, S.c1, S.c1b], [a1 + 0.5, S.c2, S.c2b]];
  rows.forEach(([a, h, sub], i) => {
    const y = 432 + i * 62, k = seg(t, a - 0.5, a);
    withAlpha(k, () => {
      ring(RX + 30, y, 13, P.edge, 2);
      text(h, RX + 58, y - 6, { size: 17, weight: 700, color: P.ink });
      text(sub, RX + 58, y + 16, { size: 14, color: P.muted });
    });
    check(RX + 30, y, 12, P.landed, seg(t, a + 0.3, a + 0.9));
  });
}

function sBack(t, d, cues) {
  headline(S.h5, seg(t, 0.15, 0.8), 118, 46);
  const a0 = at(cues, 0, 0.5), a1 = at(cues, 1, 2), a2 = at(cues, 2, 7.5);
  const merged = t > a0 + 0.9, pulled = t > a1 + 2.2;
  rule(196, pulled ? "9e1f3b2" : "4c5a275", merged ? "9e1f3b2" : "4c5a275",
    pulled ? "equal" : merged ? "ahead" : "drift", pulled ? S.agree : merged ? S.ahead : S.drift);
  wire(240, 590);
  bay(LX, 240, BW, 330, S.onBox);
  bay(RX, 240, BW, 150, S.backup, 1, merged ? S.merged : S.passed);
  bay(RX, 404, BW, 166, S.landed);
  const k = inOut(seg(t, a0 + 0.3, a0 + 1.3));
  strip(RX + 12, lerp(286, 450, k), BW - 24, k < 0.5 ? "review" : "landed", k < 0.5 ? S.edit : "9e1f3b2", "automations.yaml", { right: "+6 −0" });
  strip(RX + 12, 504, BW - 24, "landed", "4c5a275", "initial config import", { sans: true });
  // the pull: packets run back along the wire to the box
  if (t > a1 + 0.2 && t < a1 + 2.6) packets(RX - 4, 310, LX + BW + 4, 310, t - a1, { n: 4, period: 0.9, color: P.landed, r: 5 });
  const rows = [[a1 + 2.2, S.check], [a1 + 3.2, S.reload]];
  rows.forEach(([a, s], i) => {
    const y = 304 + i * 52;
    check(LX + 30, y, 12, P.landed, seg(t, a, a + 0.5));
    text(s, LX + 58, y + 6, { size: 18, weight: 600, font: i ? MONO : INTER, color: P.ink, alpha: seg(t, a - 0.2, a + 0.3) });
  });
  text(S.only, LX + 58, 386, { size: 15, color: P.muted, alpha: seg(t, a1 + 3.8, a1 + 4.4) });
  const r = seg(t, a2 + 0.2, a2 + 0.8);
  withAlpha(r, () => {
    ctx.fillStyle = P.strip; rr(LX + 12, 452, BW - 24, 52, 3); ctx.fill();
    text(S.restart, LX + 30, 479, { size: 17, weight: 700, color: P.sink, base: "middle" });
  });
}

function sSecrets(t, d, cues) {
  headline(S.h6, seg(t, 0.15, 0.8), 118, 46);
  const a0 = at(cues, 0, 0.5), a1 = at(cues, 1, 3), a2 = at(cues, 2, 4.5);
  rule(196, "9e1f3b2", "9e1f3b2", "drift", S.drift, seg(t, 0.2, 0.8));
  bay(LX, 240, BW, 330, S.heldBay);
  bay(RX, 240, BW, 330, S.onHub);
  // the barrier: the wire thickens where the strip tries to cross
  const push = seg(t, a0 + 0.9, a1) * (1 - seg(t, a1, a1 + 0.5));
  ctx.fillStyle = P.edge; ctx.fillRect(639, 240, 2, 350);
  withAlpha(seg(t, a0 + 1.2, a1 + 0.1), () => { ctx.fillStyle = P.held; ctx.fillRect(636, 270, 8, 78); });
  const state = t < a0 + 0.7 ? "fresh" : "held";
  strip(LX + 12 + outCubic(push) * 74, 286, BW - 24, state, state === "held" ? S.held : S.fresh, "mqtt.yaml", { lock: state === "held", alpha: seg(t, a0, a0 + 0.5) });
  text(S.looks, LX + 14, 362, { size: 16, color: P.muted, alpha: seg(t, a0 + 0.7, a0 + 1.2) });
  text(S.neverSees, RX + 14, 304, { size: 18, weight: 600, color: P.muted, alpha: seg(t, a1 + 0.2, a1 + 0.8) });
  ["secrets.yaml", ".storage/", "*.key   *.pem   *.token", "secrets.yaml.bak"].forEach((n, i) => {
    const k = outCubic(seg(t, a2 + i * 0.3, a2 + 0.5 + i * 0.3));
    strip(LX + 12, 396 + i * 42 + (1 - k) * 10, BW - 24, "held", S.held, n, { h: 36, size: 14, lock: true, alpha: k });
  });
}

function sInstall(t, d, cues) {
  const a1 = at(cues, 1, 4);
  const out = 1 - seg(t, a1 - 0.2, a1 + 0.4);
  // the panel, schematically
  withAlpha(out, () => {
    rule(110, "9e1f3b2", "9e1f3b2", "drift", S.drift, seg(t, 0.2, 0.8));
    const cols = [[80, S.notBacked, "fresh", [[S.edit, "configuration.yaml"], [S.fresh, "dashboards/energy.yaml"]]],
      [460, S.backup.replace(" #12", ""), "review", [[S.edit, "automations.yaml"], [S.edit, "scripts.yaml"]]],
      [840, S.heldBay, "held", [[S.held, "mqtt.yaml"]]]];
    cols.forEach(([x, title, st, rows], i) => {
      const k = seg(t, 0.4 + i * 0.25, 1 + i * 0.25);
      bay(x, 170, 360, 230, title, k);
      rows.forEach(([w, n], j) => strip(x + 10, 214 + j * 52, 340, st, w, n, { size: 14, fw: 66, alpha: k, lock: st === "held" }));
    });
    bay(80, 420, 1120, 150, S.landed, seg(t, 1.2, 1.8));
    strip(90, 464, 1100, "landed", "9e1f3b2", "automations.yaml", { alpha: seg(t, 1.3, 1.9) });
    strip(90, 516, 1100, "landed", "4c5a275", "initial config import", { sans: true, alpha: seg(t, 1.4, 2) });
    text(S.demo, 1200, 596, { size: 12, font: MONO, color: P.muted, align: "right", alpha: seg(t, 1.5, 2) });
  });
  // the close
  const k = seg(t, a1 + 0.3, a1 + 1);
  text(S.h7, 640, 250, { size: 84, weight: 800, font: SORA, color: P.ink, align: "center", alpha: k });
  text(S.sub7, 640, 312, { size: 26, color: P.muted, align: "center", alpha: seg(t, a1 + 0.6, a1 + 1.2) });
  key(S.add, 640, 410, seg(t, a1 + 1.0, a1 + 1.7), { size: 26, h: 70 });
  text("mmornati.github.io/ha-gitops", 640, 500, { size: 20, font: MONO, color: P.ink, align: "center", alpha: seg(t, a1 + 1.5, a1 + 2.1) });
}

VIDEO.create({
  title: { en: "HA GitOps: your config, one pull request at a time", fr: "HA GitOps : votre config, une pull request à la fois" },
  kicker: { en: "HA GitOps", fr: "HA GitOps" },
  langs: ["en", "fr"],
  theme: { bg: P.bay, bg2: P.bay, panel: P.rail, panel2: P.rail, line: P.edge, ink: P.ink, soft: P.muted, faint: "#6f807a",
           accent: P.fresh, accent2: P.review, good: P.landed, warn: P.fresh, bad: P.held },
  background: () => { ctx.fillStyle = P.bay; ctx.fillRect(0, 0, W, H); },
  strings: STR,
  poster: "install",
  scenes: { drift: sDrift, idea: sIdea, out: sOut, checked: sChecked, back: sBack, secrets: sSecrets, install: sInstall },
});

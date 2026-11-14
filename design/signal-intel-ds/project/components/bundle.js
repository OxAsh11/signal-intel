/* @ds-bundle: {"format":4,"namespace":"SignalIntel","components":[{"name":"Button"},{"name":"VerdictBadge"},{"name":"BandChip"},{"name":"StageBadge"},{"name":"ReplyClassTag"},{"name":"DerivedValue"},{"name":"EvidenceQuote"},{"name":"ScoreBreakdown"},{"name":"CostPreview"},{"name":"PushModeSwitch"},{"name":"MetricTile"},{"name":"HealthIndicator"},{"name":"ReviewQueueItem"},{"name":"Card"},{"name":"FamilyTag"}]} */
(function () {
  var React = window.React;
  var h = React.createElement;
  function cx() { return Array.prototype.filter.call(arguments, Boolean).join(" "); }
  function slug(s) { return String(s).toLowerCase().replace(/[^a-z]/g, ""); }

  /* Primary buttons carry the right-pointing chevron (guidelines: 6.504 x 9.912 viewBox). */
  function Arrow() {
    return h("svg", { className: "si-btn-arrow", viewBox: "0 0 6.504 9.912", "aria-hidden": "true" },
      h("path", { d: "M0.8 0 L6.5 4.956 L0.8 9.912 L0 9.1 L4.8 4.956 L0 0.8 Z", fill: "currentColor" }));
  }

  /* Button. Pass `cost` for any action that spends; it is shown inside the button. */
  function Button(p) {
    var variant = p.variant || "secondary", size = p.size || "md";
    var rest = Object.assign({}, p); delete rest.variant; delete rest.size; delete rest.cost; delete rest.className; delete rest.children;
    return h("button", Object.assign({ type: "button" }, rest, {
      className: cx("si-btn", variant !== "secondary" && "si-btn-" + variant, size === "sm" && "si-btn-sm", p.className)
    }), p.children, p.cost ? h("span", { className: "si-btn-cost" }, p.cost) : null, variant === "primary" ? h(Arrow) : null);
  }

  var VERDICT = {
    Confirmed: { glyph: "●", hint: "Quoted claim from an accepted source" },
    Likely: { glyph: "◐", hint: "Supporting claims, not yet the Confirmed rule" },
    Unverified: { glyph: "○", hint: "No qualifying claim yet" },
    Contradicted: { glyph: "✕", hint: "An active claim negates the hypothesis" }
  };
  function VerdictBadge(p) {
    var v = VERDICT[p.verdict] ? p.verdict : "Unverified";
    return h("span", { className: cx("si-chip", "si-verdict-" + slug(v)), title: VERDICT[v].hint },
      h("span", { className: "si-glyph", "aria-hidden": "true" }, VERDICT[v].glyph), v);
  }

  var BAND_LEVEL = { High: 3, Medium: 2, Low: 1, Excluded: 0 };
  function BandChip(p) {
    var b = p.band in BAND_LEVEL ? p.band : "Low", lvl = BAND_LEVEL[b];
    var pips = [0, 1, 2].map(function (i) {
      return h("span", { key: i, className: cx("si-pip", i < lvl && "on"), style: { height: 5 + i * 3 + "px" } });
    });
    return h("span", { className: cx("si-chip", "si-band", "si-band-" + slug(b)) },
      b === "Excluded" ? null : h("span", { className: "si-pips", "aria-hidden": "true" }, pips),
      b, p.reason && b === "Excluded" ? h("span", { style: { fontWeight: 400, textDecoration: "none" } }, " · " + p.reason) : null);
  }

  var STAGES = ["Untouched", "Queued", "Contacted", "Engaged", "Qualified", "Meeting", "Handed off"];
  function StageBadge(p) {
    var exit = p.stage === "Dormant" || p.stage === "Disqualified";
    var idx = exit ? STAGES.indexOf(p.lastStage || "Contacted") : STAGES.indexOf(p.stage);
    var steps = STAGES.map(function (s, i) { return h("span", { key: s, className: cx("si-stage-step", i <= idx && "done") }); });
    return h("span", { className: cx("si-stage", exit && "si-stage-exit", exit && "si-stage-" + slug(p.stage)), title: exit ? "Exited after " + (p.lastStage || "Contacted") : STAGES.length - idx - 1 + " stages to hand-off" },
      h("span", { className: "si-stage-track", "aria-hidden": "true" }, steps), p.stage);
  }

  var REPLY = { "Interested": "interested", "Referral": "referral", "Not now": "notnow", "Not interested": "notinterested", "Out of office": "ooo", "Unsubscribe": "unsubscribe" };
  function ReplyClassTag(p) {
    var k = REPLY[p.replyClass] || "notinterested";
    return h("span", { className: cx("si-chip", "si-reply-" + k) },
      p.source === "human" ? h("span", { className: "si-glyph", title: "Marked by a person", "aria-label": "Marked by a person" }, "✓") : null,
      p.replyClass);
  }

  /* DerivedValue: any computed value. No edit control, ever; Explain opens the reasons. */
  function DerivedValue(p) {
    var st = React.useState(!!p.defaultOpen), open = st[0], setOpen = st[1];
    return h("span", { className: "si-derived" },
      p.label ? h("span", { className: "si-label" }, p.label) : null,
      h("span", { className: "si-derived-body" },
        h("span", { className: "si-derived-val" }, p.children),
        h("button", { type: "button", className: "si-explain", "aria-expanded": open, onClick: function () { setOpen(!open); } }, "ⓘ Explain")),
      p.versions ? h("span", { className: "si-derived-stamp" }, p.versions) : null,
      open && p.explanation ? h("div", { className: "si-pop", role: "dialog", "aria-label": "Why this value" }, p.explanation) : null);
  }

  function EvidenceQuote(p) {
    return h("figure", { className: "si-quote", style: { margin: 0 } },
      h("span", { className: "si-quote-rule", "aria-hidden": "true" }),
      h("div", null,
        h("blockquote", { className: "si-quote-text", style: { margin: 0 } }, p.children),
        h("figcaption", { className: "si-quote-meta" },
          p.claim ? h("span", { className: "si-quote-claim" }, p.claim) : null,
          h("span", null, p.sourceType),
          p.sourceHref ? h("a", { href: p.sourceHref }, p.sourceName) : h("span", null, p.sourceName),
          h("span", { className: p.expired ? "si-quote-expired" : null }, p.expired ? "Expired · observed " + p.observed : "Observed " + p.observed),
          p.translated ? h("span", null, "Translated from " + p.translated) : null)));
  }

  function ScoreBreakdown(p) {
    var dims = p.dimensions || [];
    function row(d, extra) {
      return h("div", { key: d.name, className: cx("si-dim", extra) },
        h("span", null, d.name),
        h("span", { className: "si-dim-track", role: "img", "aria-label": d.name + " " + d.value + " of 100" }, h("span", { className: "si-dim-fill", style: { width: d.value + "%", display: "block" } })),
        h("span", { className: "si-dim-num" }, d.value));
    }
    return h("section", { className: "si-score" },
      h("div", { className: "si-score-head" },
        h("div", null, h("div", { className: "si-label" }, "Priority"), h("div", { className: "si-score-big" }, p.priority)),
        h(BandChip, { band: p.band })),
      dims.map(function (d) { return row(d); }),
      h("div", { className: "si-score-sep" }),
      row({ name: "Readiness", value: p.readiness }, "si-dim-readiness"),
      p.gates && p.gates.length ? h("div", { className: "si-gates" },
        p.gates.map(function (g) {
          return h("span", { key: g.name, className: cx("si-gate", g.hit && "si-gate-hit") }, g.hit ? "✕" : "✓", g.name);
        })) : null,
      p.version ? h("div", { className: "si-derived-stamp" }, p.version) : null);
  }

  function CostPreview(p) {
    var t = p.tier || "Narrow";
    function cell(label, val) { return h("div", { className: "si-cost-cell", key: label }, h("span", { className: "si-label" }, label), h("span", { className: "si-cost-num" }, val)); }
    return h("section", { className: "si-cost" },
      h("div", { className: "si-cost-head" },
        h("strong", null, p.title || "Preview of this edit"),
        h("span", { className: cx("si-tier", "si-tier-" + slug(t)) }, t)),
      h("div", { className: "si-cost-grid" },
        cell("Accounts in", p.entering), cell("Accounts out", p.leaving), cell("Est. cost", p.cost), cell("Duration", p.duration)),
      h("div", { className: "si-cost-foot" },
        p.needsApproval ? h("span", { className: "si-approval" }, "▲ Over budget: needs Admin approval") : h("span", { className: "si-muted" }, "Within the Brief budget" + (p.budgetLeft ? " · " + p.budgetLeft + " left" : "")),
        h("span", { className: "si-row", style: { padding: 0, gap: 8 } },
          h(Button, { variant: "quiet", size: "sm", onClick: p.onCancel }, "Cancel"),
          h(Button, { variant: "primary", size: "sm", onClick: p.onRun }, p.needsApproval ? "Request approval" : "Run edit"))));
  }

  function PushModeSwitch(p) {
    var modes = [["off", "Off"], ["dry run", "Dry run"], ["live", "Live"]];
    var st = React.useState(p.value || "dry run"), v = st[0], setV = st[1];
    return h("span", { className: "si-seg", role: "group", "aria-label": "Push mode" },
      modes.map(function (m) {
        return h("button", { key: m[0], type: "button", "aria-pressed": v === m[0], className: m[0] === "live" ? "si-seg-live" : null,
          onClick: function () { setV(m[0]); if (p.onChange) p.onChange(m[0]); } },
          m[0] === "live" ? h("span", { className: "si-dot", "aria-hidden": "true" }) : null, m[1]);
      }));
  }

  /* MetricTile: always a link to the rows behind the number. */
  function MetricTile(p) {
    return h("a", { className: cx("si-metric", p.family && "si-fam-" + p.family), href: p.href || "#" },
      h("span", { className: "si-label" }, p.label),
      h("span", { className: "si-metric-num" }, p.value),
      h("span", { className: "si-metric-sub" },
        p.incomplete ? h("span", { className: "si-metric-incomplete" }, "▲ Incomplete period") : h("span", null, p.delta || ""),
        h("span", { className: "si-metric-ver" }, p.version)));
  }

  var HEALTH = { ok: "Healthy", degraded: "Degraded", incident: "Incident", silent: "Silent" };
  function HealthIndicator(p) {
    var s = HEALTH[p.status] ? p.status : "ok";
    return h("span", { className: cx("si-health", "si-health-" + s) },
      h("span", { className: "si-health-mark", "aria-hidden": "true" }),
      h("span", { className: "si-health-name" }, p.name),
      h("span", null, HEALTH[s]),
      p.detail ? h("span", { className: "si-health-detail" }, "· " + p.detail) : null);
  }

  function ReviewQueueItem(p) {
    function cand(c, i) {
      return h("div", { className: "si-cand", key: i },
        h("span", { className: "si-label" }, i === 0 ? "Incoming mention" : "Existing entity"),
        h("span", { className: "si-cand-name" }, c.name),
        (c.fields || []).map(function (f) {
          return h("div", { className: "si-cand-row", key: f[0] }, h("span", { className: "si-muted" }, f[0]), h("span", { className: f[2] ? "si-diff" : null }, f[1]));
        }));
    }
    return h("article", { className: "si-review" },
      h("header", { className: "si-review-head" },
        h("span", { className: "si-review-kind" }, p.kind || "Identity match", h("span", { className: "si-conf" }, "match " + p.confidence)),
        h("span", { className: "si-muted", style: { fontSize: 12 } }, p.waiting)),
      h("div", { className: "si-review-cands" }, (p.candidates || []).map(cand)),
      h("footer", { className: "si-review-actions" },
        h(Button, { variant: "primary", size: "sm" }, "Same company: merge"),
        h(Button, { size: "sm" }, "Different companies"),
        h(Button, { variant: "quiet", size: "sm" }, "Parent / subsidiary")));
  }

  /* Colour families identify kinds of components: pool discovery, mint judgement,
     grass engagement, mustard needs you, lava cost and alerts. */
  var FAMILIES = { pool: 1, mint: 1, grass: 1, mustard: 1, lava: 1 };
  function Card(p) {
    var fam = FAMILIES[p.family] ? p.family : null;
    var cls = !fam ? "si-card" : p.kind === "feature" ? "si-fcard si-fam-" + fam : "si-card si-gcard si-fam-" + fam;
    return h("section", { className: cx(cls, p.className), style: p.style }, p.children);
  }
  function FamilyTag(p) {
    var fam = FAMILIES[p.family] ? p.family : "pool";
    return h("span", { className: "si-ftag si-fam-" + fam }, p.children);
  }

  var api = { Button: Button, VerdictBadge: VerdictBadge, BandChip: BandChip, StageBadge: StageBadge, ReplyClassTag: ReplyClassTag, DerivedValue: DerivedValue, EvidenceQuote: EvidenceQuote, ScoreBreakdown: ScoreBreakdown, CostPreview: CostPreview, PushModeSwitch: PushModeSwitch, MetricTile: MetricTile, HealthIndicator: HealthIndicator, ReviewQueueItem: ReviewQueueItem, Card: Card, FamilyTag: FamilyTag };
  window.SignalIntel = Object.assign(window.SignalIntel || {}, api);
})();

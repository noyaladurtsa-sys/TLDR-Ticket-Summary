const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, HeadingLevel, BorderStyle, WidthType,
  ShadingType, VerticalAlign, PageNumber, PageBreak, LevelFormat,
  ExternalHyperlink
} = require("docx");
const fs = require("fs");

// ── Color palette ─────────────────────────────────────────────────────────────
const NAVY    = "1B3A6B";
const AMBER   = "F59E0B";
const RED     = "DC2626";
const GREEN   = "16A34A";
const BLUE    = "2563EB";
const GRAY    = "6B7280";
const LIGHT   = "F8FAFC";
const WHITE   = "FFFFFF";
const AMBER_L = "FFFBEA";
const RED_L   = "FEF2F2";
const GREEN_L = "F0FDF4";
const BLUE_L  = "EEF3FB";
const BORDER  = "E5E7EB";

function border(color = BORDER) {
  return { style: BorderStyle.SINGLE, size: 1, color };
}
function borders(color = BORDER) {
  const b = border(color);
  return { top: b, bottom: b, left: b, right: b };
}
function noBorder() {
  const nb = { style: BorderStyle.NONE, size: 0, color: "FFFFFF" };
  return { top: nb, bottom: nb, left: nb, right: nb };
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function heading1(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    spacing: { before: 320, after: 160 },
    children: [new TextRun({ text, bold: true, size: 32, color: NAVY, font: "Arial" })],
  });
}

function heading2(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    spacing: { before: 240, after: 120 },
    children: [new TextRun({ text, bold: true, size: 26, color: NAVY, font: "Arial" })],
  });
}

function heading3(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_3,
    spacing: { before: 200, after: 80 },
    children: [new TextRun({ text, bold: true, size: 22, color: NAVY, font: "Arial" })],
  });
}

function para(text, opts = {}) {
  return new Paragraph({
    spacing: { before: 60, after: 80 },
    children: [new TextRun({ text, size: 22, font: "Arial", color: "1a1a2e", ...opts })],
  });
}

function bullet(text, bold_prefix = "") {
  return new Paragraph({
    numbering: { reference: "bullets", level: 0 },
    spacing: { before: 40, after: 40 },
    children: [
      ...(bold_prefix ? [new TextRun({ text: bold_prefix + " ", bold: true, size: 22, font: "Arial", color: NAVY })] : []),
      new TextRun({ text, size: 22, font: "Arial", color: "1a1a2e" }),
    ],
  });
}

function spacer(size = 80) {
  return new Paragraph({ spacing: { before: size, after: 0 }, children: [new TextRun("")] });
}

function divider() {
  return new Paragraph({
    spacing: { before: 120, after: 120 },
    border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: BORDER } },
    children: [new TextRun("")],
  });
}

// Colored info box (single row table used as styled block)
function infoBox(text, bgColor = AMBER_L, borderColor = AMBER, bold = false) {
  return new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [9360],
    rows: [
      new TableRow({
        children: [
          new TableCell({
            borders: {
              top: { style: BorderStyle.SINGLE, size: 6, color: borderColor },
              bottom: border(borderColor),
              left: { style: BorderStyle.SINGLE, size: 20, color: borderColor },
              right: border(borderColor),
            },
            shading: { fill: bgColor, type: ShadingType.CLEAR },
            margins: { top: 100, bottom: 100, left: 180, right: 120 },
            width: { size: 9360, type: WidthType.DXA },
            children: [
              new Paragraph({
                spacing: { before: 0, after: 0 },
                children: [new TextRun({ text, size: 22, font: "Arial", color: "1a1a2e", bold })],
              }),
            ],
          }),
        ],
      }),
    ],
  });
}

// Badge pill in table cell
function badge(text, bgColor, textColor = WHITE) {
  return new TableCell({
    borders: noBorder(),
    shading: { fill: bgColor, type: ShadingType.CLEAR },
    margins: { top: 60, bottom: 60, left: 120, right: 120 },
    verticalAlign: VerticalAlign.CENTER,
    children: [
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 0, after: 0 },
        children: [new TextRun({ text, size: 18, bold: true, font: "Arial", color: textColor })],
      }),
    ],
  });
}

function priorityBadge(level) {
  const map = {
    "HIGH":   ["DC2626", WHITE],
    "MEDIUM": ["F59E0B", WHITE],
    "LOW":    ["6B7280", WHITE],
  };
  const [bg, fg] = map[level] || ["6B7280", WHITE];
  return badge(level, bg, fg);
}

function effortBadge(effort) {
  const map = {
    "HIGH EFFORT":   ["1B3A6B", WHITE],
    "MEDIUM EFFORT": ["2563EB", WHITE],
    "LOW EFFORT":    ["16A34A", WHITE],
  };
  const [bg, fg] = map[effort] || ["6B7280", WHITE];
  return badge(effort, bg, fg);
}

// Action plan card table
function actionCard(item) {
  const headerRow = new TableRow({
    children: [
      new TableCell({
        columnSpan: 4,
        borders: { top: border(NAVY), bottom: border(BORDER), left: border(NAVY), right: border(NAVY) },
        shading: { fill: NAVY, type: ShadingType.CLEAR },
        margins: { top: 120, bottom: 120, left: 180, right: 180 },
        width: { size: 9360, type: WidthType.DXA },
        children: [
          new Paragraph({
            spacing: { before: 0, after: 0 },
            children: [
              new TextRun({ text: item.number + "  ", size: 24, bold: true, font: "Arial", color: AMBER }),
              new TextRun({ text: item.title, size: 24, bold: true, font: "Arial", color: WHITE }),
            ],
          }),
        ],
      }),
    ],
  });

  const metaRow = new TableRow({
    children: [
      new TableCell({
        borders: { top: border(BORDER), bottom: border(BORDER), left: border(NAVY), right: border(BORDER) },
        shading: { fill: LIGHT, type: ShadingType.CLEAR },
        margins: { top: 80, bottom: 80, left: 180, right: 120 },
        width: { size: 2340, type: WidthType.DXA },
        children: [
          new Paragraph({ spacing: { before: 0, after: 0 }, children: [new TextRun({ text: "PRIORITY", size: 18, bold: true, font: "Arial", color: GRAY })] }),
          new Paragraph({ spacing: { before: 40, after: 0 }, children: [new TextRun({ text: item.priority, size: 20, bold: true, font: "Arial", color: item.priority === "HIGH" ? RED : item.priority === "MEDIUM" ? AMBER : GRAY })] }),
        ],
      }),
      new TableCell({
        borders: { top: border(BORDER), bottom: border(BORDER), left: border(BORDER), right: border(BORDER) },
        shading: { fill: LIGHT, type: ShadingType.CLEAR },
        margins: { top: 80, bottom: 80, left: 180, right: 120 },
        width: { size: 2340, type: WidthType.DXA },
        children: [
          new Paragraph({ spacing: { before: 0, after: 0 }, children: [new TextRun({ text: "EFFORT", size: 18, bold: true, font: "Arial", color: GRAY })] }),
          new Paragraph({ spacing: { before: 40, after: 0 }, children: [new TextRun({ text: item.effort, size: 20, bold: true, font: "Arial", color: NAVY })] }),
        ],
      }),
      new TableCell({
        borders: { top: border(BORDER), bottom: border(BORDER), left: border(BORDER), right: border(BORDER) },
        shading: { fill: LIGHT, type: ShadingType.CLEAR },
        margins: { top: 80, bottom: 80, left: 180, right: 120 },
        width: { size: 2340, type: WidthType.DXA },
        children: [
          new Paragraph({ spacing: { before: 0, after: 0 }, children: [new TextRun({ text: "TIMELINE", size: 18, bold: true, font: "Arial", color: GRAY })] }),
          new Paragraph({ spacing: { before: 40, after: 0 }, children: [new TextRun({ text: item.timeline, size: 20, bold: true, font: "Arial", color: NAVY })] }),
        ],
      }),
      new TableCell({
        borders: { top: border(BORDER), bottom: border(BORDER), left: border(BORDER), right: border(NAVY) },
        shading: { fill: LIGHT, type: ShadingType.CLEAR },
        margins: { top: 80, bottom: 80, left: 180, right: 120 },
        width: { size: 2340, type: WidthType.DXA },
        children: [
          new Paragraph({ spacing: { before: 0, after: 0 }, children: [new TextRun({ text: "OWNER", size: 18, bold: true, font: "Arial", color: GRAY })] }),
          new Paragraph({ spacing: { before: 40, after: 0 }, children: [new TextRun({ text: item.owner, size: 20, bold: true, font: "Arial", color: NAVY })] }),
        ],
      }),
    ],
  });

  const makeStepRows = (steps) => steps.map((step, i) => new TableRow({
    children: [
      new TableCell({
        columnSpan: 4,
        borders: {
          top: i === 0 ? border(BORDER) : { style: BorderStyle.NONE, size: 0, color: WHITE },
          bottom: i === steps.length - 1 ? border(NAVY) : { style: BorderStyle.NONE, size: 0, color: WHITE },
          left: border(NAVY),
          right: border(NAVY),
        },
        margins: { top: i === 0 ? 120 : 20, bottom: i === steps.length - 1 ? 120 : 20, left: 180, right: 180 },
        width: { size: 9360, type: WidthType.DXA },
        children: [
          new Paragraph({
            spacing: { before: 0, after: 0 },
            numbering: { reference: "numbers", level: 0 },
            children: [new TextRun({ text: step, size: 22, font: "Arial", color: "1a1a2e" })],
          }),
        ],
      }),
    ],
  }));

  return new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [2340, 2340, 2340, 2340],
    rows: [headerRow, metaRow, ...makeStepRows(item.steps)],
  });
}

// ── Document content ──────────────────────────────────────────────────────────
const actions = [
  {
    number: "ACTION 01",
    title: "Trend Intelligence Dashboard — PostgreSQL as Analytics Engine",
    priority: "HIGH",
    effort: "High Effort",
    timeline: "6–10 Weeks",
    owner: "Dev Team",
    steps: [
      "Create new PostgreSQL table: ticket_insights — stores ticket_id, agent_names, verdict_text, insight_text, callback_breaches, csat_score, saved_at, team_name, category.",
      "On every new AI analysis in app.py, extract and INSERT into ticket_insights alongside the existing executive_reports save.",
      "Write aggregation SQL views: weekly_closure_rates, agent_callback_breach_totals, premature_resolution_counts by team and date range.",
      "Build a new Streamlit page (page='📊 Trends Dashboard') with date-range picker, team filter, and KPI cards: premature closures %, callback breach count, avg CSAT per team.",
      "Add Plotly bar + line charts showing week-over-week trends per metric.",
      "Add agent leaderboard table ranked by breach count and CSAT score.",
      "Present to Leadership: 'Are premature ticket closures up 15% across Concentrix this week?' answered in real time.",
    ],
  },
  {
    number: "ACTION 02",
    title: "RAG Context Injection — Linksys SOPs in the System Prompt",
    priority: "HIGH",
    effort: "Medium Effort",
    timeline: "3–5 Weeks",
    owner: "Dev Team + QA",
    steps: [
      "Gather all Linksys SOPs as text: property square-footage thresholds, cascading mesh node limits, RSSI tolerances (e.g. -65 dBm), callback scheduling policy, warranty escalation rules.",
      "Create a sops.txt or sops.json file in the project directory with structured sections per product line.",
      "In app.py, load the SOP file at startup and inject a SOP CONTEXT block into SYSTEM_PROMPT dynamically.",
      "Update the SYSTEM_PROMPT to instruct Claude: 'Flag any agent action that deviates from the SOP baselines above. Be specific — quote the threshold breached.'",
      "Add a new JSON field to Claude output: 'sop_violations': [{'rule': '...', 'agent': '...', 'detail': '...'}].",
      "Display SOP Violations as a new collapsible section in both the Streamlit app and the HTML report with a red warning badge.",
      "QA team reviews and iterates on SOP content quarterly to keep context current.",
    ],
  },
  {
    number: "ACTION 03",
    title: "Accountability Ledger — Per-Agent FCR & SLA Scorecard",
    priority: "HIGH",
    effort: "Medium Effort",
    timeline: "3–4 Weeks",
    owner: "Dev Team",
    steps: [
      "In _build_agents_html(), add per-agent FCR Violation detection: scan for status changes to 'Resolved' followed by a customer reply within 48 hours — flag as premature resolution.",
      "Add SLA Breach detection: find status changes to 'Pending' or 'Waiting' with no follow-up within the committed callback window (default 24h, configurable).",
      "Calculate breach duration in hours/minutes: 'Missed by 3h 22m' displayed per agent row.",
      "Update the Agents Involved table columns to: Name | Role | Messages | FCR Violations | SLA Breaches | Active Dates | Contribution.",
      "Color-code FCR and SLA cells: green (0 breaches), amber (1), red (2+).",
      "Add a summary badge at the top of the Agents section: e.g. '2 FCR violations · 1 callback breach detected'.",
      "Export these metrics into ticket_insights table (Action 01) so they feed the Trends Dashboard.",
    ],
  },
  {
    number: "ACTION 04",
    title: "Coaching Snippets — AI-Generated Correct Handling Scripts",
    priority: "MEDIUM",
    effort: "Low Effort",
    timeline: "1–2 Weeks",
    owner: "Dev Team + QA",
    steps: [
      "Add a new JSON field to SYSTEM_PROMPT output: 'coaching_notes': [{'opportunity_index': 0, 'correct_script': '...', 'policy_reference': '...'}].",
      "For each item in 'opportunities', Claude must generate: what the agent should have said or done instead, and which policy this references.",
      "In the Opportunities expander (Streamlit) and <details> block (HTML report), display each opportunity with an expandable coaching note below it.",
      "Style the coaching note as a green box: 'Correct Handling: ...' with a Policy Reference tag.",
      "Example output — Opportunity: Premature resolution by GirlyJoy on May 01. Coaching Note: Standard Policy: Set ticket to Pending and schedule a timed outbound callback task instead of resolving.",
      "QA team reviews coaching notes monthly and adds to SOP file (Action 02) if new patterns emerge.",
    ],
  },
  {
    number: "ACTION 05",
    title: "Automated Action Triggers — Escalation Webhook to Slack / HappyFox",
    priority: "MEDIUM",
    effort: "Medium Effort",
    timeline: "2–3 Weeks",
    owner: "Dev Team + Operations",
    steps: [
      "Add SLACK_WEBHOOK_URL and HAPPYFOX_ESCALATION_QUEUE_ID to .streamlit/secrets.toml.",
      "Create _evaluate_severity(ai, ticket) function: returns 'SEVERE' if CSAT <= 2, legal/lawyer/management in verdict, or callback_breaches >= 2.",
      "In the Streamlit app, when severity == SEVERE, render a red banner: 'SEVERE RISK DETECTED' with an 'Escalate to Executive Advocacy Squad' button.",
      "On button click, POST to Slack webhook with ticket ID, subject, agent names, CSAT, verdict summary, and direct HappyFox link.",
      "Simultaneously, POST to HappyFox API to reassign ticket to the Executive Advocacy Queue and add an internal note: 'AUTO-ESCALATED by Happy Link Claude — Severe Risk.'",
      "Log all escalations to a new PostgreSQL table: escalation_log with ticket_id, triggered_at, reason, triggered_by.",
      "Add escalation history view to Trends Dashboard (Action 01): escalations per week by team.",
    ],
  },
];

// ── Roadmap summary table ─────────────────────────────────────────────────────
function roadmapTable() {
  const header = new TableRow({
    tableHeader: true,
    children: ["#", "Action", "Priority", "Effort", "Timeline", "Owner"].map(h =>
      new TableCell({
        shading: { fill: NAVY, type: ShadingType.CLEAR },
        borders: borders(NAVY),
        margins: { top: 80, bottom: 80, left: 120, right: 120 },
        children: [new Paragraph({ spacing: { before: 0, after: 0 }, children: [new TextRun({ text: h, bold: true, size: 18, font: "Arial", color: WHITE })] })],
      })
    ),
  });

  const widths = [600, 3800, 1100, 1200, 1200, 1460];
  const rows = actions.map((a, i) => {
    const bg = i % 2 === 0 ? WHITE : LIGHT;
    const cells = [
      a.number.replace("ACTION ", ""),
      a.title,
      a.priority,
      a.effort,
      a.timeline,
      a.owner,
    ].map((val, ci) => new TableCell({
      shading: { fill: bg, type: ShadingType.CLEAR },
      borders: borders(BORDER),
      margins: { top: 80, bottom: 80, left: 120, right: 120 },
      width: { size: widths[ci], type: WidthType.DXA },
      children: [new Paragraph({
        spacing: { before: 0, after: 0 },
        children: [new TextRun({
          text: val,
          size: 18,
          font: "Arial",
          color: ci === 2 ? (val === "HIGH" ? RED : val === "MEDIUM" ? AMBER : GRAY) : "1a1a2e",
          bold: ci === 2,
        })],
      })],
    }));
    return new TableRow({ children: cells });
  });

  return new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: widths,
    rows: [header, ...rows],
  });
}

// ── Build document ────────────────────────────────────────────────────────────
const doc = new Document({
  numbering: {
    config: [
      {
        reference: "bullets",
        levels: [{
          level: 0, format: LevelFormat.BULLET, text: "•",
          alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 540, hanging: 360 } } },
        }],
      },
      {
        reference: "numbers",
        levels: [{
          level: 0, format: LevelFormat.DECIMAL, text: "%1.",
          alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 540, hanging: 360 } } },
        }],
      },
    ],
  },
  styles: {
    default: { document: { run: { font: "Arial", size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 32, bold: true, font: "Arial", color: NAVY },
        paragraph: { spacing: { before: 320, after: 160 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 26, bold: true, font: "Arial", color: NAVY },
        paragraph: { spacing: { before: 240, after: 120 }, outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 22, bold: true, font: "Arial", color: NAVY },
        paragraph: { spacing: { before: 200, after: 80 }, outlineLevel: 2 } },
    ],
  },
  sections: [
    // ── Cover Page ────────────────────────────────────────────────────────────
    {
      properties: {
        page: {
          size: { width: 12240, height: 15840 },
          margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 },
        },
      },
      children: [
        spacer(1800),
        new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: { before: 0, after: 80 },
          children: [new TextRun({ text: "🔗", size: 80, font: "Arial" })],
        }),
        new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: { before: 80, after: 80 },
          children: [new TextRun({ text: "Happy Link Claude TLDR", size: 52, bold: true, font: "Arial", color: NAVY })],
        }),
        new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: { before: 40, after: 200 },
          children: [new TextRun({ text: "Ticket Summary — Action Plan", size: 36, font: "Arial", color: GRAY })],
        }),
        divider(),
        spacer(120),
        new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: { before: 0, after: 60 },
          children: [new TextRun({ text: "Based on Senior Director of Customer Advocacy Review", size: 24, bold: true, font: "Arial", color: NAVY })],
        }),
        new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: { before: 0, after: 60 },
          children: [new TextRun({ text: "& Senior Quality Assurance Specialist Feedback", size: 24, font: "Arial", color: NAVY })],
        }),
        spacer(200),
        new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: { before: 0, after: 40 },
          children: [new TextRun({ text: "Prepared for: Linksys Tech Support Leadership", size: 22, font: "Arial", color: GRAY })],
        }),
        new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: { before: 0, after: 40 },
          children: [new TextRun({ text: "Prepared by: Claude AI + Happy Link App Team", size: 22, font: "Arial", color: GRAY })],
        }),
        new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: { before: 0, after: 40 },
          children: [new TextRun({ text: "Date: June 2026", size: 22, font: "Arial", color: GRAY })],
        }),
        spacer(600),
        new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: { before: 0, after: 0 },
          border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: BORDER } },
          children: [new TextRun({ text: "CONFIDENTIAL  |  INTERNAL USE ONLY", size: 18, font: "Arial", color: GRAY, italics: true })],
        }),
        new Paragraph({ children: [new PageBreak()] }),
      ],
    },

    // ── Main Content ──────────────────────────────────────────────────────────
    {
      properties: {
        page: {
          size: { width: 12240, height: 15840 },
          margin: { top: 1200, right: 1200, bottom: 1200, left: 1200 },
        },
      },
      headers: {
        default: new Header({
          children: [
            new Paragraph({
              border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: BORDER } },
              spacing: { before: 0, after: 120 },
              children: [
                new TextRun({ text: "Happy Link Claude TLDR  |  Action Plan", size: 18, font: "Arial", color: GRAY }),
                new TextRun({ text: "   |   CONFIDENTIAL", size: 18, font: "Arial", color: RED, bold: true }),
              ],
            }),
          ],
        }),
      },
      footers: {
        default: new Footer({
          children: [
            new Paragraph({
              border: { top: { style: BorderStyle.SINGLE, size: 4, color: BORDER } },
              spacing: { before: 120, after: 0 },
              children: [
                new TextRun({ text: "Happy Link Claude TLDR  |  Linksys Tech Support  |  June 2026     Page ", size: 18, font: "Arial", color: GRAY }),
                new TextRun({ children: [PageNumber.CURRENT], size: 18, font: "Arial", color: GRAY }),
              ],
            }),
          ],
        }),
      },
      children: [
        // ── Overview ────────────────────────────────────────────────────────────
        heading1("Executive Overview"),
        infoBox(
          "The Happy Link Claude TLDR app successfully identifies operational failures in support tickets. " +
          "This Action Plan transforms it from a passive summary mirror into an active engine for behavioral " +
          "correction and performance engineering — the definitive QA standard for Linksys Tech Support.",
          BLUE_L, NAVY
        ),
        spacer(120),
        para(
          "This document outlines 5 structured action items across two pillars:",
          { bold: false }
        ),
        bullet("Technical & Architecture Upgrades — making the data work harder.", "PILLAR 1:"),
        bullet("Feature & Report Upgrades — making agents better at their jobs.", "PILLAR 2:"),
        spacer(80),
        para("Each action item includes: priority level, implementation effort, recommended timeline, step-by-step implementation guide, and the operational value delivered."),

        spacer(120),
        divider(),

        // ── Roadmap ─────────────────────────────────────────────────────────────
        heading1("Implementation Roadmap"),
        spacer(40),
        roadmapTable(),
        spacer(80),
        infoBox("Recommended sequence: Actions 01 and 02 should run in parallel (Weeks 1–5). Actions 03 and 04 follow immediately. Action 05 is the final integration milestone.", AMBER_L, AMBER),
        new Paragraph({ children: [new PageBreak()] }),

        // ── PILLAR 1 ─────────────────────────────────────────────────────────────
        heading1("PILLAR 1 — Technical & Architecture Upgrades"),
        para("These upgrades build the data infrastructure and AI intelligence layer that all future features depend on. Start here."),
        spacer(80),

        // Action 01
        actionCard(actions[0]),
        spacer(120),
        infoBox(
          "Operational Value: Leadership can answer in real time — 'Are premature ticket closures up 15% across Concentrix this week?' or 'Which team lead is approving internal meetings during active callback windows?'",
          GREEN_L, GREEN
        ),
        spacer(120),
        new Paragraph({ children: [new PageBreak()] }),

        // Action 02
        actionCard(actions[1]),
        spacer(120),
        infoBox(
          "Operational Value: Claude stops summarising what happened and starts flagging deviations from Linksys technical baselines with pinpoint accuracy — e.g. 'Agent advised 3-node mesh setup for 4,200 sq ft property. SOP maximum is 3 nodes at -65 dBm RSSI. Threshold exceeded.'",
          GREEN_L, GREEN
        ),
        new Paragraph({ children: [new PageBreak()] }),

        // ── PILLAR 2 ─────────────────────────────────────────────────────────────
        heading1("PILLAR 2 — Feature & Report Upgrades"),
        para("These upgrades transform the executive report from an administrative list into a direct coaching and accountability tool for Team Leads."),
        spacer(80),

        // Action 03
        actionCard(actions[2]),
        spacer(120),
        infoBox(
          "Operational Value: A Team Lead opens the report and immediately sees — without any additional manual calculation — exactly who caused each FCR violation and SLA breach and by how long. Direct coaching session material, ready to use.",
          GREEN_L, GREEN
        ),
        spacer(120),
        new Paragraph({ children: [new PageBreak()] }),

        // Action 04
        actionCard(actions[3]),
        spacer(120),
        infoBox(
          "Operational Value: The app stops just identifying mistakes and starts teaching agents how to fix them. Every QA finding is paired with the exact correct handling script — reducing repeat violations.",
          GREEN_L, GREEN
        ),
        spacer(120),
        new Paragraph({ children: [new PageBreak()] }),

        // Action 05
        actionCard(actions[4]),
        spacer(120),
        infoBox(
          "Operational Value: Bridges the gap between insight and action. A Severe Risk ticket that previously sat unnoticed for 24+ hours is now pushed to the Executive Advocacy Squad in seconds — bypassing standard tier queues entirely.",
          GREEN_L, GREEN
        ),
        spacer(120),
        new Paragraph({ children: [new PageBreak()] }),

        // ── Final Verdict ────────────────────────────────────────────────────────
        heading1("Summary & Final Verdict"),
        spacer(40),
        infoBox(
          "The Gemini review is correct: the app is highly effective at identifying operational flaws. " +
          "By implementing these 5 actions, the tool shifts from 'What went wrong?' to 'How do we " +
          "systematically prevent this from happening on the next call?' — and becomes the definitive " +
          "standard for Linksys Quality Assurance.",
          BLUE_L, NAVY, true
        ),
        spacer(120),
        heading2("What We Are Building Towards"),
        bullet("A living QA system that learns from every ticket analysed."),
        bullet("Automated agent scorecards that Team Leads can use directly in coaching sessions."),
        bullet("Claude-powered coaching scripts that teach agents the right behaviour — not just flag the wrong one."),
        bullet("An escalation engine that routes severe cases to leadership in seconds, not days."),
        bullet("A trend intelligence dashboard that gives Leadership real-time visibility into team performance."),
        spacer(120),
        divider(),
        new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: { before: 120, after: 0 },
          children: [new TextRun({ text: "Happy Link Claude TLDR  |  Action Plan  |  Confidential  |  June 2026", size: 18, font: "Arial", color: GRAY, italics: true })],
        }),
      ],
    },
  ],
});

Packer.toBuffer(doc).then(buffer => {
  const outPath = "C:/Users/Ashley-Admin/Desktop/Claude/Project/Happy Link Claude TLDR Ticket Summary/HappyLink_Action_Plan.docx";
  fs.writeFileSync(outPath, buffer);
  console.log("DONE: " + outPath);
}).catch(err => { console.error(err); process.exit(1); });

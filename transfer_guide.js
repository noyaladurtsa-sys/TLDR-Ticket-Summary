const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, HeadingLevel, BorderStyle, WidthType,
  ShadingType, VerticalAlign, PageNumber, PageBreak, LevelFormat
} = require("docx");
const fs = require("fs");

const NAVY  = "1B3A6B";
const AMBER = "F59E0B";
const GREEN = "16A34A";
const RED   = "DC2626";
const GRAY  = "6B7280";
const LIGHT = "F8FAFC";
const WHITE = "FFFFFF";
const BLUE_L  = "EEF3FB";
const GREEN_L = "F0FDF4";
const RED_L   = "FEF2F2";
const AMBER_L = "FFFBEA";
const BORDER  = "E5E7EB";

function b(color = BORDER) {
  return { style: BorderStyle.SINGLE, size: 1, color };
}
function allBorders(color = BORDER) {
  const bd = b(color);
  return { top: bd, bottom: bd, left: bd, right: bd };
}
function noBorders() {
  const nb = { style: BorderStyle.NONE, size: 0, color: WHITE };
  return { top: nb, bottom: nb, left: nb, right: nb };
}

function h1(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    spacing: { before: 300, after: 140 },
    children: [new TextRun({ text, bold: true, size: 30, color: NAVY, font: "Arial" })],
  });
}
function h2(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    spacing: { before: 220, after: 100 },
    children: [new TextRun({ text, bold: true, size: 24, color: NAVY, font: "Arial" })],
  });
}
function para(text, opts = {}) {
  return new Paragraph({
    spacing: { before: 60, after: 80 },
    children: [new TextRun({ text, size: 22, font: "Arial", color: "1a1a2e", ...opts })],
  });
}
function spacer(n = 80) {
  return new Paragraph({ spacing: { before: n, after: 0 }, children: [new TextRun("")] });
}
function divider() {
  return new Paragraph({
    spacing: { before: 100, after: 100 },
    border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: BORDER } },
    children: [new TextRun("")],
  });
}

// Numbered step paragraph
function step(num, title, detail) {
  return new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [720, 8640],
    rows: [new TableRow({
      children: [
        new TableCell({
          borders: noBorders(),
          shading: { fill: NAVY, type: ShadingType.CLEAR },
          margins: { top: 80, bottom: 80, left: 120, right: 120 },
          width: { size: 720, type: WidthType.DXA },
          verticalAlign: VerticalAlign.CENTER,
          children: [new Paragraph({
            alignment: AlignmentType.CENTER,
            spacing: { before: 0, after: 0 },
            children: [new TextRun({ text: String(num), bold: true, size: 22, font: "Arial", color: WHITE })],
          })],
        }),
        new TableCell({
          borders: { top: b(BORDER), bottom: b(BORDER), left: noBorders().left, right: b(BORDER) },
          shading: { fill: LIGHT, type: ShadingType.CLEAR },
          margins: { top: 80, bottom: 80, left: 160, right: 120 },
          width: { size: 8640, type: WidthType.DXA },
          children: [
            new Paragraph({
              spacing: { before: 0, after: detail ? 40 : 0 },
              children: [new TextRun({ text: title, bold: true, size: 22, font: "Arial", color: NAVY })],
            }),
            ...(detail ? [new Paragraph({
              spacing: { before: 0, after: 0 },
              children: [new TextRun({ text: detail, size: 20, font: "Arial", color: GRAY })],
            })] : []),
          ],
        }),
      ],
    })],
  });
}

// Code block
function codeBlock(text) {
  return new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [9360],
    rows: [new TableRow({
      children: [new TableCell({
        borders: allBorders("D1D5DB"),
        shading: { fill: "1E293B", type: ShadingType.CLEAR },
        margins: { top: 120, bottom: 120, left: 200, right: 200 },
        width: { size: 9360, type: WidthType.DXA },
        children: text.split("\n").map(line =>
          new Paragraph({
            spacing: { before: 0, after: 0 },
            children: [new TextRun({ text: line || " ", size: 18, font: "Courier New", color: "86EFAC" })],
          })
        ),
      })],
    })],
  });
}

// Info box
function infoBox(lines, bg = AMBER_L, borderColor = AMBER) {
  return new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [9360],
    rows: [new TableRow({
      children: [new TableCell({
        borders: {
          top: b(borderColor), bottom: b(borderColor),
          left: { style: BorderStyle.SINGLE, size: 20, color: borderColor },
          right: b(borderColor),
        },
        shading: { fill: bg, type: ShadingType.CLEAR },
        margins: { top: 100, bottom: 100, left: 180, right: 140 },
        width: { size: 9360, type: WidthType.DXA },
        children: lines.map((l, i) => new Paragraph({
          spacing: { before: i === 0 ? 0 : 40, after: 0 },
          children: [new TextRun({ text: l, size: 21, font: "Arial", color: "1a1a2e" })],
        })),
      })],
    })],
  });
}

// Checklist table
function checklist(items) {
  const headerRow = new TableRow({
    children: [
      new TableCell({
        shading: { fill: NAVY, type: ShadingType.CLEAR },
        borders: allBorders(NAVY),
        margins: { top: 80, bottom: 80, left: 120, right: 120 },
        width: { size: 600, type: WidthType.DXA },
        children: [new Paragraph({ spacing: { before: 0, after: 0 }, alignment: AlignmentType.CENTER, children: [new TextRun({ text: "#", bold: true, size: 18, font: "Arial", color: WHITE })] })],
      }),
      new TableCell({
        shading: { fill: NAVY, type: ShadingType.CLEAR },
        borders: allBorders(NAVY),
        margins: { top: 80, bottom: 80, left: 120, right: 120 },
        width: { size: 7560, type: WidthType.DXA },
        children: [new Paragraph({ spacing: { before: 0, after: 0 }, children: [new TextRun({ text: "Task", bold: true, size: 18, font: "Arial", color: WHITE })] })],
      }),
      new TableCell({
        shading: { fill: NAVY, type: ShadingType.CLEAR },
        borders: allBorders(NAVY),
        margins: { top: 80, bottom: 80, left: 120, right: 120 },
        width: { size: 1200, type: WidthType.DXA },
        children: [new Paragraph({ spacing: { before: 0, after: 0 }, alignment: AlignmentType.CENTER, children: [new TextRun({ text: "Done?", bold: true, size: 18, font: "Arial", color: WHITE })] })],
      }),
    ],
  });

  const rows = items.map((item, i) => new TableRow({
    children: [
      new TableCell({
        shading: { fill: i % 2 === 0 ? WHITE : LIGHT, type: ShadingType.CLEAR },
        borders: allBorders(BORDER),
        margins: { top: 80, bottom: 80, left: 120, right: 120 },
        width: { size: 600, type: WidthType.DXA },
        verticalAlign: VerticalAlign.CENTER,
        children: [new Paragraph({ spacing: { before: 0, after: 0 }, alignment: AlignmentType.CENTER, children: [new TextRun({ text: String(i + 1), size: 20, font: "Arial", color: NAVY, bold: true })] })],
      }),
      new TableCell({
        shading: { fill: i % 2 === 0 ? WHITE : LIGHT, type: ShadingType.CLEAR },
        borders: allBorders(BORDER),
        margins: { top: 80, bottom: 80, left: 120, right: 120 },
        width: { size: 7560, type: WidthType.DXA },
        children: [new Paragraph({ spacing: { before: 0, after: 0 }, children: [new TextRun({ text: item, size: 20, font: "Arial", color: "1a1a2e" })] })],
      }),
      new TableCell({
        shading: { fill: i % 2 === 0 ? WHITE : LIGHT, type: ShadingType.CLEAR },
        borders: allBorders(BORDER),
        margins: { top: 80, bottom: 80, left: 120, right: 120 },
        width: { size: 1200, type: WidthType.DXA },
        verticalAlign: VerticalAlign.CENTER,
        children: [new Paragraph({ spacing: { before: 0, after: 0 }, alignment: AlignmentType.CENTER, children: [new TextRun({ text: "[ ]", size: 20, font: "Arial", color: GRAY })] })],
      }),
    ],
  }));

  return new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [600, 7560, 1200],
    rows: [headerRow, ...rows],
  });
}

// ── Document ──────────────────────────────────────────────────────────────────
const doc = new Document({
  numbering: {
    config: [
      {
        reference: "bullets",
        levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 540, hanging: 360 } } } }],
      },
    ],
  },
  styles: {
    default: { document: { run: { font: "Arial", size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 30, bold: true, font: "Arial", color: NAVY },
        paragraph: { spacing: { before: 300, after: 140 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 24, bold: true, font: "Arial", color: NAVY },
        paragraph: { spacing: { before: 220, after: 100 }, outlineLevel: 1 } },
    ],
  },
  sections: [
    // ── Cover Page ──────────────────────────────────────────────────────────
    {
      properties: { page: { size: { width: 12240, height: 15840 }, margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 } } },
      children: [
        spacer(1600),
        new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 0, after: 80 }, children: [new TextRun({ text: "🔗", size: 80, font: "Arial" })] }),
        new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 60, after: 60 }, children: [new TextRun({ text: "Happy Link Claude TLDR", size: 52, bold: true, font: "Arial", color: NAVY })] }),
        new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 0, after: 200 }, children: [new TextRun({ text: "Project Transfer Guide", size: 36, font: "Arial", color: GRAY })] }),
        divider(),
        spacer(120),
        new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 0, after: 60 }, children: [new TextRun({ text: "Step-by-Step Instructions for Moving the Project", size: 24, bold: true, font: "Arial", color: NAVY })] }),
        new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 0, after: 60 }, children: [new TextRun({ text: "to a New Laptop After Factory Reset", size: 24, font: "Arial", color: NAVY })] }),
        spacer(200),
        new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 0, after: 40 }, children: [new TextRun({ text: "Project: Happy Link Claude TLDR Ticket Summary", size: 22, font: "Arial", color: GRAY })] }),
        new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 0, after: 40 }, children: [new TextRun({ text: "Prepared by: Claude AI", size: 22, font: "Arial", color: GRAY })] }),
        new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 0, after: 40 }, children: [new TextRun({ text: "Date: June 2026", size: 22, font: "Arial", color: GRAY })] }),
        spacer(500),
        new Paragraph({ alignment: AlignmentType.CENTER, border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: BORDER } }, spacing: { before: 0, after: 0 }, children: [new TextRun({ text: "CONFIDENTIAL  |  INTERNAL USE ONLY", size: 18, font: "Arial", color: GRAY, italics: true })] }),
        new Paragraph({ children: [new PageBreak()] }),
      ],
    },

    // ── Main Content ────────────────────────────────────────────────────────
    {
      properties: { page: { size: { width: 12240, height: 15840 }, margin: { top: 1200, right: 1200, bottom: 1200, left: 1200 } } },
      headers: {
        default: new Header({
          children: [new Paragraph({
            border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: BORDER } },
            spacing: { before: 0, after: 120 },
            children: [
              new TextRun({ text: "Happy Link Claude TLDR  |  Project Transfer Guide", size: 18, font: "Arial", color: GRAY }),
              new TextRun({ text: "   |   CONFIDENTIAL", size: 18, font: "Arial", color: RED, bold: true }),
            ],
          })],
        }),
      },
      footers: {
        default: new Footer({
          children: [new Paragraph({
            border: { top: { style: BorderStyle.SINGLE, size: 4, color: BORDER } },
            spacing: { before: 120, after: 0 },
            children: [
              new TextRun({ text: "Happy Link Claude TLDR  |  Transfer Guide  |  June 2026     Page ", size: 18, font: "Arial", color: GRAY }),
              new TextRun({ children: [PageNumber.CURRENT], size: 18, font: "Arial", color: GRAY }),
            ],
          })],
        }),
      },
      children: [

        // ── Overview ──────────────────────────────────────────────────────────
        h1("Overview"),
        infoBox([
          "This guide walks you through everything needed to back up the Happy Link Claude TLDR project",
          "from your current laptop BEFORE the factory reset, and set it up correctly on the new laptop.",
          "Follow each step in order. Do not skip the backup steps — they protect your API keys and cached reports.",
        ], BLUE_L, NAVY),
        spacer(100),

        // ── Part 1 ────────────────────────────────────────────────────────────
        h1("PART 1 — On This Laptop (Do BEFORE the Reset)"),
        para("Complete all steps in Part 1 before you reset the laptop. Once reset, this data is gone."),
        spacer(80),

        step(1, "Back Up the Project Folder", "Copy the entire folder below to a USB drive or OneDrive:"),
        spacer(60),
        codeBlock("C:\\Users\\Ashley-Admin\\Desktop\\Claude\\Project\\Happy Link Claude TLDR Ticket Summary"),
        spacer(60),
        para("Make sure these files are included inside the folder:"),
        new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { before: 40, after: 20 }, children: [new TextRun({ text: "app.py — the main application", size: 22, font: "Arial" })] }),
        new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { before: 20, after: 20 }, children: [new TextRun({ text: "router_nobg.png — the logo image", size: 22, font: "Arial" })] }),
        new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { before: 20, after: 20 }, children: [new TextRun({ text: "action_plan.js — Word doc generator", size: 22, font: "Arial" })] }),
        new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { before: 20, after: 40 }, children: [new TextRun({ text: ".streamlit\\secrets.toml — API keys (MOST CRITICAL)", size: 22, font: "Arial", bold: true, color: RED })] }),
        spacer(80),

        step(2, "Save Your API Keys — CRITICAL", "Open .streamlit\\secrets.toml and copy it somewhere safe (email to yourself, USB, OneDrive)."),
        spacer(60),
        infoBox([
          "WARNING: Without secrets.toml you will lose all your API keys and will need to look them up manually.",
          "This file contains: HAPPYFOX_API_KEY, ANTHROPIC_API_KEY, HAPPYFOX_AUTH_CODE, and database credentials.",
        ], RED_L, RED),
        spacer(80),

        step(3, "Back Up the Database (Optional but Recommended)", "This saves all your cached ticket reports so you won't spend API credits re-analysing them."),
        spacer(60),
        para("Open PowerShell and run:"),
        codeBlock("pg_dump -U postgres -d metaboard -f \"C:\\Users\\Ashley-Admin\\Desktop\\metaboard_backup.sql\""),
        spacer(60),
        para("Then copy metaboard_backup.sql to your USB drive or OneDrive."),
        spacer(100),
        infoBox([
          "Once all 3 steps above are done, your data is safe. You can proceed with the factory reset.",
        ], GREEN_L, GREEN),

        new Paragraph({ children: [new PageBreak()] }),

        // ── Part 2 ────────────────────────────────────────────────────────────
        h1("PART 2 — On the New Laptop (After Reset)"),
        para("Install everything fresh on the new laptop in the order below."),
        spacer(80),

        step(4, "Install Python", "Download from python.org and install."),
        spacer(40),
        infoBox(["IMPORTANT: During installation, tick the checkbox that says 'Add Python to PATH'"], AMBER_L, AMBER),
        spacer(80),

        step(5, "Install Node.js", "Download from nodejs.org and install. This is needed for the Word document generator."),
        spacer(80),

        step(6, "Install PostgreSQL", "Download from postgresql.org and install. Set a password you will remember."),
        spacer(80),

        step(7, "Copy the Project Folder", "Paste the project folder from your USB or OneDrive onto the new laptop Desktop."),
        spacer(80),

        step(8, "Install Python Packages", "Open PowerShell, navigate to the project folder, and run:"),
        spacer(60),
        codeBlock("cd \"C:\\Users\\YourName\\Desktop\\Happy Link Claude TLDR Ticket Summary\"\npip install streamlit anthropic requests psycopg2-binary pillow plotly"),
        spacer(80),

        step(9, "Install Node Package", "Run this in PowerShell inside the project folder:"),
        spacer(60),
        codeBlock("npm install docx"),
        spacer(80),

        step(10, "Restore the Database (if you did Step 3)", "Run these two commands in PowerShell:"),
        spacer(60),
        codeBlock("psql -U postgres -c \"CREATE DATABASE metaboard;\"\npsql -U postgres -d metaboard -f \"C:\\path\\to\\metaboard_backup.sql\""),
        spacer(80),

        step(11, "Install Claude Code CLI", "Run in PowerShell:"),
        spacer(60),
        codeBlock("npm install -g @anthropic-ai/claude-code"),
        spacer(40),
        para("Then fix the execution policy (allows scripts to run):"),
        codeBlock("Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser"),
        spacer(40),
        para("Type Y and press Enter when prompted."),
        spacer(80),

        step(12, "Log in to Claude Code", "Run:"),
        spacer(60),
        codeBlock("claude"),
        spacer(40),
        para("A browser window will open. Log in with your Anthropic account."),
        spacer(80),

        step(13, "Run the Streamlit App", "Navigate to the project and start the app:"),
        spacer(60),
        codeBlock("cd \"C:\\Users\\YourName\\Desktop\\Happy Link Claude TLDR Ticket Summary\"\nstreamlit run app.py"),
        spacer(40),
        para("Then open your browser and go to: http://localhost:8501"),
        spacer(100),

        new Paragraph({ children: [new PageBreak()] }),

        // ── Checklist ─────────────────────────────────────────────────────────
        h1("Quick Checklist"),
        para("Use this to track your progress:"),
        spacer(60),
        checklist([
          "Project folder copied to USB or OneDrive",
          "secrets.toml backed up (email or USB)",
          "Database exported to metaboard_backup.sql (optional)",
          "Python installed on new laptop (with Add to PATH ticked)",
          "Node.js installed on new laptop",
          "PostgreSQL installed on new laptop",
          "Project folder copied to new laptop",
          "pip packages installed (streamlit, anthropic, etc.)",
          "npm install docx completed",
          "Database restored from backup (optional)",
          "Claude Code CLI installed",
          "Execution policy set to RemoteSigned",
          "Logged in to Claude Code",
          "Streamlit app running at localhost:8501",
        ]),
        spacer(120),

        // ── Tips ──────────────────────────────────────────────────────────────
        h1("Important Tips"),
        spacer(40),
        infoBox([
          "SAVE CREDITS: Only click 'Re-analyse' when absolutely necessary.",
          "Cached tickets (already in the database) load for FREE.",
          "Each new ticket analysis costs approx. $0.05 - $0.15.",
        ], AMBER_L, AMBER),
        spacer(80),
        infoBox([
          "SET A SPENDING LIMIT: Go to console.anthropic.com > Settings > Billing > Usage Limits.",
          "Set a monthly limit of $10 to prevent unexpected charges.",
        ], RED_L, RED),
        spacer(80),
        infoBox([
          "USE CHEAPER MODEL: Run this once in PowerShell to reduce Claude Code costs:",
          "claude config set model claude-sonnet-4-5",
        ], GREEN_L, GREEN),
        spacer(120),
        divider(),
        new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: { before: 120, after: 0 },
          children: [new TextRun({ text: "Happy Link Claude TLDR  |  Transfer Guide  |  Confidential  |  June 2026", size: 18, font: "Arial", color: GRAY, italics: true })],
        }),
      ],
    },
  ],
});

Packer.toBuffer(doc).then(buffer => {
  const out = "C:/Users/Ashley-Admin/Desktop/Claude/Project/Happy Link Claude TLDR Ticket Summary/HappyLink_Transfer_Guide.docx";
  fs.writeFileSync(out, buffer);
  console.log("DONE: " + out);
}).catch(err => { console.error(err); process.exit(1); });

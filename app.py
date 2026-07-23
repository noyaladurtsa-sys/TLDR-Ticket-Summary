import os
import time
import base64
import streamlit as st
import streamlit.components.v1 as components
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import anthropic
import json
import html
import logging
from datetime import date, datetime as dt
from PIL import Image as PILImage
from deep_translator import GoogleTranslator

logger = logging.getLogger(__name__)

_translator = GoogleTranslator(source='auto', target='en')

def _auto_translate(text: str) -> str:
    if not text or len(text.strip()) < 2:
        return text
    non_ascii = sum(1 for c in text if ord(c) > 127)
    if non_ascii / max(len(text), 1) < 0.3:
        return text
    try:
        translated = _translator.translate(text)
        return translated if translated else text
    except Exception:
        return text
try:
    import psycopg2
    import psycopg2.extras
    _PG_AVAILABLE = True
except ImportError:
    _PG_AVAILABLE = False

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Happy Link Claude TLDR Ticket Summary",
    page_icon="🔗",
    layout="wide",
)

# ── Executive styling ─────────────────────────────────────────────────────────
st.markdown("""
<style>
body, .stMarkdown, .stText, p, li, td, th, input, label, button, caption, small {
  font-family: 'Segoe UI', sans-serif !important;
}
[data-testid="stAppViewContainer"] { background-color: #f4f6f9; }
.block-container { max-width: 1100px !important; padding-left: 2rem !important; padding-right: 2rem !important; }
[data-testid="stHeader"] { background-color: #1B3A6B; }
h1  { color: #1B3A6B !important; font-size: 1.6rem !important; letter-spacing: -0.5px; }
h3  { color: #1B3A6B !important; border-bottom: 2px solid #1B3A6B; padding-bottom: 4px; margin-top: 1rem; }
[data-testid="stMetricValue"]  { color: #1B3A6B !important; font-weight: 700; }
[data-testid="stMetricLabel"]  { color: #6b7280 !important; font-size: 0.72rem !important; text-transform: uppercase; letter-spacing: .05em; }
[data-testid="metric-container"] { background: white; border-radius: 8px; padding: 10px 14px; box-shadow: 0 1px 4px rgba(0,0,0,.08); }
.stExpander { background: white; border-radius: 8px; border: 1px solid #e5e7eb !important; margin-bottom: 8px; }
div[data-testid="stExpander"] summary { font-weight: 600; color: #1B3A6B; }
div[data-testid="stExpander"] > div:last-child { padding-top: 4px !important; }
div[data-testid="stExpander"] p { margin: 5px 0 !important; font-size: 13px !important; }
/* Fix bullet list spacing — no overlap */
[data-testid="stMarkdownContainer"] ul { margin: 4px 0 !important; padding-left: 18px !important; }
[data-testid="stMarkdownContainer"] ul li { margin: 3px 0 !important; line-height: 1.5 !important; font-size: 13px !important; }
/* Standardise font size */
.stInfo p, [data-testid="stMarkdownContainer"] p { font-size: 13px !important; }
.stInfo    { background: #eef3fb !important; border-left: 4px solid #1B3A6B !important; }
.stDownloadButton button { background: #1B3A6B !important; color: white !important; border-radius: 6px; font-weight: 600; }
/* Remove extra spacing between bullet points */
[data-testid="stMarkdownContainer"] ul { margin: 0; padding-left: 18px; }
[data-testid="stMarkdownContainer"] ul li { margin: 1px 0; padding: 0; }
</style>
""", unsafe_allow_html=True)

# ── Credentials ───────────────────────────────────────────────────────────────
try:
    API_KEY       = st.secrets["HAPPYFOX_API_KEY"]
    AUTH_CODE     = st.secrets["HAPPYFOX_AUTH_CODE"]
    ANTHROPIC_KEY = st.secrets["ANTHROPIC_API_KEY"]
    BASE_URL      = st.secrets["HAPPYFOX_BASE_URL"].rstrip("/") + "/api/1.1/json"
    PORTAL_BASE   = st.secrets["HAPPYFOX_BASE_URL"].rstrip("/")
except KeyError as _missing:
    st.error(
        f"Missing required secret {_missing}. "
        "Add it to `.streamlit/secrets.toml` (HAPPYFOX_API_KEY, HAPPYFOX_AUTH_CODE, "
        "ANTHROPIC_API_KEY, HAPPYFOX_BASE_URL) and reload."
    )
    st.stop()

DB_HOST = st.secrets.get("DB_HOST", "localhost")
DB_PORT = st.secrets.get("DB_PORT", "5432")
DB_NAME = st.secrets.get("DB_NAME", "metaboard")
DB_USER = st.secrets.get("DB_USER", "postgres")
DB_PASS = st.secrets.get("DB_PASS", "")


# ── PostgreSQL helpers ────────────────────────────────────────────────────────
def _pg_conn():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASS, connect_timeout=5,
    )

def init_db():
    with _pg_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS executive_reports (
                id              SERIAL PRIMARY KEY,
                ticket_id       TEXT NOT NULL,
                subject         TEXT,
                status          TEXT,
                category        TEXT,
                assignee        TEXT,
                problem_summary TEXT,
                html_report     TEXT,
                input_tokens    INTEGER DEFAULT 0,
                output_tokens   INTEGER DEFAULT 0,
                saved_at        TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        # Add columns if table already exists without them
        for col in ("input_tokens INTEGER DEFAULT 0", "output_tokens INTEGER DEFAULT 0", "num_updates INTEGER DEFAULT 0", "ai_json TEXT"):
            try:
                cur.execute(f"ALTER TABLE executive_reports ADD COLUMN IF NOT EXISTS {col};")
            except Exception:
                pass
        conn.commit()

def save_report_to_db(ticket: dict, ai: dict, html_report: str,
                      input_tokens: int = 0, output_tokens: int = 0, num_updates: int = 0):
    category = ticket.get("category", {}).get("name", "") if ticket.get("category") else ""
    assignee = (ticket.get("assigned_to") or ticket.get("agent") or {}).get("name", "")
    with _pg_conn() as conn, conn.cursor() as cur:
        # Keep one current report per ticket — drop any prior copies before inserting.
        cur.execute("DELETE FROM executive_reports WHERE ticket_id = %s", (str(ticket["id"]),))
        cur.execute("""
            INSERT INTO executive_reports
                (ticket_id, subject, status, category, assignee,
                 problem_summary, html_report, input_tokens, output_tokens, num_updates, ai_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            str(ticket["id"]),
            ticket.get("subject", ""),
            ticket.get("status", {}).get("name", ""),
            category,
            assignee,
            ai.get("problem_summary", ""),
            html_report,
            input_tokens,
            output_tokens,
            num_updates,
            json.dumps(ai),
        ))
        conn.commit()

def find_report_in_db(ticket_id: str) -> dict | None:
    """Return the most recent saved report for a ticket, or None."""
    try:
        with _pg_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, ticket_id, subject, status, category, assignee,
                       problem_summary, html_report, saved_at,
                       COALESCE(num_updates, 0) AS num_updates, ai_json
                FROM executive_reports
                WHERE ticket_id = %s
                ORDER BY saved_at DESC
                LIMIT 1
            """, (str(ticket_id),))
            return cur.fetchone()
    except Exception as exc:
        logger.warning("DB lookup failed for ticket %s: %s", ticket_id, exc)
        return None


# ── Resilient HTTP session (auto-retries on connection drops) ─────────────────
def _make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=4,
        backoff_factor=1,          # waits 1s, 2s, 4s between retries
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://",  adapter)
    session.headers.update({"User-Agent": "HappyLinkTLDR/1.0"})
    return session

_SESSION = _make_session()


# ── Helpers ───────────────────────────────────────────────────────────────────
def v(val):
    return val if val and str(val).lower() not in ("null", "none", "") else "—"


def get_custom_field(ticket: dict, *labels) -> str:
    """Search ticket custom_fields by label name (case-insensitive)."""
    for field in ticket.get("custom_fields", []) or []:
        name = str(field.get("name", "")).lower()
        val  = field.get("value") or field.get("display_value") or ""
        for label in labels:
            if label.lower() in name and val:
                return str(val)
    return ""


# ── Shared persona / chart / update helpers ───────────────────────────────────
PERSONA_COLORS = {"Customer": "#0891b2", "Support Agent": "#d97706",
                  "CAT Team": "#7c3aed", "System/Survey": "#6b7280"}
PERSONA_BG     = {"Customer": "#e0f2fe", "Support Agent": "#fffbeb",
                  "CAT Team": "#ede9fe", "System/Survey": "#f3f4f6"}
PERSONA_ICON   = {"Customer": "👤", "Support Agent": "🛠️",
                  "CAT Team": "🔺", "System/Survey": "📝"}
PERSONA_LANE_Y = {"Customer": 3, "Support Agent": 2, "CAT Team": 1, "System/Survey": 0}
CRITICAL_KW = ["escalat", "complaint", "replacement", "refund", "callback", "threat",
               "demand", "unresolved", "refused", "urgent", "legal",
               "consumer association", "mail-in", "no resolution", "csat"]


def persona_group(author: str) -> str:
    """Bucket an author/agent name into one of the four interaction personas."""
    a = (author or "").lower()
    if "customer" in a:
        return "Customer"
    if "cat" in a:
        return "CAT Team"
    if any(x in a for x in ("survey", "csat", "system")):
        return "System/Survey"
    return "Support Agent"


def persona_style(author: str):
    """Return (color, background, icon) for an author."""
    g = persona_group(author)
    return PERSONA_COLORS[g], PERSONA_BG[g], PERSONA_ICON[g]


def _hex_rgba(hex_col: str, alpha: float = 0.4) -> str:
    h = hex_col.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def iter_update_actions(ticket: dict):
    """Yield (display_name, date, description) for each meaningful ticket update.

    Shared by the on-screen 'What Happened?' list and the HTML report actions block
    so the two surfaces stay in sync.
    """
    import re as _re
    for u in ticket.get("updates", []):
        if not isinstance(u, dict):
            continue  # skip integer placeholders from a cached fake ticket
        by        = u.get("by") or {}
        agent_obj = u.get("agent") or {}
        btype     = by.get("type") or agent_obj.get("type") or ""
        raw_name  = by.get("name") or agent_obj.get("name") or ""
        timestamp = (u.get("timestamp") or u.get("created_at") or "")[:10]

        if btype == "smartrule":
            display = f"System — {raw_name}"
        elif not raw_name:
            display = "System"
        else:
            display = raw_name

        parts = []
        msg = u.get("message") or {}
        msg_text = ""
        if isinstance(msg, dict):
            msg_text = (msg.get("text") or msg.get("html") or "").strip()
        if not msg_text:
            msg_text = (u.get("text") or u.get("html") or u.get("body") or "").strip()
        boilerplate = "Linksys Technical Support Case created."
        if msg_text and not msg_text.startswith(boilerplate):
            first_line = next((l.strip() for l in msg_text.splitlines() if l.strip()), "")
            parts.append(first_line[:160] + ("…" if len(first_line) > 160 else ""))

        sc = u.get("status_change")
        if sc and sc.get("new_name"):
            parts.append(f"Status: {sc.get('old_name') or '—'} → {sc['new_name']}")
        ac = u.get("assignee_change")
        if ac and ac.get("new_name"):
            parts.append(f"Assigned to: {ac['new_name']}")
        cc = u.get("category_change")
        if cc and cc.get("new_name"):
            parts.append(f"Category → {cc['new_name']}")
        sat = u.get("satisfaction_survey")
        if sat and isinstance(sat, dict) and sat.get("rating") is not None:
            parts.append(f"CSAT survey: {sat['rating']}/5")
        if msg_text and "CSAT:" in msg_text:
            m = _re.search(r"CSAT[:\s]+(\d)", msg_text)
            if m:
                parts.append(f"CSAT survey received: {m.group(1)}/5")

        if not parts:
            continue
        yield display, timestamp, " · ".join(parts)


def _clean_agent_name(raw: str) -> str:
    """Strip @concentrix.com / @linksys.com emails to readable names."""
    import re as _re
    raw = raw.strip()
    if "@" in raw and ("concentrix.com" in raw.lower() or "linksys.com" in raw.lower()):
        local = raw.split("@")[0]
        parts = _re.split(r'[._]', local)
        return " ".join(p.capitalize() for p in parts if p)
    return raw


def _classify_action_actor(agent: str) -> str:
    """Map an actions_log agent name to actor type: customer / system / agent."""
    a = (agent or "").strip().lower()
    if not a:
        return "system"
    if a in ("customer", "client", "user", "end user"):
        return "customer"
    if a in ("system", "auto", "smart rule", "automation") or "system" in a:
        return "system"
    return "agent"


def _detect_action_badges(action: str) -> list[tuple[str, str]]:
    """Detect key-event badges from an action's text. Returns (style, label) pairs."""
    t = (action or "").lower()
    badges = []
    if "reopen" in t:
        badges.append(("background:#fffbea;color:#854f0b;", "Reopened"))
    if "escalat" in t:
        badges.append(("background:#fff7ed;color:#9a3412;", "Escalated"))
    if "callback" in t or "call back" in t or "called back" in t:
        badges.append(("background:#eef3fb;color:#1B3A6B;", "Callback"))
    if any(k in t for k in ("closed", "resolved", "status changed", "pending", "marked resolved")):
        badges.append(("background:#f1f5f9;color:#5f5e5a;", "Status"))
    if any(k in t for k in ("legal", "threatened", "complaint", "escalation to management")):
        badges.append(("background:#fef2f2;color:#991b1b;", "Risk"))
    return badges


def _build_actions_timeline_html(actions_log: list, esc_fn=None) -> str:
    """Render actions_log grouped by date, with actor dots and event badges.
    Shared by the Streamlit view and the exported HTML report."""
    import html as _html
    esc = esc_fn or _html.escape
    if not actions_log:
        return "<p style='font-size:12px;color:#6b7280;'>No action data available.</p>"

    _dot_c = {"agent": "#378ADD", "customer": "#1D9E75", "system": "#B4B2A9"}
    _nm_c = {"agent": "#185FA5", "customer": "#0F6E56", "system": "#9ca3af"}

    # Group consecutive entries by date (preserve order)
    groups = []
    for _al in actions_log:
        if not isinstance(_al, dict):
            continue
        date = str(_al.get("date", "")).strip()
        if not groups or groups[-1][0] != date:
            groups.append((date, []))
        groups[-1][1].append(_al)

    parts = []
    # Legend
    parts.append(
        "<div style='display:flex;gap:14px;margin-bottom:12px;flex-wrap:wrap;"
        "font-size:11px;color:#6b7280;align-items:center;'>"
        "<span style='display:flex;align-items:center;gap:5px;'>"
        "<span style='width:9px;height:9px;border-radius:50%;background:#1D9E75;display:inline-block;'></span>Customer</span>"
        "<span style='display:flex;align-items:center;gap:5px;'>"
        "<span style='width:9px;height:9px;border-radius:50%;background:#378ADD;display:inline-block;'></span>Agent</span>"
        "<span style='display:flex;align-items:center;gap:5px;'>"
        "<span style='width:9px;height:9px;border-radius:50%;background:#B4B2A9;display:inline-block;'></span>System</span>"
        "</div>"
    )

    for date, entries in groups:
        parts.append(
            "<div style='display:flex;align-items:center;gap:8px;margin:10px 0 4px;'>"
            f"<span style='font-size:11px;font-weight:600;color:#185FA5;background:#E6F1FB;"
            f"padding:2px 9px;border-radius:10px;'>{esc(date) if date else '—'}</span>"
            "<span style='flex:1;height:1px;background:#e5e7eb;'></span>"
            "</div>"
        )
        for _al in entries:
            agent = _clean_agent_name(str(_al.get("agent", "")))
            action = str(_al.get("action", ""))
            actor = _classify_action_actor(_al.get("agent", ""))
            dc = _dot_c.get(actor, "#B4B2A9")
            nc = _nm_c.get(actor, "#6b7280")
            ns = "font-style:italic;" if actor == "system" else ""
            badges_h = ""
            for style, label in _detect_action_badges(action):
                badges_h += (
                    f" <span style='{style}font-size:10px;font-weight:700;"
                    f"padding:1px 6px;border-radius:3px;'>{esc(label)}</span>"
                )
            parts.append(
                "<div style='display:flex;gap:9px;padding:5px 0;border-bottom:1px solid #f1f5f9;"
                "align-items:flex-start;font-size:12px;'>"
                f"<span style='display:inline-block;width:8px;height:8px;border-radius:50%;"
                f"background:{dc};margin-top:4px;flex-shrink:0;'></span>"
                f"<span style='font-weight:600;min-width:100px;flex-shrink:0;color:{nc};{ns}'>{esc(agent)}</span>"
                f"<span style='flex:1;color:#374151;line-height:1.5;'>{esc(action)}{badges_h}</span>"
                "</div>"
            )
    return "".join(parts)


def _detect_update_flags(u: dict, prev_status: str | None) -> list[tuple[str, str]]:
    """Return list of (flag_type, label) for a single update."""
    flags = []
    sc = u.get("status_change")
    if sc:
        new = (sc.get("new_name") or "").lower()
        old = (sc.get("old_name") or "").lower()
        if new in ("resolved", "closed") and old not in ("resolved", "closed"):
            flags.append(("close", "Premature close"))
        if new in ("escalated",):
            flags.append(("escalation", "Escalated"))
        if new in ("updated",) and old in ("resolved", "closed"):
            flags.append(("reopen", "Reopened"))
    ac = u.get("assignee_change")
    if ac and "escalat" in (ac.get("new_name") or "").lower():
        flags.append(("escalation", "Re-escalated"))
    msg = u.get("message") or {}
    msg_text = ""
    if isinstance(msg, dict):
        msg_text = (msg.get("text") or msg.get("html") or "").strip()
    if not msg_text:
        msg_text = (u.get("text") or u.get("html") or u.get("body") or "").strip()
    msg_lower = msg_text.lower()
    frustration_keywords = ["legal", "attorney", "lawyer", "unacceptable", "scrapped", "disgusted",
                            "hours late", "no results", "worst", "never again"]
    if any(kw in msg_lower for kw in frustration_keywords):
        by = u.get("by") or {}
        if by.get("type") == "user" or (not by.get("type") and "customer" in (by.get("name") or "").lower()):
            flags.append(("frustration", "Frustrated"))
    callback_keywords = ["call you back", "callback", "call back", "follow up", "follow-up",
                         "reach out", "contact you within"]
    if any(kw in msg_lower for kw in callback_keywords):
        by = u.get("by") or {}
        if by.get("type") != "user":
            flags.append(("callback_promise", "Callback promised"))
    return flags


def build_session_groups(ticket: dict) -> list[dict]:
    """Group ticket updates into logical sessions for the redesigned What Happened view.

    Returns list of session dicts:
      {"num": 1, "title": "...", "date_range": "...", "entries": [...], "summary_counts": {...}}
    Each entry: {"name": "...", "actor": "agent|customer|system", "action": "...",
                 "date": "...", "flags": [(type, label), ...]}
    """
    import re as _re
    updates = ticket.get("updates", [])
    customer_name = (ticket.get("user") or {}).get("name", "")
    boilerplate = "Linksys Technical Support Case created."

    entries = []
    for u in updates:
        if not isinstance(u, dict):
            continue
        by = u.get("by") or {}
        agent_obj = u.get("agent") or {}
        btype = by.get("type") or agent_obj.get("type") or ""
        raw_name = by.get("name") or agent_obj.get("name") or ""
        timestamp = (u.get("timestamp") or u.get("created_at") or "")[:10]

        if btype == "smartrule":
            continue

        if not raw_name:
            actor = "system"
            clean_name = "System"
        elif btype == "user" or raw_name == customer_name:
            actor = "customer"
            clean_name = _clean_agent_name(raw_name) if "@" in raw_name else raw_name
        else:
            actor = "agent"
            clean_name = _clean_agent_name(raw_name)

        parts = []
        msg = u.get("message") or {}
        msg_text = ""
        if isinstance(msg, dict):
            msg_text = (msg.get("text") or msg.get("html") or "").strip()
        if not msg_text:
            msg_text = (u.get("text") or u.get("html") or u.get("body") or "").strip()
        if msg_text and not msg_text.startswith(boilerplate):
            first_line = next((l.strip() for l in msg_text.splitlines() if l.strip()), "")
            truncated = first_line[:160] + ("…" if len(first_line) > 160 else "")
            parts.append(_auto_translate(truncated))

        sc = u.get("status_change")
        if sc and sc.get("new_name"):
            parts.append(f"Status: {sc.get('old_name') or '—'} → {sc['new_name']}")
        ac = u.get("assignee_change")
        if ac and ac.get("new_name"):
            parts.append(f"Assigned to: {_clean_agent_name(ac['new_name'])}")
        cc = u.get("category_change")
        if cc and cc.get("new_name"):
            parts.append(f"Category → {cc['new_name']}")

        if not parts:
            continue

        flags = _detect_update_flags(u, None)
        entries.append({
            "name": clean_name,
            "actor": actor,
            "action": " · ".join(parts),
            "date": timestamp,
            "flags": flags,
            "raw_status_change": sc,
        })

    if not entries:
        return []

    sessions = []
    current_entries = [entries[0]]
    session_start = entries[0]["date"]

    for i in range(1, len(entries)):
        e = entries[i]
        prev = entries[i - 1]
        new_session = False
        if e["date"] != prev["date"]:
            from datetime import datetime
            try:
                d1 = datetime.strptime(prev["date"], "%Y-%m-%d")
                d2 = datetime.strptime(e["date"], "%Y-%m-%d")
                if (d2 - d1).days >= 2:
                    new_session = True
            except ValueError:
                pass
        has_reopen = any(f[0] == "reopen" for f in e.get("flags", []))
        has_escalation = any(f[0] == "escalation" for f in e.get("flags", []))
        if has_reopen or (has_escalation and e["date"] != prev["date"]):
            new_session = True

        if new_session:
            sessions.append({
                "entries": current_entries,
                "start_date": session_start,
                "end_date": prev["date"],
            })
            current_entries = [e]
            session_start = e["date"]
        else:
            current_entries.append(e)

    sessions.append({
        "entries": current_entries,
        "start_date": session_start,
        "end_date": entries[-1]["date"],
    })

    def _session_title(sess, idx):
        flags_all = [f for e in sess["entries"] for f in e.get("flags", [])]
        flag_types = {f[0] for f in flags_all}
        actors = {e["actor"] for e in sess["entries"]}
        has_customer = "customer" in actors

        if idx == 0:
            return "Initial contact"
        if "reopen" in flag_types:
            return "Customer reopened — issue persists"
        if "escalation" in flag_types and "frustration" in flag_types:
            return "Customer demands escalation"
        if "escalation" in flag_types:
            return "Escalation to L2"
        if "frustration" in flag_types:
            return "Customer frustrated — risk of churn"
        if has_customer and any("close" == f[0] for f in flags_all):
            return "Follow-up attempts"
        if len(sess["entries"]) <= 2 and not has_customer:
            return "Internal review"
        return "Follow-up & resolution"

    def _format_date_range(start, end):
        from datetime import datetime
        try:
            d1 = datetime.strptime(start, "%Y-%m-%d")
            d2 = datetime.strptime(end, "%Y-%m-%d")
            if start == end:
                return d1.strftime("%b %d")
            if d1.month == d2.month:
                return f"{d1.strftime('%b %d')}–{d2.strftime('%d')}"
            return f"{d1.strftime('%b %d')} – {d2.strftime('%b %d')}"
        except ValueError:
            return start if start == end else f"{start} – {end}"

    result = []
    for idx, sess in enumerate(sessions):
        close_count = sum(1 for e in sess["entries"] for f in e.get("flags", []) if f[0] == "close")
        breach_count = sum(1 for e in sess["entries"] for f in e.get("flags", []) if f[0] == "callback_promise")
        esc_count = sum(1 for e in sess["entries"] for f in e.get("flags", []) if f[0] == "escalation")
        result.append({
            "num": idx + 1,
            "title": _session_title(sess, idx),
            "date_range": _format_date_range(sess["start_date"], sess["end_date"]),
            "entries": sess["entries"],
            "summary": {"closes": close_count, "breaches": breach_count, "escalations": esc_count},
        })
    return result


def build_interaction_figures(timeline: list):
    """Build (sankey, donut, swimlane) Plotly figures for the interaction dashboard.

    Returns None if plotly is unavailable or the timeline is empty. Shared by the
    on-screen dashboard and the downloadable HTML report.
    """
    if not timeline:
        return None
    try:
        import plotly.graph_objects as go
        from collections import defaultdict
    except ImportError:
        return None

    PC, LANE_Y, LANE_BG = PERSONA_COLORS, PERSONA_LANE_Y, PERSONA_BG

    msg_counts = defaultdict(int)
    transitions = defaultdict(int)
    prev = None
    for e in timeline:
        g = persona_group(e.get("author", ""))
        msg_counts[g] += 1
        if prev and prev != g:
            transitions[(prev, g)] += 1
        prev = g

    # ── Sankey ──────────────────────────────────────────────────────────────
    p_labels = list(PC.keys())
    p_idx = {l: i for i, l in enumerate(p_labels)}
    src, tgt, val, lc = [], [], [], []
    for (s, t), c in transitions.items():
        if s in p_idx and t in p_idx:
            src.append(p_idx[s]); tgt.append(p_idx[t])
            val.append(c); lc.append(_hex_rgba(PC[s], 0.4))
    fig_sk = go.Figure(go.Sankey(
        arrangement="snap",
        node=dict(pad=26, thickness=20, line=dict(color="white", width=2),
                  label=[f"{l}  ({msg_counts[l]})" for l in p_labels],
                  color=list(PC.values()), hovertemplate="%{label}<extra></extra>"),
        link=dict(source=src, target=tgt, value=val, color=lc,
                  hovertemplate="%{source.label} → %{target.label}<br>%{value} handoff(s)<extra></extra>"),
        textfont=dict(color="#1B3A6B", size=12, family="Segoe UI"),
    ))
    fig_sk.update_layout(title=dict(text="Interaction Flow", x=0.5, xanchor="center",
        font=dict(size=14, color="#1B3A6B", family="Segoe UI")),
        height=300, margin=dict(l=12, r=12, t=44, b=12),
        paper_bgcolor="white", font=dict(family="Segoe UI", size=11))

    # ── Donut ───────────────────────────────────────────────────────────────
    active = {k: v for k, v in msg_counts.items() if v > 0}
    fig_pie = go.Figure(go.Pie(
        labels=list(active.keys()), values=list(active.values()), hole=0.62,
        marker=dict(colors=[PC[p] for p in active], line=dict(color="white", width=3)),
        textinfo="label+percent", textposition="outside",
        textfont=dict(size=11, color="#374151", family="Segoe UI"),
        hovertemplate="%{label}<br>%{value} event(s) — %{percent}<extra></extra>",
        sort=False, rotation=0))
    fig_pie.add_annotation(text=f"<b style='font-size:22px;'>{sum(active.values())}</b><br>"
        "<span style='font-size:11px;color:#9ca3af;'>events</span>",
        x=0.5, y=0.5, showarrow=False, font=dict(color="#1B3A6B", family="Segoe UI"))
    fig_pie.update_layout(title=dict(text="Message Distribution", x=0.5, xanchor="center",
        font=dict(size=14, color="#1B3A6B", family="Segoe UI")),
        height=300, margin=dict(l=30, r=30, t=44, b=16),
        paper_bgcolor="white", showlegend=False)

    # ── Swimlane ────────────────────────────────────────────────────────────
    fig_sw = go.Figure()
    n = len(timeline)
    for grp, y in LANE_Y.items():
        fig_sw.add_shape(type="rect", x0=-0.5, x1=n - 0.5, y0=y - 0.45, y1=y + 0.45,
            fillcolor=LANE_BG[grp], line=dict(width=0), layer="below")
        fig_sw.add_annotation(x=-0.55, y=y, xanchor="right", showarrow=False,
            text=f"<b>{grp}</b>", font=dict(size=10, color=PC[grp], family="Segoe UI"))
    last_x = {}
    for i, e in enumerate(timeline):
        g = persona_group(e.get("author", ""))
        y = LANE_Y[g]
        if g in last_x:
            fig_sw.add_shape(type="line", x0=last_x[g], x1=i, y0=y, y1=y,
                line=dict(color=PC[g], width=1.5, dash="dot"))
        last_x[g] = i
    for i, e in enumerate(timeline):
        author  = e.get("author", "—")
        summary = e.get("summary", "")
        date_s  = e.get("date", "")
        g = persona_group(author)
        y = LANE_Y[g]
        crit = any(kw in summary.lower() for kw in CRITICAL_KW)
        fig_sw.add_trace(go.Scatter(
            x=[i], y=[y], mode="markers+text",
            marker=dict(size=40 if crit else 28, color=PC[g],
                symbol="diamond" if crit else "circle",
                line=dict(width=3 if crit else 1.5, color="#ef4444" if crit else "white")),
            text=[f"<b>{i+1}</b>"], textfont=dict(color="white", size=10),
            textposition="middle center",
            customdata=[[html.escape(author), date_s, html.escape(summary),
                         "⚠️ Critical Event" if crit else ""]],
            hovertemplate="<b>%{customdata[0]}</b>  ·  %{customdata[1]}<br>"
                          "<i>%{customdata[3]}</i><br><br>%{customdata[2]}<extra></extra>",
            showlegend=False))
        if crit:
            fig_sw.add_annotation(x=i, y=y + 0.54, text="⚠️",
                showarrow=False, font=dict(size=11), xanchor="center")
    fig_sw.add_annotation(x=0.5, y=1.10, xref="paper", yref="paper", showarrow=False,
        text="hover a dot for details   ·   ◆ diamond = critical event",
        font=dict(size=10, color="#9ca3af", family="Segoe UI"), xanchor="center")
    fig_sw.update_layout(
        title=dict(text="Interaction Swimlane", x=0.5, xanchor="center", y=0.97,
            font=dict(size=14, color="#1B3A6B", family="Segoe UI")),
        height=370, margin=dict(l=115, r=20, t=58, b=36),
        paper_bgcolor="white", plot_bgcolor="white",
        xaxis=dict(tickvals=list(range(n)), ticktext=[f"#{i+1}" for i in range(n)],
            showgrid=False, zeroline=False, tickfont=dict(size=10, color="#9ca3af")),
        yaxis=dict(tickvals=[], showgrid=False, zeroline=False, range=[-0.7, 3.7]),
        hovermode="closest",
        hoverlabel=dict(bgcolor="white", bordercolor="#d1d5db",
            font=dict(size=12, family="Segoe UI"), namelength=-1))

    return fig_sk, fig_pie, fig_sw


def _build_dashboard_kpis_html(ai: dict, timeline: list, esc_fn=None) -> str:
    """KPI stat-card strip + agent scores + customer sentiment journey.
    Uses only existing data — no API cost. Shared by app view and HTML report."""
    import html as _html
    from datetime import datetime as _dt
    esc = esc_fn or _html.escape

    # ── Derive metrics ────────────────────────────────────────────────────────
    resolution = str(ai.get("resolution_quality", "") or "").strip()
    _res_head = resolution.split(" —")[0].strip() if " —" in resolution else resolution

    # Days open (timeline dates are ISO YYYY-MM-DD)
    days_open = None
    _dates = [str(e.get("date", "")).strip() for e in timeline if e.get("date")]
    if len(_dates) >= 2:
        try:
            _d0 = _dt.strptime(_dates[0], "%Y-%m-%d")
            _d1 = _dt.strptime(_dates[-1], "%Y-%m-%d")
            days_open = (_d1 - _d0).days
        except ValueError:
            days_open = None

    # Handoffs (persona changes across timeline)
    handoffs, _prev = 0, None
    for e in timeline:
        g = persona_group(e.get("author", ""))
        if _prev and g != _prev:
            handoffs += 1
        _prev = g

    # Agents involved
    _agents = [a for a in ai.get("agents_involved", []) if isinstance(a, dict) and a.get("name")]
    n_agents = len(_agents)

    # Callbacks missed / premature closes
    _cb_log = ai.get("callback_promise_log", []) or []
    callbacks_missed = len([c for c in _cb_log if isinstance(c, dict) and c.get("status") == "Breached"])
    if not callbacks_missed:
        try:
            callbacks_missed = int(ai.get("callback_breaches", 0) or 0)
        except (ValueError, TypeError):
            callbacks_missed = 0
    premature = len(ai.get("premature_closes", []) or [])

    # Avg agent scores
    _ts = [a["technical_score"] for a in _agents if isinstance(a.get("technical_score"), int)]
    _hs = [a["handling_score"] for a in _agents if isinstance(a.get("handling_score"), int)]
    avg_tech = round(sum(_ts) / len(_ts), 1) if _ts else None
    avg_hand = round(sum(_hs) / len(_hs), 1) if _hs else None

    def _score_color(v):
        if v is None:
            return "#6b7280"
        return "#e24b4a" if v <= 2.5 else ("#ba7517" if v <= 3.5 else "#3B6D11")

    def _card(label, value, tone="neutral", icon=""):
        tones = {
            "neutral": ("#ffffff", "#e5e7eb", "#6b7280", "#1B3A6B"),
            "danger":  ("#fef2f2", "#fca5a5", "#991b1b", "#991b1b"),
            "warning": ("#fffbea", "#fcd34d", "#854f0b", "#854f0b"),
            "good":    ("#f0fdf4", "#86efac", "#166534", "#166534"),
        }
        bg, bd, lc, vc = tones.get(tone, tones["neutral"])
        _ic = f"<span style='margin-right:3px;'>{icon}</span>" if icon else ""
        return (
            f"<div style='background:{bg};border:1px solid {bd};border-radius:10px;padding:11px 13px;'>"
            f"<div style='font-size:10px;color:{lc};text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;'>{_ic}{esc(label)}</div>"
            f"<div style='font-size:19px;font-weight:700;color:{vc};line-height:1.1;'>{value}</div>"
            f"</div>"
        )

    # Resolution tone
    _res_tone = "neutral"
    if _res_head in ("Unresolved", "Premature Close"):
        _res_tone = "danger"
    elif _res_head in ("Assumed Fix", "Workaround"):
        _res_tone = "warning"
    elif _res_head == "Verified Fix":
        _res_tone = "good"

    cards = []
    if resolution:
        cards.append(_card("Resolution", esc(_res_head or resolution), _res_tone))
    if days_open is not None:
        cards.append(_card("Days Open", days_open))
    if n_agents:
        cards.append(_card("Agents", n_agents))
    cards.append(_card("Handoffs", handoffs))
    cards.append(_card("Callbacks Missed", callbacks_missed, "danger" if callbacks_missed else "good"))
    cards.append(_card("Premature Closes", premature, "danger" if premature else "good"))

    kpi_grid = (
        "<div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(104px,1fr));"
        "gap:9px;margin-bottom:14px;'>" + "".join(cards) + "</div>"
    )

    # ── Agent score + sentiment panels ────────────────────────────────────────
    panels = []
    if avg_tech is not None or avg_hand is not None:
        _t_html = f"<span style='font-size:11px;color:#6b7280;'>Technical</span><div style='font-size:17px;font-weight:700;color:{_score_color(avg_tech)};'>{avg_tech if avg_tech is not None else '—'}<span style='font-size:11px;color:#9ca3af;'>/5</span></div>"
        _h_html = f"<span style='font-size:11px;color:#6b7280;'>Handling</span><div style='font-size:17px;font-weight:700;color:{_score_color(avg_hand)};'>{avg_hand if avg_hand is not None else '—'}<span style='font-size:11px;color:#9ca3af;'>/5</span></div>"
        panels.append(
            "<div style='flex:1;min-width:150px;background:#ffffff;border:1px solid #e5e7eb;border-radius:10px;padding:11px 13px;'>"
            "<div style='font-size:10px;color:#6b7280;text-transform:uppercase;letter-spacing:.05em;margin-bottom:7px;'>Avg agent scores</div>"
            f"<div style='display:flex;gap:20px;'><div>{_t_html}</div><div>{_h_html}</div></div>"
            "</div>"
        )

    # Sentiment journey — approximate tension per timeline entry
    if timeline:
        _frust_kw = ("angry", "frustrat", "upset", "disappoint", "unhappy", "threat",
                     "complaint", "legal", "demand", "refus", "escalat", "urgent")
        bars = ""
        for e in timeline:
            _s = str(e.get("summary", "")).lower()
            if any(k in _s for k in CRITICAL_KW):
                col, h = "#E24B4A", 100
            elif any(k in _s for k in _frust_kw):
                col, h = "#EF9F27", 72
            else:
                col, h = "#5DCAA5", 46
            bars += f"<span style='flex:1;background:{col};height:{h}%;border-radius:2px;min-width:3px;'></span>"
        panels.append(
            "<div style='flex:1;min-width:180px;background:#ffffff;border:1px solid #e5e7eb;border-radius:10px;padding:11px 13px;'>"
            "<div style='font-size:10px;color:#6b7280;text-transform:uppercase;letter-spacing:.05em;margin-bottom:7px;'>Customer sentiment journey</div>"
            f"<div style='display:flex;align-items:flex-end;gap:3px;height:34px;'>{bars}</div>"
            "<div style='font-size:10px;color:#9ca3af;margin-top:4px;'>calm → frustrated over time</div>"
            "</div>"
        )

    panel_row = ""
    if panels:
        panel_row = "<div style='display:flex;gap:12px;flex-wrap:wrap;margin-bottom:14px;'>" + "".join(panels) + "</div>"

    # ── "What this shows" narrative (auto-built from the metrics above) ────────
    narrative_box = ""
    if timeline:
        touchpoints = len(timeline)
        _escalations = sum(1 for e in timeline if "escalat" in str(e.get("summary", "")).lower())
        _lanes = {persona_group(e.get("author", "")) for e in timeline}

        bits = []
        _dur = f"{days_open}-day" if days_open else "short"
        _agent_txt = f"{n_agents} agent{'s' if n_agents != 1 else ''}" if n_agents else "the support team"
        bits.append(f"A {_dur} case handled by {_agent_txt} across {touchpoints} touchpoint{'s' if touchpoints != 1 else ''}.")

        _lane_bits = []
        if "CAT Team" in _lanes:
            _lane_bits.append("reached the CAT team")
        else:
            _lane_bits.append("stayed on the front-line support lane")
        if _escalations:
            _lane_bits.append(f"escalated {_escalations} time{'s' if _escalations != 1 else ''}")
        if premature:
            _lane_bits.append(f"was closed prematurely {premature} time{'s' if premature != 1 else ''}")
        if callbacks_missed:
            _lane_bits.append(f"missed {callbacks_missed} callback{'s' if callbacks_missed != 1 else ''}")
        if _lane_bits:
            bits.append("It " + ", ".join(_lane_bits) + ".")

        # Sentiment trend: critical density in the last third vs first third
        if touchpoints >= 3:
            _third = max(1, touchpoints // 3)
            _crit = [any(k in str(e.get("summary", "")).lower() for k in CRITICAL_KW) for e in timeline]
            _early, _late = sum(_crit[:_third]), sum(_crit[-_third:])
            _sent = "with the customer growing more frustrated over time" if _late > _early else \
                    ("with tension easing toward the end" if _early > _late else "with steady customer sentiment")
        else:
            _sent = ""
        _end = _res_head or resolution
        if _end:
            bits.append(f"It ended <b>{esc(_end)}</b>" + (f" {_sent}." if _sent else "."))

        narrative_box = (
            "<div style='background:#eff6ff;border-left:4px solid #3b82f6;border-radius:0 8px 8px 0;"
            "padding:11px 14px;margin-bottom:14px;'>"
            "<div style='font-size:10px;font-weight:700;color:#1e40af;text-transform:uppercase;"
            "letter-spacing:.05em;margin-bottom:3px;'>What this shows</div>"
            f"<div style='font-size:12px;line-height:1.6;color:#1e3a5f;'>{' '.join(bits)}</div>"
            "</div>"
        )

    return narrative_box + kpi_grid + panel_row


def build_dot_timeline_figure(timeline: list):
    """Interactive dot-timeline Plotly figure. Returns None if unavailable/empty.

    Shared by the on-screen Timeline section and the HTML report.
    """
    if not timeline:
        return None
    try:
        import plotly.graph_objects as go
    except ImportError:
        return None

    n = len(timeline)
    fig = go.Figure()
    fig.add_shape(type="line", x0=0, x1=n - 1, y0=0, y1=0,
        line=dict(color="#d1d5db", width=3))
    for i, entry in enumerate(timeline):
        author  = entry.get("author", "—")
        date_s  = entry.get("date", "")
        summary = entry.get("summary", "")
        color   = PERSONA_COLORS[persona_group(author)]
        y       = 1 if i % 2 == 0 else -1
        fig.add_shape(type="line", x0=i, x1=i, y0=0, y1=y * 0.72,
            line=dict(color=color, width=1.5))
        fig.add_trace(go.Scatter(
            x=[i], y=[y], mode="markers+text",
            marker=dict(size=32, color=color, line=dict(width=2, color="white")),
            text=[str(i + 1)], textfont=dict(color="white", size=11),
            textposition="middle center",
            customdata=[[html.escape(author), date_s, html.escape(summary)]],
            hovertemplate="<b>%{customdata[0]}</b>  ·  %{customdata[1]}<br><br>%{customdata[2]}<extra></extra>",
            showlegend=False))
        label_y = y + (0.44 if y > 0 else -0.44)
        fig.add_annotation(x=i, y=label_y,
            text=f"<b>{html.escape(author)}</b><br><span style='color:#9ca3af'>{date_s}</span>",
            showarrow=False, font=dict(size=9, color=color), xanchor="center")
    fig.update_layout(
        height=320, margin=dict(l=10, r=10, t=10, b=10),
        plot_bgcolor="white", paper_bgcolor="white",
        yaxis=dict(visible=False, range=[-2, 2]),
        xaxis=dict(tickvals=list(range(n)), ticktext=[f"#{i+1}" for i in range(n)],
            showgrid=False, showline=False, tickfont=dict(size=10, color="#9ca3af")),
        hovermode="closest",
        hoverlabel=dict(bgcolor="white", bordercolor="#d1d5db",
            font=dict(size=12, family="Segoe UI"), namelength=-1))
    return fig


def fetch_all_updates(ticket_id: str) -> list:
    updates, page = [], 1
    while True:
        resp = _SESSION.get(
            f"{BASE_URL}/ticket/{ticket_id}/updates/?page={page}",
            auth=(API_KEY, AUTH_CODE), timeout=15,
        )
        if resp.status_code != 200:
            break
        data = resp.json()
        batch = data.get("data", [])
        if not batch:
            break
        updates.extend(batch)
        info = data.get("page_info") or {}
        count = info.get("count", 0)
        end_index = info.get("end_index", 0)
        # If pagination metadata says we've reached the end, stop. If it is
        # missing, keep paging until an empty batch (handled above).
        if count and end_index >= count:
            break
        if page >= 100:  # safety cap against a non-paginating endpoint
            break
        page += 1
    return updates


def fetch_contact(user_id) -> dict:
    """Fetch full contact details (phone, country, etc.)."""
    try:
        resp = _SESSION.get(
            f"{BASE_URL}/contact/{user_id}/",
            auth=(API_KEY, AUTH_CODE), timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return {}


def fetch_ticket(ticket_id: str) -> dict:
    resp = _SESSION.get(
        f"{BASE_URL}/ticket/{ticket_id}/", auth=(API_KEY, AUTH_CODE), timeout=15
    )
    resp.raise_for_status()
    ticket = resp.json()

    # Fetch all update pages
    all_updates = fetch_all_updates(ticket_id)
    if all_updates:
        ticket["updates"] = all_updates

    # Enrich user with full contact details
    user_id = ticket.get("user", {}).get("id")
    if user_id:
        contact = fetch_contact(user_id)
        if contact:
            ticket["user"].update(contact)

    return ticket


def build_ticket_text(ticket: dict) -> str:
    user = ticket.get("user", {})
    cf_lines = []
    for field in ticket.get("custom_fields", []) or []:
        name = field.get("name", "")
        val  = field.get("value") or field.get("display_value") or ""
        if name and val:
            cf_lines.append(f"  {name}: {val}")

    lines = [
        f"Ticket #{ticket['id']}: {ticket['subject']}",
        f"Status:   {ticket['status']['name']}",
        f"Category: {ticket.get('category', {}).get('name', 'N/A') if ticket.get('category') else 'N/A'}",
        f"Priority: {ticket['priority']['name']}",
        f"Created:  {ticket.get('created_at', 'N/A')}",
        f"Customer: {user.get('name','?')} | Email: {user.get('email','?')} | Phone: {user.get('phone','?')} | Country: {user.get('country','?')}",
    ]
    if cf_lines:
        lines.append("Custom Fields:")
        lines.extend(cf_lines)

    all_updates = ticket.get("updates", [])

    # ── Parse each update into a readable block, handling both API formats:
    #   Format A (fetch_all_updates endpoint): agent.name, created_at, text/html/body
    #   Format B (embedded in ticket object):  by.name,    timestamp,  message.text + change fields
    def _parse_update(u):
        # Author
        by    = u.get("by") or {}
        agent = u.get("agent") or {}
        if by:
            author      = by.get("name") or "Unknown"
            author_type = by.get("type", "")
        elif agent:
            author      = agent.get("name") or "Customer"
            author_type = "staff"
        else:
            author      = "Customer"
            author_type = "user"

        # Timestamp
        timestamp = u.get("timestamp") or u.get("created_at") or ""

        # Message text
        msg = u.get("message") or {}
        msg_text = ""
        if isinstance(msg, dict):
            msg_text = (msg.get("text") or msg.get("html") or "").strip()
        if not msg_text:
            msg_text = (u.get("text") or u.get("html") or u.get("body") or "").strip()
        # Skip the boilerplate case-creation line
        if msg_text == "Linksys Technical Support Case created.\n\n" or msg_text == "Linksys Technical Support Case created.":
            msg_text = ""

        # Change events
        parts = []
        if msg_text:
            parts.append(msg_text[:900])
        sc = u.get("status_change")
        if sc and sc.get("new_name"):
            parts.append(f"[STATUS: {sc.get('old_name','?')} -> {sc['new_name']}]")
        ac = u.get("assignee_change")
        if ac and ac.get("new_name"):
            parts.append(f"[ASSIGNED TO: {ac['new_name']}]")
        cc = u.get("category_change")
        if cc and cc.get("new_name"):
            parts.append(f"[CATEGORY -> {cc['new_name']}]")
        sat = u.get("satisfaction_survey")
        if sat and isinstance(sat, dict):
            parts.append(f"[CSAT SURVEY: {sat}]")
        ts_spent = u.get("time_spent")
        if ts_spent:
            parts.append(f"[Time spent: {ts_spent} min]")

        text = "\n".join(parts).strip()
        return author, author_type, timestamp, text

    # Filter to meaningful updates only (have message text or change events)
    meaningful = []
    for u in all_updates:
        author, author_type, timestamp, text = _parse_update(u)
        if text:
            meaningful.append((author, author_type, timestamp, text))

    total_raw  = len(all_updates)
    total_mean = len(meaningful)

    # Cap at 60 meaningful updates: first 10 + last 50
    if total_mean > 60:
        to_render = meaningful[:10] + meaningful[-50:]
        skipped   = total_mean - 60
    else:
        to_render = meaningful
        skipped   = 0

    lines += [
        "",
        f"Total raw updates: {total_raw} | Meaningful updates: {total_mean}"
        + (f" (showing 60 — {skipped} middle updates omitted)" if skipped else ""),
        "── THREAD / UPDATES ──────────────────────────────────────",
    ]
    for author, author_type, timestamp, text in to_render:
        if len(text) > 1200:
            text = text[:1200] + "… [truncated]"
        lines.append(f"\n[{timestamp}]  {author}  ({author_type}):\n{text}")

    return "\n".join(lines)


# ── Claude prompt ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """\
You are a Senior Quality Assurance Analyst AND an expert Linksys network technician \
AND a Customer Experience Director. You are reviewing a customer support ticket to \
produce a coaching-ready QA scorecard.
The ticket thread may be in any language. Always respond in English only.
Translate any non-English content before analysing it.
Read EVERY message carefully — extract phone numbers, country, serial numbers and warranty dates
even if they are mentioned only briefly in the thread.

SCORING INSTRUCTIONS — For each named agent, evaluate TWO separate dimensions on a 1-5 scale:

TECHNICAL ACCURACY (1-5):
  5 = Perfect diagnosis, correct troubleshooting sequence, accurate product knowledge
  4 = Minor gap but fundamentally correct approach
  3 = Reached the right outcome but skipped steps or used inefficient path
  2 = Incorrect diagnosis or wrong troubleshooting steps given
  1 = Gave harmful or completely wrong technical advice

CUSTOMER HANDLING (1-5):
  5 = Empathetic, proactive, clear communication, set proper expectations
  4 = Professional and adequate but missed an empathy opportunity
  3 = Neutral — correct but robotic, no acknowledgment of frustration
  2 = Dismissive, ignored customer concerns, or used inappropriate tone
  1 = Rude, argumentative, or made the situation worse

For "technical_findings": evaluate ONLY technical troubleshooting accuracy. Did the agent follow
correct diagnostic steps for the product? Did they identify the right root cause? Was the solution
appropriate? Name the specific step that was wrong or skipped and state what should have been done.

For "soft_skill_findings": evaluate ONLY communication and customer handling. Did the agent
acknowledge the customer frustration? Set clear expectations for next steps and timelines?
Use de-escalation language when needed? Over-promise or under-deliver on callbacks?

For "opportunities": combine all QA findings (technical + soft skills + process) into a single list.
Be specific and direct — name exactly what was wrong or missed and what should have been done instead.

For "coaching_notes": for each major finding, provide the agent name, what they did wrong,
what they should have said or done instead (an actual script), and which policy this references.

For "agents_involved": list every named agent/staff member who participated. Identify their role,
summarise their contribution, AND score them on both dimensions. Add flags for FCR Violation
(closed prematurely then customer called back), Callback Breach (missed promised callback),
or Premature Close (resolved without confirming with customer). Skip smart-rules and automated systems.

For "efficiency_rating": evaluate whether the agent took a direct path to resolution or went in
circles. Count unnecessary transfers, repeated questions, and redundant steps.

For "resolution_quality": evaluate how the case was closed — did the agent confirm with the
customer that the issue was truly resolved?

For "case_verdict": write 2-3 sentences as a senior executive reviewer. State clearly whether the
case was truly resolved, identify the single biggest risk or failure, and give one concrete
recommendation. Flag if the customer mentioned legal action, escalation to management, or extreme
dissatisfaction. If a CSAT score is visible in the thread, reference it.

For "callback_breaches": count how many times a promised callback was missed, late, or not delivered.

For "coaching_priority": set to "URGENT" if any agent scored 1-2 on either dimension,
"REVIEW" if any agent scored 3, or "GOOD" if all agents scored 4-5.

PREMATURE CLOSE DETECTION — scan the full ticket lifecycle for this pattern:
  1. Agent changes status to "Resolved" or "Closed"
  2. Customer contacts back about the SAME issue (reopens, calls again, replies with same symptoms)
  This is a "Premature Close" — the agent closed the ticket without a confirmed fix.
  Count every occurrence. For each one, record which agent closed it, the date, and what happened next.
  Also flag any "Observe and Close" pattern — agent tells customer to "observe" or "monitor" and then
  closes the ticket without a follow-up. This is a KPI manipulation red flag.

CALLBACK BREACH DETECTION — scan every agent message for callback promises:
  Look for language like "I will call you back in X minutes/hours", "expect a callback", "I'll follow up",
  "we will reach out", "I'll contact you within". For each promise found:
  1. Record the agent name, the promise text, and the promised timeframe
  2. Check if a follow-up from that agent (or any agent) actually happened within the promised window
  3. Mark as "Honored" or "Breached" with the actual delay if breached
  A broken callback to a frustrated customer is a critical failure.

COMPLEX ENVIRONMENT CHECKLIST — if the ticket involves a mesh network with 4+ nodes,
  a large property (4000+ sqft), or mixed hardware generations, auto-evaluate whether agents completed:
  1. Node Inventory: Did any agent document exact model, serial, and placement of every node?
  2. RSSI/Signal Check: Were signal strength readings captured? Any node worse than -65 dBm?
  3. Wired Bypass Test: Was a direct-to-modem Ethernet test performed to isolate the fault domain?
  4. Topology Reconciliation: Did agents agree on the number of nodes, or were there conflicting counts?
  5. Firmware Consistency: Was firmware version checked/matched across all nodes?
  Mark each item as "Done", "Skipped", or "Not Applicable". This is only required when the environment
  qualifies as complex (4+ nodes or mixed hardware). Set "complex_environment" to false otherwise.

Respond with a single valid JSON object only. No markdown, no explanation — raw JSON only.

{
  "subject_is_english": true or false,
  "subject_english": "English translation of the subject if it is NOT in English. If already English set to null.",
  "customer_phone": "Customer phone number found anywhere in the thread or custom fields, else null",
  "customer_country": "Customer country or region inferred from phone prefix, email, address, or language used, else null",
  "problem_summary": "1-2 sentences describing what the customer needs",
  "problem_category_1": "Primary issue category (e.g. Connectivity, Hardware Failure, Setup, Firmware, Warranty/RMA, RMA or Refund)",
  "problem_category_2": "Secondary issue category or refund reason or null",
  "root_cause": "Product Defect / User Error / Configuration Gap / Network Environment / Firmware Bug / Warranty Process / Agent Error / System Failure",
  "model_number": "Product model number extracted from anywhere in thread or custom fields or null",
  "serial_number": "Serial number extracted from anywhere in thread or custom fields or null",
  "warranty_start_date": "Warranty start date from thread or custom fields or null. Look for contract start date too.",
  "warranty_status": "Active / Expired / Unknown — infer from warranty start date and any expiry mentions",
  "actions_taken": ["action 1", "action 2"],
  "actions_log": [
    {"date": "DD Mon", "action": "Concise description of action taken", "agent": "First name or short name of agent or 'Customer'"}
  ],
  "actions_summary": "One sentence summary of all support actions taken",
  "actions_insights": "1-2 sentences of insight — patterns, gaps, what is or is not working",
  "technical_findings": ["Technical QA finding — what was technically wrong and what should have been done"],
  "soft_skill_findings": ["Soft skill finding — what the agent said or failed to say and what best practice requires"],
  "opportunities": ["Combined QA finding 1 — what was wrong or missed and what should have been done", "finding 2"],
  "efficiency_rating": "Efficient / Acceptable / Inefficient — [reason]",
  "resolution_quality": "Verified Fix / Assumed Fix / Workaround / Unresolved / Premature Close",
  "next_steps": ["step 1", "step 2"],
  "coaching_notes": [
    {"agent": "Agent Name", "finding": "What they did wrong", "correct_script": "What they should have said or done instead", "policy_ref": "SOP or best practice reference"}
  ],
  "agents_involved": [
    {"name": "Agent Full Name", "role": "L1 Support / CAT L2 / Team Coach / etc.", "summary": "One sentence of their contribution", "technical_score": 4, "handling_score": 3, "flags": ["FCR Violation", "Callback Breach", "Premature Close"]}
  ],
  "case_verdict": "2-3 sentence executive verdict on case outcome, biggest risk, and recommendation.",
  "callback_breaches": 0,
  "coaching_priority": "URGENT / REVIEW / GOOD",
  "premature_closes": [
    {"agent": "Agent who closed", "date": "YYYY-MM-DD", "what_happened": "Customer called back 2 days later with same issue", "pattern": "Observe and Close / Unconfirmed Resolution"}
  ],
  "callback_promise_log": [
    {"agent": "Agent Name", "promise": "Exact quote or paraphrase of the callback promise", "promised_timeframe": "30 minutes / 1 hour / next day", "actual_followup": "Honored — called back in 25 min / Breached — no follow-up for 3 hours / Breached — never followed up", "status": "Honored / Breached"}
  ],
  "complex_environment": true or false,
  "environment_checklist": {
    "node_inventory": "Done / Skipped / Not Applicable",
    "node_inventory_detail": "Agent X documented 6 nodes with serials on Day 3 / No agent ever reconciled node count",
    "rssi_signal_check": "Done / Skipped / Not Applicable",
    "rssi_detail": "Aysah recorded -76 dBm on Child Node 2 / No RSSI readings captured",
    "wired_bypass_test": "Done / Skipped / Not Applicable",
    "wired_bypass_detail": "Eric performed wired test on Day 14 / Never attempted despite 3 weeks of troubleshooting",
    "topology_reconciliation": "Done / Skipped / Not Applicable",
    "topology_detail": "Conflicting counts: 3, 6, 8, 11 nodes reported across sessions / Consistent 4-node count",
    "firmware_consistency": "Done / Skipped / Not Applicable",
    "firmware_detail": "All nodes confirmed on FW 1.0.11 / Mixed firmware versions not checked"
  },
  "timeline": [
    {"date": "YYYY-MM-DD", "author": "Name or Customer", "summary": "One sentence of what happened"}
  ]
}
"""


def _repair_json(raw: str) -> dict:
    """Try to parse JSON; if truncated, attempt to close open structures."""
    raw = raw.strip()
    # Strip markdown fences
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    # First try clean parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Truncated response — walk back to the last complete value and close brackets
    # Find last valid-looking closing position
    depth_brace = 0
    depth_bracket = 0
    last_safe = 0
    in_string = False
    escape = False
    for i, ch in enumerate(raw):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
        if in_string:
            continue
        if ch == "{":
            depth_brace += 1
        elif ch == "}":
            depth_brace -= 1
            if depth_brace == 0:
                last_safe = i + 1
        elif ch == "[":
            depth_bracket += 1
        elif ch == "]":
            depth_bracket -= 1
    # Truncate to last safe complete object boundary
    if last_safe > 0:
        try:
            return json.loads(raw[:last_safe])
        except json.JSONDecodeError:
            pass
    # Last resort: strip from the last complete key-value pair
    for end in range(len(raw) - 1, 0, -1):
        candidate = raw[:end].rstrip().rstrip(",") + "\n}"
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise ValueError("Could not repair truncated JSON from Claude")


def analyse_ticket(ticket_text: str) -> tuple[dict, int, int]:
    """Returns (ai_result, input_tokens, output_tokens)."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    max_attempts = 5
    for attempt in range(1, max_attempts + 1):
        try:
            msg = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=8192,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": ticket_text}],
            )
            raw = msg.content[0].text.strip()
            parsed = _repair_json(raw)
            return (parsed, msg.usage.input_tokens, msg.usage.output_tokens)
        except anthropic.APIStatusError as e:
            if e.status_code in (529, 500, 503) and attempt < max_attempts:
                wait = 2 ** attempt
                st.toast(f"Claude is busy — retrying in {wait}s (attempt {attempt}/{max_attempts})")
                time.sleep(wait)
                continue
            raise


def _open_in_new_tab(html_content: str):
    """Open html_content in a new browser tab via base64 Blob URL (safe with embedded JS/scripts)."""
    b64 = base64.b64encode(html_content.encode("utf-8")).decode("ascii")
    components.html(
        f"""<script>
        (function() {{
            var b64 = "{b64}";
            var binary = atob(b64);
            var bytes = new Uint8Array(binary.length);
            for (var i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
            var blob = new Blob([bytes], {{type: 'text/html; charset=utf-8'}});
            window.open(URL.createObjectURL(blob), '_blank');
        }})();
        </script>""",
        height=0,
    )


# ── Executive dashboard HTML (embedded Plotly charts) ─────────────────────────
def _build_dashboard_html(timeline: list) -> str:
    """Serialise the shared interaction figures to embeddable HTML for the report."""
    figs = build_interaction_figures(timeline)
    if not figs:
        return ""
    try:
        _fsk, _fpie, fig_sw = figs
        cfg = {"responsive": True, "displayModeBar": False}
        sw_html = fig_sw.to_html(full_html=False, include_plotlyjs=False, config=cfg)
        return f"""
        <h2>Case Snapshot</h2>
        <div style="margin-bottom:20px;">{sw_html}</div>
        """
    except Exception:
        return ""


def _build_dot_timeline_html(timeline: list) -> str:
    """Serialise the shared dot-timeline figure to embeddable HTML for the report."""
    fig = build_dot_timeline_figure(timeline)
    if fig is None:
        return ""
    try:
        return fig.to_html(full_html=False, include_plotlyjs=False,
                           config={"responsive": True, "displayModeBar": False})
    except Exception:
        return ""


# ── HTML / PDF report helpers ─────────────────────────────────────────────────

def _build_gap_analysis(ticket: dict, threshold_days: int = 5) -> str:
    """Detect dead zones — periods with no staff activity exceeding threshold_days."""
    from datetime import datetime as _dt
    import html as _hm
    updates = ticket.get("updates", [])
    staff_events = []
    for u in updates:
        if not isinstance(u, dict):
            continue
        by = u.get("by") or {}
        btype = by.get("type", "")
        ts = u.get("timestamp") or ""
        if btype not in ("staff",) or not ts:
            continue
        try:
            d = _dt.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
            name = (by.get("name") or "Staff").strip()
            staff_events.append((d, name))
        except ValueError:
            continue
    if len(staff_events) < 2:
        return ""
    staff_events.sort(key=lambda x: x[0])
    gaps = []
    for i in range(1, len(staff_events)):
        prev_dt, prev_name = staff_events[i - 1]
        curr_dt, curr_name = staff_events[i]
        delta = (curr_dt - prev_dt).days
        if delta >= threshold_days:
            gaps.append({
                "from_date": prev_dt.strftime("%d %b %Y"),
                "to_date":   curr_dt.strftime("%d %b %Y"),
                "days":      delta,
                "last_agent": prev_name,
                "next_agent": curr_name,
            })
    if not gaps:
        return ""
    rows = "".join(
        f"<tr>"
        f"<td style='color:#b91c1c;font-weight:700;'>{g['days']} days</td>"
        f"<td>{g['from_date']}</td>"
        f"<td>{g['to_date']}</td>"
        f"<td>{_hm.escape(g['last_agent'])}</td>"
        f"<td>{_hm.escape(g['next_agent'])}</td>"
        f"</tr>"
        for g in gaps
    )
    worst = max(gaps, key=lambda x: x["days"])
    warning = (
        f"<div style='background:#fff1f2;border-left:4px solid #ef4444;padding:8px 12px;"
        f"border-radius:0 4px 4px 0;margin-bottom:8px;font-size:11px;color:#7f1d1d;'>"
        f"<strong>Longest gap: {worst['days']} days</strong> — "
        f"No staff activity between {worst['from_date']} and {worst['to_date']}. "
        f"Case was left idle after {_hm.escape(worst['last_agent'])}'s last action."
        f"</div>"
    )
    return (
        "<h2>Dead Zones — Activity Gaps</h2>"
        + warning +
        "<table>"
        "<tr>"
        "<th>Gap</th><th>From</th><th>To</th>"
        "<th>Last Staff Action</th><th>Resumed By</th>"
        "</tr>"
        + rows +
        "</table>"
    )

def _build_agents_html(ticket: dict, ai_agents: list) -> str:
    """Build Agents Involved table from raw ticket updates + AI role labels."""
    import html as _hm
    updates = ticket.get("updates", [])
    seen: dict = {}
    for u in updates:
        if not isinstance(u, dict):
            continue
        by = u.get("by") or {}
        btype = by.get("type", "")
        name = (by.get("name") or "").strip()
        if not name or btype == "smartrule":
            continue
        if name not in seen:
            seen[name] = {"type": btype, "msgs": 0, "first": u.get("timestamp", ""), "last": u.get("timestamp", ""), "flags": []}
        seen[name]["last"] = u.get("timestamp", "")
        msg = u.get("message") or {}
        if msg.get("text", "").strip():
            seen[name]["msgs"] += 1
        sc = u.get("status_change")
        if sc and sc.get("new_name"):
            flag = f"Status -> {sc['new_name']}"
            if flag not in seen[name]["flags"]:
                seen[name]["flags"].append(flag)
        ac = u.get("assignee_change")
        if ac and ac.get("new_name") and "escalat" in (ac.get("new_name") or "").lower():
            seen[name]["flags"].append("Escalated")

    # Build role lookup from AI agents_involved list
    role_map = {a.get("name", ""): (a.get("role", ""), a.get("summary", ""), a.get("technical_score"), a.get("handling_score"), a.get("flags", [])) for a in (ai_agents or [])}

    # Separate customer vs staff
    customer_name = (ticket.get("user") or {}).get("name", "")
    rows = []
    for name, info in seen.items():
        is_customer = info["type"] == "user" or name == customer_name
        role_label, ai_summary, ts, hs, ai_flags = role_map.get(name, ("", "", None, None, []))
        if not role_label:
            role_label = "Customer" if is_customer else ("System" if info["type"] == "staff" and name == "System" else "Support Agent")
        bg = "#eef3fb" if is_customer else "white"
        all_flags = list(info["flags"][:3]) + [str(f) for f in (ai_flags or [])]
        flags_html = " ".join(f"<span style='background:#fee2e2;color:#b91c1c;border-radius:3px;padding:1px 5px;font-size:9px;font-weight:700;'>{_hm.escape(f)}</span>" for f in all_flags)
        first_d = (info["first"] or "")[:10]
        last_d  = (info["last"] or "")[:10]
        date_range = first_d if first_d == last_d else f"{first_d} – {last_d}"
        ts_cell = ""
        hs_cell = ""
        if ts is not None:
            ts_c = "#ef4444" if isinstance(ts, int) and ts <= 2 else ("#f59e0b" if isinstance(ts, int) and ts == 3 else "#16a34a")
            ts_cell = f"<td style='text-align:center;color:{ts_c};font-weight:700;'>{ts}/5</td>"
        else:
            ts_cell = "<td style='text-align:center;color:#9ca3af;'>—</td>"
        if hs is not None:
            hs_c = "#ef4444" if isinstance(hs, int) and hs <= 2 else ("#f59e0b" if isinstance(hs, int) and hs == 3 else "#16a34a")
            hs_cell = f"<td style='text-align:center;color:{hs_c};font-weight:700;'>{hs}/5</td>"
        else:
            hs_cell = "<td style='text-align:center;color:#9ca3af;'>—</td>"
        rows.append(
            f"<tr style='background:{bg};'>"
            f"<td style='font-weight:600;'>{_hm.escape(name)}</td>"
            f"<td>{_hm.escape(role_label)}</td>"
            f"{ts_cell}{hs_cell}"
            f"<td style='text-align:center;'>{info['msgs']}</td>"
            f"<td style='color:#6b7280;font-size:10px;'>{date_range}</td>"
            f"<td>{_hm.escape(ai_summary) if ai_summary else flags_html}</td>"
            f"</tr>"
        )
    if not rows:
        return ""
    return (
        "<h2>Agents Involved</h2>"
        "<table>"
        "<tr><th>Name</th><th>Role</th><th style='text-align:center;'>Tech</th><th style='text-align:center;'>Handling</th><th style='text-align:center;'>Messages</th><th>Active Dates</th><th>Contribution / Flags</th></tr>"
        + "".join(rows) +
        "</table>"
    )


def _get_csat_data(ticket: dict) -> dict:
    """Extract CSAT score and feedback from raw ticket updates."""
    import re
    for u in ticket.get("updates", []):
        if not isinstance(u, dict):
            continue
        s = u.get("satisfaction_survey")
        if s and isinstance(s, dict) and s.get("rating") is not None:
            return s
        msg = (u.get("message") or {}).get("text", "") or ""
        if "CSAT:" in msg or "csat:" in msg.lower():
            csat_match = re.search(r"CSAT[:\s]+(\d+)", msg, re.IGNORECASE)
            res_match  = re.search(r"Resolution Rate[:\s]+(\w+)", msg, re.IGNORECASE)
            fcr_match  = re.search(r"FCR[:\s]+([^\n]+)", msg, re.IGNORECASE)
            fb_match   = re.search(r"Customer.s Feedback[:\s]+([^\n]+)", msg, re.IGNORECASE)
            return {
                "rating": int(csat_match.group(1)) if csat_match else None,
                "resolution": res_match.group(1).strip() if res_match else None,
                "fcr": fcr_match.group(1).strip() if fcr_match else None,
                "feedback": fb_match.group(1).strip() if fb_match else None,
            }
    return {}


def _build_verdict_html(ticket: dict, ai: dict) -> str:
    """Build Case Verdict box from AI case_verdict + raw CSAT data."""
    import html as _hm
    verdict_text = ai.get("case_verdict", "")
    cb_breaches  = ai.get("callback_breaches", 0) or 0
    csat         = _get_csat_data(ticket)
    csat_score   = csat.get("rating")
    csat_res     = csat.get("resolution")
    csat_fcr     = csat.get("fcr")
    csat_fb      = csat.get("feedback")

    # Colour the verdict box based on CSAT / content
    is_critical = (
        csat_score is not None and csat_score <= 2
        or "lawyer" in verdict_text.lower()
        or "legal" in verdict_text.lower()
        or "management" in verdict_text.lower()
        or (isinstance(cb_breaches, int) and cb_breaches >= 2)
    )
    border = "#ef4444" if is_critical else "#f59e0b"
    bg     = "#fff1f2" if is_critical else "#fffbea"
    icon   = "🚨" if is_critical else "⚠️"

    csat_html = ""
    if csat_score is not None:
        stars = "★" * csat_score + "☆" * (5 - csat_score)
        csat_color = "#ef4444" if csat_score <= 2 else ("#f59e0b" if csat_score <= 3 else "#16a34a")
        csat_html = (
            f"<div style='display:flex;gap:24px;flex-wrap:wrap;margin-bottom:8px;'>"
            f"<div><span style='font-weight:700;color:#1B3A6B;'>CSAT:</span> "
            f"<span style='font-size:14px;color:{csat_color};font-weight:700;'>{stars} ({csat_score}/5)</span></div>"
        )
        if csat_res:
            csat_html += f"<div><span style='font-weight:700;color:#1B3A6B;'>Resolved:</span> {_hm.escape(csat_res)}</div>"
        if csat_fcr:
            csat_html += f"<div><span style='font-weight:700;color:#1B3A6B;'>FCR:</span> {_hm.escape(csat_fcr)}</div>"
        csat_html += "</div>"
        if csat_fb:
            csat_html += f"<div style='font-style:italic;color:#374151;margin-bottom:8px;'>&ldquo;{_hm.escape(csat_fb)}&rdquo;</div>"

    breach_html = ""
    if isinstance(cb_breaches, int) and cb_breaches > 0:
        breach_html = (
            f"<div style='margin-bottom:6px;'>"
            f"<span style='background:#fef2f2;border:1px solid #fca5a5;color:#b91c1c;"
            f"border-radius:4px;padding:2px 8px;font-size:10px;font-weight:700;'>"
            f"📵 {cb_breaches} callback breach{'es' if cb_breaches != 1 else ''} detected</span></div>"
        )

    verdict_p = f"<p style='margin:0;line-height:1.6;font-size:12px;'>{_hm.escape(verdict_text)}</p>" if verdict_text else ""

    if not (csat_html or breach_html or verdict_p):
        return ""

    return (
        f"<h2>{icon} Case Verdict</h2>"
        f"<div style='background:{bg};border-left:4px solid {border};padding:12px 16px;"
        f"border-radius:0 4px 4px 0;'>"
        f"{csat_html}{breach_html}{verdict_p}"
        f"</div>"
    )


# ── QA badge / scorecard HTML helpers (used inside build_html_report) ─────────
def _build_qa_badges_html(ai: dict, esc) -> str:
    cp = ai.get("coaching_priority", "")
    rq = ai.get("resolution_quality", "")
    ef = ai.get("efficiency_rating", "")
    rc = ai.get("root_cause", "")
    if not (cp or rq or ef or rc):
        return ""
    parts = []
    if cp:
        cp_map = {"URGENT": ("#991b1b", "#fef2f2", "#ef4444"), "REVIEW": ("#92400e", "#fffbea", "#f59e0b"), "GOOD": ("#166534", "#f0fdf4", "#16a34a")}
        tc, bg, bc = cp_map.get(cp.split()[0] if cp else "", ("#6b7280", "#f8fafc", "#d1d5db"))
        parts.append(f"<div style='background:{bg};border-left:4px solid {bc};border-radius:0 6px 6px 0;padding:8px 14px;margin:4px 0;'><span style='font-weight:700;font-size:12px;color:{tc};'>COACHING PRIORITY: {esc(cp)}</span></div>")
    badge_cells = []
    if rq:
        rq_map = {"Verified Fix": "#16a34a", "Assumed Fix": "#f59e0b", "Workaround": "#f59e0b", "Unresolved": "#ef4444", "Premature Close": "#ef4444"}
        c = rq_map.get(rq.split(" —")[0].strip() if " —" in rq else rq.strip(), "#6b7280")
        badge_cells.append(f"<td style='padding:8px 14px;background:white;border:1px solid #e5e7eb;border-radius:8px;'><div style='font-size:9px;color:#6b7280;text-transform:uppercase;letter-spacing:.07em;'>Resolution Quality</div><div style='font-size:13px;font-weight:700;color:{c};'>{esc(rq)}</div></td>")
    if ef:
        c = "#16a34a" if ef.startswith("Efficient") else ("#f59e0b" if ef.startswith("Acceptable") else "#ef4444")
        badge_cells.append(f"<td style='padding:8px 14px;background:white;border:1px solid #e5e7eb;border-radius:8px;'><div style='font-size:9px;color:#6b7280;text-transform:uppercase;letter-spacing:.07em;'>Efficiency</div><div style='font-size:13px;font-weight:700;color:{c};'>{esc(ef)}</div></td>")
    if rc:
        badge_cells.append(f"<td style='padding:8px 14px;background:white;border:1px solid #e5e7eb;border-radius:8px;'><div style='font-size:9px;color:#6b7280;text-transform:uppercase;letter-spacing:.07em;'>Root Cause</div><div style='font-size:13px;font-weight:700;color:#1B3A6B;'>{esc(rc)}</div></td>")
    if badge_cells:
        parts.append(f"<table style='border-collapse:separate;border-spacing:8px 0;margin:4px 0;'><tr>{''.join(badge_cells)}</tr></table>")
    return "\n".join(parts)


def _build_scorecard_html(agents: list, esc) -> str:
    if not agents or not any(a.get("technical_score") is not None for a in agents):
        return ""
    rows = ""
    for a in agents:
        ts = a.get("technical_score", "—")
        hs = a.get("handling_score", "—")
        ts_c = "#ef4444" if isinstance(ts, int) and ts <= 2 else ("#f59e0b" if isinstance(ts, int) and ts == 3 else "#16a34a")
        hs_c = "#ef4444" if isinstance(hs, int) and hs <= 2 else ("#f59e0b" if isinstance(hs, int) and hs == 3 else "#16a34a")
        flags = a.get("flags", [])
        flags_h = " ".join(f"<span style='background:#fef2f2;color:#991b1b;border-radius:3px;padding:1px 6px;font-size:9px;font-weight:700;'>{esc(f)}</span>" for f in flags) if flags else ""
        rows += f"<tr><td style='font-weight:600;'>{esc(a.get('name',''))}</td><td>{esc(a.get('role',''))}</td><td style='text-align:center;color:{ts_c};font-weight:700;'>{ts}/5</td><td style='text-align:center;color:{hs_c};font-weight:700;'>{hs}/5</td><td>{flags_h}</td><td style='font-size:10px;'>{esc(a.get('summary',''))}</td></tr>"
    return f"<table><tr><th>Agent</th><th>Role</th><th>Tech</th><th>Handling</th><th>Flags</th><th>Summary</th></tr>{rows}</table>"


def _build_coaching_notes_html(notes: list, esc) -> str:
    if not notes:
        return ""
    items = []
    for cn in notes:
        if not isinstance(cn, dict):
            continue
        items.append(
            f"<div style='background:#f0fdf4;border-left:4px solid #16a34a;border-radius:0 6px 6px 0;padding:10px 14px;margin:6px 0;'>"
            f"<p style='margin:0 0 3px;font-weight:700;font-size:11px;color:#1B3A6B;'>{esc(cn.get('agent',''))}</p>"
            f"<p style='margin:0 0 3px;font-size:11px;color:#991b1b;'><strong>Finding:</strong> {esc(cn.get('finding',''))}</p>"
            f"<p style='margin:0 0 3px;font-size:11px;color:#166534;'><strong>Correct Handling:</strong> {esc(cn.get('correct_script',''))}</p>"
            f"<p style='margin:0;font-size:10px;color:#6b7280;font-style:italic;'>Policy: {esc(cn.get('policy_ref',''))}</p>"
            f"</div>"
        )
    return "".join(items)


def _build_premature_closes_html(closes: list, esc) -> str:
    if not closes:
        return ""
    items = []
    for pc in closes:
        if not isinstance(pc, dict):
            continue
        pattern = pc.get("pattern", "")
        bg = "#fef2f2" if "Observe" in pattern else "#fff7ed"
        border = "#ef4444" if "Observe" in pattern else "#f97316"
        items.append(
            f"<div style='background:{bg};border-left:4px solid {border};border-radius:0 6px 6px 0;padding:10px 14px;margin:6px 0;'>"
            f"<p style='margin:0 0 3px;font-weight:700;font-size:11px;color:#991b1b;'>{esc(pc.get('agent',''))} — {esc(pc.get('date',''))}</p>"
            f"<p style='margin:0 0 3px;font-size:11px;color:#7f1d1d;'>{esc(pc.get('what_happened',''))}</p>"
            f"<p style='margin:0;font-size:10px;color:#9a3412;font-style:italic;'>Pattern: {esc(pattern)}</p>"
            f"</div>"
        )
    return "".join(items)


def _build_callback_tracker_html(log: list, esc) -> str:
    if not log:
        return ""
    rows = ""
    for cb in log:
        if not isinstance(cb, dict):
            continue
        is_breach = cb.get("status") == "Breached"
        row_bg = "#fef2f2" if is_breach else "#f0fdf4"
        s_color = "#ef4444" if is_breach else "#16a34a"
        s_icon = "&#10060;" if is_breach else "&#9989;"
        rows += (
            f"<tr style='background:{row_bg};'>"
            f"<td style='font-weight:600;'>{esc(cb.get('agent',''))}</td>"
            f"<td style='font-size:10px;'>{esc(cb.get('promise',''))}</td>"
            f"<td style='text-align:center;'>{esc(cb.get('promised_timeframe',''))}</td>"
            f"<td style='font-size:10px;'>{esc(cb.get('actual_followup',''))}</td>"
            f"<td style='text-align:center;color:{s_color};font-weight:700;'>{s_icon} {esc(cb.get('status',''))}</td>"
            f"</tr>"
        )
    return (
        "<table><tr><th>Agent</th><th>Promise</th><th>Timeframe</th>"
        "<th>Actual Follow-up</th><th>Status</th></tr>"
        + rows + "</table>"
    )


def _build_env_checklist_html(ai: dict, esc) -> str:
    if not ai.get("complex_environment"):
        return ""
    env = ai.get("environment_checklist", {})
    if not env:
        return ""
    checks = [
        ("Node Inventory", "node_inventory", "node_inventory_detail"),
        ("RSSI / Signal Check", "rssi_signal_check", "rssi_detail"),
        ("Wired Bypass Test", "wired_bypass_test", "wired_bypass_detail"),
        ("Topology Reconciliation", "topology_reconciliation", "topology_detail"),
        ("Firmware Consistency", "firmware_consistency", "firmware_detail"),
    ]
    items = []
    for label, key, detail_key in checks:
        status = env.get(key, "Not Applicable")
        detail = env.get(detail_key, "")
        if status == "Done":
            icon, bg, border = "&#9989;", "#f0fdf4", "#16a34a"
        elif status == "Skipped":
            icon, bg, border = "&#10060;", "#fef2f2", "#ef4444"
        else:
            icon, bg, border = "&#11036;", "#f8fafc", "#d1d5db"
        items.append(
            f"<div style='background:{bg};border-left:4px solid {border};border-radius:0 6px 6px 0;padding:8px 14px;margin:4px 0;'>"
            f"<p style='margin:0;font-size:12px;'>{icon} <strong>{esc(label)}:</strong> <span style='color:#6b7280;font-size:11px;margin-left:6px;'>{esc(status)}</span></p>"
            f"<p style='margin:2px 0 0;font-size:10px;color:#4b5563;'>{esc(detail)}</p>"
            f"</div>"
        )
    return "".join(items)


# ── HTML / PDF report ─────────────────────────────────────────────────────────
def build_html_report(ticket: dict, ai: dict, ticket_url: str, contact: dict) -> str:
    def esc(x):  # escape all dynamic content before it reaches the page (non-str safe)
        return html.escape(str(x))
    user         = ticket.get("user", {})
    category_raw = ticket.get("category", {}).get("name", "—") if ticket.get("category") else "—"
    is_refund    = "refund" in category_raw.lower()
    cat2_label   = "Refund Reason" if is_refund else "Problem Category 2"

    subject      = esc(ticket["subject"])
    subject_en   = ai.get("subject_english")
    if not ai.get("subject_is_english", True) and subject_en:
        subject_display = f"{subject}<br><span style='color:#888;font-size:11px;font-weight:400;'>🇬🇧 {esc(subject_en)}</span>"
    else:
        subject_display = subject

    timeline_rows = "".join(
        f"<tr><td>{esc(e.get('date',''))}</td><td>{esc(e.get('author',''))}</td><td>{esc(e.get('summary',''))}</td></tr>"
        for e in ai.get("timeline", [])
    )
    steps_html  = "".join(f"<li>{esc(s)}</li>" for s in ai.get("next_steps", []))
    opps_html_r = "".join(f"<li>{esc(o)}</li>" for o in ai.get("opportunities", [])) or "<li>No issues identified.</li>"

    # Build session-grouped actions block for the HTML report
    def _build_actions_block(ticket_obj):
        sessions = build_session_groups(ticket_obj)
        if not sessions:
            return "<p>No update data available.</p>"
        _flag_styles = {
            "close": ("background:#fef2f2;color:#991b1b;", "⚠️ Premature close"),
            "reopen": ("background:#fffbea;color:#854f0b;", "🔄 Reopened"),
            "escalation": ("background:#fff7ed;color:#9a3412;", "⬆️ Escalated"),
            "frustration": ("background:#fffbea;color:#854f0b;", "😤 Frustrated"),
            "callback_promise": ("background:#eef3fb;color:#1B3A6B;", "📞 Callback promised"),
        }
        _dot_c = {"agent": "#378ADD", "customer": "#1D9E75", "system": "#B4B2A9"}
        _nm_c = {"agent": "#185FA5", "customer": "#0F6E56", "system": "#9ca3af"}
        parts = []
        parts.append(
            "<div style='display:flex;gap:16px;margin-bottom:10px;padding:6px 10px;"
            "background:#f8fafc;border-radius:4px;font-size:10px;color:#6b7280;flex-wrap:wrap;'>"
            "<span>● Agent</span> <span style='color:#1D9E75;'>● Customer</span> "
            "<span style='color:#B4B2A9;'>● System</span>"
            "</div>"
        )
        for sess in sessions:
            parts.append(
                f"<div style='margin:12px 0 6px;padding-bottom:4px;border-bottom:1px solid #e5e7eb;'>"
                f"<span style='background:#f1f5f9;color:#6b7280;font-size:10px;font-weight:600;"
                f"padding:2px 6px;border-radius:3px;'>Session {sess['num']}</span> "
                f"<span style='font-weight:600;font-size:12px;color:#1B3A6B;'>{esc(sess['title'])}</span>"
                f"<span style='font-size:10px;color:#9ca3af;float:right;'>{esc(sess['date_range'])}</span>"
                f"</div>"
            )
            for e in sess["entries"]:
                dc = _dot_c.get(e["actor"], "#B4B2A9")
                nc = _nm_c.get(e["actor"], "#6b7280")
                ns = "font-style:italic;" if e["actor"] == "system" else ""
                flags_h = ""
                for ft, fl in e.get("flags", []):
                    fs, fl_label = _flag_styles.get(ft, ("background:#f8fafc;color:#6b7280;", fl))
                    flags_h += f" <span style='{fs}font-size:9px;font-weight:700;padding:1px 5px;border-radius:3px;'>{fl_label}</span>"
                parts.append(
                    f"<div style='display:flex;gap:8px;padding:4px 0;border-bottom:1px solid #f8fafc;"
                    f"align-items:flex-start;font-size:11px;'>"
                    f"<span style='display:inline-block;width:7px;height:7px;border-radius:50%;"
                    f"background:{dc};margin-top:4px;flex-shrink:0;'></span>"
                    f"<span style='font-weight:600;min-width:110px;flex-shrink:0;color:{nc};{ns}'>"
                    f"{esc(e['name'])}</span>"
                    f"<span style='flex:1;color:#374151;line-height:1.5;'>"
                    f"{esc(e['action'])}{flags_h}</span>"
                    f"</div>"
                )
        return "".join(parts)

    actions_block = _build_actions_block(ticket)

    phone   = esc(v(user.get("phone") or user.get("phone_number") or ai.get("customer_phone")))
    country = esc(v(user.get("country") or user.get("country_code") or ai.get("customer_country")))

    # Device fields — AI first, then custom fields fallback
    model   = esc(v(ai.get("model_number")   or get_custom_field(ticket, "model", "product")))
    serial  = esc(v(ai.get("serial_number")  or get_custom_field(ticket, "serial")))
    w_start = esc(v(ai.get("warranty_start_date") or get_custom_field(ticket, "warranty start", "warranty date")))
    w_stat  = esc(v(ai.get("warranty_status") or get_custom_field(ticket, "warranty status")))

    # Pre-build dynamic sections
    agents_section  = _build_agents_html(ticket, ai.get("agents_involved", []))
    verdict_section = _build_verdict_html(ticket, ai)
    gap_section     = _build_gap_analysis(ticket)
    assignee_name   = esc(v((ticket.get("assigned_to") or ticket.get("agent") or {}).get("name")))
    category_disp   = esc(category_raw)

    def _det(title, inner, icon=""):
        """Wrap content in a collapsible <details> panel."""
        if not inner or not inner.strip():
            return ""
        return (
            f"<details><summary>{icon} {title}</summary>"
            f"<div class='det-body'>{inner}</div>"
            f"</details>"
        )

    # Strip h2 wrappers from helpers so we can re-wrap in <details>
    import re as _re
    def _strip_h2(html_str):
        """Remove the leading <h2>...</h2> from a helper's HTML, return (title, rest)."""
        m = _re.match(r'\s*<h2[^>]*>(.*?)</h2>(.*)', html_str, _re.DOTALL | _re.IGNORECASE)
        if m:
            return m.group(1).strip(), m.group(2).strip()
        return "", html_str.strip()

    _dash_html   = _build_dashboard_html(ai.get('timeline', []))
    _tline_inner = _build_dot_timeline_html(ai.get('timeline', []))

    _agents_title, _agents_inner = _strip_h2(agents_section)
    _gap_title,    _gap_inner    = _strip_h2(gap_section)
    _verdict_title, _verdict_inner = _strip_h2(verdict_section)

    # Dashboard: strip its outer section title if present
    _dash_title, _dash_inner = _strip_h2(_dash_html)
    if not _dash_title:
        _dash_title = "Case Snapshot"
        _dash_inner = _dash_html
    # Prepend KPI stat cards to the dashboard
    _dash_inner = _build_dashboard_kpis_html(ai, ai.get('timeline', []), esc) + _dash_inner

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Ticket #{ticket['id']} — Happy Link Claude TLDR</title>
<script src="https://cdn.plot.ly/plotly-2.32.0.min.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Segoe UI', Arial, sans-serif; font-size: 12px; color: #1a1a2e; background: #f4f6f9; padding: 40px 50px; }}
  .header {{ background: #1B3A6B; color: white; padding: 20px 28px; border-radius: 6px; margin-bottom: 6px; }}
  .header h1 {{ font-size: 20px; letter-spacing: .5px; margin-bottom: 2px; }}
  .header p  {{ font-size: 11px; opacity: .8; }}
  .confidential {{ text-align: right; font-size: 10px; color: #888; margin-bottom: 14px; font-style: italic; }}
  .subject-box {{ font-size: 17px; font-weight: 700; color: #1B3A6B; margin: 0 0 4px 0; padding-bottom: 6px; }}
  .ticket-url {{ font-size: 11px; color: #6b7280; margin: 0 0 18px 0; padding-bottom: 14px; border-bottom: 2px solid #e5e7eb; }}
  .ticket-url a {{ color: #1B3A6B; text-decoration: none; }}
  h2.section {{ font-size: 13px; color: #1B3A6B; border-bottom: 2px solid #1B3A6B; padding-bottom: 4px;
    margin: 18px 0 10px; text-transform: uppercase; letter-spacing: .08em; }}
  .problem-box {{ background: white; border-left: 4px solid #1B3A6B; padding: 12px 16px;
    border-radius: 0 6px 6px 0; font-size: 13px; line-height: 1.7; margin-bottom: 10px;
    box-shadow: 0 1px 3px rgba(0,0,0,.06); }}
  .summary-box {{ background: #fffbea; border-left: 4px solid #f59e0b; padding: 10px 14px;
    border-radius: 0 6px 6px 0; font-size: 12px; line-height: 1.6; margin: 0 0 10px 0; }}
  .insights-box {{ background: #fffbea; border-left: 4px solid #f59e0b; padding: 10px 14px;
    border-radius: 0 6px 6px 0; font-size: 12px; line-height: 1.6; margin: 8px 0; }}
  .label {{ font-weight: 700; color: #1B3A6B; }}
  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 5px 30px; margin: 4px 0; }}
  .grid div {{ font-size: 12px; }}
  ul {{ padding-left: 16px; margin: 6px 0; line-height: 1.8; }}
  .insight {{ background: #fffbea; border-left: 4px solid #f59e0b; padding: 8px 12px;
    border-radius: 0 4px 4px 0; font-size: 11px; margin-top: 8px; line-height: 1.5; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 6px; }}
  th, td {{ border: 1px solid #d1d5db; padding: 6px 10px; text-align: left; font-size: 11px; }}
  th {{ background: #1B3A6B; color: white; font-weight: 600; font-size: 10px; text-transform: uppercase; }}
  tr:nth-child(even) {{ background: #f8fafc; }}
  /* ── Collapsible panels ── */
  details {{ background: white; border: 1px solid #e5e7eb; border-radius: 8px;
    margin: 6px 0; box-shadow: 0 1px 3px rgba(0,0,0,.05); overflow: hidden; }}
  details > summary {{ font-size: 12px; font-weight: 700; color: #1B3A6B;
    padding: 11px 16px; cursor: pointer; text-transform: uppercase;
    letter-spacing: .07em; background: #f8fafc; display: flex;
    align-items: center; justify-content: space-between; list-style: none;
    user-select: none; }}
  details > summary::-webkit-details-marker {{ display: none; }}
  details > summary::after {{ content: "▾"; font-size: 14px; color: #9ca3af; font-weight: 400; }}
  details[open] > summary {{ border-bottom: 1px solid #e5e7eb; }}
  details[open] > summary::after {{ content: "▴"; }}
  .det-body {{ padding: 12px 16px; }}
  .det-body h2 {{ display: none; }}
  .footer {{ margin-top: 30px; padding-top: 10px; border-top: 1px solid #d1d5db;
    font-size: 10px; color: #9ca3af; display: flex; justify-content: space-between; }}
  a {{ color: #1B3A6B; }}
  @media print {{
    body {{ padding: 20px 30px; background: white; }}
    .header, th {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
    details {{ display: block; }}
    details > summary::after {{ display: none; }}
  }}
</style>
</head><body>

<div class="header">
  <h1>🔗 Happy Link Claude TLDR Ticket Summary</h1>
  <p>AI-powered support ticket analysis — generated {date.today()}</p>
</div>
<div class="confidential">Confidential &nbsp;|&nbsp; Internal Use Only</div>

<div class="subject-box">{subject_display}</div>
<p class="ticket-url">🔗 <a href="{ticket_url}">{ticket_url}</a></p>

{_det("📋 Ticket Overview",
  f"<div class='grid'>"
  f"<div><span class='label'>Ticket #:</span> {ticket['id']}</div>"
  f"<div><span class='label'>Status:</span> {esc(v(ticket['status']['name']))}</div>"
  f"<div><span class='label'>Category:</span> {category_disp}</div>"
  f"<div><span class='label'>Assignee:</span> {assignee_name}</div>"
  f"<div><span class='label'>Updates:</span> {len(ticket.get('updates', []))}</div>"
  f"<div><span class='label'>Time Spent:</span> {esc(v(ticket.get('time_spent')))} min</div>"
  f"</div>", "")}

{_det("👤 Customer Details",
  f"<div class='grid'>"
  f"<div><span class='label'>Name:</span> {esc(v(user.get('name')))}</div>"
  f"<div><span class='label'>Email:</span> {esc(v(user.get('email')))}</div>"
  f"<div><span class='label'>Phone:</span> {phone}</div>"
  f"<div><span class='label'>Country:</span> {country}</div>"
  f"</div>", "")}

{_det("🖥️ Device Details",
  f"<div class='grid'>"
  f"<div><span class='label'>Problem Category 1:</span> {esc(v(ai.get('problem_category_1')))}</div>"
  f"<div><span class='label'>{cat2_label}:</span> {esc(v(ai.get('problem_category_2')))}</div>"
  f"<div><span class='label'>Model:</span> {model}</div>"
  f"<div><span class='label'>Serial:</span> {serial}</div>"
  f"<div><span class='label'>Warranty Start:</span> {w_start}</div>"
  f"<div><span class='label'>Warranty Status:</span> {w_stat}</div>"
  f"</div>", "")}

<h2 class="section">The Problem</h2>
<div class="problem-box">{esc(v(ai.get('problem_summary')))}</div>

<h2 class="section">Summary</h2>
<div class="summary-box">{esc(v(ai.get('actions_summary')))}</div>

{_det("📋 Actions Taken (" + str(len(ai.get('actions_log', []))) + ")",
  _build_actions_timeline_html(ai.get('actions_log', []), esc), "")
  if ai.get('actions_log') else ""}

{_build_qa_badges_html(ai, esc)}

{_det("📊 Agent Scorecard", _build_scorecard_html(ai.get('agents_involved', []), esc), "")}

{_det("🔧 Technical QA Findings (" + str(len(ai.get('technical_findings', []))) + ")",
  "<ul style='line-height:1.9;'>" + "".join(f"<li style='color:#991b1b;'>{esc(f)}</li>" for f in ai.get('technical_findings', [])) + "</ul>"
  if ai.get('technical_findings') else "", "")}

{_det("💬 Soft Skill Findings (" + str(len(ai.get('soft_skill_findings', []))) + ")",
  "<ul style='line-height:1.9;'>" + "".join(f"<li style='color:#92400e;'>{esc(f)}</li>" for f in ai.get('soft_skill_findings', [])) + "</ul>"
  if ai.get('soft_skill_findings') else "", "")}

{_det("🎓 Coaching Notes (" + str(len(ai.get('coaching_notes', []))) + ")",
  _build_coaching_notes_html(ai.get('coaching_notes', []), esc), "")}

{_det("📸 Case Snapshot", _dash_inner, "")}

{_det("📋 What Happened?", actions_block, "")}

<h2 class="section">Insights</h2>
<div class="insights-box">{esc(v(ai.get('actions_insights')))}</div>

{_det("🎯 What Happened?", "<ul>" + opps_html_r + "</ul>", "")}

{_det("✅ Recommended Next Steps", "<ul>" + steps_html + "</ul>", "")}

{_det("🚫 Premature Closes (" + str(len(ai.get('premature_closes', []))) + ")",
  _build_premature_closes_html(ai.get('premature_closes', []), esc), "")}

{_det("📞 Callback Tracker",
  _build_callback_tracker_html(ai.get('callback_promise_log', []), esc), "")}

{_det("🏠 Complex Environment Checklist",
  _build_env_checklist_html(ai, esc), "")}

{_det(_agents_title or "👥 Agents Involved", _agents_inner, "")}

{_det(_gap_title or "⏱️ Dead Zones — Activity Gaps", _gap_inner, "")}

{_det(_verdict_title or "🚨 Case Verdict", _verdict_inner, "")}

{_det("📅 Timeline (" + str(len(ai.get('timeline', []))) + " entries)",
  _tline_inner + "<table><tr><th>Date</th><th>Persona</th><th>Summary</th></tr>" + timeline_rows + "</table>", "")}

<div class="footer">
  <span>Happy Link Claude TLDR &nbsp;|&nbsp; Ticket #{ticket['id']}</span>
  <span>Generated: {date.today()} &nbsp;|&nbsp; Confidential</span>
</div>
</body></html>"""


# ── UI ────────────────────────────────────────────────────────────────────────
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
router_img = PILImage.open(os.path.join(_APP_DIR, "router_nobg.png"))
w, h = router_img.size
router_small = router_img.resize((w // 2, h // 2))

col_l, col_c, col_r = st.columns([2, 1, 2])
with col_c:
    st.image(router_small)

st.markdown(
    "<h1 style='text-align:center;color:#1B3A6B;font-size:2.8rem;margin-top:4px;'>"
    "Happy Link Claude TLDR Ticket Summary</h1>",
    unsafe_allow_html=True,
)
st.markdown(
    "<p style='text-align:center;color:#6b7280;font-size:13px;margin-top:-12px;'>"
    "AI-powered HappyFox ticket analysis — Problem · Actions · Next Steps · Timeline</p>",
    unsafe_allow_html=True,
)

st.write("")
st.markdown("""
<style>
  /* Centre the text-input and button in the middle column */
  div[data-testid="stTextInput"] input { text-align: center; }
  div[data-testid="stTextInput"] small { display:block; text-align:center; }
  div[data-testid="stButton"] { display:flex; justify-content:center; }
  div[data-testid="stButton"] button { min-width: 220px; }
</style>
""", unsafe_allow_html=True)

_, mid_col, _ = st.columns([1, 1, 1])
with mid_col:
    ticket_input = st.text_input("Ticket number", placeholder="e.g. 56178", label_visibility="collapsed")
    investigate  = st.button("🔍 Investigate Ticket", type="primary", use_container_width=True)

if investigate:
    ticket_id = ticket_input.strip()
    if not ticket_id:
        st.warning("Please enter a ticket number first.")
        st.stop()

    # ── Check database first ──────────────────────────────────────────────────
    if _PG_AVAILABLE:
        saved = find_report_in_db(ticket_id)
        if saved and not st.session_state.get("force_reanalyse"):
            saved_at = saved["saved_at"].strftime("%d %b %Y %H:%M") if saved["saved_at"] else "—"
            st.session_state["cached_report"] = saved
            st.session_state["ticket_id"]     = ticket_id
            st.rerun()

    # ── Not in DB — fetch from HappyFox and analyse ───────────────────────────
    st.session_state.pop("force_reanalyse", None)
    _, _sc, _ = st.columns([1, 2, 1])
    with _sc:
        with st.spinner("Fetching ticket from HappyFox…"):
            try:
                ticket = fetch_ticket(ticket_id)
            except Exception as exc:
                if "404" in str(exc):
                    st.markdown(
                        "<div style='text-align:center;max-width:560px;margin:12px auto;padding:14px 18px;"
                        "background:#fef2f2;border:1px solid #fca5a5;border-radius:6px;font-size:13px;color:#991b1b;'>"
                        "🔍 <b>Ticket not found.</b> Use only the number at the end of the URL<br>"
                        "<span style='font-family:monospace;font-size:12px;'>.../ticket/<b>129157</b></span>"
                        "</div>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f"<div style='text-align:center;max-width:560px;margin:12px auto;padding:14px 18px;"
                        f"background:#fef2f2;border:1px solid #fca5a5;border-radius:6px;font-size:13px;color:#991b1b;'>"
                        f"⚠️ {type(exc).__name__}: {exc}</div>",
                        unsafe_allow_html=True,
                    )
                st.stop()

    ticket_text = build_ticket_text(ticket)
    with st.spinner("Analysing with Claude…"):
        try:
            ai, input_tokens, output_tokens = analyse_ticket(ticket_text)
        except Exception as exc:
            st.error(f"AI analysis failed: {exc}")
            st.stop()

    st.session_state["ticket"]    = ticket
    st.session_state["ai"]        = ai
    st.session_state["ticket_id"] = ticket_id
    st.session_state["tokens"]    = (input_tokens, output_tokens)
    st.session_state.pop("cached_report", None)

    # Auto-save to database
    if _PG_AVAILABLE:
        try:
            _html = build_html_report(ticket, ai, f"{PORTAL_BASE}/staff/ticket/{ticket_id}", ticket.get("user", {}))
            init_db()
            save_report_to_db(ticket, ai, _html, input_tokens, output_tokens, len(ticket.get("updates", [])))
            st.toast(f"Report for ticket #{ticket_id} saved to database.", icon="💾")
        except Exception as exc:
            logger.warning("Auto-save to DB failed for ticket %s: %s", ticket_id, exc)
            st.toast(f"Could not save report to database: {exc}", icon="⚠️")

# ── Show cached report from DB if available ───────────────────────────────────
if "cached_report" in st.session_state and "ticket" not in st.session_state:
    cached   = st.session_state["cached_report"]
    saved_at = cached["saved_at"].strftime("%d %b %Y %H:%M") if cached["saved_at"] else "—"

    st.divider()
    st.markdown(
        f"<div style='background:#eef3fb;border-left:4px solid #1B3A6B;border-radius:0 6px 6px 0;"
        f"padding:14px 18px;margin:8px 0;font-size:13px;'>"
        f"<span style='font-weight:700;color:#1B3A6B;'>📂 Found in database</span> — "
        f"Ticket <b>#{cached['ticket_id']}</b> was saved on <b>{saved_at}</b>.<br>"
        f"<span style='color:#6b7280;'>Opening the saved report. Click <b>Re-analyse</b> to fetch a fresh copy from HappyFox.</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

    st.markdown("""
    <style>
    div[data-testid="stDownloadButton"] button {
        background: #1B3A6B !important;
        color: white !important;
        border: none !important;
        font-weight: 600 !important;
    }
    </style>
    """, unsafe_allow_html=True)

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        if st.button("📄 Open Report", use_container_width=True):
            if cached.get("ai_json"):
                try:
                    _ai = json.loads(cached["ai_json"])
                    _ticket_url = f"{PORTAL_BASE}/staff/ticket/{cached['ticket_id']}"
                    # Fetch real ticket from HappyFox to get actual updates for actions list
                    with st.spinner("Loading ticket data…"):
                        try:
                            _real_ticket = fetch_ticket(cached["ticket_id"])
                        except Exception:
                            _real_ticket = None
                    if _real_ticket:
                        _fresh_html = build_html_report(_real_ticket, _ai, _ticket_url, _real_ticket.get("user", {}))
                    else:
                        # Fallback: fake ticket (actions list will be empty but rest renders fine)
                        _fake_ticket = {
                            "id": cached["ticket_id"],
                            "subject": cached["subject"] or "",
                            "status": {"name": cached["status"] or "—"},
                            "category": {"name": cached["category"]} if cached.get("category") else None,
                            "updates": [],
                            "user": {},
                            "assigned_to": {"name": cached["assignee"]} if cached.get("assignee") else None,
                        }
                        _fresh_html = build_html_report(_fake_ticket, _ai, _ticket_url, {})
                    # Update the stored HTML so download also gets the fresh version
                    st.session_state["_cached_fresh_html"] = _fresh_html
                    st.session_state["_open_html"] = _fresh_html
                except Exception:
                    st.session_state["_open_html"] = cached["html_report"]
            else:
                st.session_state["_open_html"] = cached["html_report"]
    with col_b:
        # Use freshly generated HTML if available, else fall back to stored version
        _dl_html = st.session_state.get("_cached_fresh_html", cached["html_report"])
        clicked_dl = st.download_button(
            label="⬇️ Download Report",
            data=_dl_html.encode("utf-8"),
            file_name=f"ticket_{cached['ticket_id']}_TLDR.html",
            mime="text/html",
            use_container_width=True,
        )
        if clicked_dl:
            st.toast("Report downloaded successfully.", icon="⬇️")
    with col_c:
        if st.button("🔄 Re-analyse (fetch fresh from HappyFox)", use_container_width=True):
            st.session_state["force_reanalyse"] = True
            st.session_state.pop("cached_report", None)
            st.rerun()

    # Open saved report from DB in a new browser tab via JS
    if "_open_html" in st.session_state:
        html_content = st.session_state.pop("_open_html")
        _open_in_new_tab(html_content)

    st.markdown(
        f"<p style='font-size:13px;margin:6px 0;'>"
        f"<b>Subject:</b> {html.escape(cached['subject'] or '—')} &nbsp;·&nbsp; "
        f"<b>Status:</b> {html.escape(cached['status'] or '—')} &nbsp;·&nbsp; "
        f"<b>Category:</b> {html.escape(cached['category'] or '—')} &nbsp;·&nbsp; "
        f"<b>Assignee:</b> {html.escape(cached['assignee'] or '—')}</p>",
        unsafe_allow_html=True,
    )
    if cached["problem_summary"]:
        st.markdown(
            f"<div style='background:#eef3fb;border-left:4px solid #1B3A6B;border-radius:0 4px 4px 0;"
            f"padding:10px 14px;margin:8px 0;font-size:13px;'>{html.escape(cached['problem_summary'])}</div>",
            unsafe_allow_html=True,
        )
    st.stop()

# Render results from session state (persists across Save/Download clicks)
if "ticket" in st.session_state and not investigate:
    ticket    = st.session_state["ticket"]
    ai        = st.session_state["ai"]
    ticket_id = st.session_state["ticket_id"]

if "ticket" in st.session_state:
    ticket_url   = f"{PORTAL_BASE}/staff/ticket/{ticket_id}"
    category_raw = ticket.get("category", {}).get("name", "—") if ticket.get("category") else "—"
    is_refund    = "refund" in category_raw.lower()
    cat2_label   = "Refund Reason" if is_refund else "Problem Category 2"
    user         = ticket.get("user", {})

    # Device values — AI first, then custom fields
    model   = v(ai.get("model_number")        or get_custom_field(ticket, "model", "product"))
    serial  = v(ai.get("serial_number")       or get_custom_field(ticket, "serial"))
    w_start = v(ai.get("warranty_start_date") or get_custom_field(ticket, "warranty start", "warranty date"))
    w_stat  = v(ai.get("warranty_status")     or get_custom_field(ticket, "warranty status"))

    # Phone / country — HappyFox user first, then AI extraction from thread
    phone   = v(user.get("phone") or user.get("phone_number") or ai.get("customer_phone"))
    country = v(user.get("country") or user.get("country_code") or ai.get("customer_country"))

    # ── Subject + URL (shown directly) ───────────────────────────────────────
    st.divider()
    subject    = ticket["subject"]
    subject_en = ai.get("subject_english")
    assignee   = v((ticket.get("assigned_to") or ticket.get("agent") or {}).get("name"))

    st.markdown(
        f"<p style='text-align:center;margin-bottom:2px;font-size:1.3rem;font-weight:700;color:#1B3A6B;'>{html.escape(str(subject))}</p>",
        unsafe_allow_html=True,
    )
    if not ai.get("subject_is_english", True) and subject_en:
        st.markdown(
            f"<p style='text-align:center;font-size:12px;color:#6b7280;font-style:italic;margin-top:0;'>EN: {html.escape(str(subject_en))}</p>",
            unsafe_allow_html=True,
        )
    st.markdown(
        f"<p style='text-align:center;'><a href='{ticket_url}' target='_blank' "
        f"style='font-size:12px;color:#1B3A6B;text-decoration:none;'>"
        f"🔗 {ticket_url}</a></p>",
        unsafe_allow_html=True,
    )
    st.divider()

    # ── Ticket Overview metrics ───────────────────────────────────────────────
    def kv(label, value):
        """Bold label + plain value row matching Customer Details style."""
        return (
            f"<p style='margin:8px 0;font-size:13px;'>"
            f"<span style='font-weight:700;color:#1B3A6B;'>{label}:</span> "
            f"<span style='color:#1a1a2e;'>{html.escape(str(value))}</span></p>"
        )

    def calc_duration(ticket):
        # Use HappyFox native time_spent field (stored in minutes)
        raw = ticket.get("time_spent") or ticket.get("timespent") or ticket.get("time_spent_in_minutes")
        try:
            minutes = int(raw)
            if minutes > 0:
                h, m = divmod(minutes, 60)
                if h and m:
                    return f"{h}h {m}m"
                elif h:
                    return f"{h}h"
                else:
                    return f"{m}m"
        except (TypeError, ValueError):
            pass
        return "—"

    duration = calc_duration(ticket)

    with st.expander("📋 Ticket Overview", expanded=True):
        col_l, col_r = st.columns(2)
        with col_l:
            st.markdown(kv("Ticket",   f"#{ticket['id']}"),             unsafe_allow_html=True)
            st.markdown(kv("Status",   ticket["status"]["name"]),       unsafe_allow_html=True)
            st.markdown(kv("Assignee", assignee),                       unsafe_allow_html=True)
        with col_r:
            st.markdown(kv("Category", category_raw),                   unsafe_allow_html=True)
            st.markdown(kv("Updates",  len(ticket.get("updates", []))), unsafe_allow_html=True)
            st.markdown(kv("Time Spent", duration),                     unsafe_allow_html=True)


    # ── Customer Details ──────────────────────────────────────────────────────
    with st.expander("👤 Customer Details", expanded=True):
        col_l, col_r = st.columns(2)
        with col_l:
            st.markdown(kv("Name",  v(user.get('name'))),  unsafe_allow_html=True)
            st.markdown(kv("Phone", phone),                unsafe_allow_html=True)
        with col_r:
            st.markdown(kv("Email",   v(user.get('email'))), unsafe_allow_html=True)
            st.markdown(kv("Country", country),              unsafe_allow_html=True)

    # ── Device Details ────────────────────────────────────────────────────────
    def detail_row(label, val):
        return (
            f"<p style='margin:6px 0;font-size:13px;'>"
            f"<span style='font-weight:700;color:#1B3A6B;'>{label}:</span> "
            f"<span style='color:#1a1a2e;'>{html.escape(str(val))}</span></p>"
        )

    with st.expander("🖥️ Device Details", expanded=False):
        col_l, col_r = st.columns(2)
        with col_l:
            st.markdown(detail_row("Model Number", model), unsafe_allow_html=True)
            st.markdown(detail_row("Warranty Start Date", w_start), unsafe_allow_html=True)
            st.markdown(detail_row("Problem Category 1", v(ai.get("problem_category_1"))), unsafe_allow_html=True)
        with col_r:
            st.markdown(detail_row("Serial Number", serial), unsafe_allow_html=True)
            st.markdown(detail_row("Warranty Status", w_stat), unsafe_allow_html=True)
            st.markdown(detail_row(cat2_label, v(ai.get("problem_category_2"))), unsafe_allow_html=True)

    # ── Timeline data (shared by dashboard and timeline charts) ──────────────────
    timeline = ai.get("timeline", [])

    # ── The Problem ───────────────────────────────────────────────────────────
    st.markdown(
        "<p style='font-size:0.95rem;font-weight:700;color:#1B3A6B;"
        "border-bottom:2px solid #1B3A6B;padding-bottom:4px;margin-bottom:6px;'>"
        "The Problem</p>",
        unsafe_allow_html=True,
    )
    st.info(ai.get("problem_summary", "—"))

    # ── Summary (visible, yellow) ─────────────────────────────────────────────
    st.markdown(
        f"""<div style="background:#fffbea;border-left:4px solid #f59e0b;
                        border-radius:0 6px 6px 0;padding:12px 16px;margin:8px 0 10px;">
            <p style="margin:0;font-size:13px;">
                <span style="font-weight:700;color:#92400e;">📊 Summary:</span>&nbsp;
                {html.escape(v(ai.get('actions_summary')))}
            </p>
        </div>""",
        unsafe_allow_html=True,
    )

    # ── Insights (visible, blue) ──────────────────────────────────────────────
    st.markdown(
        f"""<div style="background:#eff6ff;border-left:4px solid #3b82f6;
                        border-radius:0 6px 6px 0;padding:12px 16px;margin:8px 0 10px;">
            <p style="margin:0;font-size:13px;">
                <span style="font-weight:700;color:#1e40af;">💡 Insights:</span>&nbsp;
                {html.escape(v(ai.get('actions_insights')))}
            </p>
        </div>""",
        unsafe_allow_html=True,
    )

    # ══════════════════════════════════════════════════════════════════════════
    #  TWO-TAB LAYOUT: Details | Deep Dive
    # ══════════════════════════════════════════════════════════════════════════
    _tab_summary, _tab_deep = st.tabs(["📄 Details", "🔍 Deep Dive"])

    with _tab_summary:

        # ── Executive Interaction Dashboard ──────────────────────────────────
        _figs = build_interaction_figures(timeline)
        if _figs:
            try:
                _fig_sk, _fig_pie, _fig_sw = _figs
                _plotly_cfg = {"displayModeBar": False, "responsive": True}
                with st.expander("📸 Case Snapshot", expanded=False):
                    st.markdown(_build_dashboard_kpis_html(ai, timeline), unsafe_allow_html=True)
                    _sw_l, _sw_c, _sw_r = st.columns([1, 8, 1])
                    with _sw_c:
                        st.plotly_chart(_fig_sw, use_container_width=True, config=_plotly_cfg)
            except Exception as _exc:
                st.error(f"Dashboard error: {_exc}")

        # ── Actions Taken (collapsed, grouped by date) ───────────────────────
        _actions_log = ai.get("actions_log", [])
        if _actions_log:
            with st.expander(f"📋 Actions Taken ({len(_actions_log)} entries)", expanded=False):
                st.markdown(_build_actions_timeline_html(_actions_log), unsafe_allow_html=True)

        # ── What Happened? (formerly Opportunities) ──────────────────────────
        opps = ai.get("opportunities", [])
        with st.expander(f"🎯 What Happened? ({len(opps)})", expanded=False):
            if opps:
                for o in opps:
                    st.markdown(
                        f"<p style='margin:4px 0;font-size:13px;color:#7f1d1d;'>• {html.escape(str(o))}</p>",
                        unsafe_allow_html=True,
                    )
            else:
                st.markdown(
                    "<p style='margin:0;font-size:13px;color:#6b7280;'>No issues identified.</p>",
                    unsafe_allow_html=True,
                )

        # ── Recommended Next Steps ───────────────────────────────────────────
        with st.expander("✅ Recommended Next Steps", expanded=False):
            for s in ai.get("next_steps", []):
                st.markdown(f"- {s}")

    with _tab_deep:

        # ── Resolution & Efficiency badges ───────────────────────────────────
        _res_q = ai.get("resolution_quality", "")
        _eff_r = ai.get("efficiency_rating", "")
        _root_c = ai.get("root_cause", "")
        if _res_q or _eff_r or _root_c:
            with st.expander("🏁 Resolution · Efficiency · Root Cause", expanded=False):
                _badge_cols = st.columns(3)
                with _badge_cols[0]:
                    if _res_q:
                        _rq_colors = {"Verified Fix": "#16a34a", "Assumed Fix": "#f59e0b", "Workaround": "#f59e0b", "Unresolved": "#ef4444", "Premature Close": "#ef4444"}
                        _rq_c = _rq_colors.get(_res_q.split(" —")[0].strip() if " —" in _res_q else _res_q.strip(), "#6b7280")
                        st.markdown(f"<div style='background:white;border:1px solid #e5e7eb;border-radius:8px;padding:10px 14px;'><p style='margin:0 0 2px;font-size:10px;color:#6b7280;text-transform:uppercase;letter-spacing:.07em;'>Resolution Quality</p><p style='margin:0;font-size:14px;font-weight:700;color:{_rq_c};'>{html.escape(_res_q)}</p></div>", unsafe_allow_html=True)
                with _badge_cols[1]:
                    if _eff_r:
                        _ef_c = "#16a34a" if _eff_r.startswith("Efficient") else ("#f59e0b" if _eff_r.startswith("Acceptable") else "#ef4444")
                        st.markdown(f"<div style='background:white;border:1px solid #e5e7eb;border-radius:8px;padding:10px 14px;'><p style='margin:0 0 2px;font-size:10px;color:#6b7280;text-transform:uppercase;letter-spacing:.07em;'>Efficiency</p><p style='margin:0;font-size:14px;font-weight:700;color:{_ef_c};'>{html.escape(_eff_r)}</p></div>", unsafe_allow_html=True)
                with _badge_cols[2]:
                    if _root_c:
                        st.markdown(f"<div style='background:white;border:1px solid #e5e7eb;border-radius:8px;padding:10px 14px;'><p style='margin:0 0 2px;font-size:10px;color:#6b7280;text-transform:uppercase;letter-spacing:.07em;'>Root Cause</p><p style='margin:0;font-size:14px;font-weight:700;color:#1B3A6B;'>{html.escape(_root_c)}</p></div>", unsafe_allow_html=True)

        # ── Agent Scorecard ──────────────────────────────────────────────────
        _agents_ai = ai.get("agents_involved", [])
        _has_scores = any(a.get("technical_score") is not None for a in _agents_ai)
        if _has_scores and _agents_ai:
            with st.expander("📊 Agent Scorecard", expanded=False):
                _sc_rows = ""
                for _a in _agents_ai:
                    _ts = _a.get("technical_score", "—")
                    _hs = _a.get("handling_score", "—")
                    _ts_color = "#ef4444" if isinstance(_ts, int) and _ts <= 2 else ("#f59e0b" if isinstance(_ts, int) and _ts == 3 else "#16a34a")
                    _hs_color = "#ef4444" if isinstance(_hs, int) and _hs <= 2 else ("#f59e0b" if isinstance(_hs, int) and _hs == 3 else "#16a34a")
                    _flags = _a.get("flags", [])
                    _flags_html = " ".join(f"<span style='background:#fef2f2;color:#991b1b;border-radius:3px;padding:1px 6px;font-size:10px;font-weight:700;'>{html.escape(str(f))}</span>" for f in _flags) if _flags else ""
                    _sc_rows += (
                        f"<tr>"
                        f"<td style='font-weight:600;'>{html.escape(str(_a.get('name', '')))}</td>"
                        f"<td>{html.escape(str(_a.get('role', '')))}</td>"
                        f"<td style='text-align:center;color:{_ts_color};font-weight:700;'>{_ts}/5</td>"
                        f"<td style='text-align:center;color:{_hs_color};font-weight:700;'>{_hs}/5</td>"
                        f"<td>{_flags_html}</td>"
                        f"<td style='font-size:11px;'>{html.escape(str(_a.get('summary', '')))}</td>"
                        f"</tr>"
                    )
                st.markdown(
                    f"""<table style='border-collapse:collapse;width:100%;font-family:Segoe UI,sans-serif;font-size:12px;'>
                    <thead><tr style='background:#1B3A6B;color:white;'>
                        <th style='padding:8px 10px;text-align:left;'>Agent</th>
                        <th style='padding:8px 10px;text-align:left;'>Role</th>
                        <th style='padding:8px 10px;text-align:center;'>Tech</th>
                        <th style='padding:8px 10px;text-align:center;'>Handling</th>
                        <th style='padding:8px 10px;text-align:left;'>Flags</th>
                        <th style='padding:8px 10px;text-align:left;'>Summary</th>
                    </tr></thead>
                    <tbody>{_sc_rows}</tbody></table>""",
                    unsafe_allow_html=True,
                )

        # ── Technical QA Findings ────────────────────────────────────────────
        _tech_f = ai.get("technical_findings", [])
        if _tech_f:
            with st.expander(f"🔧 Technical QA Findings ({len(_tech_f)})", expanded=False):
                for _tf in _tech_f:
                    st.markdown(f"<p style='margin:4px 0;font-size:13px;color:#991b1b;'>• {html.escape(str(_tf))}</p>", unsafe_allow_html=True)

        # ── Soft Skill Findings ──────────────────────────────────────────────
        _soft_f = ai.get("soft_skill_findings", [])
        if _soft_f:
            with st.expander(f"💬 Soft Skill Findings ({len(_soft_f)})", expanded=False):
                for _sf in _soft_f:
                    st.markdown(f"<p style='margin:4px 0;font-size:13px;color:#92400e;'>• {html.escape(str(_sf))}</p>", unsafe_allow_html=True)

        # ── Coaching Notes ───────────────────────────────────────────────────
        _coach = ai.get("coaching_notes", [])
        if _coach:
            with st.expander(f"🎓 Coaching Notes ({len(_coach)})", expanded=False):
                for _cn in _coach:
                    if not isinstance(_cn, dict):
                        continue
                    st.markdown(
                        f"""<div style='background:#f0fdf4;border-left:4px solid #16a34a;border-radius:0 6px 6px 0;padding:10px 14px;margin:6px 0;'>
                            <p style='margin:0 0 4px;font-size:12px;font-weight:700;color:#1B3A6B;'>{html.escape(str(_cn.get('agent', '')))}:</p>
                            <p style='margin:0 0 4px;font-size:12px;color:#991b1b;'><strong>Finding:</strong> {html.escape(str(_cn.get('finding', '')))}</p>
                            <p style='margin:0 0 4px;font-size:12px;color:#166534;'><strong>Correct Handling:</strong> {html.escape(str(_cn.get('correct_script', '')))}</p>
                            <p style='margin:0;font-size:11px;color:#6b7280;'><em>Policy: {html.escape(str(_cn.get('policy_ref', '')))}</em></p>
                        </div>""",
                        unsafe_allow_html=True,
                    )

        # ── Premature Close Detection ────────────────────────────────────────
        _pc_list = ai.get("premature_closes", [])
        if _pc_list:
            with st.expander(f"🚫 Premature Closes ({len(_pc_list)})", expanded=False):
                for _pc in _pc_list:
                    if not isinstance(_pc, dict):
                        continue
                    _pc_pattern = _pc.get("pattern", "")
                    _pc_bg = "#fef2f2" if "Observe" in _pc_pattern else "#fff7ed"
                    _pc_border = "#ef4444" if "Observe" in _pc_pattern else "#f97316"
                    st.markdown(
                        f"""<div style='background:{_pc_bg};border-left:4px solid {_pc_border};border-radius:0 6px 6px 0;padding:10px 14px;margin:6px 0;'>
                            <p style='margin:0 0 3px;font-size:12px;font-weight:700;color:#991b1b;'>
                                {html.escape(str(_pc.get('agent', '')))} — {html.escape(str(_pc.get('date', '')))}
                            </p>
                            <p style='margin:0 0 3px;font-size:12px;color:#7f1d1d;'>{html.escape(str(_pc.get('what_happened', '')))}</p>
                            <p style='margin:0;font-size:11px;color:#9a3412;font-style:italic;'>Pattern: {html.escape(_pc_pattern)}</p>
                        </div>""",
                        unsafe_allow_html=True,
                    )

        # ── Callback Breach Tracker ──────────────────────────────────────────
        _cb_log = ai.get("callback_promise_log", [])
        if _cb_log:
            _breached = [c for c in _cb_log if isinstance(c, dict) and c.get("status") == "Breached"]
            _honored = [c for c in _cb_log if isinstance(c, dict) and c.get("status") == "Honored"]
            _cb_label = f"📞 Callback Tracker ({len(_breached)} breached / {len(_honored)} honored)"
            with st.expander(_cb_label, expanded=False):
                _cb_rows = ""
                for _cb in _cb_log:
                    if not isinstance(_cb, dict):
                        continue
                    _is_breach = _cb.get("status") == "Breached"
                    _row_bg = "#fef2f2" if _is_breach else "#f0fdf4"
                    _status_color = "#ef4444" if _is_breach else "#16a34a"
                    _status_icon = "❌" if _is_breach else "✅"
                    _cb_rows += (
                        f"<tr style='background:{_row_bg};'>"
                        f"<td style='font-weight:600;'>{html.escape(str(_cb.get('agent', '')))}</td>"
                        f"<td style='font-size:11px;'>{html.escape(str(_cb.get('promise', '')))}</td>"
                        f"<td style='text-align:center;'>{html.escape(str(_cb.get('promised_timeframe', '')))}</td>"
                        f"<td style='font-size:11px;'>{html.escape(str(_cb.get('actual_followup', '')))}</td>"
                        f"<td style='text-align:center;color:{_status_color};font-weight:700;'>{_status_icon} {html.escape(str(_cb.get('status', '')))}</td>"
                        f"</tr>"
                    )
                st.markdown(
                    f"""<table style='border-collapse:collapse;width:100%;font-family:Segoe UI,sans-serif;font-size:12px;'>
                    <thead><tr style='background:#1B3A6B;color:white;'>
                        <th style='padding:8px 10px;text-align:left;'>Agent</th>
                        <th style='padding:8px 10px;text-align:left;'>Promise</th>
                        <th style='padding:8px 10px;text-align:center;'>Timeframe</th>
                        <th style='padding:8px 10px;text-align:left;'>Actual Follow-up</th>
                        <th style='padding:8px 10px;text-align:center;'>Status</th>
                    </tr></thead>
                    <tbody>{_cb_rows}</tbody></table>""",
                    unsafe_allow_html=True,
                )

        # ── Complex Environment Checklist ────────────────────────────────────
        _is_complex = ai.get("complex_environment", False)
        _env_check = ai.get("environment_checklist", {})
        if _is_complex and _env_check:
            with st.expander("🏠 Complex Environment Checklist", expanded=False):
                _check_items = [
                    ("Node Inventory", "node_inventory", "node_inventory_detail"),
                    ("RSSI / Signal Check", "rssi_signal_check", "rssi_detail"),
                    ("Wired Bypass Test", "wired_bypass_test", "wired_bypass_detail"),
                    ("Topology Reconciliation", "topology_reconciliation", "topology_detail"),
                    ("Firmware Consistency", "firmware_consistency", "firmware_detail"),
                ]
                for _label, _key, _detail_key in _check_items:
                    _status = _env_check.get(_key, "Not Applicable")
                    _detail = _env_check.get(_detail_key, "")
                    if _status == "Done":
                        _icon, _bg, _border = "✅", "#f0fdf4", "#16a34a"
                    elif _status == "Skipped":
                        _icon, _bg, _border = "❌", "#fef2f2", "#ef4444"
                    else:
                        _icon, _bg, _border = "⬜", "#f8fafc", "#d1d5db"
                    st.markdown(
                        f"""<div style='background:{_bg};border-left:4px solid {_border};border-radius:0 6px 6px 0;padding:8px 14px;margin:4px 0;'>
                            <p style='margin:0;font-size:13px;'>
                                {_icon} <strong>{html.escape(_label)}:</strong>
                                <span style='color:#6b7280;font-size:11px;margin-left:6px;'>{html.escape(_status)}</span>
                            </p>
                            <p style='margin:2px 0 0;font-size:11px;color:#4b5563;'>{html.escape(_detail)}</p>
                        </div>""",
                        unsafe_allow_html=True,
                    )

        # ── Detailed Case Summary (session-grouped, color-coded) ─────────────
        _sessions = build_session_groups(ticket)
        with st.expander(f"📋 Detailed Case Summary ({len(_sessions)} sessions)", expanded=False):
            if not _sessions:
                st.markdown("_No update data available._")
            else:
                _flag_colors = {
                    "close": ("#fef2f2", "#991b1b", "⚠️"),
                    "reopen": ("#fffbea", "#854f0b", "🔄"),
                    "escalation": ("#fff7ed", "#9a3412", "⬆️"),
                    "frustration": ("#fffbea", "#854f0b", "😤"),
                    "callback_promise": ("#eef3fb", "#1B3A6B", "📞"),
                }
                _dot_colors = {"agent": "#378ADD", "customer": "#1D9E75", "system": "#B4B2A9"}
                _name_colors = {"agent": "#185FA5", "customer": "#0F6E56", "system": "#9ca3af"}
                _legend = (
                    "<div style='display:flex;gap:16px;margin-bottom:12px;padding:8px 12px;"
                    "background:#f8fafc;border-radius:6px;flex-wrap:wrap;'>"
                    "<div style='display:flex;align-items:center;gap:5px;font-size:11px;color:#6b7280;'>"
                    "<span style='display:inline-block;width:8px;height:8px;border-radius:50%;background:#378ADD;'></span> Agent</div>"
                    "<div style='display:flex;align-items:center;gap:5px;font-size:11px;color:#6b7280;'>"
                    "<span style='display:inline-block;width:8px;height:8px;border-radius:50%;background:#1D9E75;'></span> Customer</div>"
                    "<div style='display:flex;align-items:center;gap:5px;font-size:11px;color:#6b7280;'>"
                    "<span style='display:inline-block;width:8px;height:8px;border-radius:50%;background:#B4B2A9;'></span> System</div>"
                    "<div style='display:flex;align-items:center;gap:5px;font-size:11px;color:#6b7280;'>"
                    "<span style='background:#fef2f2;color:#991b1b;border-radius:3px;padding:1px 6px;font-size:10px;font-weight:700;'>⚠️ Flag</span> Critical event</div>"
                    "</div>"
                )
                st.markdown(_legend, unsafe_allow_html=True)

                _total_agents = set()
                _total_closes = 0
                _total_breaches = 0
                _total_escalations = 0

                for _sess in _sessions:
                    _sess_header = (
                        f"<div style='display:flex;align-items:center;gap:8px;margin:16px 0 6px;padding-bottom:5px;"
                        f"border-bottom:1px solid #e5e7eb;'>"
                        f"<span style='background:#f1f5f9;color:#6b7280;font-size:11px;font-weight:600;padding:2px 8px;"
                        f"border-radius:4px;'>Session {_sess['num']}</span>"
                        f"<span style='font-weight:600;font-size:13px;color:#1B3A6B;'>{html.escape(_sess['title'])}</span>"
                        f"<span style='font-size:11px;color:#9ca3af;margin-left:auto;'>{html.escape(_sess['date_range'])}</span>"
                        f"</div>"
                    )
                    st.markdown(_sess_header, unsafe_allow_html=True)

                    for _e in _sess["entries"]:
                        if _e["actor"] == "agent":
                            _total_agents.add(_e["name"])
                        _dot_c = _dot_colors.get(_e["actor"], "#B4B2A9")
                        _nm_c = _name_colors.get(_e["actor"], "#6b7280")
                        _nm_style = "font-style:italic;" if _e["actor"] == "system" else ""
                        _flags_html = ""
                        for _ft, _fl in _e.get("flags", []):
                            _fb, _fc, _fi = _flag_colors.get(_ft, ("#f8fafc", "#6b7280", ""))
                            _flags_html += (
                                f" <span style='display:inline-flex;align-items:center;gap:2px;background:{_fb};"
                                f"color:{_fc};font-size:10px;font-weight:700;padding:1px 6px;"
                                f"border-radius:3px;margin-left:4px;'>{_fi} {html.escape(_fl)}</span>"
                            )
                            if _ft == "close":
                                _total_closes += 1
                            elif _ft == "escalation":
                                _total_escalations += 1
                            elif _ft == "callback_promise":
                                _total_breaches += 1
                        _entry_html = (
                            f"<div style='display:flex;gap:10px;padding:5px 0;border-bottom:1px solid #f1f5f9;"
                            f"align-items:flex-start;'>"
                            f"<span style='display:inline-block;width:8px;height:8px;border-radius:50%;"
                            f"background:{_dot_c};margin-top:5px;flex-shrink:0;'></span>"
                            f"<span style='font-weight:600;min-width:130px;flex-shrink:0;font-size:12px;"
                            f"color:{_nm_c};{_nm_style}'>{html.escape(_e['name'])}</span>"
                            f"<span style='flex:1;font-size:12px;line-height:1.5;color:#374151;'>"
                            f"{html.escape(_e['action'])}{_flags_html}</span>"
                            f"</div>"
                        )
                        st.markdown(_entry_html, unsafe_allow_html=True)

                _first_date = _sessions[0]["entries"][0]["date"] if _sessions and _sessions[0]["entries"] else ""
                _last_date = _sessions[-1]["entries"][-1]["date"] if _sessions and _sessions[-1]["entries"] else ""
                _days_total = 0
                if _first_date and _last_date:
                    from datetime import datetime as _dt_wh
                    try:
                        _days_total = (_dt_wh.strptime(_last_date, "%Y-%m-%d") - _dt_wh.strptime(_first_date, "%Y-%m-%d")).days
                    except ValueError:
                        pass
                _summary_bar = (
                    f"<div style='margin-top:14px;padding:10px 16px;background:#f1f5f9;border-radius:6px;"
                    f"display:flex;gap:24px;flex-wrap:wrap;'>"
                    f"<div style='font-size:12px;color:#6b7280;'>👥 <strong style='color:#1B3A6B;'>{len(_total_agents)}</strong> agents</div>"
                    f"<div style='font-size:12px;color:#6b7280;'>⚠️ <strong style='color:#991b1b;'>{_total_closes}</strong> premature closes</div>"
                    f"<div style='font-size:12px;color:#6b7280;'>📞 <strong style='color:#1B3A6B;'>{_total_breaches}</strong> callback promises</div>"
                    f"<div style='font-size:12px;color:#6b7280;'>⬆️ <strong style='color:#9a3412;'>{_total_escalations}</strong> escalations</div>"
                    f"<div style='font-size:12px;color:#6b7280;'>📅 <strong style='color:#1B3A6B;'>{_days_total}</strong> days total</div>"
                    f"</div>"
                )
                st.markdown(_summary_bar, unsafe_allow_html=True)

    # ── Timeline ──────────────────────────────────────────────────────────────
    with st.expander(f"📅 Timeline ({len(timeline)} entries)"):
        # ── Interactive Plotly chart (shared builder) ──────────────────────────
        fig = build_dot_timeline_figure(timeline)
        if fig is not None:
            st.plotly_chart(fig, use_container_width=True,
                config={"displayModeBar": False, "responsive": True})
        elif timeline:
            st.info("Run `pip install plotly` to enable the interactive timeline.")

        st.divider()

        st.markdown("<div style='margin-top:20px;'></div>", unsafe_allow_html=True)
        rows_html = "".join(
            f"<tr style='background:{'#f8fafc' if i % 2 else 'white'};'>"
            f"<td style='padding:8px 12px;border:1px solid #d1d5db;white-space:nowrap;font-size:13px;'>{html.escape(str(entry.get('date','')))}</td>"
            f"<td style='padding:8px 12px;border:1px solid #d1d5db;font-size:13px;'>{html.escape(str(entry.get('author','')))}</td>"
            f"<td style='padding:8px 12px;border:1px solid #d1d5db;font-size:13px;'>{html.escape(str(entry.get('summary','')))}</td>"
            f"</tr>"
            for i, entry in enumerate(timeline)
        )
        st.markdown(
            f"""<table style='border-collapse:collapse;width:100%;font-family:Segoe UI,sans-serif;'>
              <thead>
                <tr style='background:#1B3A6B;color:white;'>
                  <th style='padding:9px 12px;text-align:left;font-size:11px;letter-spacing:.07em;text-transform:uppercase;border:1px solid #1B3A6B;white-space:nowrap;'>Date</th>
                  <th style='padding:9px 12px;text-align:left;font-size:11px;letter-spacing:.07em;text-transform:uppercase;border:1px solid #1B3A6B;'>Persona</th>
                  <th style='padding:9px 12px;text-align:left;font-size:11px;letter-spacing:.07em;text-transform:uppercase;border:1px solid #1B3A6B;'>Summary</th>
                </tr>
              </thead>
              <tbody>{rows_html}</tbody>
            </table>""",
            unsafe_allow_html=True,
        )

    # ── Download / Print ──────────────────────────────────────────────────────
    st.divider()
    html_report = build_html_report(ticket, ai, ticket_url, user)

    btn1, btn2, btn3 = st.columns(3)
    with btn1:
        if st.button("📄 Open Report", use_container_width=True):
            st.session_state["_open_html"] = html_report
    with btn2:
        clicked_dl = st.download_button(
            label="⬇️ Download Executive Report",
            data=html_report.encode("utf-8"),
            file_name=f"ticket_{ticket['id']}_TLDR.html",
            mime="text/html",
            use_container_width=True,
        )
        if clicked_dl:
            st.toast("Executive Report downloaded successfully.", icon="⬇️")
    with btn3:
        st.markdown(
            """<a href="javascript:window.print()" target="_self"
               style="display:flex;align-items:center;justify-content:center;height:38px;
               background:#1B3A6B;color:white;border-radius:6px;text-decoration:none;
               font-weight:600;font-size:14px;">
               🖨️ Print / Save as PDF</a>""",
            unsafe_allow_html=True,
        )

    # Open report in new tab via JS
    if "_open_html" in st.session_state:
        html_content = st.session_state.pop("_open_html")
        _open_in_new_tab(html_content)



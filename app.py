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
from datetime import date, datetime as dt
from PIL import Image as PILImage
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
API_KEY       = st.secrets["HAPPYFOX_API_KEY"]
AUTH_CODE     = st.secrets["HAPPYFOX_AUTH_CODE"]
ANTHROPIC_KEY = st.secrets["ANTHROPIC_API_KEY"]
BASE_URL      = st.secrets["HAPPYFOX_BASE_URL"].rstrip("/") + "/api/1.1/json"
PORTAL_BASE   = st.secrets["HAPPYFOX_BASE_URL"].rstrip("/")

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
    except Exception:
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
        updates.extend(data.get("data", []))
        info = data.get("page_info", {})
        if info.get("end_index", 0) >= info.get("count", 0):
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
    # Collect custom fields as readable text
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
    total = len(all_updates)
    # Keep first 5 + last 45 updates to preserve context without exceeding token limits
    if total > 50:
        updates_to_use = all_updates[:5] + all_updates[-45:]
        skipped = total - 50
    else:
        updates_to_use = all_updates
        skipped = 0

    lines += [
        "",
        f"Total updates: {total}" + (f" (showing 50 — {skipped} middle updates omitted for brevity)" if skipped else ""),
        "── THREAD / UPDATES ──────────────────────────────────────",
    ]
    for update in updates_to_use:
        agent     = update.get("agent") or {}
        author    = agent.get("name") if agent else "Customer"
        kind      = update.get("type", "reply")
        timestamp = update.get("created_at", "")
        raw = (
            update.get("text") or update.get("html") or update.get("body") or
            update.get("content") or update.get("message") or
            {k: vv for k, vv in update.items() if k not in ("agent", "type", "created_at", "id")}
        )
        text = str(raw).strip() if raw else "(no content)"
        # Truncate very long individual messages
        if len(text) > 1000:
            text = text[:1000] + "… [truncated]"
        lines.append(f"\n[{timestamp}]  {author}  ({kind}):\n{text}")
    return "\n".join(lines)


# ── Claude prompt ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """\
You are a super-smart and helpful customer support detective AND a senior quality assurance critic.
The ticket thread may be in any language. Always respond in English only.
Translate any non-English content before analysing it.
Read EVERY message carefully — extract phone numbers, country, serial numbers and warranty dates
even if they are mentioned only briefly in the thread.

For the "opportunities" field: act as a rigorous QA critic. Tediously identify any incorrect
troubleshooting steps, wrong advice given, missed diagnostic steps, unnecessary delays, repeated
questions already answered, incorrect product assumptions, policy violations, or anything that
could have been handled better. Be specific and direct — name exactly what was wrong or missed
and what should have been done instead. If everything was handled correctly, say so.

Respond with a single valid JSON object only. No markdown, no explanation — raw JSON only.

{
  "subject_is_english": true or false,
  "subject_english": "English translation of the subject if it is NOT in English. If already English set to null.",
  "customer_phone": "Customer phone number found anywhere in the thread or custom fields, else null",
  "customer_country": "Customer country or region inferred from phone prefix, email, address, or language used, else null",
  "problem_summary": "1-2 sentences describing what the customer needs",
  "problem_category_1": "Primary issue category (e.g. Connectivity, Hardware Failure, Setup, Firmware, Warranty/RMA, RMA or Refund)",
  "problem_category_2": "Secondary issue category or refund reason or null",
  "model_number": "Product model number extracted from anywhere in thread or custom fields or null",
  "serial_number": "Serial number extracted from anywhere in thread or custom fields or null",
  "warranty_start_date": "Warranty start date from thread or custom fields or null. Look for contract start date too.",
  "warranty_status": "Active / Expired / Unknown — infer from warranty start date and any expiry mentions",
  "actions_taken": ["action 1", "action 2"],
  "actions_summary": "One sentence summary of all support actions taken",
  "actions_insights": "1-2 sentences of insight — patterns, gaps, what is or is not working",
  "opportunities": ["Specific QA finding 1 — what was wrong or missed and what should have been done", "finding 2"],
  "next_steps": ["step 1", "step 2"],
  "timeline": [
    {"date": "YYYY-MM-DD", "author": "Name or Customer", "summary": "One sentence of what happened"}
  ]
}
"""


def analyse_ticket(ticket_text: str) -> tuple[dict, int, int]:
    """Returns (ai_result, input_tokens, output_tokens)."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    max_attempts = 5
    for attempt in range(1, max_attempts + 1):
        try:
            msg = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": ticket_text}],
            )
            raw = msg.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            return (
                json.loads(raw.strip()),
                msg.usage.input_tokens,
                msg.usage.output_tokens,
            )
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
    if not timeline:
        return ""
    try:
        import plotly.graph_objects as go
        from collections import defaultdict
        import html as _hm

        def _hex_rgba(h, a=0.38):
            h = h.lstrip("#")
            r,g,b = int(h[0:2],16),int(h[2:4],16),int(h[4:6],16)
            return f"rgba({r},{g},{b},{a})"

        def _grp(author):
            a = (author or "").lower()
            if "customer" in a: return "Customer"
            if "cat" in a:      return "CAT Team"
            if any(x in a for x in ("survey","csat","system")): return "System/Survey"
            return "Support Agent"

        PC = {"Customer":"#0891b2","Support Agent":"#d97706","CAT Team":"#7c3aed","System/Survey":"#6b7280"}
        LANE_Y  = {"Customer":3,"Support Agent":2,"CAT Team":1,"System/Survey":0}
        LANE_BG = {"Customer":"#e0f2fe","Support Agent":"#fffbeb","CAT Team":"#ede9fe","System/Survey":"#f3f4f6"}
        CRIT_KW = ["escalat","complaint","replacement","refund","callback","threat","demand",
                   "unresolved","refused","urgent","legal","consumer association","mail-in","csat"]

        msg_counts = defaultdict(int)
        transitions = defaultdict(int)
        prev = None
        for e in timeline:
            g = _grp(e.get("author",""))
            msg_counts[g] += 1
            if prev and prev != g:
                transitions[(prev,g)] += 1
            prev = g

        # ── Sankey ────────────────────────────────────────────────────────────
        p_labels = list(PC.keys())
        p_idx = {l:i for i,l in enumerate(p_labels)}
        src,tgt,val,lc = [],[],[],[]
        for (s,t),c in transitions.items():
            if s in p_idx and t in p_idx:
                src.append(p_idx[s]); tgt.append(p_idx[t])
                val.append(c); lc.append(_hex_rgba(PC[s],0.4))
        fig_sk = go.Figure(go.Sankey(
            arrangement="snap",
            node=dict(pad=28,thickness=28,line=dict(color="white",width=0),
                      label=[f"{l}  ({msg_counts[l]})" for l in p_labels],
                      color=list(PC.values()),hovertemplate="%{label}<extra></extra>"),
            link=dict(source=src,target=tgt,value=val,color=lc,
                      hovertemplate="%{source.label} → %{target.label}<br>%{value} handoff(s)<extra></extra>"),
        ))
        fig_sk.update_layout(title=dict(text="Interaction Flow",x=0.5,
            font=dict(size=13,color="#1B3A6B",family="Segoe UI")),
            height=300,margin=dict(l=10,r=10,t=38,b=10),
            paper_bgcolor="white",font=dict(family="Segoe UI",size=11))

        # ── Donut ─────────────────────────────────────────────────────────────
        active = {k:v for k,v in msg_counts.items() if v>0}
        fig_pie = go.Figure(go.Pie(
            labels=list(active.keys()),values=list(active.values()),hole=0.58,
            marker=dict(colors=[PC[p] for p in active],line=dict(color="white",width=2)),
            textinfo="label+percent",textfont=dict(size=11,family="Segoe UI"),
            hovertemplate="%{label}<br>%{value} event(s) — %{percent}<extra></extra>"))
        fig_pie.add_annotation(text=f"<b>{sum(active.values())}</b><br>events",
            x=0.5,y=0.5,showarrow=False,font=dict(size=15,color="#1B3A6B",family="Segoe UI"))
        fig_pie.update_layout(title=dict(text="Message Distribution",x=0.5,
            font=dict(size=13,color="#1B3A6B",family="Segoe UI")),
            height=300,margin=dict(l=10,r=10,t=38,b=10),
            paper_bgcolor="white",showlegend=False)

        # ── Swimlane ──────────────────────────────────────────────────────────
        fig_sw = go.Figure()
        n = len(timeline)
        for grp,y in LANE_Y.items():
            fig_sw.add_shape(type="rect",x0=-0.5,x1=n-0.5,y0=y-0.45,y1=y+0.45,
                fillcolor=LANE_BG[grp],line=dict(width=0),layer="below")
            fig_sw.add_annotation(x=-0.55,y=y,xanchor="right",showarrow=False,
                text=f"<b>{grp}</b>",font=dict(size=10,color=PC[grp],family="Segoe UI"))
        last_x = {}
        for i,e in enumerate(timeline):
            g = _grp(e.get("author",""))
            y = LANE_Y[g]
            if g in last_x:
                fig_sw.add_shape(type="line",x0=last_x[g],x1=i,y0=y,y1=y,
                    line=dict(color=PC[g],width=1.5,dash="dot"))
            last_x[g] = i
        for i,e in enumerate(timeline):
            author  = e.get("author","—")
            summary = e.get("summary","")
            date_s  = e.get("date","")
            g       = _grp(author)
            y       = LANE_Y[g]
            crit    = any(kw in summary.lower() for kw in CRIT_KW)
            fig_sw.add_trace(go.Scatter(
                x=[i],y=[y],mode="markers+text",
                marker=dict(size=40 if crit else 28,color=PC[g],
                    symbol="diamond" if crit else "circle",
                    line=dict(width=3 if crit else 1.5,color="#ef4444" if crit else "white")),
                text=[f"<b>{i+1}</b>"],textfont=dict(color="white",size=10),
                textposition="middle center",
                customdata=[[_hm.escape(author),date_s,_hm.escape(summary),
                             "⚠️ Critical Event" if crit else ""]],
                hovertemplate="<b>%{customdata[0]}</b>  ·  %{customdata[1]}<br>"
                              "<i>%{customdata[3]}</i><br><br>%{customdata[2]}<extra></extra>",
                showlegend=False))
            if crit:
                fig_sw.add_annotation(x=i,y=y+0.54,text="⚠️",
                    showarrow=False,font=dict(size=11),xanchor="center")
        fig_sw.update_layout(
            title=dict(text="Swimlane  ·  hover dots for details  ·  ⚠️ diamond = critical event",
                x=0.5,font=dict(size=12,color="#6b7280",family="Segoe UI")),
            height=360,margin=dict(l=115,r=20,t=38,b=36),
            paper_bgcolor="white",plot_bgcolor="white",
            xaxis=dict(tickvals=list(range(n)),ticktext=[f"#{i+1}" for i in range(n)],
                showgrid=False,zeroline=False,tickfont=dict(size=10,color="#9ca3af")),
            yaxis=dict(tickvals=[],showgrid=False,zeroline=False,range=[-0.7,3.7]),
            hovermode="closest",
            hoverlabel=dict(bgcolor="white",bordercolor="#d1d5db",
                font=dict(size=12,family="Segoe UI"),namelength=-1))

        cfg = {"responsive": True, "displayModeBar": False}
        sk_html  = fig_sk.to_html(full_html=False, include_plotlyjs=False, config=cfg)
        pie_html = fig_pie.to_html(full_html=False, include_plotlyjs=False, config=cfg)
        sw_html  = fig_sw.to_html(full_html=False, include_plotlyjs=False, config=cfg)

        return f"""
        <h2>Executive Interaction Dashboard</h2>
        <div style="display:grid;grid-template-columns:3fr 2fr;gap:16px;margin-bottom:12px;">
          <div>{sk_html}</div>
          <div>{pie_html}</div>
        </div>
        <div style="margin-bottom:20px;">{sw_html}</div>
        """
    except Exception:
        return ""


def _build_dot_timeline_html(timeline: list) -> str:
    """Interactive dot timeline chart for embedding inside the Timeline section of the report."""
    if not timeline:
        return ""
    try:
        import plotly.graph_objects as go
        import html as _hm

        PC = {"Customer":"#0891b2","Support Agent":"#d97706","CAT Team":"#7c3aed","System/Survey":"#6b7280"}

        def _grp(author):
            a = (author or "").lower()
            if "customer" in a: return "Customer"
            if "cat" in a:      return "CAT Team"
            if any(x in a for x in ("survey","csat","system")): return "System/Survey"
            return "Support Agent"

        n = len(timeline)
        fig = go.Figure()
        fig.add_shape(type="line", x0=0, x1=n-1, y0=0, y1=0,
            line=dict(color="#d1d5db", width=3))

        for i, entry in enumerate(timeline):
            author  = entry.get("author", "—")
            date_s  = entry.get("date", "")
            summary = entry.get("summary", "")
            color   = PC[_grp(author)]
            y       = 1 if i % 2 == 0 else -1

            fig.add_shape(type="line", x0=i, x1=i, y0=0, y1=y*0.72,
                line=dict(color=color, width=1.5))
            fig.add_trace(go.Scatter(
                x=[i], y=[y], mode="markers+text",
                marker=dict(size=32, color=color, line=dict(width=2, color="white")),
                text=[str(i+1)], textfont=dict(color="white", size=11),
                textposition="middle center",
                customdata=[[_hm.escape(author), date_s, _hm.escape(summary)]],
                hovertemplate="<b>%{customdata[0]}</b>  ·  %{customdata[1]}<br><br>%{customdata[2]}<extra></extra>",
                showlegend=False))
            label_y = y + (0.44 if y > 0 else -0.44)
            fig.add_annotation(x=i, y=label_y,
                text=f"<b>{_hm.escape(author)}</b><br><span style='color:#9ca3af'>{date_s}</span>",
                showarrow=False, font=dict(size=9, color=color), xanchor="center")

        fig.update_layout(
            height=300, margin=dict(l=10, r=10, t=10, b=10),
            plot_bgcolor="white", paper_bgcolor="white",
            yaxis=dict(visible=False, range=[-2, 2]),
            xaxis=dict(tickvals=list(range(n)), ticktext=[f"#{i+1}" for i in range(n)],
                showgrid=False, showline=False, tickfont=dict(size=10, color="#9ca3af")),
            hovermode="closest",
            hoverlabel=dict(bgcolor="white", bordercolor="#d1d5db",
                font=dict(size=12, family="Segoe UI"), namelength=-1))

        return fig.to_html(full_html=False, include_plotlyjs=False,
                           config={"responsive": True, "displayModeBar": False})
    except Exception:
        return ""


# ── HTML / PDF report ─────────────────────────────────────────────────────────
def build_html_report(ticket: dict, ai: dict, ticket_url: str, contact: dict) -> str:
    user         = ticket.get("user", {})
    category_raw = ticket.get("category", {}).get("name", "—") if ticket.get("category") else "—"
    is_refund    = "refund" in category_raw.lower()
    cat2_label   = "Refund Reason" if is_refund else "Problem Category 2"

    subject      = ticket["subject"]
    subject_en   = ai.get("subject_english")
    if not ai.get("subject_is_english", True) and subject_en:
        subject_display = f"{subject}<br><span style='color:#888;font-size:11px;font-weight:400;'>🇬🇧 {subject_en}</span>"
    else:
        subject_display = subject

    timeline_rows = "".join(
        f"<tr><td>{e.get('date','')}</td><td>{e.get('author','')}</td><td>{e.get('summary','')}</td></tr>"
        for e in ai.get("timeline", [])
    )
    actions_html = "".join(f"<li>{a}</li>" for a in ai.get("actions_taken", []))
    steps_html   = "".join(f"<li>{s}</li>" for s in ai.get("next_steps", []))
    opps_html_r  = "".join(f"<li>{o}</li>" for o in ai.get("opportunities", [])) or "<li>No issues identified.</li>"

    phone   = v(user.get("phone") or user.get("phone_number") or ai.get("customer_phone"))
    country = v(user.get("country") or user.get("country_code") or ai.get("customer_country"))

    # Device fields — AI first, then custom fields fallback
    model   = v(ai.get("model_number")   or get_custom_field(ticket, "model", "product"))
    serial  = v(ai.get("serial_number")  or get_custom_field(ticket, "serial"))
    w_start = v(ai.get("warranty_start_date") or get_custom_field(ticket, "warranty start", "warranty date"))
    w_stat  = v(ai.get("warranty_status") or get_custom_field(ticket, "warranty status"))

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Ticket #{ticket['id']} — Happy Link Claude TLDR</title>
<script src="https://cdn.plot.ly/plotly-2.32.0.min.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Segoe UI', Arial, sans-serif; font-size: 12px; color: #1a1a2e; background: white; padding: 40px 50px; }}
  .header {{ background: #1B3A6B; color: white; padding: 20px 28px; border-radius: 6px; margin-bottom: 6px; }}
  .header h1 {{ font-size: 20px; letter-spacing: .5px; margin-bottom: 2px; }}
  .header p  {{ font-size: 11px; opacity: .8; }}
  .confidential {{ text-align: right; font-size: 10px; color: #888; margin-bottom: 22px; font-style: italic; }}
  h2 {{ font-size: 13px; color: #1B3A6B; border-bottom: 2px solid #1B3A6B; padding-bottom: 4px; margin: 20px 0 10px; text-transform: uppercase; letter-spacing: .08em; }}
  .subject-box {{ font-size: 17px; font-weight: 700; color: #1B3A6B; margin: 0 0 16px 0; padding-bottom: 10px; border-bottom: 2px solid #e5e7eb; }}
  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 5px 30px; margin: 8px 0; }}
  .grid div {{ font-size: 12px; }}
  .label {{ font-weight: 700; color: #1B3A6B; }}
  .problem-box {{ background: #eef3fb; border-left: 4px solid #1B3A6B; padding: 10px 14px; border-radius: 0 4px 4px 0; font-size: 12px; line-height: 1.6; }}
  .badge {{ display: inline-block; background: #1B3A6B; color: white; border-radius: 4px; padding: 2px 8px; font-size: 10px; font-weight: 700; margin-right: 6px; }}
  .badge2 {{ background: #374151; }}
  ul {{ padding-left: 16px; margin: 6px 0; line-height: 1.7; }}
  .insight {{ background: #fffbea; border-left: 4px solid #f59e0b; padding: 8px 12px; border-radius: 0 4px 4px 0; font-size: 11px; margin-top: 8px; line-height: 1.5; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 8px; }}
  th, td {{ border: 1px solid #d1d5db; padding: 6px 10px; text-align: left; font-size: 11px; }}
  th {{ background: #1B3A6B; color: white; font-weight: 600; font-size: 10px; text-transform: uppercase; }}
  tr:nth-child(even) {{ background: #f8fafc; }}
  .footer {{ margin-top: 30px; padding-top: 10px; border-top: 1px solid #d1d5db; font-size: 10px; color: #9ca3af; display: flex; justify-content: space-between; }}
  a {{ color: #1B3A6B; }}
  @media print {{
    body {{ padding: 20px 30px; }}
    .header {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
    th {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  }}
</style>
</head><body>

<div class="header">
  <h1>🔗 Happy Link Claude TLDR Ticket Summary</h1>
  <p>AI-powered support ticket analysis — generated {date.today()}</p>
</div>
<div class="confidential">Confidential &nbsp;|&nbsp; Internal Use Only</div>

<div class="subject-box">{subject_display}</div>

<h2>Ticket Overview</h2>
<div class="grid">
  <div><span class="label">Ticket #:</span> {ticket['id']}</div>
  <div><span class="label">Status:</span> {ticket['status']['name']}</div>
  <div><span class="label">Category:</span> {category_raw}</div>
  <div><span class="label">Updates:</span> {len(ticket.get('updates', []))}</div>
  <div style="grid-column:1/-1"><span class="label">URL:</span> <a href="{ticket_url}">{ticket_url}</a></div>
</div>

<h2>Customer Details</h2>
<div class="grid">
  <div><span class="label">Name:</span> {v(user.get('name'))}</div>
  <div><span class="label">Email:</span> {v(user.get('email'))}</div>
  <div><span class="label">Phone:</span> {phone}</div>
  <div><span class="label">Country:</span> {country}</div>
</div>

<h2>Device Details</h2>
<div class="grid">
  <div><span class="label">Problem Category 1:</span> {v(ai.get('problem_category_1'))}</div>
  <div><span class="label">{cat2_label}:</span> {v(ai.get('problem_category_2'))}</div>
  <div><span class="label">Model Number:</span> {model}</div>
  <div><span class="label">Serial Number:</span> {serial}</div>
  <div><span class="label">Warranty Start Date:</span> {w_start}</div>
  <div><span class="label">Warranty Status:</span> {w_stat}</div>
</div>

<h2>The Problem</h2>
<div class="problem-box">{v(ai.get('problem_summary'))}</div>

{_build_dashboard_html(ai.get('timeline', []))}

<h2>Actions Taken</h2>
<ul>{actions_html}</ul>
<div class="insight">
  <strong>Summary:</strong> {v(ai.get('actions_summary'))}<br>
  <strong>Insights:</strong> {v(ai.get('actions_insights'))}<br><br>
  <strong>🎯 Opportunities:</strong>
  <ul style="margin:4px 0 0;padding-left:16px;">{opps_html_r}</ul>
</div>

<h2>Next Steps</h2>
<ul>{steps_html}</ul>

<h2>Timeline ({len(ai.get('timeline', []))} entries)</h2>
{_build_dot_timeline_html(ai.get('timeline', []))}
<table>
  <tr><th>Date</th><th>Persona</th><th>Summary</th></tr>
  {timeline_rows}
</table>

<div class="footer">
  <span>Happy Link Claude TLDR Ticket Summary &nbsp;|&nbsp; Ticket #{ticket['id']}</span>
  <span>Generated: {date.today()} &nbsp;|&nbsp; Confidential</span>
</div>
</body></html>"""


# ── UI ────────────────────────────────────────────────────────────────────────
router_img = PILImage.open("C:/Users/Ashley-Admin/Desktop/Claude/Project/Happy Link Claude TLDR Ticket Summary/router_nobg.png")
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
    st.session_state.pop("cached_report", None)

    # Auto-save to database
    if _PG_AVAILABLE:
        try:
            _html = build_html_report(ticket, ai, f"{PORTAL_BASE}/staff/ticket/{ticket_id}", ticket.get("user", {}))
            init_db()
            save_report_to_db(ticket, ai, _html, input_tokens, output_tokens, len(ticket.get("updates", [])))
            st.toast(f"Report for ticket #{ticket_id} saved to database.", icon="💾")
        except Exception:
            pass

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
                    _fake_ticket = {
                        "id": cached["ticket_id"],
                        "subject": cached["subject"] or "",
                        "status": {"name": cached["status"] or "—"},
                        "category": {"name": cached["category"]} if cached.get("category") else None,
                        "updates": list(range(cached.get("num_updates", 0))),
                        "user": {},
                        "assigned_to": {"name": cached["assignee"]} if cached.get("assignee") else None,
                    }
                    _ticket_url = f"{PORTAL_BASE}/staff/ticket/{cached['ticket_id']}"
                    st.session_state["_open_html"] = build_html_report(_fake_ticket, _ai, _ticket_url, {})
                except Exception:
                    st.session_state["_open_html"] = cached["html_report"]
            else:
                st.session_state["_open_html"] = cached["html_report"]
    with col_b:
        clicked_dl = st.download_button(
            label="⬇️ Download Report",
            data=cached["html_report"].encode("utf-8"),
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
        f"<b>Subject:</b> {cached['subject'] or '—'} &nbsp;·&nbsp; "
        f"<b>Status:</b> {cached['status'] or '—'} &nbsp;·&nbsp; "
        f"<b>Category:</b> {cached['category'] or '—'} &nbsp;·&nbsp; "
        f"<b>Assignee:</b> {cached['assignee'] or '—'}</p>",
        unsafe_allow_html=True,
    )
    if cached["problem_summary"]:
        st.markdown(
            f"<div style='background:#eef3fb;border-left:4px solid #1B3A6B;border-radius:0 4px 4px 0;"
            f"padding:10px 14px;margin:8px 0;font-size:13px;'>{cached['problem_summary']}</div>",
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
        f"<p style='text-align:center;margin-bottom:2px;font-size:1.3rem;font-weight:700;color:#1B3A6B;'>{subject}</p>",
        unsafe_allow_html=True,
    )
    if not ai.get("subject_is_english", True) and subject_en:
        st.markdown(
            f"<p style='text-align:center;font-size:12px;color:#6b7280;font-style:italic;margin-top:0;'>EN: {subject_en}</p>",
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
            f"<span style='color:#1a1a2e;'>{value}</span></p>"
        )

    # Calculate time spent: ticket created_at → last updated_at (robust multi-format)
    def parse_ts(ts):
        if not ts:
            return None
        # Unix timestamp (int or float)
        try:
            numeric = float(ts)
            if numeric > 1_000_000:
                return dt.utcfromtimestamp(numeric)
        except (TypeError, ValueError):
            pass
        # String formats
        s = str(ts).strip()
        # Strip timezone offset (+HH:MM or -HH:MM) if present
        import re as _re
        s = _re.sub(r'[+-]\d{2}:\d{2}$', '', s)
        s = s[:19].replace(" ", "T")
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return dt.strptime(s, fmt)
            except ValueError:
                continue
        return None

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

    with st.expander("📋 Ticket Overview", expanded=False):
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
    with st.expander("👤 Customer Details", expanded=False):
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
            f"<span style='color:#1a1a2e;'>{val}</span></p>"
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

    # ── Timeline data + persona helper (shared by dashboard and timeline) ────────
    timeline = ai.get("timeline", [])

    def _persona_style(author: str):
        a = (author or "").lower()
        if "customer" in a:
            return "#0891b2", "#e0f2fe", "👤"
        if "cat" in a:
            return "#7c3aed", "#ede9fe", "🔺"
        if any(x in a for x in ("l1", "l2", "agent", "support", "charlotte", "zhiliang", "eric", "kris")):
            return "#d97706", "#fffbeb", "🛠️"
        return "#6b7280", "#f3f4f6", "📝"

    # ── The Problem ───────────────────────────────────────────────────────────
    st.markdown(
        "<p style='font-size:0.95rem;font-weight:700;color:#1B3A6B;"
        "border-bottom:2px solid #1B3A6B;padding-bottom:4px;margin-bottom:6px;'>"
        "The Problem</p>",
        unsafe_allow_html=True,
    )
    st.info(ai.get("problem_summary", "—"))

    # ── Executive Interaction Dashboard ──────────────────────────────────────
    if timeline:
        try:
            import plotly.graph_objects as go
            from collections import defaultdict
            import html as _html_mod

            def _hex_rgba(hex_col, alpha=0.35):
                h = hex_col.lstrip("#")
                r, g, b = int(h[0:2],16), int(h[2:4],16), int(h[4:6],16)
                return f"rgba({r},{g},{b},{alpha})"

            def _grp(author):
                a = (author or "").lower()
                if "customer" in a: return "Customer"
                if "cat" in a:      return "CAT Team"
                if any(x in a for x in ("survey","csat","system")): return "System/Survey"
                return "Support Agent"

            PC = {"Customer":"#0891b2","Support Agent":"#d97706","CAT Team":"#7c3aed","System/Survey":"#6b7280"}
            LANE_Y  = {"Customer":3,"Support Agent":2,"CAT Team":1,"System/Survey":0}
            LANE_BG = {"Customer":"#e0f2fe","Support Agent":"#fffbeb","CAT Team":"#ede9fe","System/Survey":"#f3f4f6"}
            CRITICAL_KW = ["escalat","complaint","replacement","refund","callback","threat","demand",
                           "unresolved","refused","urgent","legal","consumer association","mail-in","no resolution","csat"]

            msg_counts = defaultdict(int)
            transitions = defaultdict(int)
            prev_grp = None
            for entry in timeline:
                grp = _grp(entry.get("author",""))
                msg_counts[grp] += 1
                if prev_grp and prev_grp != grp:
                    transitions[(prev_grp, grp)] += 1
                prev_grp = grp

            with st.expander("🎯 Executive Interaction Dashboard", expanded=False):
                col_sk, col_pie = st.columns([3, 2])
                with col_sk:
                    p_labels = list(PC.keys())
                    p_idx = {l: i for i, l in enumerate(p_labels)}
                    sources, targets, values, lk_cols = [], [], [], []
                    for (src, tgt), cnt in transitions.items():
                        if src in p_idx and tgt in p_idx:
                            sources.append(p_idx[src]); targets.append(p_idx[tgt])
                            values.append(cnt); lk_cols.append(_hex_rgba(PC[src], 0.4))
                    fig_sk = go.Figure(go.Sankey(
                        arrangement="snap",
                        node=dict(pad=28, thickness=28, line=dict(color="white", width=0),
                                  label=[f"{l}  ({msg_counts[l]})" for l in p_labels],
                                  color=list(PC.values()), hovertemplate="%{label}<extra></extra>"),
                        link=dict(source=sources, target=targets, value=values, color=lk_cols,
                                  hovertemplate="%{source.label} → %{target.label}<br>%{value} handoff(s)<extra></extra>"),
                    ))
                    fig_sk.update_layout(title=dict(text="Interaction Flow", x=0.5,
                        font=dict(size=13, color="#1B3A6B", family="Segoe UI")),
                        height=300, margin=dict(l=10, r=10, t=38, b=10),
                        paper_bgcolor="white", font=dict(family="Segoe UI", size=11))
                    st.plotly_chart(fig_sk, use_container_width=True)
                with col_pie:
                    active = {k: v for k, v in msg_counts.items() if v > 0}
                    fig_pie = go.Figure(go.Pie(
                        labels=list(active.keys()), values=list(active.values()), hole=0.58,
                        marker=dict(colors=[PC[p] for p in active], line=dict(color="white", width=2)),
                        textinfo="label+percent", textfont=dict(size=11, family="Segoe UI"),
                        hovertemplate="%{label}<br>%{value} event(s) — %{percent}<extra></extra>"))
                    fig_pie.add_annotation(text=f"<b>{sum(active.values())}</b><br>events",
                        x=0.5, y=0.5, showarrow=False, font=dict(size=15, color="#1B3A6B", family="Segoe UI"))
                    fig_pie.update_layout(title=dict(text="Message Distribution", x=0.5,
                        font=dict(size=13, color="#1B3A6B", family="Segoe UI")),
                        height=300, margin=dict(l=10, r=10, t=38, b=10),
                        paper_bgcolor="white", showlegend=False)
                    st.plotly_chart(fig_pie, use_container_width=True)
                fig_sw = go.Figure()
                n = len(timeline)
                for grp, y_pos in LANE_Y.items():
                    fig_sw.add_shape(type="rect", x0=-0.5, x1=n-0.5, y0=y_pos-0.45, y1=y_pos+0.45,
                        fillcolor=LANE_BG[grp], line=dict(width=0), layer="below")
                    fig_sw.add_annotation(x=-0.55, y=y_pos, xanchor="right", showarrow=False,
                        text=f"<b>{grp}</b>", font=dict(size=10, color=PC[grp], family="Segoe UI"))
                grp_last_x = {}
                for i, entry in enumerate(timeline):
                    grp = _grp(entry.get("author",""))
                    y_pos = LANE_Y[grp]
                    if grp in grp_last_x:
                        fig_sw.add_shape(type="line", x0=grp_last_x[grp], x1=i, y0=y_pos, y1=y_pos,
                            line=dict(color=PC[grp], width=1.5, dash="dot"))
                    grp_last_x[grp] = i
                for i, entry in enumerate(timeline):
                    author = entry.get("author","—"); summary = entry.get("summary",""); date_s = entry.get("date","")
                    grp = _grp(author); y_pos = LANE_Y[grp]
                    is_crit = any(kw in summary.lower() for kw in CRITICAL_KW)
                    fig_sw.add_trace(go.Scatter(
                        x=[i], y=[y_pos], mode="markers+text",
                        marker=dict(size=40 if is_crit else 28, color=PC[grp],
                            symbol="diamond" if is_crit else "circle",
                            line=dict(width=3 if is_crit else 1.5, color="#ef4444" if is_crit else "white")),
                        text=[f"<b>{i+1}</b>"], textfont=dict(color="white", size=10),
                        textposition="middle center",
                        customdata=[[_html_mod.escape(author), date_s, _html_mod.escape(summary),
                                     "⚠️ Critical Event" if is_crit else ""]],
                        hovertemplate="<b>%{customdata[0]}</b>  ·  %{customdata[1]}<br>"
                                      "<i>%{customdata[3]}</i><br><br>%{customdata[2]}<extra></extra>",
                        showlegend=False))
                    if is_crit:
                        fig_sw.add_annotation(x=i, y=y_pos+0.54, text="⚠️",
                            showarrow=False, font=dict(size=11), xanchor="center")
                fig_sw.update_layout(
                    title=dict(text="Swimlane  ·  hover dots for details  ·  ⚠️ diamond = critical event",
                        x=0.5, font=dict(size=12, color="#6b7280", family="Segoe UI")),
                    height=360, margin=dict(l=115, r=20, t=38, b=36),
                    paper_bgcolor="white", plot_bgcolor="white",
                    xaxis=dict(tickvals=list(range(n)), ticktext=[f"#{i+1}" for i in range(n)],
                        showgrid=False, zeroline=False, tickfont=dict(size=10, color="#9ca3af")),
                    yaxis=dict(tickvals=[], showgrid=False, zeroline=False, range=[-0.7, 3.7]),
                    hovermode="closest",
                    hoverlabel=dict(bgcolor="white", bordercolor="#d1d5db",
                        font=dict(size=12, family="Segoe UI"), namelength=-1))
                st.plotly_chart(fig_sw, use_container_width=True)
        except Exception as _exc:
            st.error(f"Dashboard error: {_exc}")

    # ── Actions Taken ─────────────────────────────────────────────────────────
    with st.expander("✅ Actions Taken"):
        for a in ai.get("actions_taken", []):
            st.markdown(f"- {a}")

    # ── Summary & Insights ────────────────────────────────────────────────────
    st.markdown(
        f"""<div style="background:#fffbea;border-left:4px solid #f59e0b;
                        border-radius:0 6px 6px 0;padding:14px 18px;margin:8px 0;">
            <p style="margin:0 0 6px;font-size:13px;">
                <span style="font-weight:700;color:#92400e;">📊 Summary:</span>&nbsp;
                {v(ai.get('actions_summary'))}
            </p>
            <p style="margin:0;font-size:13px;">
                <span style="font-weight:700;color:#92400e;">💡 Insights:</span>&nbsp;
                {v(ai.get('actions_insights'))}
            </p>
        </div>""",
        unsafe_allow_html=True,
    )

    # ── Opportunities (collapsible, light red) ────────────────────────────────
    opps = ai.get("opportunities", [])
    st.markdown("""
    <style>
    div[data-testid="stExpander"].opp-expander {
        background: #fff5f5 !important;
        border: 1px solid #fca5a5 !important;
        border-left: 4px solid #ef4444 !important;
    }
    div[data-testid="stExpander"].opp-expander summary {
        color: #b91c1c !important;
    }
    </style>
    """, unsafe_allow_html=True)
    with st.expander(f"🎯 Opportunities ({len(opps)})", expanded=False):
        st.markdown(
            f"""<div style="background:#fff5f5;border-radius:4px;padding:10px 14px;">""",
            unsafe_allow_html=True,
        )
        if opps:
            for o in opps:
                st.markdown(
                    f"<p style='margin:4px 0;font-size:13px;color:#7f1d1d;'>• {o}</p>",
                    unsafe_allow_html=True,
                )
        else:
            st.markdown(
                "<p style='margin:0;font-size:13px;color:#6b7280;'>No issues identified.</p>",
                unsafe_allow_html=True,
            )
        st.markdown("</div>", unsafe_allow_html=True)

    # ── Next Steps ────────────────────────────────────────────────────────────
    with st.expander("⏭️ Next Steps", expanded=False):
        for s in ai.get("next_steps", []):
            st.markdown(f"- {s}")

    # ── Timeline ──────────────────────────────────────────────────────────────
    with st.expander(f"📅 Timeline ({len(timeline)} entries)"):
        # ── Interactive Plotly chart ───────────────────────────────────────────
        try:
            import plotly.graph_objects as go
            import html as _html

            if timeline:
                fig = go.Figure()
                n = len(timeline)

                # Baseline across all indices
                fig.add_shape(
                    type="line",
                    x0=0, x1=n - 1, y0=0, y1=0,
                    line=dict(color="#d1d5db", width=3),
                )

                for i, entry in enumerate(timeline):
                    color, bg, icon = _persona_style(entry.get("author", ""))
                    y = 1 if i % 2 == 0 else -1
                    author = entry.get("author", "—")
                    date_str = entry.get("date", "")
                    summary = entry.get("summary", "")

                    # Connector from baseline to dot
                    fig.add_shape(
                        type="line",
                        x0=i, x1=i, y0=0, y1=y * 0.72,
                        line=dict(color=color, width=1.5),
                    )

                    # Dot with number
                    fig.add_trace(go.Scatter(
                        x=[i], y=[y],
                        mode="markers+text",
                        marker=dict(size=32, color=color, line=dict(width=2, color="white")),
                        text=[str(i + 1)],
                        textfont=dict(color="white", size=11),
                        textposition="middle center",
                        customdata=[[author, date_str, summary]],
                        hovertemplate=(
                            "<b>%{customdata[0]}</b>  ·  %{customdata[1]}<br><br>"
                            "%{customdata[2]}<extra></extra>"
                        ),
                        showlegend=False,
                    ))

                    # Author + date label
                    label_y = y + (0.44 if y > 0 else -0.44)
                    fig.add_annotation(
                        x=i, y=label_y,
                        text=f"<b>{author}</b><br><span style='color:#9ca3af'>{date_str}</span>",
                        showarrow=False,
                        font=dict(size=9, color=color),
                        xanchor="center",
                    )

                fig.update_layout(
                    height=320,
                    margin=dict(l=10, r=10, t=10, b=10),
                    plot_bgcolor="white",
                    paper_bgcolor="white",
                    yaxis=dict(visible=False, range=[-2, 2]),
                    xaxis=dict(
                        tickvals=list(range(n)),
                        ticktext=[f"#{i+1}" for i in range(n)],
                        showgrid=False,
                        showline=False,
                        tickfont=dict(size=10, color="#9ca3af"),
                    ),
                    hovermode="closest",
                    hoverlabel=dict(
                        bgcolor="white",
                        bordercolor="#d1d5db",
                        font=dict(size=12, family="Segoe UI"),
                        namelength=-1,
                    ),
                )
                st.plotly_chart(fig, use_container_width=True)

        except ImportError:
            st.info("Run `pip install plotly` to enable the interactive timeline.")

        st.divider()

        st.markdown("<div style='margin-top:20px;'></div>", unsafe_allow_html=True)
        rows_html = "".join(
            f"<tr style='background:{'#f8fafc' if i % 2 else 'white'};'>"
            f"<td style='padding:8px 12px;border:1px solid #d1d5db;white-space:nowrap;font-size:13px;'>{entry.get('date','')}</td>"
            f"<td style='padding:8px 12px;border:1px solid #d1d5db;font-size:13px;'>{entry.get('author','')}</td>"
            f"<td style='padding:8px 12px;border:1px solid #d1d5db;font-size:13px;'>{entry.get('summary','')}</td>"
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



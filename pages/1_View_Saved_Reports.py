import base64
import streamlit as st
import streamlit.components.v1 as components
import psycopg2
import psycopg2.extras
from datetime import datetime as dt


def _open_in_new_tab(html_content: str):
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

st.set_page_config(
    page_title="Saved Reports — Happy Link TLDR",
    page_icon="📂",
    layout="wide",
)

# ── Styling ───────────────────────────────────────────────────────────────────
st.markdown("""
<style>
body, .stMarkdown, p, li, td, th, input, label, button, caption, small {
  font-family: 'Segoe UI', sans-serif !important;
}
[data-testid="stAppViewContainer"] { background-color: #f4f6f9; }
.block-container { max-width: 1100px !important; padding: 2rem; }
[data-testid="stHeader"] { background-color: #1B3A6B; }
h1 { color: #1B3A6B !important; }
h3 { color: #1B3A6B !important; border-bottom: 2px solid #1B3A6B; padding-bottom: 4px; }
.stExpander { background: white; border-radius: 8px; border: 1px solid #e5e7eb !important; margin-bottom: 8px; }
div[data-testid="stExpander"] summary { font-weight: 600; color: #1B3A6B; }
</style>
""", unsafe_allow_html=True)

# ── DB connection ─────────────────────────────────────────────────────────────
def _pg_conn():
    return psycopg2.connect(
        host=st.secrets.get("DB_HOST", "localhost"),
        port=st.secrets.get("DB_PORT", "5432"),
        dbname=st.secrets.get("DB_NAME", "metaboard"),
        user=st.secrets.get("DB_USER", "postgres"),
        password=st.secrets.get("DB_PASS", ""),
        connect_timeout=5,
    )

def load_reports():
    with _pg_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT id, ticket_id, subject, status, category, assignee,
                   problem_summary, saved_at,
                   COALESCE(input_tokens, 0)  AS input_tokens,
                   COALESCE(output_tokens, 0) AS output_tokens,
                   COALESCE(num_updates, 0)   AS num_updates
            FROM executive_reports
            ORDER BY saved_at DESC
        """)
        return cur.fetchall()

def load_html(report_id: int) -> str:
    with _pg_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT html_report FROM executive_reports WHERE id = %s", (report_id,))
        row = cur.fetchone()
        return row[0] if row else ""

def delete_report(report_id: int):
    with _pg_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM executive_reports WHERE id = %s", (report_id,))
        conn.commit()

# ── Header ────────────────────────────────────────────────────────────────────
st.markdown(
    "<h1 style='text-align:center;font-size:2rem;'>📂 Saved Executive Reports</h1>",
    unsafe_allow_html=True,
)
st.markdown(
    "<p style='text-align:center;color:#6b7280;font-size:13px;margin-top:-10px;'>"
    "All reports saved to the local Metaboard database</p>",
    unsafe_allow_html=True,
)
st.divider()

# ── Load & display ────────────────────────────────────────────────────────────
try:
    reports = load_reports()
except Exception as exc:
    st.error(f"Could not connect to database: {exc}")
    st.stop()

# ── API Usage Summary (real token data from DB) ───────────────────────────────
total_tickets  = len(reports) if reports else 0
total_input    = sum(r["input_tokens"]  for r in reports) if reports else 0
total_output   = sum(r["output_tokens"] for r in reports) if reports else 0
total_cost     = (total_input / 1_000_000 * 3) + (total_output / 1_000_000 * 15)
has_real_data  = total_input > 0

st.markdown(
    f"""<div style="background:white;border-radius:10px;border:1px solid #e5e7eb;
                    padding:18px 24px;margin-bottom:20px;">
        <p style="margin:0 0 14px;font-size:13px;font-weight:700;color:#1B3A6B;
                  border-bottom:1px solid #e5e7eb;padding-bottom:8px;">
            🧾 Claude API Usage Summary</p>
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;text-align:center;">
            <div style="background:#f4f6f9;border-radius:8px;padding:14px 8px;">
                <p style="margin:0;font-size:11px;color:#6b7280;text-transform:uppercase;
                          letter-spacing:.05em;">Tickets Analysed</p>
                <p style="margin:4px 0 0;font-size:22px;font-weight:700;color:#1B3A6B;">
                    {total_tickets}</p>
            </div>
            <div style="background:#f4f6f9;border-radius:8px;padding:14px 8px;">
                <p style="margin:0;font-size:11px;color:#6b7280;text-transform:uppercase;
                          letter-spacing:.05em;">Input Tokens</p>
                <p style="margin:4px 0 0;font-size:22px;font-weight:700;color:#1B3A6B;">
                    {total_input:,}</p>
            </div>
            <div style="background:#f4f6f9;border-radius:8px;padding:14px 8px;">
                <p style="margin:0;font-size:11px;color:#6b7280;text-transform:uppercase;
                          letter-spacing:.05em;">Output Tokens</p>
                <p style="margin:4px 0 0;font-size:22px;font-weight:700;color:#1B3A6B;">
                    {total_output:,}</p>
            </div>
            <div style="background:#eef3fb;border-radius:8px;padding:14px 8px;
                        border:1px solid #bfdbfe;">
                <p style="margin:0;font-size:11px;color:#6b7280;text-transform:uppercase;
                          letter-spacing:.05em;">Total Cost (USD)</p>
                <p style="margin:4px 0 0;font-size:22px;font-weight:700;color:#1B3A6B;">
                    ${total_cost:.3f}</p>
            </div>
        </div>
        <p style="margin:10px 0 0;font-size:11px;color:#9ca3af;text-align:right;">
            {"✅ Real token data from database" if has_real_data else
             "⚠️ Token data not yet available — re-analyse tickets to capture real usage"}</p>
    </div>""",
    unsafe_allow_html=True,
)

st.divider()

if not reports:
    st.info("No reports saved yet. Go back to the main page, investigate a ticket, and click 💾 Save to Database.")
    st.stop()

# ── Search / filter ───────────────────────────────────────────────────────────
search = st.text_input("🔍 Search by ticket ID, subject or category", placeholder="e.g. 130211")

filtered = [
    r for r in reports
    if not search or
       search.lower() in str(r["ticket_id"]).lower() or
       search.lower() in (r["subject"] or "").lower() or
       search.lower() in (r["category"] or "").lower()
]

st.markdown(f"**{len(filtered)} report{'s' if len(filtered) != 1 else ''}** found", )

st.divider()

# ── Report cards ──────────────────────────────────────────────────────────────
for r in filtered:
    saved_at = r["saved_at"]
    if isinstance(saved_at, dt):
        saved_str = saved_at.strftime("%d %b %Y  %H:%M")
    else:
        saved_str = str(saved_at)

    header = f"Ticket #{r['ticket_id']}  ·  {r['subject'] or '—'}  ·  {saved_str}"

    with st.expander(header, expanded=False):
        col_l, col_r = st.columns(2)
        with col_l:
            st.markdown(f"**Status:** {r['status'] or '—'}")
            st.markdown(f"**Category:** {r['category'] or '—'}")
        with col_r:
            st.markdown(f"**Assignee:** {r['assignee'] or '—'}")
            st.markdown(f"**Saved:** {saved_str}")

        ticket_cost = (r["input_tokens"] / 1_000_000 * 3) + (r["output_tokens"] / 1_000_000 * 15)
        st.markdown(
            f"<p style='font-size:13px;margin:4px 0;color:#6b7280;'>"
            f"🔄 <b style='color:#1B3A6B;'>Updates:</b> {r['num_updates']} &nbsp;·&nbsp; "
            f"💰 <b style='color:#1B3A6B;'>Token Cost:</b> ${ticket_cost:.4f}</p>",
            unsafe_allow_html=True,
        )

        if r["problem_summary"]:
            st.markdown(
                f"<div style='background:#eef3fb;border-left:4px solid #1B3A6B;"
                f"border-radius:0 4px 4px 0;padding:10px 14px;margin:8px 0;"
                f"font-size:13px;'>{r['problem_summary']}</div>",
                unsafe_allow_html=True,
            )

        try:
            html = load_html(r["id"])
        except Exception as exc:
            st.error(f"Could not load report: {exc}")
            html = None

        btn_a, btn_b, btn_c, btn_d = st.columns([2, 2, 2, 1])
        with btn_a:
            if html and st.button("📄 Open Report", key=f"open_{r['id']}", use_container_width=True):
                st.session_state[f"_open_{r['id']}"] = html
        with btn_b:
            if html:
                st.download_button(
                    label="⬇️ Download HTML Report",
                    data=html.encode("utf-8"),
                    file_name=f"ticket_{r['ticket_id']}_TLDR.html",
                    mime="text/html",
                    use_container_width=True,
                    key=f"dl_{r['id']}",
                )
        with btn_c:
            if st.button("🔁 Re-open in Main App", key=f"reopen_{r['id']}", use_container_width=True):
                st.switch_page("app.py")
        with btn_d:
            if st.button("🗑️ Delete", key=f"del_{r['id']}", use_container_width=True):
                delete_report(r["id"])
                st.success(f"Report for ticket #{r['ticket_id']} deleted.")
                st.rerun()

        # Open report in new tab via JS
        if f"_open_{r['id']}" in st.session_state:
            html_content = st.session_state.pop(f"_open_{r['id']}")
            _open_in_new_tab(html_content)

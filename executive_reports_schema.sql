CREATE TABLE IF NOT EXISTS executive_reports (
    id SERIAL PRIMARY KEY,
    ticket_id TEXT,
    subject TEXT,
    status TEXT,
    category TEXT,
    assignee TEXT,
    problem_summary TEXT,
    html_report TEXT,
    saved_at TIMESTAMPTZ DEFAULT NOW(),
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    num_updates INTEGER DEFAULT 0,
    ai_json TEXT
);

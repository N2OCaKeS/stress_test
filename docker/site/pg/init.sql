CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE INDEX idx_message_content ON message (content);

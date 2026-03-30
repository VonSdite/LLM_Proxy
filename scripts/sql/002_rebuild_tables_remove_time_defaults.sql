BEGIN TRANSACTION;

DROP INDEX IF EXISTS idx_users_ip;
DROP INDEX IF EXISTS idx_start_time;
DROP INDEX IF EXISTS idx_daily_stats_date;

ALTER TABLE users RENAME TO users_old;
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    ip_address TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    is_admin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
INSERT INTO users (id, username, password_hash, ip_address, is_active, is_admin, created_at, updated_at)
SELECT id, username, password_hash, ip_address, is_active, is_admin, created_at, updated_at
FROM users_old;
DROP TABLE users_old;

ALTER TABLE request_logs RENAME TO request_logs_old;
CREATE TABLE request_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_model TEXT NOT NULL,
    response_model TEXT,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    start_time TEXT NOT NULL,
    end_time TEXT,
    ip_address TEXT,
    username TEXT,
    created_at TEXT NOT NULL
);
INSERT INTO request_logs (
    id,
    request_model,
    response_model,
    total_tokens,
    prompt_tokens,
    completion_tokens,
    start_time,
    end_time,
    ip_address,
    username,
    created_at
)
SELECT
    id,
    request_model,
    response_model,
    total_tokens,
    prompt_tokens,
    completion_tokens,
    start_time,
    end_time,
    ip_address,
    username,
    created_at
FROM request_logs_old;
DROP TABLE request_logs_old;

ALTER TABLE daily_request_stats RENAME TO daily_request_stats_old;
CREATE TABLE daily_request_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stat_date TEXT NOT NULL,
    ip_address TEXT NOT NULL,
    username TEXT,
    request_model TEXT NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
INSERT INTO daily_request_stats (
    id,
    stat_date,
    ip_address,
    username,
    request_model,
    request_count,
    total_tokens,
    prompt_tokens,
    completion_tokens,
    created_at,
    updated_at
)
SELECT
    id,
    stat_date,
    ip_address,
    username,
    request_model,
    request_count,
    total_tokens,
    prompt_tokens,
    completion_tokens,
    created_at,
    updated_at
FROM daily_request_stats_old;
DROP TABLE daily_request_stats_old;

CREATE INDEX idx_users_ip ON users(ip_address);
CREATE INDEX idx_start_time ON request_logs(start_time);
CREATE INDEX idx_daily_stats_date ON daily_request_stats(stat_date);

COMMIT;

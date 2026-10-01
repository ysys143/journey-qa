-- Minimal sqlite stand-in for the example-app tables identity.sql reads. Column names follow
-- the product schema described in the adapter README; types are simplified.
CREATE TABLE app_users (email TEXT NOT NULL UNIQUE, role TEXT NOT NULL DEFAULT 'user', app_user_id TEXT NOT NULL DEFAULT '');
CREATE TABLE app_sessions (session_id TEXT NOT NULL DEFAULT '', user_id TEXT NOT NULL DEFAULT '', profile_email TEXT NOT NULL DEFAULT '', login_email TEXT NOT NULL DEFAULT '', account_id TEXT NOT NULL DEFAULT '', provider TEXT NOT NULL DEFAULT 'provider-a');
CREATE TABLE app_usage_samples (provider TEXT NOT NULL, account_id TEXT NOT NULL, window_key TEXT NOT NULL, sampled_at TEXT NOT NULL, login_email TEXT NOT NULL DEFAULT '', profile_email TEXT, plan TEXT NOT NULL DEFAULT '');

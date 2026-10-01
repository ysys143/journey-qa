-- Minimal product-agnostic identity schema used only by the gate selftest.
CREATE TABLE users (
  id INTEGER PRIMARY KEY,
  handle TEXT NOT NULL UNIQUE,
  email TEXT NOT NULL,
  role TEXT NOT NULL
);
CREATE TABLE accounts (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  provider TEXT NOT NULL,
  account_id TEXT NOT NULL,
  login_email TEXT NOT NULL
);

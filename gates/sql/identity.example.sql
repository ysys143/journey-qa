-- Product-agnostic identity assertions (example).
-- Written against the selftest sqlite schema (gates/selftest/fixtures/sql/schema.sql):
--   users(id, handle, email, role)
--   accounts(id, user_id, provider, account_id, login_email)
-- Adapters copy this file and map the table/column names to the product's
-- stored shape. Every block must return 0 rows. Run with --roster so that the
-- roster_identity(kind, value) CTE is available.

-- assert: user-handle-in-roster
SELECT u.id
FROM users AS u
WHERE u.handle NOT IN (SELECT value FROM roster_identity WHERE kind = 'handle')

-- assert: user-email-in-roster
SELECT u.id
FROM users AS u
WHERE u.email NOT IN (SELECT value FROM roster_identity WHERE kind IN ('email', 'login_email'))

-- assert: account-login-email-in-roster
SELECT a.id
FROM accounts AS a
WHERE a.login_email NOT IN (SELECT value FROM roster_identity WHERE kind IN ('login_email', 'email'))

-- assert: login-email-one-account-per-provider
SELECT a.login_email, a.provider
FROM accounts AS a
GROUP BY a.login_email, a.provider
HAVING count(DISTINCT a.account_id) > 1

-- assert: account-id-one-login-email
SELECT a.account_id
FROM accounts AS a
GROUP BY a.account_id
HAVING count(DISTINCT a.login_email) > 1

-- assert: provider-account-in-roster
SELECT a.id
FROM accounts AS a
WHERE (a.provider || '|' || a.account_id)
      NOT IN (SELECT value FROM roster_identity WHERE kind = 'provider_account')

-- assert: user-count-equals-roster
SELECT 1
WHERE (SELECT count(*) FROM users)
      <> (SELECT count(*) FROM roster_identity WHERE kind = 'handle')

-- assert: admins-are-roster-admins
SELECT u.handle
FROM users AS u
WHERE u.role = 'admin'
  AND u.handle NOT IN (SELECT value FROM roster_identity WHERE kind = 'privileged_handle')
UNION ALL
SELECT r.value
FROM roster_identity AS r
WHERE r.kind = 'privileged_handle'
  AND r.value NOT IN (SELECT u2.handle FROM users AS u2 WHERE u2.role = 'admin')

-- Accounts: who may sign in (Ubisoft Connect's sign-in screen, the launcher). An account owns one player;
-- the email can change, the account id never does. password_hash is scrypt (see services/accounts.py).
CREATE TABLE accounts (
    account_id    TEXT PRIMARY KEY,                  -- GUID, lower-case
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    profile_id    TEXT NOT NULL UNIQUE REFERENCES players(profile_id) ON DELETE CASCADE,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

-- Remember-me tickets handed to Ubisoft Connect: it signs in with them (rm_v1) instead of the password,
-- also after a server restart. Changing the password revokes them.
CREATE TABLE remember_tickets (
    ticket     TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
    created_at TEXT NOT NULL
);
CREATE INDEX remember_tickets_account ON remember_tickets(account_id);

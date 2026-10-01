-- Seed: a user email that is not in the roster. Expected FAIL.
INSERT INTO users (id, handle, email, role) VALUES
  (1, 'adaplaceholder', 'ada.placeholder@example.com', 'admin'),
  (2, 'basilmock', 'basil.mockingworth@example.org', 'user'),
  (3, 'cleostub', 'cleo.stubbington@example.net', 'user'),
  (4, 'dorianfaux', 'dorian.fauxley@example.com', 'user');
INSERT INTO accounts (id, user_id, provider, account_id, login_email) VALUES
  (1, 1, 'provider-x', 'd2cbcbc9-6ee1-4bf5-a5e9-161d4fbb3273', 'ada.placeholder@example.com'),
  (2, 2, 'provider-x', 'd859cad8-9c36-496b-9c4f-145312d5440c', 'basil.mockingworth@example.org'),
  (3, 3, 'provider-x', '146e2c2a-b2a7-42c1-99bf-db1e9d6704a7', 'cleo.stubbington@example.net'),
  (4, 4, 'provider-x', 'c94a485a-d9d4-4a6b-9a35-a26f3bd9f939', 'dorian.fauxley@example.com');
UPDATE users SET email = 'intruder.fixture@example.net' WHERE id = 3;

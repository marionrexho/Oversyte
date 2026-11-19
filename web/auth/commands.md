# Auth / SQLite quick commands

- Install the SQLite CLI on macOS:
  - `brew install sqlite`
- Check the installed version:
  - `sqlite3 --version`
- Open the app database (created automatically the first time the server runs):
  - `sqlite3 data/oversyte.db`
- Show tables:
  - `.tables`
- Inspect saved users:
  - `SELECT id, name, email, datetime(created, 'unixepoch') AS created_at FROM users ORDER BY id DESC;`
- Exit SQLite:
  - `.quit`

Notes:
- Python already includes the `sqlite3` module, so no extra pip package is needed.
- The auth data lives in the same local database file as the rest of the app.

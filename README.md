# BT Standup Progress Tracker

A small shared progress tracker for a technical team. Entries support required
target dates, owner initials, notes, completion percentages, soft deletion,
change history, filtering, and drag-and-drop ordering. A Flask web server hosts
the browser interface and stores everything in a SQLite database. Server-Sent
Events notify connected browsers about changes made by teammates.
Entries can also link to a BiztechProjects project. Configure
`BIZTECH_PROJECTS_BASE_URL` and `BIZTECH_PROJECTS_TOKEN` in `.env`; linked
entries use that project's task-completion percentage instead of local progress.
The deployed application version comes from `VERSION.txt` and appears in the page
footer. Update that file as part of each release.

## Windows setup

Install Python 3.11 or newer from [python.org](https://www.python.org/downloads/).
During installation, select **Add Python to PATH**.

Open Command Prompt in the project directory and run:

```bat
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

Edit `.env` if needed. The defaults are:

```dotenv
HOST=0.0.0.0
PORT=8080
DATABASE_PATH=./data/standup.db
BACKUP_DIRECTORY=./backups
BACKUP_RETENTION_DAYS=30
SERVER_THREADS=32
SSE_HEARTBEAT_SECONDS=20
```

Relative paths are resolved from the project directory. Windows absolute paths,
such as `C:\BTStandup\data\standup.db`, are also supported.

Start the application with:

```bat
scripts\start.bat
```

The database and its parent directory are created automatically. Team members can
open `http://SERVER_IP:8080`, replacing `SERVER_IP` with the Windows computer's IP.
Keep `SERVER_THREADS` comfortably above the maximum number of simultaneously open
browser tabs because each live-update connection occupies one Waitress thread.

## Database migrations

Numbered SQL migrations live in `migrations`. Applied versions are recorded in
the database's `schema_migrations` table. The application checks and applies
pending migrations during startup. They can also be run explicitly with:

```bat
scripts\migrate.bat
```

For a production update, stop the application, run `scripts\backup.bat`, deploy
the new files, run `scripts\migrate.bat`, and then start the application. Existing
MVP databases are detected and baselined before the new migration is applied.

## Windows Firewall

Allow inbound TCP traffic to the configured port on the Private network profile.
For the default configuration, create an inbound rule for TCP port `8080`. Do not
enable the rule for Public networks unless that is intentional.

## Daily backups

Running the following command creates a safe SQLite backup named with the current
date and removes backups older than `BACKUP_RETENTION_DAYS`:

```bat
scripts\backup.bat
```

To schedule it daily:

1. Open **Task Scheduler** and select **Create Basic Task**.
2. Name it `BT Standup Daily Backup` and choose the **Daily** trigger.
3. Choose **Start a program**.
4. For the program, browse to `scripts\backup.bat` in this project.
5. Set **Start in** to the project's full directory path.
6. Save the task and use **Run** once to verify that a dated file appears in
   `backups`.

Only one backup is retained for each date. Set `BACKUP_RETENTION_DAYS=0` to keep
all backups.

## Restore a backup

1. Stop the application.
2. Copy the current database somewhere safe if it may still be needed.
3. Copy the selected backup over the path configured by `DATABASE_PATH`.
4. Start the application and verify the entries.

Do not copy the live database directly for routine backups; use `backup.bat` so
SQLite can produce a consistent backup while the application is running.

## Development and tests

Install the development dependencies and run the tests:

```bat
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest
```

API endpoints:

- `GET /api/health`
- `GET /api/version`
- `GET /api/entries`
- `POST /api/entries`
- `PATCH /api/entries/<id>`
- `DELETE /api/entries/<id>`
- `POST /api/entries/<id>/restore`
- `GET /api/entries/<id>/history`
- `PUT /api/entries/order`
- `GET /api/events` (Server-Sent Events)

`GET /api/entries` accepts `target_date_from`, `target_date_to`, `q`, `initials`,
`deleted`, and `sort` query parameters. Updates, deletion, restoration, and manual
ordering use entry revisions and return `409 Conflict` for stale browser data.

## Repository hygiene

The `.env` file, virtual environment, SQLite databases, generated backups, logs,
and Python caches are ignored by Git. Commit `.env.example`, but never commit the
real `.env` or database files.

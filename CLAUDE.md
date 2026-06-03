# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies (uses vend/ as the virtual environment directory)
pip install -r requirements.txt

# Run the development server (http://localhost:5001)
python app.py

# Run tests
pytest

# Run a specific test file
pytest tests/test_auth.py
```

## Architecture

This is a **Flask 3.1.3** server-rendered web app called **Spendly** — an expense tracker with an Indian Rupee (₹) theme.

### Structure

- `app.py` — All Flask routes and app factory. Routes are grouped by feature with step comments (Step 1, Step 3, etc.) indicating which curriculum step implements that route.
- `database/db.py` — SQLite database helpers (`get_db`, `init_db`, `seed_db`). Currently a stub; the database file is `expense_tracker.db` (gitignored).
- `templates/` — Jinja2 templates. All pages extend `base.html`, which provides the shared navbar and footer.
- `static/css/` — `style.css` holds global CSS variables and component styles; `landing.css` is landing-page-specific.

### Routing

Flask decorator-based routes in `app.py`. Planned routes (currently returning placeholder strings until implemented):
- `GET/POST /register`, `/login`, `/logout`
- `GET /profile`
- `GET/POST /expenses/add`
- `GET/POST /expenses/<int:id>/edit`
- `POST /expenses/<int:id>/delete`

### Design System

CSS custom properties defined in `style.css`:
- Colors: `--color-ink-*`, `--color-paper-*`, `--color-accent-*`, `--color-danger-*`
- Typography: DM Serif Display (headings) + DM Sans (body), loaded from Google Fonts in `base.html`

### Project Context

This is a **training/curriculum project** — routes are scaffolded with step numbers guiding students to implement features incrementally. When adding features, preserve the step-comment pattern in `app.py`.

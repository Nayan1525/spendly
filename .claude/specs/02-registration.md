# Spec: User Registration

## Overview

Step 2 implements user account registration. The `GET /register` route displays a registration form; the `POST /register` route validates input, checks for duplicate emails, hashes passwords, and creates new user accounts in the database. This step establishes the foundation for user authentication before implementing login and dashboard features.

## Depends on

- Step 1: Database Setup — users table must exist with email uniqueness constraint

## Routes

- `GET /register` — Display registration form (already exists, return `register.html`)
- `POST /register` — Handle form submission, validate, create user, redirect on success

## Database changes

No new tables or columns. Uses existing `users` table from Step 1.

## Templates

- **Modify:** `register.html` — already exists with form fields (name, email, password); add error display via `{% if error %}` block (already present)

## Files to change

- `app.py` — implement `POST /register` handler

## Files to create

None.

## New dependencies

No new dependencies. Uses existing `werkzeug.security.generate_password_hash`.

## Rules for implementation

- No SQLAlchemy or ORMs
- Parameterised queries only — no string formatting in SQL
- Passwords hashed with `werkzeug.security.generate_password_hash`
- Use CSS variables — never hardcode hex values
- All templates extend `base.html` (already done in `register.html`)
- Use sessions or cookies for authentication state (Step 3)

## Validation rules

- **Name:** non-empty, min 2 characters, max 100 characters
- **Email:** valid email format (basic check: contains `@` and `.`), max 120 characters
- **Password:** min 8 characters, max 128 characters
- **Confirm password:** must match password field exactly, error "Passwords do not match."
- **Duplicate email:** reject if email already exists in database with error "Email already registered"

## Definition of done

- [x] `POST /register` accepts name, email, password, confirm_password from form submission
- [x] Validates all fields and returns errors on invalid input
- [x] Confirms that password and confirm_password fields match before creating user
- [x] Rejects duplicate emails with appropriate error message
- [x] Hashes password using `werkzeug.security.generate_password_hash`
- [x] Inserts user into database with hashed password
- [x] Redirects to `/login` on successful registration
- [x] Error messages display in the form's error div
- [x] Form preserves user's name/email input on validation errors (repopulation)
- [x] No unhandled exceptions — all SQL errors caught and shown as user-friendly messages


# Spec: Login and Logout

## Overview

Step 3 implements user authentication and session management. The `POST /login` route validates user credentials against the database, sets a session cookie, and redirects to a dashboard on success. The `GET /logout` route clears the session and returns the user to the landing page. This step also updates the navbar to show different links based on authentication state, providing the foundation for protected routes in later steps.

## Depends on

- Step 1: Database Setup — users table with password_hash column
- Step 2: Registration — users can create accounts with hashed passwords

## Routes

- `GET /login` — Display login form (already exists, return `login.html`)
- `POST /login` — Handle form submission, validate credentials, set session, redirect to dashboard
- `GET /logout` — Clear session, redirect to landing page

## Database changes

No new tables or columns. Uses existing `users` table from Step 1.

## Templates

- **Modify:** `login.html` — add email field value repopulation on error (like register.html)
- **Modify:** `base.html` — update navbar to show different links based on session state:
  - When logged out: show "Sign in" and "Get started" (current state)
  - When logged in: show user's name and "Sign out" button

## Files to change

- `app.py` — implement `POST /login` and `GET /logout` handlers
- `templates/base.html` — update navbar with session-aware links
- `templates/login.html` — add email field value repopulation

## Files to create

None.

## New dependencies

No new dependencies. Uses `werkzeug.security.check_password_hash` (already available).

## Rules for implementation

- No SQLAlchemy or ORMs
- Parameterised queries only — no string formatting in SQL
- Passwords verified with `werkzeug.security.check_password_hash`
- Use Flask's built-in `session` object (cookie-backed)
- Set `session['user_id']` and `session['user_name']` on successful login
- Clear session completely on logout using `session.clear()`
- Use CSS variables — never hardcode hex values
- All templates extend `base.html`
- Protect login form: if user is already logged in (session exists), redirect to dashboard instead of showing form

## Validation rules

- **Email:** required, must exist in users table
- **Password:** required, must match the hashed password in database using `check_password_hash`
- **Error handling:** on invalid email or wrong password, show error "Invalid email or password." (don't reveal which is wrong for security)

## Definition of done

- [ ] `GET /login` displays form when user is not logged in
- [ ] `POST /login` accepts email and password from form submission
- [ ] Validates email exists in users table
- [ ] Validates password against stored hash using `check_password_hash`
- [ ] Sets `session['user_id']` and `session['user_name']` on successful login
- [ ] Redirects to `/dashboard` on successful login
- [ ] Shows error "Invalid email or password." for wrong credentials (no field-specific errors)
- [ ] Form repopulates email on login error
- [ ] Password field NOT repopulated on error (security best practice)
- [ ] `GET /logout` clears session and redirects to `/` (landing page)
- [ ] Navbar shows different links based on login state:
  - Logged out: "Sign in" and "Get started" buttons
  - Logged in: "Welcome, [name]" text and "Sign out" button
- [ ] If already logged in, `GET /login` redirects to `/dashboard` instead of showing form
- [ ] All SQL errors handled gracefully with user-friendly messages
- [ ] No unhandled exceptions

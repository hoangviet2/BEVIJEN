# Frontend

React app (Vite) for **specialists**: they sign in, review flagged records, and verify the facilities
that the AI agents and data feeds couldn't confirm. It talks only to the backend REST API, never to AWS.

**Backend reference:** [`../docs/BACKEND-FOR-FRONTEND.md`](../docs/BACKEND-FOR-FRONTEND.md).
Live at **https://52-41-36-135.sslip.io/ui/** (the backend serves `frontend/dist`; `backend/infra/deploy.sh server`
builds it into the image). `UI_prototype.html` is the old single-file review page; its record-review flow is now the
**Records** tab.

## Run

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
npm test           # unit checks for auth/role/zip helpers
npm run build      # static files in dist/ (relative paths: host at / or /ui/)
```

`.env` points at the deployed backend (`https://52-41-36-135.sslip.io`, Cognito sign-in). The dev server
proxies API paths to `VITE_BACKEND_URL`, so no CORS is needed. To use a **local backend** instead, create
`.env.local`:

```
VITE_BACKEND_URL=http://127.0.0.1:8000
VITE_AUTH_MODE=dev
```

and start the backend as in §2 of the backend guide (`STORE_BACKEND=memory AUTH_MODE=dev …`). In dev mode the
login page asks for an email and a role, and sends them as `X-Dev-User` / `X-Dev-Groups`.

## Sign-in (Cognito)

"Sign in" goes to `<backend>/auth/login`. The Hosted UI sends the browser back to the server's `UI_URL` with
`#id_token=…`. The app reads that, keeps it in `localStorage.re_session`, and sends `Authorization: Bearer`.
Tokens last 1 h with no refresh: on expiry or any 401 the app goes back to the login page.

Served at `/ui/`, which is the server's default `UI_URL`, so the redirect lands here. New people use
**Request access** on the login page; an admin approves them in the **Team** tab (only admins see it). Signed-in
users without a role see "Waiting for approval".

## Screens

| Screen | Endpoints |
|---|---|
| Records | `GET /records?status=…`, `GET /findings?status=open`, `GET /records/{id}` |
| Sign-up / Team (admin) | `POST /auth/signup` · `GET /admin/signups`, `…/approve`, `…/reject` · `GET /admin/users`, `POST /admin/users/{email}/role` |
| Record decisions | `POST /records/{id}/resolve` (one call: every decision + verified) · `POST /verify/{id}` (paid AI run, asks first) |

Files: `src/api.js` (fetch + session), `src/lib.js` (pure helpers, tested), `src/App.jsx` (login + shell),
`src/Records.jsx`, `src/Team.jsx`, `src/ui.jsx`, `src/styles.css`.

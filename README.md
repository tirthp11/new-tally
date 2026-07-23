# abs-tally-suite

Invoice-to-Tally automation for Asia Bulk Sacks Pvt Ltd.

- `cloud/backend`   - FastAPI service: auth, AI extraction, XML building, job queue, WebSocket hub.
- `cloud/frontend`  - Plain HTML/CSS/JavaScript single-page shell (no framework, no build step).
- `connector/`      - Desktop Connector (PySide6). The only component that talks to Tally.
- `shared/contracts`- The message and data contracts between cloud and connector.
- `reference/`      - The original scripts kept for reference while their logic is ported.
- `docs/`           - The implementation plan (source of truth for design decisions).

## Development quick start

1. Create the database in your own PostgreSQL (pgAdmin), for example `abs_tally`.
2. Copy `.env.example` to `cloud/backend/.env` and fill in `DATABASE_URL`,
   `OPENAI_API_KEY`, `JWT_SECRET`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`.
3. Install and run the backend:

   ```
   cd cloud/backend
   pip install -r requirements.txt
   alembic upgrade head
   uvicorn app.main:app --reload
   ```

4. Open http://localhost:8000 - the backend serves the frontend directly.
5. Run the connector on the machine that has Tally:

   ```
   cd connector
   pip install -r requirements.txt
   python -m connector.main
   ```

## Production (Railway)

Deploy the repository on Railway with the Railway PostgreSQL add-on and set the same
environment variables. The only database change from development is the value of
`DATABASE_URL`. `railway.json` runs migrations and starts uvicorn.

## Invariants (do not break)

- The connector supports exactly three actions: `list_companies`, `fetch_masters`,
  `post_tally_xml`. There is no delete action.
- Deleting a voucher in the web app is a soft delete plus an audit row. It never
  creates a connector job and never touches Tally.
- Uploaded files are processed in memory and discarded after extraction. They are
  never written to disk or object storage.
- The extracted JSON is stored exactly as the AI returned it. Only the user edits it.
- The OpenAI key lives only on the cloud backend, never in the connector.

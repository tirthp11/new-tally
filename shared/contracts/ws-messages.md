# WebSocket messages: cloud <-> connector

Endpoint: `wss://<cloud-host>/api/v1/connector/ws`. The connector always
initiates. Messages are JSON objects with a `type` field. `job_id` ties a job
to its result. This file is the single source of truth for both sides.

## Handshake

Connector sends first:

```json
{ "type": "hello", "token": "<pairing token>", "app_version": "1.0.0" }
```

Cloud replies with one of:

```json
{ "type": "auth_ok", "connector_id": "..." }
{ "type": "auth_error", "reason": "invalid token" }
```

After `auth_ok`, the cloud marks the connector online, delivers any pending
jobs, and refreshes the company list with a `list_companies` job.

## Heartbeat

Every 20 seconds:

```json
connector -> { "type": "ping" }
cloud     -> { "type": "pong" }
```

## Jobs (cloud to connector)

The only three actions that exist. There is no delete action by design.

```json
{ "type": "job", "job_id": "...", "action": "list_companies", "payload": {} }

{ "type": "job", "job_id": "...", "action": "fetch_masters",
  "payload": { "company": "Tirth_dvbc" } }

{ "type": "job", "job_id": "...", "action": "post_tally_xml",
  "payload": { "company": "Tirth_dvbc", "label": "Voucher Import",
               "xml": "<ENVELOPE>...</ENVELOPE>" } }
```

## Job results (connector to cloud)

```json
{ "type": "job_result", "job_id": "...", "ok": true,
  "data": { "companies": ["Tirth_dvbc", "Asia Bulk Sacks"] } }

{ "type": "job_result", "job_id": "...", "ok": true,
  "data": { "ledgers": [ { "name": "...", "parent": "..." } ],
            "groups": ["..."], "units": ["..."],
            "stock_items": [ { "name": "...", "unit": "..." } ],
            "company_start_date": "20260401" } }

{ "type": "job_result", "job_id": "...", "ok": true,
  "data": { "raw_response": "<ENVELOPE>...Tally response...</ENVELOPE>" } }

{ "type": "job_result", "job_id": "...", "ok": false,
  "error": "Tally connection error: ..." }
```

For `post_tally_xml` the connector returns Tally's raw XML response untouched;
the cloud parses it and runs the self-heal loop if needed.

## Sync initiated from the connector window

```json
connector -> { "type": "sync_now" }
cloud     -> { "type": "sync_done", "ok": true,
               "companies_seen": ["..."], "masters_synced": ["..."] }
```

## Rules

- The connector processes one job at a time to avoid overlapping Tally imports.
- Every job has a timeout on the cloud side; a job with no result becomes
  `error` and the user sees "connector did not respond".
- The only `action` values are `list_companies`, `fetch_masters`,
  `post_tally_xml`.

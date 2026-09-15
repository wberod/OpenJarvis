# OpenJarvis — working notes

## Testing

- Run tests with an isolated config dir, otherwise connector tests pick up real
  credentials from `~/.openjarvis` and fail with false `is_connected()` results:
  `OPENJARVIS_HOME=/tmp/oj_test_home uv run pytest ...`
- Frontend typecheck: `cd frontend && npx tsc --noEmit`

## SC_Assist (company agent)

- Template: `src/openjarvis/agents/templates/sc_assist.toml` — seeded on server
  start in `cli/serve.py` (`create_from_template("sc_assist", "SC_Assist")`).
- Frontend auto-selects the agent named `SC_Assist` in
  `frontend/src/lib/store.ts` (`setManagedAgents`).
- `include_mcp_tools = true` in an agent's config grants every tool discovered
  from `[tools.mcp]` servers (e.g. Box at `https://mcp.box.com`).

## Env vars for Sheridan integrations

- `SNOWFLAKE_ACCOUNT/USER/PASSWORD/WAREHOUSE/DATABASE/SCHEMA/ROLE` — `snowflake_query` tool
- `OPENJARVIS_MICROSOFT_CLIENT_ID` / `OPENJARVIS_MICROSOFT_CLIENT_SECRET`
  (+ optional `OPENJARVIS_MICROSOFT_TENANT_ID`, default `common`) — `ms365`
  connector + `ms365_*` tools; also used to refresh hub-stored user tokens
- `SUPABASE_HUB_URL` / `SUPABASE_HUB_KEY` — SC Command Center (project `fhciongdwuanykfklzvk`);
  service-role key required — the `ms365_*` tools read/write per-user Microsoft
  tokens in the `profiles` table (columns `microsoft_access_token`,
  `microsoft_refresh_token`, `microsoft_token_expires_at`, keyed by `email`;
  overridable via `MS365_PROFILE_*` env vars)
- `OPENJARVIS_HUB_URL` (default `https://hub.sheridanfunds.com`) — where agents
  send users to sign in with Microsoft
- `SUPABASE_CREDIT_URL` / `SUPABASE_CREDIT_KEY` — Originator Pipeline (project `wygxdnududzgilepvsxe`)
- Box MCP: add `{"name": "box", "url": "https://mcp.box.com", "token": "<oauth-token>"}`
  to `tools.mcp.servers` in config.toml (see docs/user-guide/mcp-external-servers.md)

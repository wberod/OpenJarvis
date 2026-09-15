"""Supabase query tool — read-only access to Sheridan dashboards' data.

Both internal dashboards (SC Command Center / hub.sheridanfunds.com and
Originator Pipeline / credit.sheridanfunds.com) are Supabase-backed. This
tool queries their Postgres data through PostgREST so the agent can
summarize the same data the dashboards render.

Configuration (environment variables):

- ``SUPABASE_HUB_URL`` / ``SUPABASE_HUB_KEY`` — e.g.
  ``https://fhciongdwuanykfklzvk.supabase.co`` + a publishable or
  service-role key.
- ``SUPABASE_CREDIT_URL`` / ``SUPABASE_CREDIT_KEY`` — e.g.
  ``https://wygxdnududzgilepvsxe.supabase.co`` + key.

A service-role key bypasses RLS; an anon/publishable key sees only what
RLS allows. All access is read-only (HTTP GET against /rest/v1).
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional, Tuple

import httpx

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

# Map friendly project aliases to env-var pairs.
_PROJECT_ENV: Dict[str, Tuple[str, str]] = {
    "hub": ("SUPABASE_HUB_URL", "SUPABASE_HUB_KEY"),
    "commandcenter": ("SUPABASE_HUB_URL", "SUPABASE_HUB_KEY"),
    "sccommandcenter": ("SUPABASE_HUB_URL", "SUPABASE_HUB_KEY"),
    "credit": ("SUPABASE_CREDIT_URL", "SUPABASE_CREDIT_KEY"),
    "originator": ("SUPABASE_CREDIT_URL", "SUPABASE_CREDIT_KEY"),
    "originator-pipeline": ("SUPABASE_CREDIT_URL", "SUPABASE_CREDIT_KEY"),
}

# PostgREST root spec paths that aren't data tables.
_SCHEMA_SKIP_PREFIXES = ("rpc/", "pg_", "information_schema")


def _resolve_project(project: str) -> Tuple[Optional[str], Optional[str]]:
    env = _PROJECT_ENV.get(project.strip().lower())
    if env is None:
        return None, None
    url = os.environ.get(env[0], "").rstrip("/")
    key = os.environ.get(env[1], "")
    return (url or None), (key or None)


@ToolRegistry.register("supabase_query")
class SupabaseQueryTool(BaseTool):
    """Read-only queries against the Sheridan Supabase projects."""

    tool_id = "supabase_query"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="supabase_query",
            description=(
                "Read data from the internal Sheridan dashboards' databases "
                "(SC Command Center 'hub' and Originator Pipeline 'credit'). "
                "Read-only PostgREST queries: choose a project, optionally "
                "list its tables (mode='schema'), or query a table with "
                "select/filters/order/limit. Filter syntax follows PostgREST "
                "(e.g. 'status=eq.Active&amount=gt.100')."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "project": {
                        "type": "string",
                        "description": (
                            "Which dashboard's database: 'hub' (SC Command "
                            "Center) or 'credit' (Originator Pipeline)."
                        ),
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["query", "schema"],
                        "description": (
                            "'schema' lists available tables/views; "
                            "'query' (default) reads rows from a table."
                        ),
                    },
                    "table": {
                        "type": "string",
                        "description": "Table or view name (mode='query').",
                    },
                    "select": {
                        "type": "string",
                        "description": (
                            "Comma-separated columns to return. Default '*'."
                        ),
                    },
                    "filters": {
                        "type": "string",
                        "description": (
                            "PostgREST filter string, e.g. "
                            "'status=eq.Active&created_at=gte.2026-01-01'."
                        ),
                    },
                    "order": {
                        "type": "string",
                        "description": (
                            "Order clause, e.g. 'created_at.desc'."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max rows. Default 50, max 500.",
                    },
                },
                "required": ["project"],
            },
            category="database",
            timeout_seconds=30.0,
            required_capabilities=["database:read"],
        )

    def execute(self, **params: Any) -> ToolResult:
        project = str(params.get("project", "")).strip()
        mode = str(params.get("mode", "query")).strip().lower()

        if not project:
            return ToolResult(
                tool_name="supabase_query",
                content="No project provided. Use 'hub' or 'credit'.",
                success=False,
            )

        url, key = _resolve_project(project)
        if not url or not key:
            env = _PROJECT_ENV.get(project.lower())
            if env is None:
                known = "'hub' or 'credit'"
                return ToolResult(
                    tool_name="supabase_query",
                    content=(
                        f"Unknown project '{project}'. Use {known}."
                    ),
                    success=False,
                )
            return ToolResult(
                tool_name="supabase_query",
                content=(
                    f"Project '{project}' not configured. Set {env[0]} "
                    f"and {env[1]} environment variables."
                ),
                success=False,
            )

        headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
        }

        if mode == "schema":
            return self._list_tables(url, headers, project)
        return self._query_table(url, headers, params)

    # ------------------------------------------------------------------

    def _list_tables(
        self, url: str, headers: Dict[str, str], project: str
    ) -> ToolResult:
        try:
            resp = httpx.get(
                f"{url}/rest/v1/", headers=headers, timeout=20.0
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            return ToolResult(
                tool_name="supabase_query",
                content=f"Schema request failed: {exc}",
                success=False,
            )

        spec = resp.json()
        definitions = spec.get("definitions") or spec.get("components", {}).get(
            "schemas", {}
        )
        tables = sorted(
            name
            for name in definitions
            if not name.startswith(_SCHEMA_SKIP_PREFIXES)
        )
        content = (
            f"Tables/views in '{project}' ({len(tables)}):\n"
            + "\n".join(f"- {t}" for t in tables)
        )
        return ToolResult(
            tool_name="supabase_query",
            content=content,
            success=True,
            metadata={"table_count": len(tables), "project": project},
        )

    def _query_table(
        self, url: str, headers: Dict[str, str], params: Dict[str, Any]
    ) -> ToolResult:
        table = str(params.get("table", "")).strip()
        if not table:
            return ToolResult(
                tool_name="supabase_query",
                content=(
                    "No table provided. Call with mode='schema' first to "
                    "list available tables."
                ),
                success=False,
            )
        if not table.replace("_", "").replace(".", "").isalnum():
            return ToolResult(
                tool_name="supabase_query",
                content=f"Invalid table name: {table!r}",
                success=False,
            )

        limit = int(params.get("limit") or 50)
        limit = max(1, min(limit, 500))

        query: Dict[str, Any] = {
            "select": params.get("select") or "*",
            "limit": limit,
        }
        if params.get("order"):
            query["order"] = params["order"]
        # Merge PostgREST filter string ("a=eq.1&b=gt.2") into query params.
        filters = str(params.get("filters") or "").strip()
        if filters:
            for pair in filters.split("&"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    query[k.strip()] = v.strip()

        try:
            resp = httpx.get(
                f"{url}/rest/v1/{table}",
                headers=headers,
                params=query,
                timeout=25.0,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = ""
            try:
                detail = exc.response.json().get("message", "")
            except Exception:
                detail = exc.response.text[:300]
            return ToolResult(
                tool_name="supabase_query",
                content=(
                    f"Query failed ({exc.response.status_code}): "
                    f"{detail or exc}"
                ),
                success=False,
            )
        except httpx.HTTPError as exc:
            return ToolResult(
                tool_name="supabase_query",
                content=f"Query request failed: {exc}",
                success=False,
            )

        rows = resp.json()
        if not isinstance(rows, list):
            rows = [rows]
        content = json.dumps(rows, indent=2, default=str)
        if len(content) > 20000:
            content = content[:20000] + "\n... (truncated)"

        return ToolResult(
            tool_name="supabase_query",
            content=content or "[]",
            success=True,
            metadata={
                "row_count": len(rows),
                "table": table,
                "truncated": len(json.dumps(rows, default=str)) > 20000,
            },
        )


__all__ = ["SupabaseQueryTool"]

"""Tests for the supabase_query tool — read-only PostgREST access."""

from __future__ import annotations

from unittest.mock import patch

from openjarvis.tools.supabase_query import SupabaseQueryTool


def test_spec() -> None:
    tool = SupabaseQueryTool()
    assert tool.spec.name == "supabase_query"
    assert tool.spec.category == "database"


def test_unconfigured_project() -> None:
    tool = SupabaseQueryTool()
    res = tool.execute(project="hub", table="deals")
    assert res.success is False
    assert "SUPABASE_HUB_URL" in res.content


def test_unknown_project() -> None:
    tool = SupabaseQueryTool()
    res = tool.execute(project="bogus")
    assert res.success is False
    assert "Unknown project" in res.content


def test_missing_table() -> None:
    tool = SupabaseQueryTool()
    with patch.dict(
        "os.environ",
        {"SUPABASE_HUB_URL": "https://x.supabase.co", "SUPABASE_HUB_KEY": "k"},
    ):
        res = tool.execute(project="hub")
    assert res.success is False
    assert "No table" in res.content


def test_invalid_table_name() -> None:
    tool = SupabaseQueryTool()
    with patch.dict(
        "os.environ",
        {"SUPABASE_HUB_URL": "https://x.supabase.co", "SUPABASE_HUB_KEY": "k"},
    ):
        res = tool.execute(project="hub", table="deals; drop table x--")
    assert res.success is False
    assert "Invalid table" in res.content


def test_query_success() -> None:
    tool = SupabaseQueryTool()

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"id": 1, "status": "Active"}]

    with patch.dict(
        "os.environ",
        {"SUPABASE_HUB_URL": "https://x.supabase.co", "SUPABASE_HUB_KEY": "k"},
    ), patch("httpx.get", return_value=_Resp()) as get:
        res = tool.execute(
            project="hub",
            table="deals",
            filters="status=eq.Active",
            limit=10,
        )
    assert res.success is True
    assert '"Active"' in res.content
    assert res.metadata["row_count"] == 1
    call = get.call_args
    assert call[0][0] == "https://x.supabase.co/rest/v1/deals"
    assert call[1]["params"]["status"] == "eq.Active"
    assert call[1]["headers"]["apikey"] == "k"


def test_schema_mode() -> None:
    tool = SupabaseQueryTool()

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"definitions": {"deals": {}, "brokers": {}, "rpc/x": {}}}

    with patch.dict(
        "os.environ",
        {"SUPABASE_HUB_URL": "https://x.supabase.co", "SUPABASE_HUB_KEY": "k"},
    ), patch("httpx.get", return_value=_Resp()):
        res = tool.execute(project="hub", mode="schema")
    assert res.success is True
    assert "deals" in res.content
    assert "brokers" in res.content
    assert "rpc/" not in res.content

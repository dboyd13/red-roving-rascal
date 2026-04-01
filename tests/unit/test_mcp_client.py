"""Unit tests for the MCP-based RascalClient."""
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest

from rascal.client import RascalClient, MCPError, sigv4_auth


GATEWAY_URL = "https://test-gw.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"


def _mock_mcp_response(result: dict) -> MagicMock:
    """Create a mock httpx response with MCP JSON-RPC result."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": result}
    return resp


def _mock_mcp_error(code: int, message: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"jsonrpc": "2.0", "id": 1, "error": {"code": code, "message": message}}
    return resp


def _mock_tool_result(text: str) -> MagicMock:
    return _mock_mcp_response({"content": [{"type": "text", "text": text}]})


class TestListTools:
    @patch("httpx.Client")
    def test_returns_tools(self, mock_client_cls):
        tools = [{"name": "rascal-api___GetHealth", "description": "Health check"}]
        mock_client_cls.return_value.__enter__ = lambda s: s
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value.post.return_value = _mock_mcp_response({"tools": tools})

        client = RascalClient(GATEWAY_URL)
        result = client.list_tools()
        assert result == tools


class TestHealth:
    @patch("httpx.Client")
    def test_returns_health_dict(self, mock_client_cls):
        mock_client_cls.return_value.__enter__ = lambda s: s
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value.post.return_value = _mock_tool_result('{"status": "ok"}')

        client = RascalClient(GATEWAY_URL)
        result = client.health()
        assert result == {"status": "ok"}

    @patch("httpx.Client")
    def test_tool_name_uses_prefix(self, mock_client_cls):
        mock_client_cls.return_value.__enter__ = lambda s: s
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value.post.return_value = _mock_tool_result('{}')

        client = RascalClient(GATEWAY_URL, target_prefix="my-api")
        client.health()

        call_body = json.loads(mock_client_cls.return_value.post.call_args.kwargs["content"])
        assert call_body["params"]["name"] == "my-api___GetHealth"


class TestGetSuites:
    @patch("httpx.Client")
    def test_returns_list(self, mock_client_cls):
        mock_client_cls.return_value.__enter__ = lambda s: s
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value.post.return_value = _mock_tool_result('["suite-1", "suite-2"]')

        client = RascalClient(GATEWAY_URL)
        result = client.get_suites()
        assert result == ["suite-1", "suite-2"]


class TestMCPError:
    @patch("httpx.Client")
    def test_raises_on_error_response(self, mock_client_cls):
        mock_client_cls.return_value.__enter__ = lambda s: s
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value.post.return_value = _mock_mcp_error(-32602, "Unknown tool")

        client = RascalClient(GATEWAY_URL)
        with pytest.raises(MCPError, match="Unknown tool"):
            client.health()


class TestSigV4Auth:
    def test_sigv4_auth_uses_bedrock_agentcore_service(self):
        """sigv4_auth() should sign with service='bedrock-agentcore'."""
        with patch("rascal.client.sigv4_headers") as mock_headers:
            mock_headers.return_value = {"Authorization": "AWS4-HMAC-SHA256 ..."}
            signer = sigv4_auth("us-east-1")
            signer("POST", "https://example.com", b"body")
            mock_headers.assert_called_once_with(
                "POST", "https://example.com", b"body",
                region="us-east-1", service="bedrock-agentcore",
            )

"""Client for the rascal API via AgentCore Gateway MCP protocol."""
from __future__ import annotations

import json
import time
from typing import Callable

import httpx

from rascal.models import (
    EvaluateRequest,
    EvaluateResponse,
    EvaluationStatus,
    InputOutputPair,
    ScoringConfig,
    TestSuite,
)
from rascal.auth import sigv4_headers


# Type alias for auth providers: (method, url, body) -> headers dict
AuthSigner = Callable[[str, str, bytes], dict[str, str]]


def sigv4_auth(region: str | None = None) -> AuthSigner:
    """Create a SigV4 auth signer for AgentCore Gateway."""
    def signer(method: str, url: str, body: bytes = b"") -> dict[str, str]:
        return sigv4_headers(method, url, body, region=region, service="bedrock-agentcore")
    return signer


class MCPError(Exception):
    """Raised when the MCP gateway returns a JSON-RPC error."""
    def __init__(self, code: int, message: str):
        self.code = code
        super().__init__(f"MCP error {code}: {message}")


class RascalClient:
    """Client for interacting with a rascal backend via AgentCore Gateway MCP.

    All calls go through the MCP JSON-RPC protocol. The gateway translates
    MCP tool calls into REST requests to the backend API Gateway.

    Args:
        endpoint: AgentCore Gateway MCP endpoint URL.
        auth: Auth signer. Use sigv4_auth() for IAM auth.
        timeout: Request timeout in seconds.
        target_prefix: Gateway target name prefix for tool names.

    Examples:
        # SigV4 auth (production)
        client = RascalClient(
            "https://my-gw.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp",
            auth=sigv4_auth("us-east-1"),
        )

        # Custom auth provider
        client = RascalClient("https://...", auth=my_custom_auth_signer)
    """

    def __init__(
        self,
        endpoint: str,
        auth: AuthSigner | None = None,
        timeout: float = 30.0,
        target_prefix: str = "rascal-api",
    ):
        self.endpoint = endpoint.rstrip("/")
        self.auth = auth
        self.timeout = timeout
        self._prefix = target_prefix
        self._request_id = 0

    def _tool_name(self, operation: str) -> str:
        """Build the MCP tool name: {target}___{operation}."""
        return f"{self._prefix}___{operation}"

    def _mcp_call(self, method: str, params: dict | None = None) -> dict:
        """Send an MCP JSON-RPC request and return the result."""
        self._request_id += 1
        payload: dict = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
        }
        if params:
            payload["params"] = params

        body = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"}
        if self.auth:
            headers.update(self.auth("POST", self.endpoint, body))

        with httpx.Client(timeout=self.timeout) as http:
            resp = http.post(self.endpoint, content=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        if "error" in data:
            err = data["error"]
            raise MCPError(err.get("code", -1), err.get("message", "Unknown error"))

        return data.get("result", {})

    def _call_tool(self, operation: str, arguments: dict | None = None) -> str:
        """Call an MCP tool and return the text content."""
        result = self._mcp_call("tools/call", {
            "name": self._tool_name(operation),
            "arguments": arguments or {},
        })
        # MCP returns content as [{type: "text", text: "..."}]
        content = result.get("content", [])
        if content and isinstance(content, list):
            return content[0].get("text", "")
        return json.dumps(result)

    # --- Public API (same interface as before) ---

    def list_tools(self) -> list[dict]:
        """List all MCP tools available on the gateway."""
        result = self._mcp_call("tools/list")
        return result.get("tools", [])

    def health(self) -> dict:
        """Check backend health via MCP tool call."""
        text = self._call_tool("GetHealth")
        try:
            return json.loads(text) if text else {}
        except json.JSONDecodeError:
            return {"raw": text}

    def get_suites(self) -> list[str]:
        """List available test suites."""
        text = self._call_tool("ListSuites")
        return json.loads(text) if text else []

    def get_suite(self, suite_id: str) -> TestSuite:
        """Get a specific test suite by ID."""
        text = self._call_tool("GetSuite", {"suite_id": suite_id})
        return TestSuite.model_validate_json(text)

    def evaluate(self, pairs: list[InputOutputPair], config: ScoringConfig) -> EvaluateResponse:
        """Submit an async evaluation via MCP tool call."""
        request = EvaluateRequest(pairs=pairs, config=config)
        # The tool arguments are the JSON body fields
        text = self._call_tool("Evaluate", json.loads(request.model_dump_json()))
        return EvaluateResponse.model_validate_json(text)

    def get_evaluation(self, evaluation_id: str) -> EvaluateResponse:
        """Poll for evaluation status/result."""
        text = self._call_tool("GetEvaluation", {"evaluation_id": evaluation_id})
        return EvaluateResponse.model_validate_json(text)

    def evaluate_and_wait(
        self,
        pairs: list[InputOutputPair],
        config: ScoringConfig,
        poll_interval: float = 2.0,
        timeout: float = 300.0,
    ) -> EvaluateResponse:
        """Submit an evaluation and poll until complete or failed."""
        response = self.evaluate(pairs, config)
        deadline = time.monotonic() + timeout
        while response.status not in (EvaluationStatus.COMPLETE, EvaluationStatus.FAILED):
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Evaluation {response.evaluation_id} did not complete within {timeout}s"
                )
            time.sleep(poll_interval)
            response = self.get_evaluation(response.evaluation_id)
        return response

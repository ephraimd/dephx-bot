import secrets

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from praxium.errors import error_response


def new_launch_token() -> str:
    return secrets.token_urlsafe(32)


class LaunchGuard:
    """Only the Praxium window may talk to this server.

    The Host check stops DNS-rebinding pages in the user's browser, which can reach
    127.0.0.1 under a hostname of their own. The token stops every other local
    process and page. A plain ASGI middleware, because BaseHTTPMiddleware buffers
    streaming responses and would break the SSE event stream.
    """

    def __init__(self, app: ASGIApp, *, token: str, host: str) -> None:
        self.app = app
        self.expected_auth = f"Bearer {token}"
        self.host = host

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        if headers.get("host") != self.host:
            response = error_response(
                421, "wrong_host", "Requests must come from the Praxium window."
            )
        elif _is_api(scope["path"]) and not secrets.compare_digest(
            headers.get("authorization", ""), self.expected_auth
        ):
            response = error_response(401, "unauthorized", "Missing or invalid launch token.")
        else:
            await self.app(scope, receive, send)
            return
        await response(scope, receive, send)


def _is_api(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")

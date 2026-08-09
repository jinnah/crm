import json
from collections.abc import Awaitable, Callable

Scope = dict
Message = dict
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class BodyLimitMiddleware:
    """Rejects oversized request bodies on selected path prefixes at the ASGI
    layer.

    The body is drained here, one chunk at a time, before the application is
    invoked: as soon as the running total exceeds the limit the request is
    answered with 413 and the endpoint never runs, so no JSON parsing or
    handler code ever sees an oversized payload. Content-Length, when present,
    is only a fast-fail hint — the actual received bytes are what count.
    """

    def __init__(self, app: ASGIApp, max_bytes: int, path_prefixes: tuple[str, ...]) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.path_prefixes = path_prefixes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(self.path_prefixes):
            await self.app(scope, receive, send)
            return

        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = self.max_bytes + 1
                if declared > self.max_bytes:
                    await self._reject(send)
                    return

        chunks: list[bytes] = []
        received = 0
        disconnected = False
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                disconnected = True
                break
            body = message.get("body", b"")
            received += len(body)
            if received > self.max_bytes:
                # Stop reading immediately; the application is never called.
                await self._reject(send)
                return
            if body:
                chunks.append(body)
            if not message.get("more_body", False):
                break

        replay: list[Message] = [
            {"type": "http.request", "body": b"".join(chunks), "more_body": False}
        ]
        if disconnected:
            replay = [{"type": "http.disconnect"}]

        async def replay_receive() -> Message:
            if replay:
                return replay.pop(0)
            return {"type": "http.disconnect"}

        await self.app(scope, replay_receive, send)

    async def _reject(self, send: Send) -> None:
        body = json.dumps({"detail": "Request body too large."}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

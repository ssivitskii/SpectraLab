from __future__ import annotations

import httpx
import pytest
from app.core.body_limit import BodyLimitMiddleware
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path", "size", "expected_status", "expected_limit"),
    [
        ("POST", "/measured", 20, 200, None),
        ("POST", "/measured", 21, 413, 20),
        ("POST", "/other", 11, 413, 10),
        ("PATCH", "/measured", 11, 413, 10),
    ],
)
async def test_body_limits_count_streamed_chunks_and_are_route_and_method_specific(
    method, path, size, expected_status, expected_limit
):
    async def accept(request: Request):
        return JSONResponse({"size": len(await request.body())})

    app = BodyLimitMiddleware(
        Starlette(
            routes=[
                Route("/measured", accept, methods=["POST", "PATCH"]),
                Route("/other", accept, methods=["POST"]),
            ]
        ),
        max_bytes=10,
        route_limits={("POST", "/measured"): 20},
    )

    async def chunks():
        for offset in range(0, size, 3):
            yield b"x" * min(3, size - offset)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.request(method, path, content=chunks())
    assert response.status_code == expected_status
    if expected_limit is None:
        assert response.json()["size"] == size
    else:
        assert response.json()["error"]["details"] == {"max_request_bytes": expected_limit}

from __future__ import annotations

import logging
import math
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes import experiments, predict, spectra, system
from app.core.body_limit import BodyLimitMiddleware
from app.core.config import settings
from app.core.logging import configure_logging
from app.schemas.api import ErrorResponse
from app.services.container import build_services

configure_logging()


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.services = build_services(settings)
    yield


app = FastAPI(
    title="SpectraLab API",
    version="0.1.0",
    description="Research API for synthetic atomic-emission spectra. Not a certified analyser.",
    lifespan=lifespan,
    responses={code: {"model": ErrorResponse} for code in (400, 404, 413, 422, 500, 507)},
)
app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_request_bytes)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
for router in (system.router, spectra.router, predict.router, experiments.router):
    app.include_router(router, prefix="/api/v1")


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "Request validation failed",
                "details": _json_safe(exc.errors()),
            }
        },
    )


@app.exception_handler(StarletteHTTPException)
async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": "http_error", "message": str(exc.detail), "details": None}},
    )


@app.exception_handler(FileNotFoundError)
async def not_found(_: Request, exc: FileNotFoundError) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": {"code": "not_found", "message": str(exc), "details": None}},
    )


@app.exception_handler(ValueError)
async def value_error(_: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "invalid_value", "message": str(exc), "details": None}},
    )


@app.exception_handler(OSError)
async def storage_error(_: Request, exc: OSError) -> JSONResponse:
    logging.getLogger("spectralab").error("storage_error", exc_info=exc)
    return JSONResponse(
        status_code=507,
        content={
            "error": {
                "code": "storage_error",
                "message": "Недостаточно ресурсов файлового хранилища",
                "details": None,
            }
        },
    )


@app.exception_handler(Exception)
async def unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    logging.getLogger("spectralab").error("internal_error", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_error",
                "message": "Внутренняя ошибка сервиса",
                "details": None,
            }
        },
    )


@app.middleware("http")
async def log_request(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    logging.getLogger("spectralab").info(
        "request",
        extra={
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": round((time.perf_counter() - start) * 1000, 2),
        },
    )
    return response

FROM ghcr.io/astral-sh/uv:0.6.17 AS uv
FROM python:3.12.14-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY ml ./ml
COPY backend ./backend
COPY data/fixtures ./data/fixtures
RUN uv sync --frozen --all-packages --no-dev
ENV PATH="/app/.venv/bin:$PATH" SPECTRALAB_PROJECT_ROOT=/app MPLCONFIGDIR=/tmp/matplotlib
EXPOSE 8000
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000"]

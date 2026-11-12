# Build from the repo root:  docker build -f services/worker/Dockerfile .
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1
WORKDIR /repo
COPY pyproject.toml uv.lock .python-version ./
COPY packages/signal_core packages/signal_core
COPY services/worker services/worker
RUN uv sync --frozen --no-dev --package worker
RUN useradd --create-home app && chown -R app /repo
USER app
ENV PORT=8080
EXPOSE 8080
CMD ["/repo/.venv/bin/worker"]

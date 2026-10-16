FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.8 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
ENV UV_PYTHON_DOWNLOADS=never
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --python /usr/local/bin/python --no-cache

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

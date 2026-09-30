FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY pyproject.toml ./
COPY dropship ./dropship
RUN pip install --no-cache-dir .

COPY alembic.ini ./
COPY migrations ./migrations

RUN useradd --uid 1000 --no-create-home app
USER app

# The same image runs the API (default) and the worker (`python -m dropship.worker`).
CMD ["uvicorn", "dropship.api:app", "--host", "0.0.0.0", "--port", "8000"]

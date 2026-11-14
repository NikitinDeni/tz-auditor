FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home auditor
USER auditor
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn tz_auditor.api:app --host 0.0.0.0 --port ${PORT:-8000}"]

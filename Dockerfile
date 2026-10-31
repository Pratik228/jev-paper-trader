FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY config.yaml ./

EXPOSE 8080 8081
CMD ["python", "-m", "jev_paper.loop", "--config", "/app/config.yaml", "--log-dir", "/app/logs"]

FROM python:3.11-slim@sha256:db3ff2e1800a8581e2c48a27c3995339d47bdf046da21c7627accd3d51053a93
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 HOME=/tmp
WORKDIR /app
COPY requirements.lock.txt /tmp/requirements.lock.txt
RUN pip install --no-cache-dir -r /tmp/requirements.lock.txt
COPY pyproject.toml alembic.ini /app/
COPY services /app/services
COPY ml /app/ml
COPY assets /app/assets
COPY scripts /app/scripts
COPY infra/migrations /app/infra/migrations
# Procedural fixtures are served only with APP_ENV=local and demo login enabled.
COPY tests/fixtures/images /app/tests/fixtures/images
RUN pip install --no-deps -e . && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin kc \
    && mkdir -p /app/.local /app/artifacts/manifests \
    && chown -R kc:kc /app/.local /app/artifacts
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=4 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready',timeout=4)"
CMD ["python", "-m", "uvicorn", "services.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

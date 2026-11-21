FROM python:3.11-slim@sha256:db3ff2e1800a8581e2c48a27c3995339d47bdf046da21c7627accd3d51053a93
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.lock.txt /tmp/requirements.lock.txt
RUN pip install --no-cache-dir -r /tmp/requirements.lock.txt
COPY pyproject.toml /app/
COPY services /app/services
COPY ml /app/ml
COPY assets /app/assets
RUN pip install --no-deps -e .
CMD ["python", "-m", "uvicorn", "services.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

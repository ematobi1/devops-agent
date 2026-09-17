FROM python:3.13.15-slim
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir -e ./contracts \
    && pip install --no-cache-dir -e ./services/verifier \
    && pip install --no-cache-dir -e ./services/codegen \
    && pip install --no-cache-dir -e ./services/approval \
    && pip install --no-cache-dir -e ./services/orchestrator
RUN useradd --create-home app
USER app
CMD ["python", "scripts/diagnose.py"]

FROM python:3.12-slim
WORKDIR /app

# Install dependencies first (cached unless pyproject.toml changes)
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir .

# Then copy source (only this layer rebuilds on code changes)
COPY src/ src/
RUN pip install --no-cache-dir --no-deps .

# Run as non-root user
RUN useradd --create-home appuser
USER appuser

ENV PORT=8080
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"
CMD ["python", "-m", "rascal.server"]

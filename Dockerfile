FROM python:3.12-slim
WORKDIR /app
COPY src/ src/
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir .
ENV PORT=8080
EXPOSE 8080
CMD ["python", "-m", "rascal.server"]

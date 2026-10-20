FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml ./
COPY mocksite ./mocksite
RUN pip install --no-cache-dir .
RUN useradd --create-home mocksite
USER mocksite
EXPOSE 8765
HEALTHCHECK --interval=5s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/__mock/api/v1/status', timeout=2)"
ENTRYPOINT ["mocksite", "serve", "--host", "0.0.0.0", "--port", "8765"]

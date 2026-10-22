FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY collector ./collector
COPY tests ./tests
COPY pytest.ini .
CMD ["python", "-m", "collector.main"]

FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Persistent data volume for the SQLite database
VOLUME ["/app/data"]
ENV DATABASE_PATH=/app/data/kassenstuerzle.db

EXPOSE 5000

CMD ["python", "app.py"]

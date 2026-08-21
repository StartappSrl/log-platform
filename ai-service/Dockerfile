FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 8100
CMD ["gunicorn", "-b", "0.0.0.0:8100", "-w", "2", "app:app"]

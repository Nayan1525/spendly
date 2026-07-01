FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends libmagic1 curl && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5002

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s \
    CMD curl -f http://localhost:5002/ready || exit 1

ENV FLASK_APP=app
ENV FLASK_ENV=production
ENV ENV=production

CMD ["python", "-m", "flask", "run", "--host", "0.0.0.0", "--port", "5002"]

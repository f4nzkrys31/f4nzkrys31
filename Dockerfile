FROM python:3.13-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt "psycopg[binary]==3.2.3"
COPY alpha_scanner ./alpha_scanner
COPY config ./config
RUN useradd -r -u 10001 alpha && mkdir -p /app/data && chown alpha /app/data
USER alpha
# Inside the container the server must listen on all interfaces so the port can be published;
# docker-compose publishes it on 127.0.0.1 only.
ENV ALPHA_ALLOW_REMOTE_BIND=1 ALPHA_HOST=0.0.0.0
EXPOSE 8800
CMD ["python", "-m", "alpha_scanner", "run"]

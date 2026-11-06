# Local single-user profile in a container. NOT VERIFIED: no container runtime was available when 2.0 was
# written, so this image has not been built or run. Pin the base image by digest when you build it.
FROM python:3.14-slim
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY requirements-lock.txt .
RUN pip install --no-cache-dir -r requirements-lock.txt && python -m playwright install --with-deps chromium
COPY app ./app
COPY static ./static
COPY run.sh ./
USER app
ENV RMJF_PORT=5070 RMJF_APPLY_HEADLESS=1
# The server binds to 127.0.0.1 inside the container on purpose (no LAN exposure). Use host networking for
# local access; do not publish it on 0.0.0.0. Mount the job-search folder read-only and the data folder rw.
VOLUME ["/app/data"]
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:5070/healthz',timeout=5)"
CMD ["python", "-m", "app"]

# DuGS Runner — runs your workflows, headless.
#
# Everything needed ships in the image: the engine, the nodes, the runner.
# Only your workflows are mounted in, so the image never needs rebuilding
# when a workflow changes.
#
#   docker build -t dugs-runner .
#   docker run -d --name dugs -p 5801:5801 \
#     -v ./projects:/data/projects -v ./runs:/data/runs dugs-runner
#
# Port 5801, not 5800, so it never collides with the desktop app's own API
# server when both run on the same machine. It's defined ONCE below via the
# build arg and every other line reads from it -- previously the number was
# hardcoded in three separate places (ENV, EXPOSE, HEALTHCHECK) and they
# silently drifted apart, which is a genuinely painful bug to chase.
#
# Alpine + pure standard library, so the whole thing is tiny and has nothing
# to install.
FROM python:3.12-alpine

ARG DUGS_PORT=5801

WORKDIR /runner
COPY dugs_runner.py engine.py node_base.py storage.py tabel_store.py ai_helper.py ./
COPY nodes/ ./nodes/

# your workflows live on a volume so they survive restarts and can be swapped
# without touching the image
ENV DUGS_DATA_DIR=/data \
    DUGS_HOST=0.0.0.0 \
    DUGS_PORT=${DUGS_PORT} \
    PYTHONUNBUFFERED=1

RUN mkdir -p /data/projects /data/runs
VOLUME ["/data"]
EXPOSE ${DUGS_PORT}

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s \
  CMD python3 -c "import os,urllib.request;urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"DUGS_PORT\"]}/health',timeout=3)" || exit 1

CMD ["python3", "/runner/dugs_runner.py"]

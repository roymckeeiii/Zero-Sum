# Zero Sum: unified inbox / cleanup / analytics / rule sync add-on for self-hosted Inbox Zero.
FROM docker.io/library/python:3.12-slim
RUN pip install --no-cache-dir --disable-pip-version-check cryptography==43.0.3 "psycopg[binary]==3.2.3"
WORKDIR /app
COPY *.py *.html *.js grants.sql inbox-zero.version /app/
USER 65534:65534
# Inside a container the app must listen on every interface to be reachable through the published port; who can
# reach it is decided by the publish rule (publish on 127.0.0.1) and the network, not here. Outside a container the
# app's own default is 127.0.0.1.
ENV LISTEN_HOST=0.0.0.0
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8080'), timeout=4)"
CMD ["python", "-u", "app.py"]

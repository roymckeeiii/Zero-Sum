# Zero Sum: unified inbox / cleanup / analytics / rule sync add-on for self-hosted Inbox Zero.
FROM docker.io/library/python:3.12-slim
RUN pip install --no-cache-dir --disable-pip-version-check cryptography==43.0.3 "psycopg[binary]==3.2.3"
WORKDIR /app
COPY *.py *.html *.js grants.sql /app/
USER 65534:65534
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8080'), timeout=4)"
CMD ["python", "-u", "app.py"]

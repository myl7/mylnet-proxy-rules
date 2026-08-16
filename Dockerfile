FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore \
    HOME=/home/app

# git commits the rule change, openssh-client is how Ansible reaches sg.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git openssh-client \
    && rm -rf /var/lib/apt/lists/*

# Matches the uid of the user that owns the repositories on the host, so the
# bind mounted working trees stay writable and git sees no ownership mismatch.
RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid 1000 --create-home --home-dir /home/app app

WORKDIR /srv/app
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir ".[deploy]"

USER app

# proxy.yaml references community.docker in the plays this app never runs. The
# collections cost little and keep a stray parse from failing.
RUN ansible-galaxy collection install community.docker ansible.posix

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

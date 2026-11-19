#!/bin/sh
# Bring this plugin up to date in one go, from the folder its docker-compose.yml is in:   sudo sh update.sh
#
# The new image is built first, while the stream keeps going; then the container is swapped for one made from it,
# and the stream goes live again by itself (the holding screen on the site while its delay fills again). If Docker
# can't stop the old container at once (a process inside held on a NAS that's slow to answer), the swap is tried
# again until the old one has gone, the half-made new one cleared away each time, so the stream isn't left down.
# Its settings, stream key and sign-in are in its own Docker volume, and kept.
set -u
cd "$(dirname "$0")" || exit 1

name=$(sed -n 's/^ *container_name: *\([A-Za-z0-9_.-]*\).*/\1/p' docker-compose.yml | head -1)
if [ -z "$name" ]; then
  echo "update.sh: docker-compose.yml here names no container_name; nothing changed." >&2
  exit 1
fi

echo "Building the new image (the stream keeps going meanwhile)..."
if ! docker compose build --pull; then
  echo "update.sh: the build failed, so nothing was changed: $name is still running as it was." >&2
  exit 1
fi

running() { [ "$(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null)" = "true" ]; }

tries=0
while :; do
  tries=$((tries + 1))
  echo "Starting $name from the new image (try $tries)..."
  # (--build: Compose compares against the image just built, which it otherwise may not, keeping the old container;
  # everything is cached by now, so it takes no time)
  docker compose up -d --build --remove-orphans && running && break
  if [ "$tries" -ge 8 ]; then
    echo "update.sh: $name didn't start after $tries tries. See 'sudo docker compose logs' and 'sudo docker ps -a'." >&2
    exit 1
  fi
  # A swap that couldn't stop the old container leaves the new one made but not started, under a name like
  # "1a2b3c4d5e6f_$name": cleared away, and the old one given a moment longer to finish going
  for leftover in $(docker ps -a --filter "name=_${name}\$" --format '{{.Names}}'); do
    echo "Clearing the half-made container $leftover"
    docker rm -f "$leftover" >/dev/null 2>&1
  done
  echo "Docker couldn't swap the container yet (the old one may still be stopping): trying again in 15 s."
  sleep 15
done

# Running: wait (up to two minutes) for its own health check to say it's well
for _ in $(seq 24); do
  health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$name" 2>/dev/null)
  [ "$health" = "healthy" ] || [ "$health" = "none" ] && break
  sleep 5
done
docker compose ps
[ "${health:-}" = "healthy" ] || [ "${health:-}" = "none" ] || echo "update.sh: $name is running, but its health check says \"$health\"." >&2

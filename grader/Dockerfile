# AutoMark grader sandbox image (SRS: JDK 21 + Python, non-root).
# Build once:   docker build -t automark-grader grader/
# Then set AUTOMARK_SANDBOX=docker in .env and restart the server. Untrusted
# student programs run inside this image with:
#   --network none --read-only --memory 256m --cpus 0.5 --pids-limit 64
#   --cap-drop ALL --security-opt no-new-privileges
# so network access, host filesystem access and resource abuse are blocked.
FROM eclipse-temurin:21-jdk-jammy

# Python for Python assignments; run as an unprivileged user.
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 1000 grader

USER grader
WORKDIR /work

# The job folder is bind-mounted read-write at run time; the rest of the
# filesystem is read-only. No ENTRYPOINT: the worker passes the exact command
# (e.g. `java -Xmx256m Main` or `python3 -I sol.py`).

"""Download the git-ignored binary dependencies for a fresh clone.

Fetches into lib/ the JUnit launcher, Checkstyle and JaCoCo jars, and (unless a
JDK is already available) a Temurin JDK 21 into jdk/. Standard library only.

    python fetch_deps.py            # jars + JDK if needed
    python fetch_deps.py --no-jdk   # jars only (you already have a JDK)
"""

import glob
import os
import platform
import shutil
import sys
import tarfile
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, "lib")
JDK = os.path.join(HERE, "jdk")

JARS = {
    "junit-platform-console-standalone.jar":
        "https://repo1.maven.org/maven2/org/junit/platform/"
        "junit-platform-console-standalone/1.10.3/"
        "junit-platform-console-standalone-1.10.3.jar",
    "checkstyle-all.jar":
        "https://github.com/checkstyle/checkstyle/releases/download/"
        "checkstyle-10.17.0/checkstyle-10.17.0-all.jar",
    "jacocoagent.jar":
        "https://repo1.maven.org/maven2/org/jacoco/org.jacoco.agent/"
        "0.8.12/org.jacoco.agent-0.8.12-runtime.jar",
    "jacococli.jar":
        "https://repo1.maven.org/maven2/org/jacoco/org.jacoco.cli/"
        "0.8.12/org.jacoco.cli-0.8.12-nodeps.jar",
}


def _download(url, dest):
    print("  downloading %s" % os.path.basename(dest))
    with urllib.request.urlopen(url, timeout=120) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


def fetch_jars():
    os.makedirs(LIB, exist_ok=True)
    for name, url in JARS.items():
        dest = os.path.join(LIB, name)
        if os.path.isfile(dest) and os.path.getsize(dest) > 1000:
            print("  have %s" % name)
            continue
        _download(url, dest)


def have_jdk():
    if glob.glob(os.path.join(JDK, "jdk-*")):
        return True
    return bool(shutil.which("javac") and shutil.which("java"))


def fetch_jdk():
    system = platform.system().lower()
    os_name = {"windows": "windows", "linux": "linux", "darwin": "mac"}.get(system)
    arch = "aarch64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
    if not os_name:
        print("  unknown OS %s - install a JDK 21 yourself and set AUTOMARK_JAVA_HOME" % system)
        return
    url = ("https://api.adoptium.net/v3/binary/latest/21/ga/%s/%s/jdk/hotspot/"
           "normal/eclipse?project=jdk" % (os_name, arch))
    os.makedirs(JDK, exist_ok=True)
    archive = os.path.join(JDK, "jdk." + ("zip" if os_name == "windows" else "tar.gz"))
    print("  downloading Temurin JDK 21 (%s/%s, ~190MB)..." % (os_name, arch))
    _download(url, archive)
    print("  extracting...")
    if archive.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(JDK)
    else:
        with tarfile.open(archive) as t:
            t.extractall(JDK)
    os.remove(archive)
    print("  JDK ready at", glob.glob(os.path.join(JDK, "jdk-*"))[0])


def main():
    print("Fetching jars into lib/ ...")
    fetch_jars()
    if "--no-jdk" in sys.argv:
        print("Skipping JDK (--no-jdk).")
    elif have_jdk():
        print("JDK already available - skipping.")
    else:
        print("Fetching JDK into jdk/ ...")
        fetch_jdk()
    print("Done. Next: copy .env.example to .env and fill it in, then "
          "`python migrate.py` and `python app.py`.")


if __name__ == "__main__":
    main()

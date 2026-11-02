"""Persistent, conservative vanilla worldgen filter; never generates a world or loot."""
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import zipfile


def prepare_filter(jar, java, runtime, javac=None):
    """Reuse the official bundle's libraries and compile our reflection-only helper."""
    runtime = Path(runtime).resolve()
    runtime.mkdir(parents=True, exist_ok=True)
    paths = []
    with zipfile.ZipFile(jar) as bundle:
        for group in ("versions", "libraries"):
            for row in bundle.read(f"META-INF/{group}.list").decode().splitlines():
                digest, _, relative = row.split("\t")
                path = runtime / group / relative
                if not path.is_relative_to(runtime) or ".." in Path(relative).parts:
                    raise ValueError("Unsafe path in server bundle")
                if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                    data = bundle.read(f"META-INF/{group}/{relative}")
                    if hashlib.sha256(data).hexdigest() != digest:
                        raise ValueError("Server bundle member failed SHA-256 verification")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
                paths.append(path)
    source = Path(__file__).with_name("SeedFilter.java")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    classes = runtime / "filter-classes" / digest
    if not (classes / "SeedFilter.class").exists():
        compiler = javac or shutil.which("javac")
        if not compiler:
            sibling = Path(java).with_name("javac")
            compiler = str(sibling) if sibling.is_file() else None
        if not compiler:
            raise RuntimeError("Seed filtering needs a Java 17+ compiler; install a JDK or pass --javac")
        classes.mkdir(parents=True, exist_ok=True)
        result = subprocess.run([compiler, "--release", "17", "-d", str(classes), str(source)],
                                capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise RuntimeError("Could not compile seed filter (Java 17+ javac required): " + result.stderr)
    return os.pathsep.join(map(str, [classes, *paths]))


class SeedPrefilter:
    def __init__(self, jar, java, runtime, javac=None):
        self.process = None
        self.log = None
        self.thread = None
        try:
            classpath = prepare_filter(jar, java, runtime, javac)
            self.log_path = Path(runtime) / "prefilter.log"
            self.log = self.log_path.open("a", encoding="utf-8")
            self.queue = queue.Queue()
            self.process = subprocess.Popen([java, "--enable-native-access=ALL-UNNAMED", "-Xms64M", "-Xmx1G",
                                             "-cp", classpath, "SeedFilter"],
                                            cwd=Path(runtime).resolve(),
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", bufsize=1)
            self.thread = threading.Thread(target=self._pump, daemon=True)
            self.thread.start()
            self._wait("SF_READY", 120)
        except BaseException:
            self.close()
            raise

    def _pump(self):
        try:
            for line in self.process.stdout:
                self.log.write(line)
                self.log.flush()
                if line.startswith("SF_"):
                    self.queue.put(line.rstrip())
        finally:
            self.queue.put(None)

    def _wait(self, prefix, timeout):
        try:
            line = self.queue.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError(f"Seed filter timed out; see {self.log_path}") from None
        if line is None or not line.startswith(prefix):
            raise RuntimeError(f"Seed filter failed; see {self.log_path}")
        return line[len(prefix):].strip()

    def check(self, seed, distance):
        self.process.stdin.write(f"{seed} {distance}\n")
        self.process.stdin.flush()
        result = json.loads(self._wait("SF_RESULT ", 120))
        if result["seed"] != seed:
            raise RuntimeError("Seed filter returned the wrong seed")
        return result

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.stdin.close()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=5)
            if self.thread is not None:
                self.thread.join(timeout=5)
            self.process.stdout.close()
        if self.log is not None:
            self.log.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

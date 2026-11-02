"""A local-only, stoppable vanilla server controller; no RCON or third-party jars."""
import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
import uuid
import zipfile

VERSION = "26.3"
MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
DIMENSIONS = {"overworld": "minecraft:overworld", "nether": "minecraft:the_nether", "end": "minecraft:the_end"}


def add_runtime_arguments(parser):
    parser.add_argument("--server-jar", type=Path, help="Official 26.3 server bundle; otherwise download and verify it")
    parser.add_argument("--java", help="Java 25+ executable; otherwise discover an installed runtime")
    parser.add_argument("--heap", default="4G", help="Maximum Java heap, e.g. 4G or 8G")
    parser.add_argument("--accept-eula", action="store_true", help="Accept https://aka.ms/MinecraftEULA for this runtime")
    parser.add_argument("--poll-interval", type=float, default=0.1, help="Chunk completion polling interval in seconds")


def ensure_server(path, cache):
    if path is None:
        cache = Path(cache)
        cache.mkdir(parents=True, exist_ok=True)
        path = cache / f"server-{VERSION}.jar"
        with urllib.request.urlopen(MANIFEST, timeout=30) as response:
            manifest = json.load(response)
        version = next(v for v in manifest["versions"] if v["id"] == VERSION)
        with urllib.request.urlopen(version["url"], timeout=30) as response:
            download = json.load(response)["downloads"]["server"]
        if not path.exists() or hashlib.sha1(path.read_bytes()).hexdigest() != download["sha1"]:
            temporary = path.with_suffix(".part")
            try:
                with urllib.request.urlopen(download["url"], timeout=120) as response, temporary.open("wb") as output:
                    shutil.copyfileobj(response, output)
                if hashlib.sha1(temporary.read_bytes()).hexdigest() != download["sha1"]:
                    raise RuntimeError("Official server download failed its SHA-1 check")
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
    path = Path(path).expanduser().resolve()
    with zipfile.ZipFile(path) as jar:
        records = jar.read("META-INF/versions.list").decode().splitlines()
        if not any(line.split("\t")[1] == VERSION for line in records):
            raise ValueError("Expected the official Minecraft 26.3 server bundle")
    return path


def find_java(explicit=None):
    candidates = [explicit] if explicit else []
    if not explicit:
        if os.environ.get("JAVA_HOME"):
            candidates.append(str(Path(os.environ["JAVA_HOME"]) / "bin/java"))
        candidates.append(shutil.which("java"))
        root = Path.home() / "Library/Application Support"
        candidates.extend(str(p) for p in (root / "ModrinthApp/meta/java_versions").glob("*/Contents/Home/bin/java"))
        candidates.extend(str(p) for p in (root / "minecraft/runtime").glob("**/bin/java"))
    for candidate in dict.fromkeys(c for c in candidates if c):
        candidate = str(Path(candidate).expanduser())
        try:
            result = subprocess.run([candidate, "-version"], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            continue
        match = re.search(r'(?:version\s+"|openjdk\s+)(\d+)', result.stderr + result.stdout)
        if result.returncode == 0 and match and int(match[1]) >= 25:
            return candidate
    raise RuntimeError("Java 25+ not found; pass --java /path/to/java")


def chunk_bounds(cx, cz, radius):
    """Half-open square [center-radius, center+radius), covering negative coordinates."""
    if radius <= 0:
        raise ValueError("Radius must be positive")
    return (math.floor((cx - radius) / 16), math.floor((cz - radius) / 16),
            math.ceil((cx + radius) / 16) - 1, math.ceil((cz + radius) / 16) - 1)


def batches(bounds, size=16):
    if not 1 <= size <= 16:
        raise ValueError("Batch side must be between 1 and 16 chunks")
    x0, z0, x1, z1 = bounds
    for x in range(x0, x1 + 1, size):
        for z in range(z0, z1 + 1, size):
            yield x, z, min(x + size - 1, x1), min(z + size - 1, z1)


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


class Server:
    def __init__(self, jar, java, runtime, world, heap="4G", seed=None,
                 accept_eula=False, bonus_chest=False, poll_interval=0.1,
                 player_view_distance=None, player_simulation_distance=12,
                 defer_player=False):
        self.runtime = Path(runtime).resolve()
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.process = None
        self.player = None
        self.player_view_distance = player_view_distance
        self.player_simulation_distance = player_simulation_distance
        self.defer_player = defer_player
        if player_view_distance is not None and not (2 <= player_view_distance <= 32
                                                      and 2 <= player_simulation_distance <= 32):
            raise ValueError("Player view/simulation distances must be 2..32 chunks")
        self.log = None
        self.control_lock = (self.runtime / ".controller.lock").open("a")
        try:
            try:
                fcntl.flock(self.control_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError(f"Another tool is using runtime {self.runtime}") from None
            self._start(jar, java, runtime, world, heap, seed, accept_eula, bonus_chest, poll_interval)
        except BaseException:
            self.close()
            raise

    def _start(self, jar, java, runtime, world, heap, seed, accept_eula, bonus_chest, poll_interval):
        self.forced_dimensions = set()
        self.world = Path(world).resolve()
        self.runtime = Path(runtime).resolve()
        if not re.fullmatch(r"[1-9][0-9]*[MG]", heap):
            raise ValueError("Heap must look like 4G or 8192M")
        if not 0.02 <= poll_interval <= 5:
            raise ValueError("Polling interval must be 0.02 to 5 seconds")
        self.poll_interval = poll_interval
        self.runtime.mkdir(parents=True, exist_ok=True)
        marker = self.runtime / ".challenge-runtime"
        if (self.runtime / "server.properties").exists() and not marker.exists():
            raise RuntimeError("Refusing to overwrite an unrelated server.properties")
        marker.touch()
        eula = self.runtime / "eula.txt"
        if not eula.exists() or "eula=true" not in eula.read_text().splitlines():
            if not accept_eula:
                raise RuntimeError("Pass --accept-eula after reading https://aka.ms/MinecraftEULA")
            eula.write_text("# Accepted by the caller with --accept-eula\neula=true\n")
        # Find a free localhost port. Vanilla holds the world/session lock itself.
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.port = port
        properties = {"server-ip": "127.0.0.1", "server-port": port,
                      "level-name": self.world.name, "view-distance": 2,
                      "simulation-distance": 2, "pause-when-empty-seconds": -1,
                      "max-players": 1, "white-list": "true", "online-mode": "true",
                      "enable-rcon": "false", "enable-query": "false",
                      "management-server-enabled": "false", "sync-chunk-writes": "false",
                      "region-file-compression": "deflate", "generate-structures": "true"}
        if seed is not None:
            properties["level-seed"] = seed
        if self.player_view_distance is not None:
            properties.update({"view-distance": self.player_view_distance,
                               "simulation-distance": self.player_simulation_distance,
                               "online-mode": "false", "white-list": "false",
                               "gamemode": "creative"})
        (self.runtime / "server.properties").write_text("\n".join(f"{k}={v}" for k, v in properties.items()) + "\n")
        self.queue = queue.Queue()
        self.log = (self.runtime / "controller.log").open("a", encoding="utf-8")
        self.process = None
        command = [str(java), "--enable-native-access=ALL-UNNAMED", "-Xms512M", f"-Xmx{heap}",
                   "-jar", str(jar), "--nogui", "--universe", str(self.world.parent), "--world", self.world.name]
        if bonus_chest:
            command.append("--bonusChest")
        try:
            self.process = subprocess.Popen(command, cwd=self.runtime, stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            text=True, encoding="utf-8", bufsize=1)
            def pump():
                for line in self.process.stdout:
                    self.log.write(line)
                    self.log.flush()
                    self.queue.put(line.rstrip())
            self.thread = threading.Thread(target=pump, daemon=True)
            self.thread.start()
            self.wait(lambda line: "Done (" in line, 180)
            if self.player_view_distance is not None and not self.defer_player:
                self.connect_player()
        except BaseException:
            self.close()
            raise

    def connect_player(self):
        if self.player_view_distance is None:
            raise RuntimeError("Player loading was not configured")
        if self.player is None:
            from challenge.player import LocalPlayer
            self.player = LocalPlayer(self.port, self.player_view_distance)
            self.player.wait_ready()

    def send(self, command):
        if self.process.poll() is not None:
            raise RuntimeError(f"Minecraft exited; see {self.runtime / 'controller.log'}")
        self.process.stdin.write(command + "\n")
        self.process.stdin.flush()

    def wait(self, predicate, timeout):
        deadline = time.monotonic() + timeout
        lines = []
        while time.monotonic() < deadline:
            try:
                line = self.queue.get(timeout=min(0.1, max(0.001, deadline - time.monotonic())))
            except queue.Empty:
                if self.process.poll() is not None:
                    raise RuntimeError(f"Minecraft exited; see {self.runtime / 'controller.log'}")
                continue
            lines.append(line)
            if predicate(line):
                return lines
        raise TimeoutError(f"Minecraft response timed out; see {self.runtime / 'controller.log'}")

    def run(self, *commands, timeout=120):
        marker = "CV_DONE_" + uuid.uuid4().hex
        for command in commands:
            self.send(command)
        self.send("say " + marker)
        lines = self.wait(lambda line: line.endswith("[Server] " + marker), timeout)
        for line in lines:
            if any(error in line for error in ("Unknown or incomplete command", "Incorrect argument", "<--[HERE]")):
                raise RuntimeError("Minecraft rejected a command: " + line)
        return lines

    def save(self, *before):
        lines = self.run(*before, "save-all flush", timeout=180)
        if not any("Saved the game" in line for line in lines):
            raise RuntimeError("Minecraft did not confirm its save")

    def wait_loaded(self, dimension, chunks, timeout=180):
        chunks = list(dict.fromkeys(chunks))
        if not chunks or len(chunks) > 256:
            raise ValueError("Expected between 1 and 256 chunks")
        checks = " ".join(f"if loaded {x * 16} 64 {z * 16}" for x, z in chunks)
        marker = "CV_READY_" + uuid.uuid4().hex
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.player is not None:
                self.player.check()
            lines = self.run(f"execute in {dimension} {checks} run say {marker}", timeout=max(1, deadline - time.monotonic()))
            if any(line.endswith("[Server] " + marker) for line in lines):
                return
            time.sleep(self.poll_interval)
        raise TimeoutError(f"Chunks did not become fully loaded in {dimension}")

    def force_batch(self, dimension, bounds):
        self.start_batch(dimension, bounds)
        x0, z0, x1, z1 = bounds
        self.wait_loaded(dimension, ((x, z) for x in range(x0, x1 + 1) for z in range(z0, z1 + 1)))

    def start_batch(self, dimension, bounds):
        """Queue generation without waiting, allowing other dimensions to overlap."""
        x0, z0, x1, z1 = bounds
        if (x1 - x0 + 1) * (z1 - z0 + 1) > 256:
            raise ValueError("Vanilla forceload batches must not exceed 256 chunks")
        self.forced_dimensions.add(dimension)
        self.run(f"execute in {dimension} run forceload remove all",
                 f"execute in {dimension} run forceload add {x0 * 16} {z0 * 16} {x1 * 16 + 15} {z1 * 16 + 15}")

    def batch_loaded(self, dimension, bounds):
        x0, z0, x1, z1 = bounds
        checks = " ".join(f"if loaded {x * 16} 64 {z * 16}"
                          for x in range(x0, x1 + 1) for z in range(z0, z1 + 1))
        marker = "CV_READY_" + uuid.uuid4().hex
        lines = self.run(f"execute in {dimension} {checks} run say {marker}")
        return any(line.endswith("[Server] " + marker) for line in lines)

    def close(self):
        if self.player is not None:
            self.player.close()
            self.player = None
        if self.process is not None and self.process.poll() is None:
            try:
                for dimension in sorted(getattr(self, "forced_dimensions", set())):
                    self.send(f"execute in {dimension} run forceload remove all")
                self.send("save-on")
                self.send("stop")
                self.process.wait(timeout=120)
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                self.process.terminate()
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=10)
        if hasattr(self, "thread"):
            self.thread.join(timeout=5)
        if self.log is not None:
            self.log.close()
        if not self.control_lock.closed:
            fcntl.flock(self.control_lock.fileno(), fcntl.LOCK_UN)
            self.control_lock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

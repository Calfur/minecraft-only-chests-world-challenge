"""Minimal localhost client for vanilla Java 26.3 (protocol 777).

Joins as a real network player so vanilla schedules player-centered chunks.
Packet IDs/fields are pinned to the official 26.3 server's protocol codecs.
This client does not render, move, open containers, or authenticate an account.
"""
import io
import socket
import struct
import threading
import uuid
import zlib


def varint(value):
    value &= 0xffffffff
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def read_varint(stream):
    result = 0
    for shift in range(0, 35, 7):
        byte = stream.read(1)
        if not byte:
            raise EOFError("Local player connection closed")
        result |= (byte[0] & 127) << shift
        if not byte[0] & 128:
            return result
    raise ValueError("Invalid protocol VarInt")


def string(value):
    encoded = value.encode("utf-8")
    return varint(len(encoded)) + encoded


class LocalPlayer:
    name = "SeedInspector"

    def __init__(self, port, view_distance):
        self.error = None
        self.position = None
        self.rotation = [0.0, 0.0]
        self.chunks_received = 0
        self.ready = threading.Event()
        self.stopping = threading.Event()
        self.compression = None
        self.state = "login"
        self.view_distance = view_distance
        self.socket = socket.create_connection(("127.0.0.1", port), timeout=30)
        self.socket.settimeout(None)
        self.stream = self.socket.makefile("rb")
        try:
            self.send(0, varint(777) + string("127.0.0.1") + struct.pack(">H", port) + varint(2))
            self.send(0, string(self.name) + uuid.UUID(int=0).bytes)
        except BaseException:
            self.stream.close()
            self.socket.close()
            raise
        self.thread = threading.Thread(target=self._pump, daemon=True)
        self.thread.start()

    def send(self, packet_id, payload=b""):
        packet = varint(packet_id) + payload
        if self.compression is not None:
            if len(packet) >= self.compression:
                packet = varint(len(packet)) + zlib.compress(packet)
            else:
                packet = b"\0" + packet
        self.socket.sendall(varint(len(packet)) + packet)

    def _receive(self):
        size = read_varint(self.stream)
        if not 0 < size <= 16 * 1024 * 1024:
            raise ValueError("Invalid local player packet length")
        payload = self.stream.read(size)
        if len(payload) != size:
            raise EOFError("Truncated local player packet")
        if self.compression is not None:
            stream = io.BytesIO(payload)
            expanded = read_varint(stream)
            if expanded > 16 * 1024 * 1024:
                raise ValueError("Local player packet expands beyond limit")
            payload = stream.read()
            if expanded:
                decoder = zlib.decompressobj()
                payload = decoder.decompress(payload, expanded + 1)
                if len(payload) != expanded or not decoder.eof:
                    raise ValueError("Invalid compressed local player packet")
        stream = io.BytesIO(payload)
        return read_varint(stream), stream.read()

    def _pump(self):
        try:
            while not self.stopping.is_set():
                packet_id, payload = self._receive()
                if self.state == "login":
                    if packet_id == 0:
                        raise RuntimeError("Local player login rejected: " + repr(payload[:500]))
                    if packet_id == 1:
                        raise RuntimeError("Local player requires a localhost offline-mode runtime")
                    if packet_id == 3:
                        self.compression = read_varint(io.BytesIO(payload))
                    elif packet_id == 2:
                        self.send(3)  # login acknowledged
                        self.state = "configuration"
                        # language, view distance, chat, colors, skin, hand,
                        # filtering, listing, particle status
                        self.send(0, string("en_us") + bytes((self.view_distance, 0, 1, 127, 1, 0, 1, 0)))
                elif self.state == "configuration":
                    if packet_id == 2:
                        raise RuntimeError("Local player configuration rejected: " + repr(payload[:500]))
                    if packet_id == 15:
                        self.send(7, b"\0")  # no cached registry packs
                    elif packet_id == 4:
                        self.send(4, payload)  # keep alive
                    elif packet_id == 5:
                        self.send(5, payload)  # pong
                    elif packet_id == 3:
                        self.send(3)  # finish configuration
                        self.state = "play"
                else:
                    if packet_id == 32:
                        raise RuntimeError("Local player disconnected: " + repr(payload[:500]))
                    if packet_id == 45:
                        self.send(28, payload)  # keep alive
                    elif packet_id == 62:
                        self.send(45, payload)  # pong
                    elif packet_id == 11:
                        self.send(11, struct.pack(">f", 9.0))  # chunk batch feedback
                    elif packet_id == 46:
                        self.chunks_received += 1
                    elif packet_id == 73:
                        stream = io.BytesIO(payload)
                        teleport_id = read_varint(stream)
                        x, y, z, dx, dy, dz, yaw, pitch = struct.unpack(">ddddddff", stream.read(56))
                        flags = struct.unpack(">I", stream.read(4))[0]
                        if flags & 7 and self.position is None:
                            raise RuntimeError("Relative teleport before initial player position")
                        previous = self.position or [0.0, 0.0, 0.0]
                        x += previous[0] if flags & 1 else 0
                        y += previous[1] if flags & 2 else 0
                        z += previous[2] if flags & 4 else 0
                        yaw += self.rotation[0] if flags & 8 else 0
                        pitch += self.rotation[1] if flags & 16 else 0
                        self.position = [x, y, z]
                        self.rotation = [yaw, pitch]
                        self.send(0, varint(teleport_id) + struct.pack(">dddff", x, y, z, yaw, pitch))
                        self.send(31, struct.pack(">dddffB", x, y, z, yaw, pitch, 0))
                        self.send(44)  # player loaded
                        self.ready.set()
        except (OSError, EOFError, ValueError, RuntimeError, struct.error, zlib.error) as error:
            if not self.stopping.is_set():
                self.error = error
            self.ready.set()

    def check(self):
        if self.error is not None:
            raise RuntimeError(f"Local player failed: {self.error}")

    def wait_ready(self, timeout=120):
        if not self.ready.wait(timeout):
            raise TimeoutError("Local player did not enter the world")
        self.check()
        if self.position is None:
            raise RuntimeError("Local player did not receive a spawn position")

    def close(self):
        self.stopping.set()
        try:
            self.socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.socket.close()
        self.thread.join(timeout=5)
        self.stream.close()

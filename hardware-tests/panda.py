import logging
import numpy as np
import re
import socket

from base64 import b64encode
from typing import List

log = logging.getLogger(__name__)
TIMEOUT = 3


class PandaClient(object):
    def __init__(self, host: str):
        self.host = host
        self.fields = []
        self.capture_fields = []
        self.instances = set()
        self.sock = None

    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(TIMEOUT)
        # disable Nagle's algorithm to reduce latency
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.connect((self.host, 8888))
        self.fetch_metadata()

    def get_first_instance_name(self, block_name: str):
        for instance in sorted(self.instances):
            if instance.startswith(block_name):
                return instance
        return ''

    def get_field_names_with(self, string: str):
        result = []
        for i in self.fields:
            if re.search(string, i):
                result.append(i)
        return result

    def close(self):
        self.sock.close()

    def disable_captures(self):
        for field in self.capture_fields:
            self.send_recv(f'{field}=No')

    def fetch_metadata(self):
        result = bytearray()
        self.send('*CHANGES?')
        while True:
            chunk = self.recv()
            result.extend(chunk)
            if chunk.endswith(b'.\n'):
                break

        self.capture_fields = []
        for line in result.split(b'\n'):
            if b'=' not in line:
                continue
            part1, part2 = line.split(b'=', 1)
            field = part1[1:].decode()
            if '.CAPTURE' in field:
                self.capture_fields.append(field)

            self.fields.append(field)
            self.instances.add(field.split('.')[0])

    def send(self, command: str | List[str]):
        if isinstance(command, str):
            command = [command]

        for line in command:
            self.sock.sendall(line.encode())
            self.sock.sendall(b'\n')

    def recv(self):
        result = bytearray()
        while not result.endswith(b'\n'):
            result.extend(self.sock.recv(4096))

        return result

    def send_recv(self, commands: str | List[str]):
        self.send(commands)
        return self.recv()

    def prepare_table_command(self, name: str, content: np.ndarray,
                              streaming=False, last=False):

        suffix = '<'
        if streaming and last:
            suffix = '<<|'
        elif streaming:
            suffix = '<<'

        commands = [f'{name}{suffix}B']
        chunk_size = 191
        #chunk_size = 767
        for i in range(0, len(content), chunk_size):
            commands.append(b64encode(content[i:i+chunk_size]).decode())
        commands.append('')
        return commands

    def put_table(self, name: str, content: np.ndarray,
                  streaming=False, last=False):
        return self.send_recv(self.prepare_table_command(name, content,
                                                         streaming, last))
    def arm(self):
        self.send_recv('*PCAP.ARM=')

    def disarm(self):
        self.send_recv('*PCAP.DISARM=')

    def collect(self, nbytes=None):
        data_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # disable Nagle's algorithm to reduce latency
        data_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        data_sock.connect((self.host, 8889))
        data_sock.sendall(b'UNFRAMED RAW NO_HEADER NO_STATUS ONE_SHOT\n')
        acc = bytearray()
        while True:
            chunk = data_sock.recv(1<<17)
            if not chunk:
                break

            acc.extend(chunk)
            if nbytes:
                if len(acc) >= nbytes:
                    yield acc[:nbytes]
                    acc = acc[nbytes:]
            elif len(acc) % 4 == 0:
                yield acc
                acc = bytearray()

        if acc:
            yield acc

        data_sock.close()

    def __getattr__(self, name):
        if name.isupper():
            return Item(name, self)

    def __getitem__(self, item):
        item = item.upper()
        if '.' in item:
            part1, part2 = item.split('.', 1)
            return getattr(self, part1)[part2]
        else:
            return getattr(self, item)


class Item(object):
    def __init__(self, path: str, client: PandaClient):
        self.path = path
        self.client = client

    def __getattr__(self, name):
        return Item(f'{self.path}.{name}', self.client)

    def __getitem__(self, item):
        item = item.upper()
        if '.' in item:
            part1, part2 = item.split('.', 1)
            return getattr(self, part1)[part2]
        else:
            return getattr(self, item)

    def get(self):
        result = bytearray()
        self.client.send(f'{self.path}?')
        chunk = self.client.recv()
        result.extend(chunk)
        if chunk.startswith(b'!'):
            while not chunk.endswith(b'.\n'):
                chunk = self.client.recv()
                result.extend(chunk)

        if result.startswith(b'ERR'):
            raise ValueError(f'Error putting {self.path}: {result}')
        elif result.startswith(b'!'):
            return [int(i[1:]) for i in result.split()[:-1]]
        else:
            if b'=' not in result:
                raise ValueError(
                    f'Unexpected response for {self.path}: {result}')
            val = result.split(b'=', 1)[1].strip()
            if val.isdigit():
                return int(val)
            try:
                return float(val)
            except ValueError:
                return val.decode()

        return result

    def put(self, val: str | np.ndarray):
        if isinstance(val, np.ndarray):
            result = self.client.put_table(self.path, val)
        else:
            result = self.client.send_recv(f'{self.path}={val}')

        if not result.startswith(b'OK'):
            raise ValueError(f'Error putting {self.path}: {result}')

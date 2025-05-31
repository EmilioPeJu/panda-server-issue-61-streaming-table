import logging
import numpy as np
import socket
import time

from base64 import b64encode
from typing import List

log = logging.getLogger(__name__)


class Client(object):
    def __init__(self, host: str):
        self.host = host

    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # disable nagle algorithm to reduce latency
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.connect((self.host, 8888))

    def close(self):
        self.sock.close()

    def send(self, commands: str | List[str]):
        if isinstance(commands, str):
            commands = [commands]

        for command in commands:
            self.sock.sendall(command.encode())
            self.sock.sendall(b'\n')

    def recv(self):
        return self.sock.recv(4096)

    def send_recv(self, commands: str | List[str]):
        self.send(commands)
        return self.recv()

    def prepare_table_command(self, name: str, content: np.ndarray, more=False):
        commands = [f'{name}<{'|' if more else ''}B']
        chunk_size = 191
        #chunk_size = 100000
        for i in range(0, len(content), chunk_size):
            commands.append(b64encode(content[i:i+chunk_size]).decode())
        commands.append('')
        return commands

    def put_table(self, name: str, content: np.ndarray, more=False):
        return self.send_recv(self.prepare_table_command(name, content, more))

    def wait_for_table_room(self, name, thres):
        while True:
            queued = int(self.get(f'{name}.TABLE.QUEUED_LINES'))
            if queued <= thres:
                print(f'Queued lines {queued} <= {thres}')
                return queued
 
            time.sleep(0.01)

    def wait_for_table_fill(self, name,  thres):
        while True:
            queued = int(self.get(f'{name}.TABLE.QUEUED_LINES'))
            if queued >= thres:
                print(f'Queued lines {queued} >= {thres}')
                return queued
            time.sleep(0.01)

    def load_state(self, state: str):
        acc = []
        reading_table = False
        for line in state.splitlines():
            if '<' in line:
                acc.append(line)
                reading_table = True
            elif reading_table:
                acc.append(line)
                if line == '':
                    self.send_recv(acc)
                    reading_table = False
            else:
                self.send_recv(line)

    def arm(self):
        self.send_recv('*PCAP.ARM=')

    def disarm(self):
        self.send_recv('*PCAP.DISARM=')

    def collect(self):
        data_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # disable nagle algorithm to reduce latency
        data_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        data_sock.connect((self.host, 8889))
        data_sock.sendall(b'UNFRAMED RAW NO_HEADER NO_STATUS ONE_SHOT\n')
        acc = bytearray()
        while True:
            chunk = data_sock.recv(4096)
            if not chunk:
                break
            acc.extend(chunk)
            if len(acc) % 4 == 0:
                yield acc
                acc = bytearray()

        if acc:
            yield acc

        data_sock.close()

    def __getattr__(self, name):
        if name.isupper():
            return Item(name, self)


class Item(object):
    def __init__(self, path: str, client: Client):
        self.path = path
        self.client = client

    def __getattr__(self, name):
        return Item(f'{self.path}.{name}', self.client)

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
            val = result[4:-1]
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


if __name__ == '__main__':
    main()

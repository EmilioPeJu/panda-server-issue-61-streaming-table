#!/usr/bin/env python
import argparse
import time

from tui import TuiManager
from panda import PandaClient


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('host')
    parser.add_argument('name')
    parser.add_argument('--watch-period', type=float, default=1.0)
    return parser.parse_args()


def main():
    args = parse_args()
    client = PandaClient(args.host)
    client.connect()
    fields = []
    for field_name in args.name.split(','):
        new_fields = [
            client[i] for i in client.get_field_names_with(field_name)]
        new_fields.sort(key=lambda f: f.path)
        if not new_fields:
            fields.append(client[field_name])
        else:
            fields.extend(new_fields)

    tui = TuiManager()
    def draw():
        tui.clear()
        tui.reset_line()
        for field in fields:
            try:
                tui.add_str(f'{field.path}: {field.get()}')
            except ValueError as e:
                tui.add_str(f'{field.path}: {str(e)}')

    tui.add_draw_callback(draw)
    while True:
        draw()
        tui.process_events()
        try:
            time.sleep(args.watch_period)
        except KeyboardInterrupt:
            break

    tui.quit()
    client.close()

if __name__ == '__main__':
    main()

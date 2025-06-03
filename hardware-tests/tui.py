#!/usr/bin/env python

import curses
import locale
locale.setlocale(locale.LC_ALL, "")


class TuiManager:
    def __init__(self):
        self.win = curses.initscr()
        curses.noecho()
        curses.cbreak()
        curses.curs_set(0)
        self.win.nodelay(1)
        self.line = 0
        self.win.clear()
        self.on_resize()
        self.key_callbacks = []
        self.draw_callbacks = []

    def on_resize(self):
        self.ymax, self.xmax = self.win.getmaxyx()

    def add_key_callback(self, callback):
        self.key_callbacks.append(callback)

    def add_draw_callback(self, callback):
        self.draw_callbacks.append(callback)

    def notify_key(self, key):
        for callback in self.key_callbacks:
            callback(key)

    def notify_draw(self):
        for callback in self.draw_callbacks:
            callback()

    def add_str(self, string, y=None, x=0):
        if self.line >= self.ymax:
            return

        if y is None:
            y = self.line
        else:
            self.line = y

        self.win.addstr(y, x, string.encode())
        self.line += 1
        self.win.refresh()

    def reset_line(self):
        self.line = 0

    def process_events(self):
        c = self.win.getch()
        if c == curses.KEY_RESIZE:
            self.on_resize()
            self.notify_draw()
        if c != -1:
            self.notify_key(c)

    def clear(self):
        self.win.clear()

    def quit(self):
        curses.echo()
        curses.nocbreak()
        curses.endwin()

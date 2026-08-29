#!/usr/bin/env python3
"""A file tree, as a textfold plugin.

The point of this file is not the tree. It is that the tree is **part of the
editor's shape**: a column pinned down the left, at a width of its own, that
you collapse with the same key that opened it — and that all of it arrives
from a plugin, in two hundred lines of Python, with no change to textfold.

What that takes, and where each half lives:

  * the manifest says `"dock": "left"`, so the editor can lay the thing out
    before this program has ever been started;
  * `panel/set` fills it with styled, clickable lines, and the styles are
    *names* — `type`, `string`, `muted` — so the tree is themed with
    everything else and re-themes for free when you switch;
  * `panel/key` hands over the keystrokes that would otherwise have changed
    the text, which in a read-only buffer are going spare, so plain letters
    are free and nothing anybody knows is taken;
  * `file/create`, `file/rename` and `file/delete` go through the editor
    rather than through `mv` and `rm`, because a buffer open on a file that
    was renamed underneath it is a buffer that will save to the old name.

Only the directories you have opened are read, and they are read again when
you ask rather than watched, because a watcher on a large tree is a lot of
machinery for a question you can answer with `g`.
"""

import json
import os
import sys

PANEL = "files/tree"

# What is hidden until you ask. Not a policy about what matters — `.` files
# and the two directories that are always enormous and never interesting.
BORING = {".git", "node_modules", "__pycache__", ".venv", "target", ".mypy_cache"}

HELP = "enter open · space fold · n new · m folder · r rename · x delete · . hidden · g refresh"


class Editor:
    """The half of the conversation that goes back to textfold."""

    def __init__(self, out):
        self.out = out

    def send(self, message):
        body = json.dumps(message).encode("utf-8")
        self.out.write(b"Content-Length: %d\r\n\r\n" % len(body))
        self.out.write(body)
        self.out.flush()

    def notify(self, method, params):
        self.send({"jsonrpc": "2.0", "method": method, "params": params})

    def answer(self, request_id, result=None):
        self.send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def ask(self, request_id, method, params):
        """A question for the person, or a job for the editor.

        The reply arrives later, on the ordinary loop — nothing here sits and
        waits for somebody to make up their mind.
        """
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})

    def say(self, text, kind="plain"):
        self.notify("status/say", {"text": text, "kind": kind})

    def open(self, path):
        self.notify("open", {"path": path})

    def lines(self, lines):
        self.notify("panel/set", {"panel": PANEL, "lines": lines})


def read_message(stream):
    """One framed message, or None when the editor has gone."""
    length = None
    while True:
        line = stream.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            break
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":")[1])
    if length is None:
        return None
    return json.loads(stream.read(length))


class Tree:
    def __init__(self, editor, root):
        self.editor = editor
        self.root = os.path.abspath(root)
        # Only what you have opened. A tree that read the whole project to
        # draw its first row would be a tree you waited for.
        self.expanded = {self.root}
        self.hidden = False
        self.showing = False
        # The path on each drawn row, so a key knows what it is standing on
        # without the editor having to send the text back.
        self.rows = []

    # ---- reading the disk ----

    def children(self, path):
        try:
            entries = list(os.scandir(path))
        except OSError:
            return []
        if not self.hidden:
            entries = [e for e in entries if not e.name.startswith(".") and e.name not in BORING]
        # Directories first, then by name, case-insensitively — which is the
        # order every file manager uses and the one people read in.
        entries.sort(key=lambda e: (not e.is_dir(), e.name.lower()))
        return entries

    # ---- drawing ----

    def draw(self):
        if not self.showing:
            return
        self.rows = []
        lines = [{"spans": [
            {"text": "  " + os.path.basename(self.root) or "/", "style": "keyword"},
        ]}]
        self.rows.append(None)
        self.walk(self.root, 0, lines)
        lines.append("")
        self.rows.append(None)
        lines.append({"spans": [{"text": " " + HELP, "style": "muted"}]})
        self.rows.append(None)
        self.editor.lines(lines)

    def walk(self, path, depth, lines):
        for entry in self.children(path):
            full = entry.path
            indent = "  " * (depth + 1)
            if entry.is_dir():
                open_ = full in self.expanded
                lines.append({"spans": [
                    {"text": indent, "style": "muted"},
                    {"text": "▾ " if open_ else "▸ ", "style": "muted",
                     "action": "toggle:" + full},
                    {"text": entry.name, "style": "type", "action": "toggle:" + full},
                ]})
                self.rows.append(full)
                if open_:
                    self.walk(full, depth + 1, lines)
            else:
                lines.append({"spans": [
                    {"text": indent + "  ", "style": "muted"},
                    {"text": entry.name,
                     "style": "muted" if entry.name.startswith(".") else "variable",
                     "action": "open:" + full},
                ]})
                self.rows.append(full)

    # ---- what the keys do ----

    def at(self, line):
        """The path on a drawn row, or None for a row that is not one."""
        if 0 <= line < len(self.rows):
            return self.rows[line]
        return None

    def toggle(self, path):
        if path in self.expanded:
            self.expanded.discard(path)
        else:
            self.expanded.add(path)
        self.draw()

    def act(self, action):
        """A click or Enter on something this plugin marked."""
        if action.startswith("toggle:"):
            self.toggle(action[len("toggle:"):])
        elif action.startswith("open:"):
            self.editor.open(action[len("open:"):])

    def near(self, line):
        """The directory a new file made on this row should go in.

        Standing on a directory means inside it; standing on a file means
        beside it. Both are what somebody pointing at a row would mean.
        """
        path = self.at(line)
        if path is None:
            return self.root
        if os.path.isdir(path):
            return path
        return os.path.dirname(path)


def main():
    editor = Editor(sys.stdout.buffer)
    tree = None
    # Questions this plugin has asked, by the id it asked under, so an answer
    # arriving later knows what it is an answer to. The same trick the editor
    # plays on us, played back.
    asked = {}
    next_id = [1000]

    def ask(what, method, params):
        next_id[0] += 1
        asked[next_id[0]] = what
        editor.ask(next_id[0], method, params)

    def answered(what, answer, error):
        kind, argument = what
        if error is not None:
            # Every one of these is worth reading: a name already taken, a
            # directory with unsaved work in it, a path outside the project.
            return editor.say(error.get("message", "that did not work"), kind="bad")
        if kind == "new-file" and answer:
            ask(("made", None), "file/create",
                {"path": os.path.join(argument, answer)})
        elif kind == "new-folder" and answer:
            ask(("made", os.path.join(argument, answer)), "file/create",
                {"path": os.path.join(argument, answer), "directory": True})
        elif kind == "rename" and answer:
            ask(("made", None), "file/rename",
                {"from": argument, "to": os.path.join(os.path.dirname(argument), answer)})
        elif kind == "delete" and answer == "yes":
            ask(("made", None), "file/delete", {"path": argument})
        elif kind == "menu" and answer:
            # A right click chose something. The menu's values are the panel's
            # own keys, so the two ways of asking for a thing meet here rather
            # than being written out twice.
            key(answer, argument)
        elif kind == "made":
            # A new directory is opened, since making one is nearly always
            # the first half of putting something in it.
            if argument:
                tree.expanded.add(argument)
            tree.draw()

    def key(name, line):
        here = tree.at(line)
        if name in ("space", "enter"):
            if here and os.path.isdir(here):
                tree.toggle(here)
        elif name == ".":
            tree.hidden = not tree.hidden
            tree.draw()
            editor.say("hidden files: " + ("on" if tree.hidden else "off"))
        elif name == "g":
            tree.draw()
        elif name == "n":
            ask(("new-file", tree.near(line)), "prompt",
                {"title": "New file in " + short(tree, tree.near(line))})
        elif name == "m":
            ask(("new-folder", tree.near(line)), "prompt",
                {"title": "New folder in " + short(tree, tree.near(line))})
        elif name == "r":
            if here is None:
                return editor.say("nothing to rename here", kind="bad")
            ask(("rename", here), "prompt",
                {"title": "Rename", "value": os.path.basename(here)})
        elif name == "x":
            if here is None:
                return editor.say("nothing to delete here", kind="bad")
            what = "folder and everything in it" if os.path.isdir(here) else "file"
            ask(("delete", here), "confirm",
                {"text": f"Delete the {what} {os.path.basename(here)}?"})

    while True:
        message = read_message(sys.stdin.buffer)
        if message is None:
            return
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}

        # An answer to something we asked.
        if method is None and request_id in asked:
            answered(asked.pop(request_id), message.get("result"), message.get("error"))
            continue

        if method == "initialize":
            tree = Tree(editor, params.get("root") or ".")
            editor.answer(request_id, {"capabilities": {"commands": True, "panel": True}})
        elif method == "panel/opened":
            tree.showing = True
            tree.draw()
        elif method == "panel/closed":
            tree.showing = False
        elif method == "panel/action":
            tree.act(params.get("action") or "")
        elif method == "panel/key":
            key(params.get("key") or "", params.get("line") or 0)
        elif method == "panel/context":
            here = tree.at(params.get("line") or 0)
            rows = [{"label": "New file", "value": "n"},
                    {"label": "New folder", "value": "m"}]
            if here is not None:
                rows = [{"label": "Rename", "value": "r"},
                        {"label": "Delete", "value": "x"}, None] + rows
            ask(("menu", params.get("line") or 0), "menu", {"items": rows})
        elif method == "shutdown":
            editor.answer(request_id, None)
            return
        elif request_id is not None:
            editor.answer(request_id, None)


def short(tree, path):
    """A path as somebody reading the tree would say it."""
    said = os.path.relpath(path, tree.root)
    return "the project" if said == "." else said


if __name__ == "__main__":
    main()

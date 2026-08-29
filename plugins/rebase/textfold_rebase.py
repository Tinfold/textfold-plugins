#!/usr/bin/env python3
"""git rebase --interactive, as a textfold plugin.

`git rebase -i` works by writing a todo list to a file and opening your editor
on it: you reorder the lines, change `pick` to `squash`, save, and git does
what the file says. This plugin keeps that arrangement and replaces the part
where you edit a file about your commits with a panel *of* your commits.

  * `git log` fills a panel along the bottom, newest last, the way the todo
    file reads;
  * `p` `r` `s` `f` `e` `d` set what happens to the row you are on;
  * `[` and `]` move it up and down the list;
  * `x` runs the rebase, and `z` throws the plan away.

Running it hands git a `GIT_SEQUENCE_EDITOR` that is `cp`: git asks for the
todo file to be edited, and the "editor" it gets replaces it with the one this
plugin wrote. That is the whole trick, and it is why nothing here has to
re-implement any of what rebase does.

**Where it stops.** A rebase that hits a conflict stops and wants files
resolved and `git rebase --continue`. This plugin says so, opens the files git
named, and gives you `c` for continue and `a` for abort — but resolving the
conflict is you and the editor, not this. Conflict resolution is its own
project and pretending otherwise here would be worse than saying it plainly.
"""

import json
import os
import shlex
import subprocess
import sys

PANEL = "rebase/plan"

# What git calls each thing, and what this shows. The letters are git's own,
# so what you press is what ends up in the file.
VERBS = [
    ("p", "pick", "keep it as it is"),
    ("r", "reword", "keep it, change the message"),
    ("e", "edit", "stop here so you can amend it"),
    ("s", "squash", "fold into the one above, keep both messages"),
    ("f", "fixup", "fold into the one above, drop this message"),
    ("d", "drop", "throw it away"),
]
BY_KEY = {key: verb for key, verb, _ in VERBS}
STYLE = {
    "pick": "keyword",
    "reword": "function",
    "edit": "attribute",
    "squash": "type",
    "fixup": "type",
    "drop": "comment",
}

HELP = "p r e s f d set · [ ] move · x run · z reset · a abort"


class Editor:
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
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})

    def say(self, text, kind="plain"):
        self.notify("status/say", {"text": text, "kind": kind})

    def lines(self, lines):
        self.notify("panel/set", {"panel": PANEL, "lines": lines})


def read_message(stream):
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


def git(root, *args):
    """Run git and hand back (ok, what it said). Nothing here is interactive."""
    try:
        done = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True,
            stdin=subprocess.DEVNULL,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except FileNotFoundError:
        return False, "git is not installed"
    return done.returncode == 0, (done.stdout + done.stderr).strip()


class Plan:
    def __init__(self, editor, root):
        self.editor = editor
        self.root = root
        # [{"sha": ..., "subject": ..., "verb": ...}], oldest first, which is
        # the order the todo file is in and the order a rebase applies them.
        self.rows = []
        # The order git had them in, so that moving a commit counts as
        # something to do even where every verb still says pick.
        self.original = []
        self.showing = False
        self.how_many = 10
        self.stopped = False

    # ---- reading git ----

    def load(self, how_many=None):
        if how_many is not None:
            self.how_many = max(1, how_many)
        ok, said = git(self.root, "log", "--no-merges", "--format=%h\x1f%s",
                       "-n", str(self.how_many))
        if not ok:
            self.rows = []
            self.editor.say(said.splitlines()[0] if said else "git log failed", kind="bad")
            return self.draw()
        self.rows = []
        for line in said.splitlines():
            if "\x1f" not in line:
                continue
            sha, subject = line.split("\x1f", 1)
            self.rows.append({"sha": sha, "subject": subject, "verb": "pick"})
        # git log is newest first; a rebase todo is oldest first, and it is the
        # todo's order that has to be on the screen — otherwise "fold into the
        # one above" means the opposite of what it looks like.
        self.rows.reverse()
        self.original = [row["sha"] for row in self.rows]
        self.check_stopped()
        self.draw()

    def check_stopped(self):
        """Whether a rebase is already half-done in this repository."""
        ok, said = git(self.root, "rev-parse", "--git-path", "rebase-merge")
        merge = os.path.join(self.root, said) if ok else ""
        ok, said = git(self.root, "rev-parse", "--git-path", "rebase-apply")
        apply = os.path.join(self.root, said) if ok else ""
        self.stopped = os.path.isdir(merge) or os.path.isdir(apply)

    # ---- drawing ----

    def draw(self):
        if not self.showing:
            return
        lines = []
        if self.stopped:
            lines.append({"spans": [
                {"text": " a rebase is already going. ", "style": "warning"},
                {"text": "c", "style": "keyword"},
                {"text": " continue · ", "style": "muted"},
                {"text": "a", "style": "keyword"},
                {"text": " abort", "style": "muted"},
            ]})
            lines.append("")
        for at, row in enumerate(self.rows):
            lines.append({"spans": [
                {"text": " " + row["verb"].ljust(7),
                 "style": STYLE.get(row["verb"], "keyword"),
                 "action": f"cycle:{at}"},
                {"text": row["sha"] + "  ", "style": "muted", "action": f"show:{at}"},
                {"text": row["subject"],
                 "style": "comment" if row["verb"] == "drop" else "variable",
                 "action": f"show:{at}"},
            ]})
        if not self.rows:
            lines.append({"spans": [{"text": " nothing to rebase", "style": "muted"}]})
        lines.append("")
        lines.append({"spans": [{"text": " " + HELP, "style": "muted"}]})
        self.editor.lines(lines)

    def row_at(self, line):
        """Which commit a drawn row is, or None for a row that is not one."""
        offset = 2 if self.stopped else 0
        at = line - offset
        return at if 0 <= at < len(self.rows) else None

    # ---- what is about to happen ----

    def problem(self):
        """Why this plan cannot be run, or None.

        Asked *before* anybody is asked to confirm it. Being invited to
        rewrite four commits and then told it was never going to work is a
        worse conversation than not being invited.
        """
        if not self.rows:
            return "nothing to rebase"
        if self.rows[0]["verb"] in ("squash", "fixup"):
            return "the oldest commit has nothing above it to fold into"
        return None

    def changes(self):
        """What differs from what git already has, in words."""
        said = [f"{r['verb']} {r['sha']}" for r in self.rows if r["verb"] != "pick"]
        # Reordering is a change even where every verb still says pick, and a
        # plan that only reorders is exactly the one somebody would be most
        # surprised to be told was empty.
        if [r["sha"] for r in self.rows] != self.original:
            said.append("reordered")
        return said

    # ---- the todo file ----

    def todo(self):
        out = []
        for row in self.rows:
            if row["verb"] == "drop":
                # Written out rather than left off, so that what git is handed
                # says what you decided and reads like what you saw.
                out.append(f"drop {row['sha']} {row['subject']}")
            else:
                out.append(f"{row['verb']} {row['sha']} {row['subject']}")
        return "\n".join(out) + "\n"

    def run(self):
        """Hand git the plan, by being the editor it asks for."""
        # Checked again here as well as before the question was asked, because
        # this is the last thing between a plan and somebody's history.
        if (why := self.problem()) is not None:
            return self.editor.say(why, kind="bad")
        # What to rebase onto: the parent of the oldest commit in the plan —
        # unless that commit *is* the root of the repository, which has no
        # parent. `--root` is git's own answer to that, and without it the
        # very first rebase anybody tries in a young repository fails with
        # "invalid upstream".
        oldest = self.rows[0]["sha"]
        has_parent, _ = git(self.root, "rev-parse", "--verify", "--quiet", oldest + "^")
        where = [oldest + "^"] if has_parent else ["--root"]
        path = os.path.join(self.root, ".git", "textfold-rebase-todo")
        try:
            with open(path, "w") as f:
                f.write(self.todo())
        except OSError as e:
            return self.editor.say(str(e), kind="bad")

        # This is the whole trick. git runs $GIT_SEQUENCE_EDITOR on the todo
        # file it wrote; `cp our-plan` run on that file replaces it with ours.
        # No editor, no prompt, nothing to wait for.
        sequence = "cp " + shlex.quote(path)
        try:
            done = subprocess.run(
                ["git", "rebase", "-i", "--autostash", *where],
                cwd=self.root, capture_output=True, text=True,
                stdin=subprocess.DEVNULL,
                env={**os.environ,
                     "GIT_SEQUENCE_EDITOR": sequence,
                     # And nothing may open an editor for a message, either:
                     # a reword mid-rebase would hang waiting for a program
                     # that is not there.
                     "GIT_EDITOR": "true",
                     "GIT_TERMINAL_PROMPT": "0"},
            )
        except FileNotFoundError:
            return self.editor.say("git is not installed", kind="bad")
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

        said = (done.stdout + done.stderr).strip()
        self.check_stopped()
        if done.returncode == 0:
            self.editor.say("rebased", kind="good")
            return self.load()
        first = said.splitlines()[0] if said else "the rebase failed"
        self.editor.say(first, kind="bad")
        self.open_conflicts()
        self.draw()

    def open_conflicts(self):
        """Put the files git could not merge in front of you."""
        ok, said = git(self.root, "diff", "--name-only", "--diff-filter=U")
        if not ok:
            return
        for name in said.splitlines()[:8]:
            self.editor.notify("open", {"path": os.path.join(self.root, name)})

    # ---- what the keys do ----

    def key(self, name, line):
        at = self.row_at(line)
        if name in BY_KEY:
            if at is None:
                return self.editor.say("stand on a commit first", kind="bad")
            self.rows[at]["verb"] = BY_KEY[name]
            return self.draw()
        if name in ("[", "]"):
            if at is None:
                return
            to = at - 1 if name == "[" else at + 1
            if 0 <= to < len(self.rows):
                self.rows[at], self.rows[to] = self.rows[to], self.rows[at]
                self.draw()
            return
        if name == "z":
            for row in self.rows:
                row["verb"] = "pick"
            return self.draw()
        if name == "g":
            return self.load()
        if name == "+":
            return self.load(self.how_many + 5)
        if name == "c":
            ok, said = git(self.root, "rebase", "--continue")
            self.check_stopped()
            self.editor.say(said.splitlines()[0] if said else "continued",
                            kind="good" if ok else "bad")
            return self.load()
        if name == "a":
            ok, said = git(self.root, "rebase", "--abort")
            self.check_stopped()
            self.editor.say("rebase abandoned" if ok else said, kind="good" if ok else "bad")
            return self.load()


def main():
    editor = Editor(sys.stdout.buffer)
    plan = None
    asked = {}
    next_id = [1000]

    def ask(what, method, params):
        next_id[0] += 1
        asked[next_id[0]] = what
        editor.ask(next_id[0], method, params)

    def request_run():
        """The one thing here that rewrites history, so the one thing that asks.

        Every route to running a rebase comes through here — the key, the
        menu, and anything added later — so there is one place that says what
        is about to happen and one box it is said in.
        """
        if (why := plan.problem()) is not None:
            return editor.say(why, kind="bad")
        doing = plan.changes()
        if not doing:
            return editor.say("nothing to do — this is what git already has")
        ask("run", "confirm",
            {"text": f"Rewrite {len(plan.rows)} commits? " + ", ".join(doing)})

    while True:
        message = read_message(sys.stdin.buffer)
        if message is None:
            return
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}

        if method is None and request_id in asked:
            what = asked.pop(request_id)
            answer = message.get("result")
            if what == "run" and answer == "yes":
                plan.run()
            elif isinstance(what, tuple) and what[0] == "menu" and answer:
                # The menu's values are the panel's own keys, so the two ways
                # of asking for a thing meet here rather than being written
                # out twice — including `x`, which still asks first.
                if answer == "x":
                    request_run()
                else:
                    plan.key(answer, what[1])
            continue

        if method == "initialize":
            plan = Plan(editor, params.get("root") or ".")
            editor.answer(request_id, {"capabilities": {"commands": True, "panel": True}})
        elif method == "panel/opened":
            plan.showing = True
            plan.load()
        elif method == "panel/closed":
            plan.showing = False
        elif method == "panel/action":
            what = params.get("action") or ""
            if what.startswith("cycle:"):
                # Clicking the verb steps through them, which is the obvious
                # thing for a pointer to do to a word that is one of six.
                at = int(what[len("cycle:"):])
                order = [v for _, v, _ in VERBS]
                row = plan.rows[at]
                row["verb"] = order[(order.index(row["verb"]) + 1) % len(order)]
                plan.draw()
        elif method == "panel/key":
            name = params.get("key") or ""
            line = params.get("line") or 0
            if name == "x":
                request_run()
            else:
                plan.key(name, line)
        elif method == "panel/context":
            at = plan.row_at(params.get("line") or 0)
            rows = [{"label": f"{verb} — {about}", "value": key} for key, verb, about in VERBS]
            if at is None:
                rows = []
            rows += ([None] if rows else []) + [
                {"label": "Run the rebase", "value": "x"},
                {"label": "Set them all back to pick", "value": "z"},
            ]
            ask(("menu", params.get("line") or 0), "menu", {"items": rows})
        elif method == "command/run":
            name = params.get("id")
            if name == "rebase/continue":
                plan.key("c", 0)
            elif name == "rebase/abort":
                plan.key("a", 0)
            editor.answer(request_id, None)
        elif method == "shutdown":
            editor.answer(request_id, None)
            return
        elif request_id is not None:
            editor.answer(request_id, None)


if __name__ == "__main__":
    main()

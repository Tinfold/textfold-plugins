#!/usr/bin/env python3
"""git rebase --interactive, as a textfold plugin.

git already opens an editor three times during a rebase: on the plan, on a
message it has been asked to reword, and — by way of the files it could not
merge — on the conflict. The interesting thing an editor can do here is not to
replace any of that. It is to *be* the editor git asks for, and then to make
those three buffers worth having opened.

So there is no panel you edit a plan in. The plan is the file git wrote, in a
buffer, and everything textfold already does to a buffer works on it: the
cursor, selections, `move-line-up`, undo, search. What this adds is the three
things the buffer cannot know on its own.

  * **The commit under the cursor**, as a diff, in a panel beside it. This is
    the whole argument for doing a rebase in an editor: you decide between
    squash and drop by looking at the change, not by recognising a subject
    line you wrote a fortnight ago.
  * **What the plan says, checked before git gets it.** A `squash` on the
    first line, a sha that is not in the range, a message with no commit left
    to fold into — in the margin, on save, the way a language server would.
  * **The state git is actually in**, in the panel: what is done, which
    commit it stopped on, which files are unmerged, and what is left to do.

The two ways in meet in the same place. `git rebase -i` in a terminal, with
textfold as the editor, opens the plan here. `rebase/start` in the palette
runs the same `git rebase -i` and hands the plan to this window. Neither is a
different program from the other.

    git config --global sequence.editor textfold
    git config --global core.editor textfold

**How that works.** git runs `$GIT_SEQUENCE_EDITOR` on the todo file and waits
for it to exit; a zero exit means go, and a non-zero one means abandon the
rebase. When the rebase is started from inside the editor there is already a
textfold running, so the "editor" git gets is this same script in `--editor`
mode: it connects back over a socket, asks the running textfold to open the
file, and blocks until you close the buffer. Closing it is what exits zero.
That is the whole trick, and it is why `reword` and a squashed message open in
a buffer here rather than being silently taken as they were.
"""

import json
import os
import shlex
import socket
import subprocess
import sys
import tempfile
import threading

PLAN = "rebase/plan"
COMMIT = "rebase/commit"

# The key the manifest binds the commit panel to, named once so that what the
# plan panel tells you to press and what the manifest binds cannot drift.
PANEL_KEY = "alt-3"

# git's own words, in git's own order, so that what you choose is what ends up
# in the file. The letters are the ones git writes in the todo's comment block.
VERBS = [
    ("pick", "p", "keep it as it is"),
    ("reword", "r", "keep it, change the message"),
    ("edit", "e", "stop here so you can amend it"),
    ("squash", "s", "fold into the one above, keep both messages"),
    ("fixup", "f", "fold into the one above, drop this message"),
    ("drop", "d", "throw it away"),
]
LONG = {short: long for long, short, _ in VERBS}
LONG.update({long: long for long, _, _ in VERBS})

# Everything else a todo may contain. Not offered in the menu — nobody reaches
# for `update-ref` from a list — but known, so that a file holding one is not
# reported as broken.
OTHER = {"exec": "x", "break": "b", "label": "l", "reset": "t", "merge": "m",
         "update-ref": "u", "noop": None}
KNOWN = set(LONG) | set(OTHER) | {s for s in OTHER.values() if s}

STYLE = {
    "pick": "keyword", "reword": "function", "edit": "attribute",
    "squash": "type", "fixup": "type", "drop": "comment",
}

MARKERS = ("<<<<<<<", ">>>>>>>")


# ---------------------------------------------------------------- the editor


class Editor:
    """The other end of the pipe, and the only thing that writes to stdout.

    Locked, because a rebase runs on a thread of its own and a socket waits on
    another, and two of them halfway through a message would be a stream the
    editor cannot parse.
    """

    def __init__(self, out):
        self.out = out
        self.lock = threading.Lock()
        self.next_id = 1000
        self.waiting = {}

    def send(self, message):
        body = json.dumps(message).encode("utf-8")
        with self.lock:
            self.out.write(b"Content-Length: %d\r\n\r\n" % len(body))
            self.out.write(body)
            self.out.flush()

    def notify(self, method, params):
        self.send({"jsonrpc": "2.0", "method": method, "params": params})

    def answer(self, request_id, result=None):
        self.send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def ask(self, method, params, then):
        """Ask the editor something, and say what to do with the answer.

        `then` is called with the result, or with None where the editor
        refused or the person pressed Escape — which is an answer, and the
        commonest one anybody gives a list.
        """
        with self.lock:
            self.next_id += 1
            request_id = self.next_id
            self.waiting[request_id] = then
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method,
                   "params": params})

    def settle(self, message):
        """A reply came back. True if it was one of ours."""
        with self.lock:
            then = self.waiting.pop(message.get("id"), None)
        if then is None:
            return False
        then(message.get("result") if "error" not in message else None)
        return True

    def say(self, text, kind="plain"):
        self.notify("status/say", {"text": text, "kind": kind})

    def lines(self, panel, lines):
        self.notify("panel/set", {"panel": panel, "lines": lines})

    def open(self, path, line=None):
        where = {"path": path}
        if line is not None:
            where["line"] = line
        self.notify("open", where)


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


# ------------------------------------------------------------------- the git


def git(root, *args, timeout=30, env=None):
    """Run git and hand back (ok, what it said). Nothing here is interactive."""
    settings = {**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})}
    # Nothing may reach for an editor by accident. Where a step genuinely
    # wants one the caller passes it in; everywhere else an editor would be a
    # program waiting on a terminal that is not there.
    settings.setdefault("GIT_EDITOR", "true")
    try:
        done = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True,
            stdin=subprocess.DEVNULL, env=settings, timeout=timeout, check=False,
        )
    except FileNotFoundError:
        return False, "git is not installed"
    except subprocess.TimeoutExpired:
        return False, "git took too long and was stopped"
    return done.returncode == 0, (done.stdout + done.stderr).strip()


def worth_saying(said, ok):
    """The one line of git's output a person needs.

    git says a great deal on its way to a conflict, and the first line of it is
    reliably `Auto-merging <file>` — which is not what went wrong, and which
    coloured red is actively misleading. The line worth showing is the one
    naming the problem, wherever in the output it turned up.
    """
    lines = [line.strip() for line in said.splitlines() if line.strip()]
    if not lines:
        return "done" if ok else "the rebase failed"
    for line in lines:
        if line.startswith("CONFLICT"):
            return line
    for line in lines:
        if line.startswith(("fatal:", "error:")):
            return line
    if ok:
        for line in lines:
            if line.startswith("Successfully"):
                return line
        return lines[-1]
    return lines[0]


def same(a, b):
    """Whether two paths are the same file, symlinks and all."""
    try:
        return os.path.realpath(a) == os.path.realpath(b)
    except OSError:
        return a == b


# --------------------------------------------------------------- the todo file


class Step:
    """One line of a todo, as git will read it."""

    def __init__(self, at, verb, sha, subject, text):
        self.at = at          # which line of the file
        self.verb = verb      # as written, long or short
        self.sha = sha
        self.subject = subject
        self.text = text

    @property
    def long(self):
        return LONG.get(self.verb, self.verb)

    @property
    def commits(self):
        """Whether it is a step about a commit, rather than exec or label."""
        return self.long in LONG.values()


def parse_todo(text):
    """The steps in a todo file, in the order git will do them."""
    steps = []
    for at, line in enumerate(text.splitlines()):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split(None, 2)
        verb = parts[0]
        sha = parts[1] if len(parts) > 1 else ""
        subject = parts[2] if len(parts) > 2 else ""
        # Since 2.55 git separates the sha from the subject with a `#`, which
        # is a comment marker everywhere else in the file and not part of what
        # anybody called the commit.
        subject = subject.removeprefix("# ")
        steps.append(Step(at, verb, sha, subject, line))
    return steps


def check(steps, known_shas):
    """What is wrong with this plan, as diagnostics the margin can hold.

    Asked before git is, because git's answer to a bad todo is to reopen the
    file with a comment at the top, and a comment at the top is a worse way to
    find out than a mark on the line.
    """
    found = []

    def problem(step, message, severity="error", column=0, end=None):
        found.append({
            "line": step.at, "column": column,
            "end_line": step.at,
            "end_column": end if end is not None else len(step.text),
            "severity": severity, "message": message, "source": "rebase",
        })

    seen = set()
    folded_into = None   # the last step a squash or fixup could fold into
    for step in steps:
        if step.verb not in KNOWN:
            problem(step, f"git has no rebase command called {step.verb!r}",
                    end=len(step.verb))
            continue
        if not step.commits:
            if step.long in ("exec", "label", "reset", "merge", "update-ref") \
                    and not step.sha:
                problem(step, f"{step.long} needs something after it")
            continue
        if not step.sha:
            problem(step, f"{step.long} needs a commit after it")
            continue
        if known_shas and not any(s.startswith(step.sha) or step.sha.startswith(s)
                                  for s in known_shas):
            problem(step, f"there is no commit in this rebase called {step.sha}",
                    column=len(step.verb) + 1,
                    end=len(step.verb) + 1 + len(step.sha))
        elif step.sha in seen:
            problem(step, f"{step.sha} is already in this plan further up",
                    column=len(step.verb) + 1,
                    end=len(step.verb) + 1 + len(step.sha))
        seen.add(step.sha)

        if step.long in ("squash", "fixup"):
            if folded_into is None:
                problem(step, "there is nothing above this to fold it into")
        elif step.long != "drop":
            folded_into = step

    if not any(s.commits and s.long != "drop" for s in steps):
        found.append({
            "line": 0, "column": 0, "severity": "warning",
            "message": "nothing is left to apply — saving this abandons the rebase",
            "source": "rebase",
        })
    return found


def outcome(steps):
    """What the history will look like afterwards, in words.

    The thing a plan does not say and everybody wants to know: how many
    commits come out of the other end, and what happened to the rest.
    """
    kept = [s for s in steps if s.commits and s.long not in ("drop", "squash", "fixup")]
    folded = [s for s in steps if s.commits and s.long in ("squash", "fixup")]
    dropped = [s for s in steps if s.commits and s.long == "drop"]
    reworded = [s for s in steps if s.commits and s.long == "reword"]
    stops = [s for s in steps if s.commits and s.long == "edit"]
    said = []
    if folded:
        said.append(f"{len(folded)} folded in")
    if dropped:
        said.append(f"{len(dropped)} dropped")
    if reworded:
        said.append(f"{len(reworded)} reworded")
    if stops:
        said.append(f"stops at {len(stops)}")
    return len(kept), said


# ------------------------------------------------------- what git is doing now


class State:
    """Whatever rebase is half-finished in this repository, read from `.git`.

    Read rather than guessed. A rebase that has stopped is not something
    `git log` can be asked about — the head is detached partway through, and a
    log of it describes a history that does not exist yet.
    """

    def __init__(self, root):
        self.root = root
        self.dir = None
        self.kind = None
        self.branch = ""
        self.onto = ""
        self.done = []
        self.todo = []
        self.stopped = ""
        self.at = 0
        self.of = 0
        self.unmerged = []
        self.read()

    def path(self, name):
        ok, said = git(self.root, "rev-parse", "--git-path", name)
        return os.path.join(self.root, said) if ok and said else ""

    def file(self, name, lines=False):
        try:
            with open(os.path.join(self.dir, name)) as f:
                text = f.read()
        except OSError:
            return [] if lines else ""
        return [l for l in text.splitlines() if l.strip()] if lines else text.strip()

    def read(self):
        merge, apply = self.path("rebase-merge"), self.path("rebase-apply")
        if merge and os.path.isdir(merge):
            self.dir, self.kind = merge, "merge"
        elif apply and os.path.isdir(apply):
            self.dir, self.kind = apply, "apply"
        else:
            return
        self.branch = self.file("head-name").replace("refs/heads/", "")
        self.onto = self.file("onto")[:9]
        self.stopped = self.file("stopped-sha")[:9]
        if self.kind == "merge":
            self.done = parse_todo("\n".join(self.file("done", lines=True)))
            self.todo = parse_todo("\n".join(self.file("git-rebase-todo", lines=True)))
            self.at = len(self.done)
            self.of = self.at + len(self.todo)
        else:
            self.at = int(self.file("next") or 0)
            self.of = int(self.file("last") or 0)
        ok, said = git(self.root, "diff", "--name-only", "--diff-filter=U")
        self.unmerged = said.splitlines() if ok and said else []

    @property
    def going(self):
        return self.dir is not None

    def todo_path(self):
        return os.path.join(self.dir, "git-rebase-todo") if self.dir else ""


def markers_in(path):
    """The first line of a file that still has a conflict marker on it."""
    try:
        with open(path, errors="replace") as f:
            for at, line in enumerate(f):
                if line.startswith(MARKERS):
                    return at
    except OSError:
        return None
    return None


# ------------------------------------------------------------------ the plugin


class Rebase:
    def __init__(self, editor, root):
        self.editor = editor
        self.root = root
        self.lock = threading.RLock()
        self.showing = set()

        # The todo buffer the editor has open, if any, and what we last read
        # of it. Kept rather than mirrored from `buffer/changed`: a todo is a
        # dozen short lines, and asking for it is exact where reconstructing
        # it from a stream of offsets is merely usually right.
        self.todo_path = ""
        self.todo_text = ""
        self.todo_version = -1
        self.stale = True
        self.shas = set()

        # Which files the editor has open, and whether they have been saved
        # since they were last changed. A conflict resolved in a buffer and
        # not written is a conflict git cannot see.
        self.buffers = {}

        # git processes blocked on us being their editor: path -> connection.
        self.pending = {}
        self.socket_path = ""
        self.line = 0
        self.showing_sha = ""
        self.diffs = {}
        self.state = State(root)

    # ---- saying things

    def redraw(self):
        if PLAN in self.showing:
            self.draw_plan()
        if COMMIT in self.showing:
            self.draw_commit()

    def reread(self):
        self.state = State(self.root)

    # ---- the todo buffer

    def is_todo(self, path):
        return os.path.basename(path) == "git-rebase-todo"

    def took(self, path, text, version):
        """A todo file's text, however it arrived."""
        with self.lock:
            self.todo_path = path
            self.todo_text = text
            self.todo_version = version
            self.stale = False
            if not self.shas:
                self.shas = {s.sha for s in parse_todo(text) if s.commits and s.sha}
        self.diagnose()
        self.redraw()

    def refresh(self, then=None):
        """Make sure we are holding the buffer as it is now, then carry on.

        The answer comes back some messages later, by which time the buffer
        may have been closed — a plan saved and closed in one gesture is two
        messages, and the read is answered after both. So what comes back is
        taken only if it is still about the buffer we asked about; otherwise
        it is the text of something nobody is looking at any more, and acting
        on it would be the panel describing a rebase that has already gone.
        """
        if not self.todo_path or not self.stale:
            return then and then()
        asked_about = self.todo_path

        def came_back(result):
            if self.todo_path != asked_about:
                return
            if result:
                with self.lock:
                    self.todo_text = result.get("text", "")
                    self.todo_version = result.get("version", -1)
                    self.stale = False
            if then:
                then()

        self.editor.ask("buffer/read", {"path": asked_about}, came_back)

    def steps(self):
        return parse_todo(self.todo_text)

    def diagnose(self):
        if not self.todo_path:
            return
        self.editor.notify("diagnostics/set", {
            "path": self.todo_path,
            "items": check(self.steps(), self.shas),
        })

    def step_on(self, line):
        for step in self.steps():
            if step.at == line:
                return step
        return None

    def after_save(self):
        """The plan was written. Check it, and say what it comes to."""
        if not self.todo_path:
            return
        self.diagnose()
        self.redraw()
        steps = self.steps()
        kept, said = outcome(steps)
        problems = [d for d in check(steps, self.shas) if d["severity"] == "error"]
        if problems:
            return self.editor.say(problems[0]["message"], kind="bad")
        commits = len([s for s in steps if s.commits])
        # How the plan gets run depends on which end started it: a buffer git
        # is blocked on goes when it is closed, and one this window was
        # started on goes when the window does.
        with self.lock:
            waiting = any(self.is_todo(path) for path in self.pending)
        go = "close the buffer to run it" if waiting else "quit when you are done"
        self.editor.say(
            f"{commits} commits → {kept}" + ("   " + " · ".join(said) if said else "")
            + f" — {go}", kind="good")

    def forget_todo(self):
        self.todo_path = ""
        self.todo_text = ""
        self.shas = set()
        self.showing_sha = ""

    def look_at_this_line(self):
        """Follow the cursor: the commit it is on, in the commit panel."""
        step = self.step_on(self.line)
        if step and step.commits and step.sha:
            self.show(step.sha)
        if PLAN in self.showing:
            self.draw_plan()

    # ---- the commit under the cursor

    def show(self, sha):
        """Fill the commit panel with a commit, on a thread."""
        if not sha or sha == self.showing_sha:
            return
        self.showing_sha = sha
        if sha in self.diffs:
            return self.draw_commit()
        threading.Thread(target=self._fetch, args=(sha,), daemon=True).start()

    def _fetch(self, sha):
        ok, said = git(self.root, "show", "--stat", "--patch",
                       "--format=%s%n%an, %ar%n", sha)
        with self.lock:
            self.diffs[sha] = said if ok else f"nothing here called {sha}"
        if self.showing_sha == sha:
            self.draw_commit()

    def draw_commit(self):
        sha = self.showing_sha
        if not sha:
            return self.editor.lines(COMMIT, [
                {"spans": [{"text": " Stand on a commit in the plan.",
                            "style": "muted"}]}])
        text = self.diffs.get(sha)
        if text is None:
            return self.editor.lines(COMMIT, [
                {"spans": [{"text": f" {sha}…", "style": "muted"}]}])
        lines = []
        for at, line in enumerate(text.splitlines()[:600]):
            if at == 0:
                style = "keyword"
            elif line.startswith(("diff --git", "index ")):
                style = "muted"
            elif line.startswith("@@"):
                style = "function"
            elif line.startswith(("+++", "---")):
                style = "muted"
            elif line.startswith("+"):
                style = "string"
            elif line.startswith("-"):
                style = "error"
            else:
                style = None
            lines.append({"spans": [{"text": line, **({"style": style} if style else {})}]}
                         if line else "")
        self.editor.lines(COMMIT, lines)

    # ---- the plan panel

    def draw_plan(self):
        """What is going on, in whichever of the four senses applies.

        The plan comes first when there is one. A rebase started from inside
        the editor is a rebase git is blocked on, and saying only "git is
        waiting for you" while the very thing it is waiting for is on the
        screen would be the panel describing itself instead of the work.
        """
        with self.lock:
            pending = dict(self.pending)
        waiting_on_the_plan = any(self.is_todo(path) for path in pending)
        lines = []

        def row(spans):
            lines.append({"spans": spans})

        def head(text):
            row([{"text": " " + text, "style": "keyword"}])

        if self.todo_path:
            steps = self.steps()
            kept, said = outcome(steps)
            commits = len([s for s in steps if s.commits])
            head(f"{commits} commits → {kept}"
                 + ("   " + " · ".join(said) if said else ""))
            lines.append("")
            for step in steps:
                here = step.at == self.line
                row([{"text": ("▸" if here else " ") + " " + step.long.ljust(7),
                      "style": STYLE.get(step.long, "keyword"),
                      "action": f"line:{step.at}"},
                     {"text": step.sha[:9] + "  ", "style": "muted",
                      "action": f"line:{step.at}"},
                     {"text": step.subject,
                      "style": "comment" if step.long == "drop" else "variable",
                      "action": f"line:{step.at}"}])
            lines.append("")
            row([{"text": " rebase/verb", "style": "function"},
                 {"text": " changes a line · ", "style": "muted"},
                 {"text": "move-line-up", "style": "function"},
                 {"text": " reorders · ", "style": "muted"},
                 {"text": PANEL_KEY, "style": "function"},
                 {"text": " shows the commit", "style": "muted"}])
            if waiting_on_the_plan:
                row([{"text": " Close the buffer to run it, or press ",
                      "style": "muted"},
                     {"text": "a", "style": "keyword"},
                     {"text": " to call the rebase off.", "style": "muted"}])
            else:
                row([{"text": " Save and quit, and git carries on — this is the "
                              "editor it is waiting for.", "style": "muted"}])
            return self.editor.lines(PLAN, lines)

        if pending:
            head("git is waiting for you")
            lines.append("")
            for path in pending:
                row([{"text": "   " + os.path.basename(path), "style": "function",
                      "action": f"open:{path}"},
                     {"text": "  — close this buffer to let git carry on",
                      "style": "muted"}])
            lines.append("")
            row([{"text": " a", "style": "keyword"},
                 {"text": " calls the whole rebase off", "style": "muted"}])
            return self.editor.lines(PLAN, lines)

        state = self.state
        if state.going:
            where = f"{state.branch or 'a detached head'} onto {state.onto}"
            head(f"rebasing {where}")
            if state.of:
                row([{"text": f"   {state.at} of {state.of} done", "style": "muted"}])
            if state.stopped:
                row([{"text": "   stopped at ", "style": "muted"},
                     {"text": state.stopped, "style": "constant"},
                     {"text": "  " + self.subject_of(state.stopped),
                      "style": "variable"}])
            if state.unmerged:
                lines.append("")
                row([{"text": "   still to resolve:", "style": "warning"}])
                for name in state.unmerged:
                    full = os.path.join(self.root, name)
                    row([{"text": "     " + name, "style": "function",
                          "action": f"open:{full}"}] +
                        ([{"text": "  (unsaved)", "style": "warning"}]
                         if self.dirty(full) else []))
            if state.todo:
                lines.append("")
                row([{"text": "   still to do:", "style": "muted"}])
                for step in state.todo[:8]:
                    row([{"text": "     " + step.long.ljust(7),
                          "style": STYLE.get(step.long, "keyword")},
                         {"text": step.sha[:9] + "  ", "style": "muted"},
                         {"text": step.subject, "style": "variable"}])
                if len(state.todo) > 8:
                    row([{"text": f"     and {len(state.todo) - 8} more",
                          "style": "muted"}])
            lines.append("")
            row([{"text": " c", "style": "keyword"},
                 {"text": " continue · ", "style": "muted"},
                 {"text": "s", "style": "keyword"},
                 {"text": " skip this commit · ", "style": "muted"},
                 {"text": "a", "style": "keyword"},
                 {"text": " abort", "style": "muted"}])
            return self.editor.lines(PLAN, lines)

        # Nothing going on: what there is to rebase, and where from.
        head("no rebase in progress")
        lines.append("")
        ok, said = git(self.root, "log", "--format=%h\x1f%s", "-n", "15")
        if not ok:
            row([{"text": " " + worth_saying(said, False), "style": "warning"}])
            return self.editor.lines(PLAN, lines)
        for line in said.splitlines():
            sha, _, subject = line.partition("\x1f")
            row([{"text": "   " + sha + "  ", "style": "muted",
                  "action": f"start:{sha}"},
                 {"text": subject, "style": "variable", "action": f"start:{sha}"}])
        lines.append("")
        row([{"text": " Click a commit to rebase from it, or run ", "style": "muted"},
             {"text": "rebase/start", "style": "function"},
             {"text": ".", "style": "muted"}])
        self.editor.lines(PLAN, lines)

    def subject_of(self, sha):
        ok, said = git(self.root, "log", "-1", "--format=%s", sha)
        return said if ok else ""

    def dirty(self, path):
        known = self.buffers.get(os.path.realpath(path))
        return bool(known and known["version"] != known["saved"])

    # ---- being the editor git asks for

    def listen(self):
        """A socket for the `--editor` half of this script to call back on."""
        directory = tempfile.mkdtemp(prefix="textfold-rebase-")
        self.socket_path = os.path.join(directory, "editor")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(self.socket_path)
        server.listen(4)

        def serve():
            while True:
                try:
                    conn, _ = server.accept()
                except OSError:
                    return
                threading.Thread(target=self.wanted, args=(conn,),
                                 daemon=True).start()

        threading.Thread(target=serve, daemon=True).start()
        return self.socket_path

    def wanted(self, conn):
        """git wants a file edited. Put it in front of somebody."""
        try:
            path = b""
            while not path.endswith(b"\n"):
                more = conn.recv(4096)
                if not more:
                    return conn.close()
                path += more
        except OSError:
            return
        path = path.decode().strip()
        with self.lock:
            self.pending[path] = conn
            # A fresh plan is a fresh set of shas to check against.
            if self.is_todo(path):
                self.shas = set()
        self.editor.open(path)
        what = "the plan" if self.is_todo(path) else "the message"
        self.editor.say(f"{what} is open — close the buffer when you are done "
                        f"with it, and git carries on", kind="good")
        self.reread()
        self.redraw()

    def done_with(self, path, go=True):
        """Let a blocked git go — or tell it to give up."""
        with self.lock:
            conn = None
            for waiting in list(self.pending):
                if same(waiting, path):
                    conn = self.pending.pop(waiting)
                    break
        if conn is None:
            return False
        try:
            conn.sendall(b"ok\n" if go else b"no\n")
            conn.close()
        except OSError:
            pass
        return True

    def let_everything_go(self, go=False):
        for path in list(self.pending):
            self.done_with(path, go)

    def editor_command(self):
        """What to hand git as `$GIT_EDITOR`, pointing back at this window."""
        return (f"{shlex.quote(sys.executable)} {shlex.quote(os.path.abspath(__file__))} "
                f"--editor {shlex.quote(self.socket_path)}")

    # ---- the things that run git

    def on_a_thread(self, what, args, then=None, env=None):
        def go():
            ok, said = git(self.root, *args, timeout=600, env=env)
            self.reread()
            # A non-zero exit from the editor is how calling a rebase off is
            # spelled, and git reports it as a problem with the editor. It is
            # not one: somebody pressed `a`, and has already been told so.
            called_off = not ok and "problem with the editor" in said
            if not called_off:
                self.editor.say(worth_saying(said, ok), kind="good" if ok else "bad")
                if not ok:
                    self.open_conflicts()
            if then:
                then(ok, said)
            self.redraw()
        threading.Thread(target=go, daemon=True).start()
        self.editor.say(what)

    def start(self, sha):
        if self.state.going:
            return self.editor.say(
                "there is already a rebase going — finish or abort it first",
                kind="bad")
        has_parent, _ = git(self.root, "rev-parse", "--verify", "--quiet", sha + "^")
        # `--root` is git's own answer to rebasing the first commit a
        # repository ever had, which otherwise fails with "invalid upstream".
        where = [sha + "^"] if has_parent else ["--root"]
        editor = self.editor_command()
        self.on_a_thread(
            "starting a rebase — the plan will open here",
            ["rebase", "-i", "--autostash", *where],
            env={"GIT_SEQUENCE_EDITOR": editor, "GIT_EDITOR": editor},
        )

    def carry_on(self):
        """`rebase --continue`, and everything a person means by it."""
        self.reread()
        state = self.state
        if not state.going:
            return self.editor.say("no rebase is going", kind="bad")

        for name in state.unmerged:
            full = os.path.join(self.root, name)
            if self.dirty(full):
                self.editor.open(full)
                return self.editor.say(
                    f"{name} has not been saved — save it, then continue",
                    kind="bad")
            at = markers_in(full)
            if at is not None:
                self.editor.open(full, at)
                return self.editor.say(
                    f"{name} still has a conflict marker on line {at + 1}",
                    kind="bad")

        # This is the step the panel used to leave out, and the reason
        # continuing never worked: a file you have finished with is a file git
        # has not been told about. `-A` rather than `add`, so that resolving a
        # conflict by deleting the file is a resolution too.
        for name in state.unmerged:
            ok, said = git(self.root, "add", "-A", "--", name)
            if not ok:
                return self.editor.say(worth_saying(said, False), kind="bad")

        def afterwards(ok, said):
            if not ok and "did not have any new changes" in said:
                self.editor.say(
                    "nothing is left of that commit — rebase/skip drops it",
                    kind="bad")
        editor = self.editor_command()
        self.on_a_thread("continuing", ["rebase", "--continue"], afterwards,
                         env={"GIT_EDITOR": editor})

    def open_conflicts(self):
        for name in self.state.unmerged[:8]:
            self.editor.open(os.path.join(self.root, name))

    # ---- what the keys and the menu do

    def verb_menu(self, line):
        step = self.step_on(line)
        if step is None or not step.commits:
            return self.editor.say("stand on a commit in the plan first", kind="bad")
        items = [{"label": f"{long} — {about}", "value": long}
                 for long, _, about in VERBS]
        self.editor.ask("menu", {"items": items},
                        lambda answer: answer and self.set_verb(step, answer))

    def set_verb(self, step, verb):
        """Change one word, through the editor, so that it is one undo."""
        self.editor.ask("buffer/edit", {
            "path": self.todo_path,
            "version": self.todo_version,
            "edits": [{
                "line": step.at,
                "column": len(step.text) - len(step.text.lstrip()),
                "end_line": step.at,
                "end_column": len(step.text) - len(step.text.lstrip()) + len(step.verb),
                "text": verb,
            }],
        }, lambda result: self.editor.say(f"{step.sha[:9]} — {verb}"))

    def skip(self):
        self.reread()
        if not self.state.going:
            return self.editor.say("no rebase is going", kind="bad")
        self.on_a_thread("skipping", ["rebase", "--skip"])

    def call_off(self):
        """Undo the rebase, by whichever of the three ways applies to it.

        A rebase blocked on us being its editor has not applied anything yet,
        and is called off by the editor exiting non-zero — `git rebase
        --abort` would say there is nothing to abort, because there is not.
        """
        with self.lock:
            pending = bool(self.pending)
        if pending:
            self.let_everything_go(go=False)
            self.forget_todo()
            self.editor.say("the rebase was called off", kind="good")
            self.reread()
            return self.redraw()
        self.reread()
        if self.state.going:
            return self.on_a_thread("abandoning the rebase", ["rebase", "--abort"])
        if self.todo_path:
            return self.editor.say(
                "quit without saving, or empty the plan — git goes by how this "
                "editor exits", kind="bad")
        self.editor.say("no rebase is going", kind="bad")

    def key(self, name, line):
        if name in ("c", "C"):
            return self.carry_on()
        if name in ("s", "S"):
            return self.skip()
        if name in ("a", "A"):
            return self.call_off()
        if name in ("g", "G"):
            self.reread()
            self.diffs.clear()
            return self.refresh(self.redraw)

    def action(self, what):
        kind, _, value = what.partition(":")
        if kind == "open":
            return self.editor.open(value)
        if kind == "line":
            return self.editor.open(self.todo_path, int(value))
        if kind == "start":
            return self.start(value)

    def show_here(self):
        """The commit on this line, in full, in a buffer you can search."""
        step = self.step_on(self.line)
        if step is None or not step.commits or not step.sha:
            return self.editor.say("stand on a commit in the plan first", kind="bad")
        _, said = git(self.root, "show", "--stat", "--patch", step.sha)
        self.editor.notify("buffer/show", {
            "name": f"{step.sha[:9]} {step.subject}"[:60],
            "text": said,
            "focus": True,
        })

    def ask_where_to_start(self):
        """Which commit to rebase from, out of the recent ones."""
        if self.state.going:
            return self.editor.say(
                "there is already a rebase going — finish or abort it first",
                kind="bad")
        ok, said = git(self.root, "log", "--format=%h\x1f%s\x1f%an, %ar", "-n", "25")
        if not ok:
            return self.editor.say(worth_saying(said, False), kind="bad")
        items = []
        for line in said.splitlines():
            sha, _, rest = line.partition("\x1f")
            subject, _, who = rest.partition("\x1f")
            items.append({"label": subject, "value": sha, "tag": sha, "detail": who})
        if not items:
            return self.editor.say("there is nothing to rebase", kind="bad")
        # The commit you pick is the oldest one in the plan, so what is
        # rebased is that commit and everything after it.
        self.editor.ask("pick", {
            "title": "Rebase from which commit?",
            "items": items,
        }, lambda sha: sha and self.start(sha))



# --------------------------------------------------------------- being the shim


def be_the_editor(socket_path, path):
    """git ran us as its editor. Hand the file to the textfold that is up.

    Exits zero when the person is done with the buffer, which is git's signal
    to carry on, and non-zero when they called it off — which is git's signal
    to put everything back. Both of those are exactly what quitting an editor
    means to git already; nothing here is a new arrangement.
    """
    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(3600)
        client.connect(socket_path)
        client.sendall((os.path.abspath(path) + "\n").encode())
        answer = b""
        while not answer.endswith(b"\n"):
            more = client.recv(64)
            if not more:
                break
            answer += more
    except OSError:
        # The window that started this is gone. Saying yes would run whatever
        # git happened to write into the file, which nobody chose.
        sys.stderr.write("textfold is not there to edit this\n")
        return 1
    return 0 if answer.strip() == b"ok" else 1


# ----------------------------------------------------------------- the main loop


def main():
    if len(sys.argv) > 3 and sys.argv[1] == "--editor":
        return sys.exit(be_the_editor(sys.argv[2], sys.argv[3]))

    editor = Editor(sys.stdout.buffer)
    rebase = None

    while True:
        message = read_message(sys.stdin.buffer)
        if message is None:
            # The editor has gone. Anything still blocked on us being its
            # editor is told no rather than left waiting for a window that
            # will not come back.
            if rebase:
                rebase.let_everything_go(go=False)
            return
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}

        # An answer to something we asked, rather than something we were told.
        if method is None and editor.settle(message):
            continue

        try:
            if method == "initialize":
                rebase = Rebase(editor, params.get("root") or ".")
                rebase.listen()
                editor.answer(request_id, {"capabilities": {
                    "commands": True, "panel": True, "buffers": True}})
            elif method == "shutdown":
                if rebase:
                    rebase.let_everything_go(go=False)
                editor.answer(request_id, None)
                return
            elif rebase is None:
                if request_id is not None:
                    editor.answer(request_id, None)
            else:
                handle(editor, rebase, method, request_id, params)
        except Exception as slip:  # noqa: BLE001
            # A plugin that falls over takes its panel with it, and a stack
            # trace on stderr is a log entry nobody reads. Say it where it
            # happened, and answer anything that was waiting on us.
            editor.say(f"rebase: {slip}", kind="bad")
            if request_id is not None:
                editor.answer(request_id, None)


def handle(editor, rebase, method, request_id, params):
    """Everything that needs a plugin already up."""
    path = params.get("path") or ""

    if method == "buffer/opened":
        rebase.buffers[os.path.realpath(path)] = {
            "version": params.get("version", 0),
            "saved": params.get("version", 0),
        }
        if rebase.is_todo(path):
            rebase.took(path, params.get("text", ""), params.get("version", -1))
            editor.say("the rebase plan — rebase/verb changes a line, "
                       f"{PANEL_KEY} shows the commit under the cursor")
            rebase.reread()
            rebase.redraw()

    elif method == "buffer/changed":
        known = rebase.buffers.get(os.path.realpath(path))
        if known:
            known["version"] = params.get("version", known["version"])
        if same(path, rebase.todo_path):
            rebase.stale = True

    elif method == "buffer/saved":
        known = rebase.buffers.get(os.path.realpath(path))
        if known:
            known["saved"] = params.get("version", known["saved"])
            known["version"] = known["saved"]
        if same(path, rebase.todo_path):
            rebase.stale = True
            rebase.refresh(rebase.after_save)
        else:
            # A conflict just written is a conflict that can be continued.
            rebase.redraw()

    elif method == "buffer/closed":
        rebase.buffers.pop(os.path.realpath(path), None)
        # A buffer git is waiting on is one whose closing is the answer.
        if rebase.done_with(path, go=True):
            rebase.reread()
        if same(path, rebase.todo_path):
            rebase.forget_todo()
        rebase.redraw()

    elif method == "selection/changed":
        if same(path, rebase.todo_path):
            rebase.line = params.get("line", 0)
            rebase.refresh(rebase.look_at_this_line)

    elif method == "panel/opened":
        rebase.showing.add(params.get("panel") or PLAN)
        rebase.reread()
        rebase.refresh(rebase.redraw)

    elif method == "panel/closed":
        rebase.showing.discard(params.get("panel") or PLAN)

    elif method == "panel/key":
        rebase.key(params.get("key") or "", params.get("line") or 0)

    elif method == "panel/action":
        rebase.action(params.get("action") or "")

    elif method == "panel/context":
        rebase.refresh(lambda: rebase.verb_menu(rebase.line))

    elif method == "command/run":
        name = params.get("id")
        if name == "rebase/continue":
            rebase.carry_on()
        elif name == "rebase/skip":
            rebase.skip()
        elif name == "rebase/abort":
            rebase.call_off()
        elif name == "rebase/verb":
            rebase.refresh(lambda: rebase.verb_menu(rebase.line))
        elif name == "rebase/show":
            rebase.refresh(rebase.show_here)
        elif name == "rebase/start":
            rebase.ask_where_to_start()
        editor.answer(request_id, None)

    elif request_id is not None:
        editor.answer(request_id, None)


if __name__ == "__main__":
    main()

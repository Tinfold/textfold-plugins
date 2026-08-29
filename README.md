# textfold-plugins

The plugins [textfold](https://github.com/Tinfold/textfold) can fetch.

One directory per plugin, each with a `plugin.json` in it. That is the whole
of the format — most of a plugin is data rather than code, and this repository
is a directory of data with a generated index beside it.

```
plugins/pyright/plugin.json          a language server, which is a table
plugins/cargo/plugin.json            a plugin that is a program, and
plugins/cargo/textfold_cargo.py      the program
…
index.json                       # generated: what is here, and at what version
dist/pyright-1.0.0.tar.gz        # generated: one tarball per plugin
```

Fourteen of these are language servers: a manifest saying which program to run
for which language, and how to get that program. Five are not, and they are
the ones worth reading if you are writing your own:

| | |
|---|---|
| [`files`](plugins/files) | A tree of the project pinned down the left, collapsed with the key that opened it. The example of a plugin changing the editor's **shape** — `"dock": "left"` in the manifest is the whole of the difference. |
| [`rebase`](plugins/rebase) | `git rebase -i` as a panel of your commits instead of a todo file. Sets verbs, reorders, and runs the rebase by being the `GIT_SEQUENCE_EDITOR` git asks for. |
| [`zig`](plugins/zig) | A language textfold has never heard of, colours and all, from a tree-sitter grammar compiled on your machine. No change to the editor. |
| [`cargo`](plugins/cargo) | Build, check, test and clippy without leaving the editor. About a hundred lines of Python, and reading all of it is the point — it is every part of talking to textfold, done by hand, with no library in the way. |
| [`copilot`](plugins/copilot) | GitHub Copilot: inline suggestions bridged to the language server GitHub ships, and a chat panel. Between them the five use every part of the plugin interface there is. |

## Panels, and docking one

A panel is a buffer the plugin owns and fills: styled, clickable lines, its
own keys, updated whenever the plugin likes. Declare it in the manifest and it
is a row in the palette, a key you can bind and a switch in the plugins list
before the program behind it has ever been started.

```json
"panels": [
  { "name": "tree", "about": "The project, as a tree down the side",
    "dock": "left", "size": 32 }
]
```

`dock` is the whole of the difference between a panel you switch to and a
panel that is part of the editor's shape. Without it you get a tab, which is
right for something you read and then leave — a build report, a list of test
failures. With it you get `left`, `right` or `bottom`: a column of a fixed
width down a side, or a row of a fixed height along the bottom, taking its
room off the edge and leaving the middle to the code.

A docked panel is a **switch**. Running its command again puts it away, which
is what collapsible means from a keyboard, and the plugin is told so
(`panel/closed`) rather than being left redrawing into nothing. It comes back
where it was next time you open the project.

It is an ordinary pane in every other respect — a cursor, the focus rule, Tab
into it and out again, `close-pane` — because a sidebar that was its own kind
of surface would need its own answer to every question a pane has already
answered. The only thing it does not get is line numbers, since a tree of file
names has no lines you refer to by number.

A plugin that wants to move or resize it while running can:

```json
{ "method": "panel/dock",
  "params": { "panel": "files/tree", "edge": "bottom", "size": 12 } }
```

`"edge": "none"` puts it back in a tab.

### Changing files

A plugin that rearranges the project asks the editor to do it rather than
running `mv`:

| | |
|---|---|
| `file/create` | `{"path": …, "directory": true}` for a folder |
| `file/rename` | `{"from": …, "to": …}` |
| `file/delete` | `{"path": …}` |

Not tidiness. A buffer open on a file that was renamed underneath it is a
buffer that will save to the old name, and a language server still being told
about a path that no longer exists — going on reporting problems in a file
nobody can open. Only the editor can carry the buffers across, so only the
editor should be doing the move.

Every path is resolved and checked to be inside the project, because a file
explorer is a thing that sends paths back and a plugin that could be talked
into `../../.ssh/id_rsa` by a directory name is a plugin nobody should run.
Deleting refuses outright if anything inside has unsaved changes; `confirm` is
there for asking first, and it asks in a box the person can read.

## Using it

Nothing to set up. textfold has this repository configured already:

```sh
textfold --list-packages         # what is here, and what is installed
textfold --install pyright       # fetch the plugin, then what it needs
textfold --update                # anything with a newer version here
textfold --uninstall pyright
```

The same three are in the command palette as `install-plugin`,
`update-plugins` and `uninstall-plugin`.

To point textfold somewhere else — a fork, or a repository of your own — put
it in `~/.config/textfold/config.json`:

```json
{
  "package_repositories": [
    { "name": "mine", "url": "https://example.invalid/textfold-plugins/main" }
  ]
}
```

Naming any repository replaces the default rather than adding to it, so that
what you get is what you said. Keep the default by writing it out alongside
yours:

```json
{
  "package_repositories": [
    { "name": "textfold-plugins",
      "url": "https://raw.githubusercontent.com/Tinfold/textfold-plugins/main" },
    { "name": "mine", "url": "https://example.invalid/plugins" }
  ]
}
```

A repository is a URL with an `index.json` under it. There is nothing else to
serve, and no server: raw file hosting is enough, which is what makes running
one of these your own business rather than an undertaking.

## Adding a plugin

Make a directory under `plugins/`, put a `plugin.json` in it, run `./build.py`,
and commit both. The directory name and the `id` must match.

```json
{
  "id": "zls",
  "version": "1.0.0",
  "name": "zls",
  "about": "Completions and diagnostics for Zig",
  "needs": ["zls"],
  "install": [
    { "about": "zls, with brew", "run": ["brew", "install", "zls"], "unless": "zls" }
  ],
  "uninstall": [{ "run": ["brew", "uninstall", "zls"] }],
  "see": "https://github.com/zigtools/zls",
  "languages": {
    "zig": {
      "servers": [{ "name": "zls", "command": "zls", "roots": ["build.zig"] }]
    }
  }
}
```

`version` is what an update is decided by. It is compared a number at a time,
so `1.10.0` is newer than `1.9.0` — and anything that is not a number sorts
before one, so `1.0.0-rc1` is older than `1.0.0`. Raise it whenever you change
anything anybody would want.

A plugin that is more than a manifest keeps its other files in the same
directory; they are in the tarball and land beside the manifest when it is
installed. `${plugin}` in the manifest is that directory, which is how a
plugin names a file it brought with it without knowing where textfold put it —
[`cargo`](plugins/cargo/plugin.json) points at its own Python that way, and
[`copilot`](plugins/copilot/plugin.json) installs its npm packages there:

```json
{
  "id": "zig",
  "version": "1.0.0",
  "languages": {
    "zig": {
      "extensions": ["zig"],
      "grammar": {
        "library": "${plugin}/zig.so",
        "symbol": "tree_sitter_zig",
        "highlights": "${plugin}/highlights.scm"
      }
    }
  }
}
```

That is a new language with its own colours, from a plugin, with no change to
textfold. [`zig`](plugins/zig) is that example finished and working, and it is
worth reading before writing your own — the interesting parts are the two that
are not obvious.

**The library is built, not published.** A `.so` is one file per platform and
it does not belong in a package index, so the manifest fetches the grammar's C
and compiles it instead. That is four install steps, and they are written to
fall through:

```json
{ "about": "compiling it, with its external scanner",
  "run": ["cc", "-shared", "-fPIC", "-O2", "-I", "${plugin}/build/src",
          "-o", "${plugin}/zig.so",
          "${plugin}/build/src/parser.c", "${plugin}/build/src/scanner.c"],
  "when": "${plugin}/build/src/scanner.c" },

{ "about": "compiling it",
  "run": ["cc", "-shared", "-fPIC", "-O2", "-I", "${plugin}/build/src",
          "-o", "${plugin}/zig.so", "${plugin}/build/src/parser.c"],
  "when":   "${plugin}/build/src/parser.c",
  "unless": "${plugin}/zig.so" },

{ "about": "building it with the tree-sitter CLI instead",
  "run": ["tree-sitter", "build", "-o", "${plugin}/zig.so", "${plugin}/build"],
  "when":   "${plugin}/build/src/parser.c",
  "unless": "${plugin}/zig.so" }
```

`when` is a file that has to exist for a step to be worth running and `unless`
is one that means it has already been done, so a grammar with an external
scanner takes the first, one without takes the second, and a machine with no C
compiler falls through to the third. Nothing branches; the table does.

`needs` is then `["${plugin}/zig.so"]` — a path rather than a program name. A
bare name is looked up on the `PATH` and has to be runnable; a path only has to
be there. So the plugins list says `needs` until the library is actually built,
rather than saying `on` beside a language with no colours.

**The query is published, and you will probably have to edit it.** Two things
catch people, and [`zig/highlights.scm`](plugins/zig/highlights.scm) has notes
on both where they happen:

- `#lua-match?` is Neovim's, not tree-sitter's. It parses as a predicate
  nobody evaluates, so the pattern fires unconditionally — which for the usual
  `((identifier) @type (#lua-match? @type "^[A-Z]…"))` means *every* name in
  the file is a type. Write `#match?`, which tree-sitter answers.
- **Order matters, in the opposite direction from Neovim's.** Where two
  patterns claim the same bytes textfold keeps the one written *earlier* —
  tree-sitter's own convention, and how a query puts a special case in front of
  a catch-all. Most queries open with `(identifier) @variable` because Neovim
  prefers the *later* pattern; left there, it swallows the whole file. It goes
  at the bottom, and rules sort most specific first.

Getting the symbol right is the third thing. It is usually
`tree_sitter_<name>`, but it is the *grammar's* name and not yours — check
rather than guess:

```sh
nm -D --defined-only zig.so | grep tree_sitter
```

Where a library will not load, textfold says why in the status bar rather than
quietly having no colours.

**Dependencies are fetched, not published.** `node_modules`, `__pycache__`,
`.venv` and `.git` are left out of the tarball whatever is sitting in your
working copy, so testing your own plugin does not put a hundred megabytes of
somebody else's platform binaries into a download. What a plugin needs is
fetched by its own `install` steps, on the machine that will run it:

```json
{ "needs": ["npm",
            "${plugin}/node_modules/@github/copilot-language-server/dist/language-server.js"],
  "install": [{ "run": ["npm", "install", "--prefix", "${plugin}", "--silent"] }],
  "uninstall": [{ "run": ["rm", "-rf", "${plugin}/node_modules"] }] }
```

A `needs` that names a file inside the plugin like that is not checked before
the plugin is installed — there is nothing to fill `${plugin}` in with until
there is a plugin.

## What is generated

`index.json` and `dist/` are written by `./build.py` and committed, so that
serving this repository needs nothing but somewhere to put files.

```sh
./build.py           # write them
./build.py --check   # say whether they are up to date, change nothing
```

The tarballs are byte-for-byte reproducible — no timestamps, no ownership — so
a rebuild that changed nothing changes no files, and a digest in the index
means what it says. CI runs `--check`, which is what catches a plugin edited
without being rebuilt.

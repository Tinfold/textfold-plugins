# textfold-plugins

The plugins [textfold](https://github.com/Tinfold/textfold) can fetch.

One directory per plugin, each with a `plugin.json` in it. That is the whole
of the format — a plugin is data, not code, and this repository is a directory
of data with a generated index beside it.

```
plugins/pyright/plugin.json
plugins/rust-analyzer/plugin.json
…
index.json                       # generated: what is here, and at what version
dist/pyright-1.0.0.tar.gz        # generated: one tarball per plugin
```

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
plugin names a file it brought with it without knowing where textfold put it:

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
textfold. Build the library with `tree-sitter build -o zig.so`, or by hand:

```sh
cc -shared -fPIC -O2 -I src -o zig.so src/parser.c src/scanner.c
nm -D --defined-only zig.so | grep tree_sitter   # what `symbol` should say
```

The symbol is usually `tree_sitter_<name>`, but it is the *grammar's* name and
not yours — check rather than guess. Where it will not load, textfold says why
in the status bar rather than quietly having no colours.

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

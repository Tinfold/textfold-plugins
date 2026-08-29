#!/usr/bin/env python3
"""Turn the plugins in this repository into something textfold can fetch.

A package repository is two things and no more: an `index.json` saying what is
here and what version it is at, and one tarball per plugin. Both are written
into the repository and committed, so that serving this needs no build server,
no release pipeline and no account anywhere — a raw file URL is a package
repository.

    ./build.py            write index.json and dist/
    ./build.py --check    say whether they are up to date, and change nothing

The second is what a CI job runs, so that a plugin edited without rebuilding
is caught in the pull request rather than by somebody's editor failing to
find a version that was never published.
"""

import argparse
import hashlib
import io
import json
import os
import pathlib
import sys
import tarfile

HERE = pathlib.Path(__file__).parent.resolve()
PLUGINS = HERE / "plugins"
DIST = HERE / "dist"
INDEX = HERE / "index.json"

# What textfold understands. Bumped only when an older textfold could not read
# this file, so that one which can carry on doing so.
FORMAT = 1

# Where this repository is served from. An entry's `url` is relative to it, so
# a fork serves its own copy without editing anything.
ABOUT = "The plugins textfold can fetch: a language server each, and what it takes to get one."


def plugins():
    """Every plugin here, as (directory, manifest), in a settled order."""
    for directory in sorted(PLUGINS.iterdir()):
        manifest = directory / "plugin.json"
        if manifest.is_file():
            yield directory, json.loads(manifest.read_text())


def tarball(directory):
    """One plugin as a gzipped tar, byte for byte the same every time.

    Reproducible on purpose: a tarball whose bytes changed because the clock
    did would be a new version of every plugin on every build, and the whole
    point of a digest in the index is that it says whether anything changed.
    """
    raw = io.BytesIO()
    # mtime=0 and no gzip filename field, or gzip stamps the time into it.
    with tarfile.open(fileobj=raw, mode="w:gz", format=tarfile.PAX_FORMAT) as tar:
        for path in sorted(p for p in directory.rglob("*") if p.is_file()):
            info = tar.gettarinfo(path, arcname=str(path.relative_to(directory)))
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o755 if os.access(path, os.X_OK) else 0o644
            with path.open("rb") as f:
                tar.addfile(info, f)
    # gzip writes the time into its header; zero it so the bytes settle.
    data = bytearray(raw.getvalue())
    data[4:8] = b"\0\0\0\0"
    return bytes(data)


def check(ident, manifest):
    """What a plugin here has to say about itself, and why.

    These used to be tests inside textfold, back when the language servers
    shipped in the binary. They belong here now: this is where the manifests
    are, and a manifest that is wrong should be caught by whoever is editing
    it rather than by somebody's editor months later.
    """
    problems = []
    if not manifest.get("about"):
        problems.append(f"{ident}: says nothing about itself")

    servers = [
        (language, server)
        for language, define in manifest.get("languages", {}).items()
        for server in define.get("servers", [])
    ]
    if not servers:
        # A plugin that is not a language server is fine; the rest of this is
        # about the ones that are.
        return problems

    needs = manifest.get("needs", [])
    if not needs:
        problems.append(f"{ident}: does not say what it needs on the machine")
    if not manifest.get("install"):
        # A row in a list saying "you have not got this" and not saying what
        # to do about it is a row that wastes an afternoon.
        problems.append(f"{ident}: does not say how to get it")
    if not manifest.get("see"):
        problems.append(f"{ident}: does not say where to get it by hand")

    for language, server in servers:
        command = server.get("command", "")
        if not command:
            problems.append(f"{ident}: a server for {language} runs nothing")
        elif command not in needs:
            # A `needs` naming something other than what is run reports a
            # server as ready that cannot start, which is worse than silence.
            problems.append(f"{ident}: runs {command} and does not say it needs it")
    return problems


def build():
    """What index.json and dist/ should contain."""
    entries, files, problems = [], {}, []
    seen = set()
    for directory, manifest in plugins():
        ident = manifest.get("id") or directory.name
        version = manifest.get("version")
        if not version:
            problems.append(f"{directory.name}: no version")
            continue
        if ident in seen:
            problems.append(f"{ident}: two plugins with the same id")
            continue
        seen.add(ident)
        if ident != directory.name:
            problems.append(f"{directory.name}: calls itself {ident}")
        problems.extend(check(ident, manifest))
        data = tarball(directory)
        name = f"{ident}-{version}.tar.gz"
        files[name] = data
        entries.append({
            "id": ident,
            "name": manifest.get("name", ident),
            "about": manifest.get("about", ""),
            "version": version,
            "url": f"dist/{name}",
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
            # So that a list can say what a plugin will want fetching before
            # anybody has downloaded it.
            "needs": manifest.get("needs", []),
            "see": manifest.get("see"),
        })
    index = {
        "format": FORMAT,
        "repository": "textfold-plugins",
        "about": ABOUT,
        "plugins": entries,
    }
    return index, files, problems


def write(index, files):
    DIST.mkdir(exist_ok=True)
    INDEX.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n")
    for name, data in files.items():
        (DIST / name).write_bytes(data)
    # A tarball for a version nobody publishes any more is litter, and litter
    # in a directory people fetch from is a download that half works.
    for stale in DIST.iterdir():
        if stale.name not in files:
            stale.unlink()
            print(f"removed {stale.name}")


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--check", action="store_true",
                      help="say whether it is up to date and change nothing")
    args = args.parse_args()

    index, files, problems = build()
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    if problems:
        return 1

    if args.check:
        want = json.dumps(index, indent=2, ensure_ascii=False) + "\n"
        stale = []
        if not INDEX.is_file() or INDEX.read_text() != want:
            stale.append("index.json")
        for name, data in files.items():
            path = DIST / name
            if not path.is_file() or path.read_bytes() != data:
                stale.append(f"dist/{name}")
        for path in DIST.iterdir() if DIST.is_dir() else []:
            if path.name not in files:
                stale.append(f"dist/{path.name} is no longer published")
        if stale:
            print("out of date — run ./build.py:", file=sys.stderr)
            for one in stale:
                print(f"  {one}", file=sys.stderr)
            return 1
        print(f"up to date: {len(files)} plugins")
        return 0

    write(index, files)
    print(f"wrote index.json and {len(files)} tarballs")
    return 0


if __name__ == "__main__":
    sys.exit(main())

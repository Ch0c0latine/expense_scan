#!/usr/bin/env python3
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Copy a released version of the module into the store repository.

The Odoo Apps Store reads a branch named after the Odoo version, one folder
per module. This script replaces the expense_scan folder of a checkout of
that branch with the files of a tag, as `git archive` gives them (the paths
marked export-ignore in .gitattributes are left out), then commits. Pushing
stays a manual step.

    python3 tools/publish_store.py v19.0.3.0.0 ../odoo-apps
"""
import ast
import io
import pathlib
import shutil
import subprocess
import sys
import tarfile

MODULE = 'expense_scan'


def git(repo, *args, data=False):
    out = subprocess.run(['git', '-C', str(repo), *args],
                         check=True, capture_output=True)
    return out.stdout if data else out.stdout.decode().strip()


def main(tag, store):
    here = pathlib.Path(__file__).resolve().parent.parent
    store = pathlib.Path(store).resolve()
    if git(store, 'status', '--porcelain'):
        sys.exit(f"{store} has uncommitted changes")
    branch = git(store, 'branch', '--show-current')
    manifest = ast.literal_eval(git(here, 'show', f'{tag}:__manifest__.py'))
    if not manifest['version'].startswith(branch + '.'):
        sys.exit(f"version {manifest['version']} does not belong on branch {branch}")

    target = store / MODULE
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir()
    archive = git(here, 'archive', '--format=tar', tag, data=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(target, filter='data')

    git(store, 'add', '-A', MODULE)
    if not git(store, 'status', '--porcelain'):
        sys.exit(f"{MODULE} {manifest['version']} is already in {store}")
    git(store, 'commit', '-q', '-m', f"{MODULE} {manifest['version']}")
    print(f"{MODULE} {manifest['version']} committed in {store} ({branch}); "
          "check it, then push.")


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(__doc__.strip().splitlines()[-1].strip())
    main(*sys.argv[1:])

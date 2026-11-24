"""Bounded snapshots and a checked, host-generated Git patch."""
from contextlib import contextmanager
import fcntl
from pathlib import Path
import os
import stat
import subprocess
from unicodedata import normalize

from ai_anti_config import ConfigError
from review_material import MAX_STAGE_BYTES, _sensitive, sha

DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def git(root, *args, patch=None):
    # Ambient GIT_* variables can redirect operations outside the checked root.
    # Restrict rewritten/new files until existing permission bits are restored.
    return subprocess.run(['/usr/bin/git', '-C', str(root), *args], input=patch, capture_output=True,
                          env={'PATH': '/usr/bin:/bin', 'GIT_CONFIG_NOSYSTEM': '1',
                               'GIT_CONFIG_GLOBAL': os.devnull, 'LC_ALL': 'C'}, timeout=15, umask=0o077)


@contextmanager
def workspace_lock(root, exclusive):
    marker = root / '.git'
    if not stat.S_ISREG(marker.lstat().st_mode):
        raise ConfigError('custom_api requires a linked Git worktree')
    checkout = git(root, 'rev-parse', '--show-toplevel', '--absolute-git-dir', '--git-common-dir')
    paths = checkout.stdout.splitlines()
    if (checkout.returncode or len(paths) != 3 or os.fsdecode(paths[0]) != str(root)
            or paths[1] == paths[2] or git(root, 'symbolic-ref', '-q', 'HEAD').returncode != 1
            or git(root, 'rev-parse', '--verify', 'HEAD^{commit}').returncode):
        raise ConfigError('custom_api requires a detached linked Git worktree root')
    fd = os.open(root, DIRECTORY)
    try:
        fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        yield fd
    finally:
        os.close(fd)


def validate_paths(paths):
    if not paths:
        raise ConfigError('custom_api requires at least one --file')
    seen = set()
    for path in paths:
        parts = path.split('/')
        if (any(not part or part in ('.', '..') or part.casefold() in
                ('.git', '.ssh', '.aws', '.codex', '.config', '.gnupg', '.kube', '.docker')
                or _sensitive(Path(part)) for part in parts)
                or any(ord(char) < 32 or ord(char) == 127 for char in path)
                or '\\' in path):
            raise ConfigError('unsafe or sensitive custom_api file path')
        alias = normalize('NFC', path).casefold()
        if alias in seen:
            raise ConfigError('duplicate or aliased custom_api file path')
        seen.add(alias)
    if any(any('/'.join(path.split('/')[:index]) in seen
               for index in range(1, len(path.split('/')))) for path in seen):
        raise ConfigError('custom_api file path is also a selected directory')


def text(data, limit):
    if len(data) > limit or b'\0' in data:
        raise ConfigError('custom_api input exceeds text limits or contains NUL')
    return data.decode('utf-8')


def snapshot(root_fd, paths, limit=MAX_STAGE_BYTES):
    result = {}
    for path in paths:
        parts = path.split('/')
        parent = os.dup(root_fd)
        try:
            for part in parts[:-1]:
                child = os.open(part, DIRECTORY, dir_fd=parent)
                os.close(parent)
                parent = child
            parent_stat = os.fstat(parent)
            parent_id = parent_stat.st_dev, parent_stat.st_ino
            try:
                baseline = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                result[path] = (None, None, parent_id)
                continue
            if not stat.S_ISREG(baseline.st_mode) or baseline.st_nlink != 1:
                raise ConfigError('custom_api targets must be unlinked regular files')
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            with os.fdopen(os.open(parts[-1], flags, dir_fd=parent), 'rb') as source:
                current = os.fstat(source.fileno())
                if current != baseline or current.st_nlink != 1:
                    raise ConfigError('custom_api target changed during preflight')
                data = source.read(limit + 1)
                if file_identity(os.fstat(source.fileno())) != file_identity(current):
                    raise ConfigError('custom_api target changed during preflight')
            result[path] = (text(data, limit), current, parent_id)
            limit -= len(data)
        finally:
            os.close(parent)
    return result


def fingerprint(source):
    return None if source is None else sha(source.encode('utf-8'))


def quote(name):
    token = ''.join(chr(ch) if 32 <= ch < 127 and ch not in (34, 92)
                    else f'\\{ch:03o}' for ch in name.encode('utf-8'))
    return '"' + token + '"'


def patch_lines(prefix, value):
    if not value:
        return []
    lines = value.split('\n')
    result = [prefix + line + '\n' for line in lines[:-1]]
    if lines[-1]:
        result += [prefix + lines[-1] + '\n', '\\ No newline at end of file\n']
    return result


def make_patch(before, changes):
    patch = []
    for path, replacement in changes.items():
        old, metadata, _ = before[path]
        left, right = quote('a/' + path), quote('b/' + path)
        patch.append(f'diff --git {left} {right}\n')
        if metadata is None:
            patch.append('new file mode 100644\n')
            left = '/dev/null'
        if old or replacement:
            removed = patch_lines('-', old)
            added = patch_lines('+', replacement)
            old_count = old.count('\n') + int(not old.endswith('\n')) if old else 0
            new_count = replacement.count('\n') + int(not replacement.endswith('\n')) if replacement else 0
            patch += [f'--- {left}\n+++ {right}\n',
                      f'@@ -{int(bool(old_count))},{old_count} +{int(bool(new_count))},{new_count} @@\n']
            patch.extend(removed + added)
    return ''.join(patch).encode('utf-8')


def unchanged(before, current):
    for path, (source, metadata, parent) in before.items():
        new_source, new_metadata, new_parent = current[path]
        if (source != new_source or parent != new_parent
                or file_identity(metadata) != file_identity(new_metadata)):
            return False
    return True


def file_identity(metadata):
    if metadata is None:
        return None
    return (metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_size,
            metadata.st_mtime_ns, metadata.st_ctime_ns)


def restore_modes(root_fd, before, after, changes):
    """Git preserves executability but may normalize other permission bits."""
    for path in changes:
        old = before[path][1]
        new = after[path][1]
        if old is None or stat.S_IMODE(old.st_mode) == stat.S_IMODE(new.st_mode):
            continue
        parts = path.split('/')
        parent = os.dup(root_fd)
        try:
            for part in parts[:-1]:
                child = os.open(part, DIRECTORY, dir_fd=parent)
                os.close(parent)
                parent = child
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            with os.fdopen(os.open(parts[-1], flags, dir_fd=parent), 'rb') as output:
                if file_identity(os.fstat(output.fileno())) != file_identity(new):
                    raise ConfigError('custom_api target changed during mode restoration')
                os.fchmod(output.fileno(), stat.S_IMODE(old.st_mode))
        finally:
            os.close(parent)

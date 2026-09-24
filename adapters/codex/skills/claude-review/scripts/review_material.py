"""Bounded and symlink-safe material preparation."""
from __future__ import annotations
import hashlib
import os
import stat
import subprocess
import tempfile
import time
from pathlib import Path

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_STAGE_BYTES = 2 * 1024 * 1024
MAX_LINE_CHARS = 2000
GIT_TIMEOUT = 15
GIT_OUTPUT_LIMIT = 2 * 1024 * 1024
SECRET_NAMES = {".env", ".envrc", "secrets.json", "credentials.json", "id_rsa", "id_ed25519"}


class MaterialError(ValueError):
    pass


def sha(data):
    return hashlib.sha256(data).hexdigest()


def no_symlink(path):
    path = Path(path)
    if ".." in path.parts:
        raise MaterialError("parent traversal is refused: %s" % path)
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            mode = os.lstat(current).st_mode
        except OSError as exc:
            raise MaterialError("cannot inspect path: %s" % path) from exc
        if stat.S_ISLNK(mode):
            raise MaterialError("symbolic-link path is refused: %s" % path)


def _git(args, cwd):
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(args, cwd=str(cwd), stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=subprocess.STDOUT, env=env)
        started = time.monotonic()
        while process.poll() is None:
            if time.monotonic() - started > GIT_TIMEOUT:
                process.kill()
                process.wait()
                raise MaterialError("git command timed out")
            if output.tell() > GIT_OUTPUT_LIMIT:
                process.kill()
                process.wait()
                raise MaterialError("git command output exceeds limit")
            time.sleep(0.02)
        output.seek(0)
        data = output.read(GIT_OUTPUT_LIMIT + 1)
    if len(data) > GIT_OUTPUT_LIMIT:
        raise MaterialError("git command output exceeds limit")
    if process.returncode:
        raise MaterialError(data.decode("utf-8", "replace").strip() or "git command failed")
    return data


def run_git(repo, *args):
    return _git(["git", "-C", str(repo), "--literal-pathspecs", "-c", "core.fsmonitor=false", *args], repo)


def git_root(value):
    raw = Path(value).expanduser()
    if not raw.is_absolute():
        raw = Path.cwd() / raw
    no_symlink(raw)
    if not raw.is_dir():
        raise MaterialError("--repo must be an existing directory")
    root = Path(run_git(raw, "rev-parse", "--show-toplevel").decode("utf-8").strip())
    no_symlink(root)
    if raw != root:
        raise MaterialError("--repo must be the Git working-tree root")
    return root


def safe_out(value, repo):
    output = Path(value).expanduser()
    if not output.is_absolute():
        output = Path.cwd() / output
    no_symlink(output.parent)
    output = output.parent.resolve() / output.name
    if output.exists():
        raise MaterialError("--out must be a new exclusive directory")
    try:
        output.relative_to(repo)
    except ValueError:
        pass
    else:
        raise MaterialError("--out must be outside --repo")
    output.mkdir(mode=0o700, parents=True)
    return output


def _sensitive(relative):
    lower = relative.name.lower()
    return lower in SECRET_NAMES or lower.startswith(".env.") or lower.endswith("_secrets.json") or relative.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}


def relative_file(repo, name, must_exist=True):
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts or ".git" in relative.parts or not relative.parts or _sensitive(relative):
        raise MaterialError("unsafe or sensitive repository path: %s" % name)
    target = repo / relative
    no_symlink(target.parent)
    if target.is_symlink():
        raise MaterialError("symbolic-link file is refused: %s" % name)
    if target.exists():
        if not stat.S_ISREG(os.lstat(target).st_mode):
            raise MaterialError("not a regular file: %s" % name)
    elif must_exist:
        raise MaterialError("selected path does not exist: %s" % name)
    return str(relative), target


def read_text(path, label):
    no_symlink(path)
    try:
        with Path(path).open("rb") as source:
            data = source.read(MAX_FILE_BYTES + 1)
    except OSError as exc:
        raise MaterialError("cannot read %s" % label) from exc
    if len(data) > MAX_FILE_BYTES or b"\0" in data:
        raise MaterialError("file is oversized or binary: %s" % label)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MaterialError("non-UTF-8 file is refused: %s" % label) from exc
    if any(len(line) > MAX_LINE_CHARS for line in text.split("\n")):
        raise MaterialError("line exceeds 2000 characters: %s" % label)
    return data


def validate_plan(value):
    plan = Path(value).expanduser()
    if not plan.is_absolute():
        plan = Path.cwd() / plan
    no_symlink(plan.parent)
    if plan.suffix != ".md" or not plan.exists() or not stat.S_ISREG(os.lstat(plan).st_mode):
        raise MaterialError("--plan must be a regular .md file")
    plan_data = read_text(plan, "plan")
    request = plan.with_name(plan.stem + ".request.md")
    if not request.exists() or not stat.S_ISREG(os.lstat(request).st_mode):
        raise MaterialError("missing regular sibling request file")
    request_data = read_text(request, "request")
    before = request_data.decode("utf-8").split("## 附录：非需求", 1)[0]
    if not any(line.strip() and not line.lstrip().startswith("#") for line in before.splitlines()):
        raise MaterialError("request lacks material before appendix")
    return plan, plan_data, request, request_data


def selected(repo, names, allow_missing=False):
    records, seen = [], set()
    for name in names:
        normalized, path = relative_file(repo, name, must_exist=not allow_missing)
        if normalized in seen:
            raise MaterialError("duplicate path: %s" % normalized)
        seen.add(normalized)
        records.append((normalized, path, read_text(path, normalized) if path.exists() else None))
    return records


def enforce_limit(*groups):
    total = sum(len(data) for group in groups for _name, _path, data in group if data is not None)
    if total > MAX_STAGE_BYTES:
        raise MaterialError("snapshot materials exceed 2MiB")


def write_snapshot(root, records):
    manifest = []
    for name, _path, data in records:
        if data is None:
            continue
        target = root / "repo" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        os.chmod(target, 0o600)
        manifest.append({"path": name, "bytes": len(data), "sha256": sha(data)})
    return manifest


def full_git_metadata(repo):
    try:
        head = run_git(repo, "rev-parse", "HEAD").decode().strip()
    except MaterialError as exc:
        raise MaterialError("--repo must have an initial HEAD commit") from exc
    return {"head": head,
            "status": run_git(repo, "status", "--short", "--untracked-files=all").decode(),
            "tracked": run_git(repo, "ls-files", "-z").decode().split("\0")[:-1]}


def _head_file(repo, name):
    probe = subprocess.run(["git", "-C", str(repo), "--literal-pathspecs", "cat-file", "-e", "HEAD:" + name],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"), timeout=GIT_TIMEOUT)
    return probe.returncode == 0


def _git_text(repo, args, label):
    data = run_git(repo, *args)
    if len(data) > MAX_FILE_BYTES or b"\0" in data:
        raise MaterialError("%s is oversized or binary" % label)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MaterialError("%s is not UTF-8" % label) from exc
    if any(len(line) > MAX_LINE_CHARS for line in text.split("\n")):
        raise MaterialError("%s has a long line" % label)
    return data


def diff_material(repo, records, destination):
    changes, total = [], 0
    for name, _path, current in records:
        head_tracked = _head_file(repo, name)
        if current is None and not head_tracked:
            raise MaterialError("missing path is not a HEAD deletion: %s" % name)
        status = run_git(repo, "status", "--short", "--untracked-files=all", "--", name).decode()
        if not head_tracked:
            changes.append({"path": name, "kind": "untracked", "status": status, "content_sha256": sha(current)})
            continue
        patch = _git_text(repo, ("diff", "--no-ext-diff", "--no-textconv", "HEAD", "--", name), name + " diff")
        patch_name = sha(name.encode()) + ".patch"
        target = destination / "diff" / patch_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(patch)
        total += len(patch)
        kind = "deleted" if current is None else "tracked"
        record = {"path": name, "kind": kind, "status": status, "diff_sha256": sha(patch)}
        if current is None:
            old = _git_text(repo, ("show", "HEAD:" + name), name + " deleted source")
            old_target = destination / "deleted" / name
            old_target.parent.mkdir(parents=True, exist_ok=True)
            old_target.write_bytes(old)
            total += len(old)
            record["head_sha256"] = sha(old)
        changes.append(record)
        if total > MAX_STAGE_BYTES:
            raise MaterialError("diff materials exceed 2MiB")
    return changes

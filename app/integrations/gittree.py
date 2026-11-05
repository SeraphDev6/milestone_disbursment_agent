"""Git object ids computed locally, so a source archive can be checked against the commit that was verified.

A commit pins a tree; the tree id is a hash over every file's bytes, name and mode. Hashing the files of an archive
the way git does and getting the pinned tree id back proves the archive is exactly the verified code, without
trusting whoever produced the archive. Anything git tracks that an archive can't carry (submodules, files dropped
by `export-ignore`, `export-subst` rewrites) changes the id, so those fail closed rather than pass.
"""

import hashlib
import io
import tarfile
from dataclasses import dataclass

MAX_FILES = 5000
MAX_BYTES = 100 * 1024 * 1024


class ArchiveError(Exception):
    pass


@dataclass(frozen=True)
class Entry:
    path: str  # slash-separated, relative to the repo root
    mode: str  # 100644, 100755 or 120000 (symlink; data is the link target)
    data: bytes


def blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()  # git object id, not a security hash


def tree_id(entries: list[Entry]) -> str:
    """The id `git write-tree` gives a tree holding exactly these files."""
    root: dict = {}
    for e in entries:
        *dirs, name = e.path.split("/")
        node = root
        for d in dirs:
            node = node.setdefault(d, {})
            if not isinstance(node, dict):
                raise ArchiveError(f"{e.path} is under a file")
        node[name] = e
    return _tree(root)


def _tree(node: dict) -> str:
    def key(item):  # git sorts directories as if their name ended in '/'
        name, value = item
        return name.encode() + (b"/" if isinstance(value, dict) else b"")

    body = b""
    for name, value in sorted(node.items(), key=key):
        if isinstance(value, dict):
            mode, oid = b"40000", _tree(value)
        else:
            mode, oid = value.mode.encode(), blob_id(value.data)
        body += mode + b" " + name.encode() + b"\0" + bytes.fromhex(oid)
    return hashlib.sha1(b"tree %d\0" % len(body) + body).hexdigest()


def untar(archive: bytes) -> list[Entry]:
    """Files in a GitHub tarball (`/repos/{repo}/tarball/{sha}`), with the top-level `owner-repo-sha/` stripped."""
    entries: list[Entry] = []
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as tar:
            for info in tar:
                if info.name == "pax_global_header" or info.isdir():
                    continue
                parts = info.name.split("/", 1)
                if len(parts) < 2 or not parts[1]:
                    continue
                path = parts[1]
                if path.startswith("/") or ".." in path.split("/"):
                    raise ArchiveError(f"unsafe path in archive: {path}")
                if info.issym():
                    entries.append(Entry(path, "120000", info.linkname.encode()))
                elif info.isfile():
                    f = tar.extractfile(info)
                    data = f.read() if f else b""
                    entries.append(Entry(path, "100755" if info.mode & 0o111 else "100644", data))
                else:
                    continue  # hard links and devices don't occur in git archives
                total += len(entries[-1].data)
                if len(entries) > MAX_FILES or total > MAX_BYTES:
                    raise ArchiveError(f"archive is over {MAX_FILES} files or {MAX_BYTES // 2**20} MB")
    except tarfile.TarError as e:
        raise ArchiveError(f"not a readable tar archive: {e}") from e
    return entries

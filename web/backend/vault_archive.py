import os
import shutil
import subprocess


class PathEscapeError(ValueError):
    """Raised when a requested relative path, once symlinks and `..` are
    fully resolved, no longer sits inside the snapshot root it was supposed
    to be confined to."""


def resolve_safe_path(snapshot_root: str, rel_path: str) -> str:
    """Resolves rel_path against snapshot_root and guarantees the result is
    still inside it - the one genuinely dangerous part of browsing a
    container's own filesystem. A container can contain arbitrary symlinks
    (to an absolute path like /etc, or via enough ../.. segments), and if
    those were followed naively, browsing or downloading through one could
    read files on the vault host itself, well outside the read-only backup
    this is supposed to be confined to. os.path.realpath() resolves BOTH
    symlinks and `..` components in one pass, so checking the result
    against the root's own realpath (also resolved, in case the root itself
    is reached through a symlink) is suffient - no separate ".." filtering
    needed beforehand."""
    root_real = os.path.realpath(snapshot_root)
    candidate = os.path.join(root_real, rel_path.lstrip("/"))
    candidate_real = os.path.realpath(candidate)
    if candidate_real != root_real and not candidate_real.startswith(root_real + os.sep):
        raise PathEscapeError(f"'{rel_path}' escapes the snapshot root")
    return candidate_real


def list_snapshot_dir(snapshot_root: str, rel_path: str, offset: int = 0, limit: int = 500) -> dict:
    """Directory listing for the file browser - lstat-based (does not
    follow symlinks just to list them), so an entry that happens to be a
    symlink is shown as one without ever resolving where it points. Actual
    navigation *into* a symlinked directory, or downloading *through* one,
    goes through resolve_safe_path again at that point and is rejected
    there if it escapes - listing itself never leaks anything beyond the
    entry's name and its own lstat metadata."""
    target = resolve_safe_path(snapshot_root, rel_path)
    if not os.path.isdir(target):
        raise NotADirectoryError(f"'{rel_path}' is not a directory")

    raw = []
    with os.scandir(target) as it:
        for entry in it:
            is_symlink = entry.is_symlink()
            try:
                st = entry.stat(follow_symlinks=False)
                is_dir = entry.is_dir(follow_symlinks=True) if is_symlink else entry.is_dir(follow_symlinks=False)
            except OSError:
                # broken symlink or a race with something deleting the file -
                # still list it, just as a non-browsable, sizeless entry
                st = None
                is_dir = False
            raw.append({
                "name": entry.name,
                "is_dir": is_dir,
                "is_symlink": is_symlink,
                "symlink_target": os.readlink(entry.path) if is_symlink else None,
                "size_bytes": st.st_size if st else None,
                "mtime_epoch": int(st.st_mtime) if st else None,
            })

    raw.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    total = len(raw)
    return {"entries": raw[offset:offset + limit], "total": total}

# (compressor argv, file extension, HTTP content-type). "none" is a plain
# tar with no compression step at all - occasionally useful if the vault's
# CPU (only 2 cores on the current test VM) is the bottleneck rather than
# network/disk to whoever's downloading.
_COMPRESSORS = {
    "zstd": (["zstd", "-T0", "-q", "-c"], "tar.zst", "application/zstd"),
    "gzip": (["gzip", "-c"], "tar.gz", "application/gzip"),
    "none": (None, "tar", "application/x-tar"),
}

CHUNK_SIZE = 256 * 1024


def compressor_available(compression: str) -> bool:
    args = _COMPRESSORS[compression][0]
    return args is None or shutil.which(args[0]) is not None


def archive_filename(container: str, snapshot: str, compression: str) -> str:
    ext = _COMPRESSORS[compression][1]
    return f"{container}-{snapshot}.{ext}"


def archive_media_type(compression: str) -> str:
    return _COMPRESSORS[compression][2]


def stream_archive(source_dir: str, compression: str):
    """Streams a tar (optionally piped through a compressor) of source_dir's
    contents without ever materializing the whole archive on disk or in
    memory. `source_dir` is expected to be a read-only ZFS snapshot mount
    (.zfs/snapshot/<name>/ under the dataset's own mountpoint) - snapshots
    are immutable, so this is safe to read from even while a fresh pull is
    concurrently writing to the live dataset next to it."""
    comp_args, _ext, _media_type = _COMPRESSORS[compression]

    tar_proc = subprocess.Popen(
        ["tar", "-C", source_dir, "-cf", "-", "."],
        stdout=subprocess.PIPE,
    )
    if comp_args:
        comp_proc = subprocess.Popen(comp_args, stdin=tar_proc.stdout, stdout=subprocess.PIPE)
        tar_proc.stdout.close()  # tar gets SIGPIPE if comp_proc exits early, instead of hanging
        out = comp_proc.stdout
    else:
        comp_proc = None
        out = tar_proc.stdout

    def generate():
        try:
            while True:
                chunk = out.read(CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk
        finally:
            out.close()
            tar_proc.wait()
            if comp_proc:
                comp_proc.wait()

    return generate()

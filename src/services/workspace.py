"""Request-local, owner-scoped directories for generated research files."""
from contextvars import ContextVar
import hashlib
from pathlib import Path
import re

from src.request_identity import get_current_user_id

_workspace = ContextVar("research_workspace", default=None)
_WORKSPACE_ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")


def set_workspace(value):
    return _workspace.set(workspace_id(value))


def reset_workspace(token):
    _workspace.reset(token)


def current_workspace():
    return _workspace.get()


def workspace_id(value=None):
    value = str(value or current_workspace() or "default")
    if not _WORKSPACE_ID.fullmatch(value):
        raise ValueError("无效的工作区编号")
    return value


def workspace_owner():
    uid = get_current_user_id()
    if uid is None or uid < 0:
        raise PermissionError("请先登录后访问工作区")
    return f"user-{uid}" if uid > 0 else "admin"


def workspace_directory(root, value=None, *, create=False):
    """Never use a client-selected folder or adopt an ownerless legacy file."""
    root = Path(root).absolute()
    digest = hashlib.sha256(workspace_id(value).encode()).hexdigest()[:32]
    candidate = root / workspace_owner() / digest
    if root.is_symlink() or any(p.is_symlink() for p in (candidate.parent, candidate)):
        raise ValueError("无效的工作区目录")
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise ValueError("无效的工作区目录")
    if create:
        candidate.mkdir(mode=0o700, parents=True, exist_ok=True)
    elif get_current_user_id() == 0 and not candidate.exists():
        # Only the operator may read ownerless pre-isolation archives. Never
        # assign these old files to a regular account merely because it knows
        # the former workspace id.
        legacy = root if workspace_id(value) == "default" else root / workspace_id(value)
        if not legacy.is_symlink() and legacy.is_dir() and legacy.resolve().is_relative_to(root.resolve()):
            return legacy
    return candidate


def workspace_file(root, value, filename):
    if (not filename or filename in {".", ".."} or len(filename) > 240
            or any(c in filename for c in ("/", "\\", "\x00"))):
        raise ValueError("无效的文件名")
    path = workspace_directory(root, value) / filename
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError("文件不存在或不属于当前账号")
    return path

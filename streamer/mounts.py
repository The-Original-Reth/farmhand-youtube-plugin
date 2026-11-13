"""
Mounting the NAS share the plugin saves to (SMB/CIFS or NFS) from inside its own container, so where it saves is set on
its settings page (shown on Farmhand's Settings page) instead of on the machine it runs on. A copy of Farmhand's own
backend/app/mounts.py (the same rules and the same plain-words errors), without Farmhand's database: the plugin keeps
one share, its save location (save_location.py).

What this needs from the container (docker-compose.yml gives it): the SYS_ADMIN capability, and for SMB DAC_READ_SEARCH
too; and the client tools, mount.cifs (cifs-utils) and mount.nfs (nfs-common), which the Dockerfile installs.

Everything typed ends up in a command, so every field is checked against a strict pattern first, commands are argument
lists (never a shell string), and an SMB password goes in a temporary 0600 credentials file: never on the command line,
where `ps` would show it.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("stream.mounts")

PROTOCOLS = ("smb", "nfs")
SMB_VERSIONS = ("", "3.1.1", "3.0", "2.1")  # "" = let the kernel negotiate
NFS_VERSIONS = ("4", "3")
MOUNT_TIMEOUT_SECONDS = 30
CAP_SYS_ADMIN_BIT = 21
CAP_DAC_READ_SEARCH_BIT = 2  # mount.cifs refuses to run without it, on top of SYS_ADMIN
_MOUNT_TOOLS = {"smb": "mount.cifs", "nfs": "mount.nfs"}


class MountError(ValueError):
    """Mounting/unmounting failed. The message is written to be shown to the person using the app."""

    def __init__(self, message: str, kind: str = "other"):
        super().__init__(message)
        self.kind = kind  # auth | permission | not_found | unreachable | privilege | unsupported | version | busy | other


@dataclass(frozen=True)
class ShareSpec:
    protocol: str
    server: str
    share: str  # SMB: share[/sub/path]   NFS: the export path, e.g. /volume1/farmhand
    username: str | None = None
    password: str | None = None
    domain: str | None = None
    version: str | None = None


# ------------------------------------------------------------------ validation

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}$")
_HOST = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_DOMAIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SMB_BAD = re.compile(r'[\\:*?"<>|,\x00-\x1f]')
_NFS_PATH = re.compile(r"^/[A-Za-z0-9 ._+@/-]*$")
_CONTROL = re.compile(r"[\x00-\x1f]")


_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*)://(.*)$")
_LOCATION_HELP = (
    "Enter the full address of the folder on your NAS, like //192.168.1.50/farmhand "
    "(SMB) or 192.168.1.50:/volume1/farmhand (NFS)."
)
_NO_LOGIN_IN_ADDRESS = "Leave the username and password out of the address — enter them in the fields below."


def parse_location(text: str | None) -> tuple[str, str, str]:
    r"""
    Split ONE pasted NAS location into (protocol, server, share) so people don't have to.
    Accepts the forms people actually copy:

        //192.168.1.50/farmhand/Projects        SMB, Linux/Mac style
        \\nas\farmhand\Projects                SMB, Windows style
        smb://nas.local/farmhand  (or cifs://)  SMB, as a URL
        192.168.1.50:/volume1/farmhand          NFS, as NAS screens show it
        nfs://nas/volume1/farmhand              NFS, as a URL
        nas/farmhand                            no prefix: assumed SMB

    For SMB the first folder is the share and anything after it is a sub-folder of it.
    Returns the raw pieces; validate_fields() still checks them. Logins aren't accepted in
    the address (they'd end up in places a password shouldn't).
    """
    raw = (text or "").strip().replace("\\", "/")
    if not raw:
        raise ValueError(_LOCATION_HELP)
    protocol, rest = _split_protocol(raw)
    head, _, tail = rest.lstrip("/").partition("/")
    if protocol == "nfs":
        return _nfs_location(head, tail)
    return _smb_location(head, tail)


def _split_protocol(raw: str) -> tuple[str, str]:
    """("smb" or "nfs", the address without its scheme or leading //)."""
    m = _SCHEME.match(raw)
    if m:
        scheme = m.group(1).lower()
        if scheme in ("smb", "cifs"):
            return "smb", m.group(2)
        if scheme == "nfs":
            return "nfs", m.group(2)
        raise ValueError(
            "That doesn't look like a NAS address. Use //server/share (SMB) or server:/export (NFS), "
            "or an smb:// or nfs:// address."
        )
    if raw.startswith("//"):
        return "smb", raw[2:]
    if re.match(r"^[^/]*:", raw):  # "host:/path" — a colon before any slash
        return "nfs", raw
    return "smb", raw


def _nfs_location(head: str, tail: str) -> tuple[str, str, str]:
    server = head[:-1] if head.endswith(":") else head
    path = tail.strip("/")
    if "@" in server:
        raise ValueError(_NO_LOGIN_IN_ADDRESS)
    if not server or not path:
        raise ValueError("Add the export path after the address, like 192.168.1.50:/volume1/farmhand.")
    return "nfs", server, "/" + path


def _smb_location(head: str, tail: str) -> tuple[str, str, str]:
    share = tail.strip("/")
    if "@" in head:
        raise ValueError(_NO_LOGIN_IN_ADDRESS)
    if not head or not share:
        raise ValueError("Add the share name after the address, like //192.168.1.50/farmhand.")
    return "smb", head, share


def validate_fields(
    protocol: str | None,
    server: str | None,
    share: str | None,
    username: str | None = None,
    password: str | None = None,
    domain: str | None = None,
    version: str | None = None,
) -> ShareSpec:
    """Normalise and validate what a person typed. Raises ValueError with a plain-language message."""
    if protocol not in PROTOCOLS:
        raise ValueError("Choose SMB or NFS.")

    server = (server or "").strip()
    if not _HOST.match(server):
        raise ValueError("Enter the NAS's address — its IP (like 192.168.1.50) or its name (like nas.local).")

    share_value = _valid_smb_share(share) if protocol == "smb" else _valid_nfs_export(share)
    username, password, domain = _valid_login(username, password, domain)
    version = _valid_version(protocol, version)

    if protocol == "nfs":
        username = password = domain = None  # NFS has no login of its own
    return ShareSpec(protocol, server, share_value, username, password if username else None, domain, version or None)


def _valid_smb_share(share: str | None) -> str:
    raw_share = (share or "").strip().strip("/")
    segments = raw_share.split("/")
    if not raw_share or any(seg in ("", ".", "..") for seg in segments) or _SMB_BAD.search(raw_share) or len(raw_share) > 200:
        raise ValueError("Enter the share's name as the NAS lists it (like farmhand), optionally followed by a sub-folder.")
    return raw_share


def _valid_nfs_export(share: str | None) -> str:
    norm = "/" + (share or "").strip().strip("/")
    if not _NFS_PATH.match(norm) or ".." in norm.split("/") or len(norm) > 200:
        raise ValueError("Enter the NFS export path as the NAS lists it (like /volume1/farmhand).")
    return norm


def _valid_login(username: str | None, password: str | None, domain: str | None) -> tuple[str | None, str | None, str | None]:
    """The SMB login fields, tidied (blank -> None) and checked."""
    username = (username or "").strip() or None
    if username is not None and (len(username) > 64 or _CONTROL.search(username)):
        raise ValueError("That username isn't valid.")
    if password is not None and (len(password) > 128 or "\n" in password or "\r" in password or "\x00" in password):
        raise ValueError("That password isn't valid (no line breaks).")
    domain = (domain or "").strip() or None
    if domain is not None and not _DOMAIN.match(domain):
        raise ValueError("That domain/workgroup isn't valid.")
    return username, password, domain


def _valid_version(protocol: str, version: str | None) -> str:
    """The protocol version, or "" for SMB's "let the kernel negotiate". NFS defaults to 4."""
    version = (version or "").strip()
    allowed = SMB_VERSIONS if protocol == "smb" else NFS_VERSIONS
    if protocol == "nfs" and not version:
        version = "4"
    if version not in allowed:
        raise ValueError(f"That {protocol.upper()} version isn't supported here.")
    return version


# ------------------------------------------------------------------ environment

def _has_cap(bit: int, status_path: str = "/proc/self/status") -> bool:
    try:
        with open(status_path) as f:
            for line in f:
                if line.startswith("CapEff:"):
                    return bool(int(line.split()[1], 16) & (1 << bit))
    except (OSError, ValueError, IndexError):
        pass
    return False


def _has_cap_sys_admin(status_path: str = "/proc/self/status") -> bool:
    return _has_cap(CAP_SYS_ADMIN_BIT, status_path)


COMPOSE_FIX = (
    "Give the plugin's service in its docker-compose.yml `cap_add: [SYS_ADMIN, DAC_READ_SEARCH]` and "
    "`security_opt: [apparmor:unconfined]`, then run `docker compose up -d` again (see INSTALL.md)."
)


def mount_capability(status_path: str = "/proc/self/status") -> dict:
    """
    What this process can mount, and why not when it can't.

      ok            can mount at least one kind of share
      smb / nfs     can mount that kind
      message       why `ok` is False
      smb_message / nfs_message   why that particular kind is unavailable (None when it's fine)

    SMB needs two permissions — SYS_ADMIN to mount and DAC_READ_SEARCH, which mount.cifs insists
    on keeping while it drops everything else (without it: "Unable to apply new capability set").
    NFS needs only SYS_ADMIN, so it can work while SMB doesn't.
    """
    smb_tool, nfs_tool = (bool(shutil.which(_MOUNT_TOOLS[p])) for p in ("smb", "nfs"))

    if not _has_cap(CAP_SYS_ADMIN_BIT, status_path):
        msg = "The plugin's container isn't allowed to mount network shares. " + COMPOSE_FIX
        return {"ok": False, "smb": False, "nfs": False, "message": msg, "smb_message": msg, "nfs_message": msg}

    smb_message = nfs_message = None
    if not smb_tool:
        smb_message = _not_installed("SMB", "cifs-utils")
    elif not _has_cap(CAP_DAC_READ_SEARCH_BIT, status_path):
        smb_message = "Mounting SMB shares needs one more permission for the container. " + COMPOSE_FIX
    if not nfs_tool:
        nfs_message = _not_installed("NFS", "nfs-common")

    smb, nfs = smb_message is None, nfs_message is None
    message = None if (smb or nfs) else (smb_message if smb_message == nfs_message else f"{smb_message} {nfs_message}")
    return {"ok": smb or nfs, "smb": smb, "nfs": nfs, "message": message, "smb_message": smb_message, "nfs_message": nfs_message}


def _not_installed(protocol: str, package: str) -> str:
    return f"{protocol} support ({package}) isn't installed in the plugin's image — rebuild it with `docker compose up -d --build`."


def is_mounted(path: str | os.PathLike) -> bool:
    try:
        return os.path.ismount(path)
    except OSError:
        return False


# ------------------------------------------------------------------ building the command


def build_mount_command(spec: ShareSpec, mount_point: str | os.PathLike, credentials_file: str | None = None) -> list[str]:
    if spec.protocol == "smb":
        opts = ["uid=0", "gid=0", "file_mode=0664", "dir_mode=0775", "iocharset=utf8"]
        if spec.version:
            opts.append(f"vers={spec.version}")
        opts.append(f"credentials={credentials_file}" if credentials_file else "guest")
        return ["mount", "-t", "cifs", f"//{spec.server}/{spec.share}", str(mount_point), "-o", ",".join(opts)]

    version = spec.version or "4"
    # soft + timeouts: a NAS that goes away yields an error instead of freezing every request that touches it
    opts = [f"vers={version}", "soft", "timeo=100", "retrans=2"]
    if version == "3":
        opts.append("nolock")  # no lock daemon inside the container
    return ["mount", "-t", "nfs", f"{spec.server}:{spec.share}", str(mount_point), "-o", ",".join(opts)]


def _write_credentials(spec: ShareSpec) -> str | None:
    """A 0600 temp file holding the SMB login, or None for guest access. The caller deletes it."""
    if spec.protocol != "smb" or not spec.username:
        return None
    lines = [f"username={spec.username}", f"password={spec.password or ''}"]
    if spec.domain:
        lines.append(f"domain={spec.domain}")
    scratch_dir = "/dev/shm" if os.path.isdir("/dev/shm") and os.access("/dev/shm", os.W_OK) else None
    fd, path = tempfile.mkstemp(prefix="stream-cred-", dir=scratch_dir)  # mkstemp creates it 0600
    try:
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(lines) + "\n")
    except BaseException:
        os.unlink(path)
        raise
    return path


# ------------------------------------------------------------------ running it


def _run(argv: list[str], timeout: int = MOUNT_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    """Indirection so tests can stand in for the real `mount`."""
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def friendly_error(stderr: str, spec: ShareSpec, allowed: bool | None = None) -> MountError:
    """
    Turn mount's raw output into something a person can act on. `allowed`: whether this container may mount at all
    (None: look). When it may, "Operation not permitted" (mount error 1) is the NAS saying no, not the container: a
    wrong login, guest access turned off, or an SMB version it won't use.
    """
    if allowed is None:
        allowed = _has_cap_sys_admin()
    text = (stderr or "").strip()
    if spec.password and len(spec.password) >= 4:
        text = text.replace(spec.password, "•••")
    low = text.lower()

    def says(*phrases: str) -> bool:
        return any(phrase in low for phrase in phrases)

    what = "share" if spec.protocol == "smb" else "export"
    proto = spec.protocol.upper()

    if says("capability set", "unable to apply new capability"):
        return MountError(
            "mount.cifs couldn't adjust its own permissions inside the container — it needs more than the container "
            "was given. " + COMPOSE_FIX,
            "privilege",
        )
    if says("only root", "must be superuser") or (says("operation not permitted", "mount error(1)") and not allowed):
        return MountError("The plugin isn't allowed to mount shares from inside its container. " + COMPOSE_FIX, "privilege")
    if says("operation not permitted", "mount error(1)"):
        if spec.protocol == "nfs":
            return MountError(
                f"{spec.server} refused this computer. On the NAS, allow this machine's IP address to use that export "
                "(and allow root access to it).",
                "permission",
            )
        login = (
            f"that the username ({spec.username}) and password are right and that user may use this share"
            if spec.username
            else "that the NAS lets guests use this share, or give a username and password"
        )
        return MountError(
            f"The NAS turned the connection down (mount error 1). Check {login}; if they are, try another SMB version "
            "(the plugin's container is allowed to mount shares, so this is the NAS saying no).",
            "auth",
        )
    if says("unknown filesystem type", "mount error(19)"):
        return MountError(
            f"This computer's kernel can't mount {proto} shares (the {'cifs' if spec.protocol == 'smb' else 'nfs'} "
            "support is missing).",
            "unsupported",
        )
    if says("name or service not known", "unable to resolve", "could not resolve", "unknown host"):
        return MountError(f"Couldn't find “{spec.server}” on the network. Try its IP address instead of its name.", "unreachable")
    if says("no route to host", "network is unreachable", "connection timed out", "mount error(113)", "mount error(110)",
            "mount error(111)", "connection refused"):
        return MountError(
            f"Couldn't reach {spec.server}. Check the address, that the NAS is switched on, and that it's on the same network.",
            "unreachable",
        )
    if spec.protocol == "nfs" and says("access denied"):
        return MountError(
            f"{spec.server} refused this computer. On the NAS, allow this machine's IP address to use that export "
            "(and allow root access to it).",
            "permission",
        )
    if says("mount error(13)", "permission denied", "access denied", "logon failure"):
        return MountError(
            "The NAS refused the connection: wrong username or password, or that user isn't allowed on this share.", "auth"
        )
    if says("mount error(2)", "no such file or directory", "bad network name", "mount error(6)"):
        return MountError(f"{spec.server} answered, but it has no {what} by that name. Check the {what} name for typos.", "not_found")
    if says("mount error(95)", "protocol not supported", "invalid argument", "mount error(22)"):
        return MountError(
            f"The NAS didn't accept that {proto} version. Try a different version.", "version"
        )
    return MountError(f"Couldn't mount the {what}: {_details(text)}", "other")


def _details(output: str) -> str:
    """The start of a command's own output, for the end of a message."""
    return output[:300] or "no details were given"


def mount(spec: ShareSpec, mount_point: str | os.PathLike) -> None:
    """Mount the share at mount_point (no-op if it's already mounted). Raises MountError."""
    mount_point = Path(mount_point)
    if is_mounted(mount_point):
        return
    _require_mount_capability(spec.protocol)

    mount_point.mkdir(parents=True, exist_ok=True)
    credentials = _write_credentials(spec)
    try:
        _run_mount(spec, build_mount_command(spec, mount_point, credentials))
    finally:
        if credentials:
            Path(credentials).unlink(missing_ok=True)


def _require_mount_capability(protocol: str) -> None:
    """Raise MountError when this container can't mount this kind of share (privilege, or a missing client tool)."""
    cap = mount_capability()
    if not cap["ok"]:
        raise MountError(cap["message"], "privilege")
    if not cap[protocol]:
        tool_present = shutil.which(_MOUNT_TOOLS[protocol])
        raise MountError(cap[f"{protocol}_message"], "privilege" if tool_present else "unsupported")


def _run_mount(spec: ShareSpec, argv: list[str]) -> None:
    try:
        result = _run(argv)
    except subprocess.TimeoutExpired:
        raise MountError(
            f"Timed out waiting for {spec.server}. Check the address and that the NAS is on and reachable.", "unreachable"
        )
    except FileNotFoundError:
        raise MountError("The `mount` command isn't available in this image — rebuild it.", "unsupported")
    if result.returncode != 0:
        # mount() has already checked this container may mount this kind of share
        raise friendly_error(result.stderr or result.stdout, spec, allowed=True)


def unmount(mount_point: str | os.PathLike, lazy: bool = False) -> None:
    """Disconnect a share. Raises MountError("busy") if something is still using it."""
    mount_point = Path(mount_point)
    if not is_mounted(mount_point):
        return
    argv = ["umount", "-l", str(mount_point)] if lazy else ["umount", str(mount_point)]
    try:
        result = _run(argv)
    except subprocess.TimeoutExpired:
        raise MountError("Timed out disconnecting the share. Try again in a moment.", "busy")
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        if "busy" in err.lower():
            raise MountError("The share is busy right now (something is still using it). Try again in a moment.", "busy")
        raise MountError(f"Couldn't disconnect the share: {_details(err)}", "other")

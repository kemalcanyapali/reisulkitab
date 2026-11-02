"""The in-app updater: look for a newer release, download it, swap it in.

Split in three. The network and the disk work run in a plain Python thread —
the same arrangement as the dictation pipeline — and report back over one
queued Qt signal, so the GUI thread only ever sets state. The folder swap runs
in a separate PowerShell helper (assets/apply-update.ps1), because a process
cannot replace the folder its own executable is running from; the helper is
copied out of the installation first, so the folder under it can be renamed.

Trust is anchored in HTTPS to this one GitHub repository: the SHA256SUMS file
guards the download against corruption, not against a hostile release, and
nothing fetched here is executed — only the installed application is started,
after the verified package has replaced the old one.
"""

import hashlib
import http.client
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication

import brand
from process import windowless_options

REPO = "kemalcanyapali/reisulkitab"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
PACKAGE_ASSET = "Reisulkuttab-windows-x64.zip"
SUMS_ASSET = "SHA256SUMS"
TOP_FOLDER = brand.EXECUTABLE                # the one folder the ZIP unpacks to
EXECUTABLE_NAME = brand.EXECUTABLE + ".exe"
INTERNAL_DIR = "_internal"

# One blocked socket call may take this long before the release is called
# unreachable. A stalled download is retried by the user, not by us.
TIMEOUT_S = 30
METADATA_LIMIT = 1 << 20    # the release JSON, asset list included
SUMS_LIMIT = 4096
ZIP_LIMIT = 1 << 30         # the package, compressed and extracted alike
ENTRY_LIMIT = 20000         # a onedir build is hundreds of files, not more

# fullmatch: "v1.2.3\n" and the like are not tags. ASCII digits only, because
# \d accepts Unicode digits that int() and the tag on the server may disagree on.
TAG = re.compile(r"v([0-9]+)\.([0-9]+)\.([0-9]+)")


class UpdaterError(Exception):
    """A failure with text ready to show next to the Update button."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def parse_tag(tag):
    """(major, minor, patch) for a stable "vX.Y.Z" tag, None for the rest."""
    match = TAG.fullmatch(tag or "")
    return tuple(int(part) for part in match.groups()) if match else None


CURRENT = parse_tag("v" + brand.VERSION) or (0, 0, 0)


# --- fetching, with the doors checked before anything is opened ------------


def _allowed_redirect(url):
    """HTTPS, and only GitHub itself or one of its release asset hosts."""
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    return (parts.scheme == "https" and port in (None, 443)
            and parts.username is None and parts.password is None
            and (host == "github.com" or host == "githubusercontent.com"
                 or host.endswith(".githubusercontent.com")))


class _GuardedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _allowed_redirect(newurl):
            raise UpdaterError("the download was redirected to a server "
                               "that is not GitHub; nothing was installed")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _urlopen(req, timeout):
    opener = urllib.request.build_opener(_GuardedRedirect())
    return opener.open(req, timeout=timeout)


def _get(url, limit, accept=None):
    """One GET, body capped at `limit` bytes. No credentials are sent."""
    headers = {"User-Agent": f"{brand.SLUG}-updater/{brand.VERSION}"}
    if accept:
        headers["Accept"] = accept
    req = urllib.request.Request(url, headers=headers)
    try:
        with _urlopen(req, TIMEOUT_S) as resp:
            body = resp.read(limit + 1)
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise UpdaterError(f"GitHub answered {code} for {url}", code) from None
    except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
        raise UpdaterError(f"could not reach {url}: {exc}") from exc
    if len(body) > limit:
        raise UpdaterError(f"GitHub's answer for {url} was larger than expected")
    return body


def fetch_release():
    """The latest release as the updater needs it, or None when GitHub has no
    stable vX.Y.Z one to offer.

    Raises UpdaterError when GitHub is unreachable, or the release does not
    carry its package the way this repository publishes it.
    """
    try:
        release = json.loads(_get(API_URL, METADATA_LIMIT,
                                  accept="application/vnd.github+json"))
    except UpdaterError as exc:
        if exc.code == 404:           # no release has been published yet
            return None
        raise
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        raise UpdaterError("GitHub's answer could not be read") from None
    if not isinstance(release, dict) or release.get("draft") \
            or release.get("prerelease"):
        return None
    tag = str(release.get("tag_name") or "")
    version = parse_tag(tag)
    if version is None:
        return None
    assets = {}
    for asset in release.get("assets") or []:
        if isinstance(asset, dict):
            assets[str(asset.get("name") or "")] = \
                str(asset.get("browser_download_url") or "")
    expected = {name: f"https://github.com/{REPO}/releases/download/{tag}/{name}"
                for name in (PACKAGE_ASSET, SUMS_ASSET)}
    for name, url in expected.items():
        if assets.get(name) != url:
            raise UpdaterError(f"the {tag} release does not carry {name} the "
                               "way this repository publishes it")
    return {"tag": tag,
            "version": ".".join(str(part) for part in version),
            "zip_url": expected[PACKAGE_ASSET], "sums_url": expected[SUMS_ASSET]}


# --- the package, checked before it is trusted -----------------------------


def parse_sums(text):
    """The digest SHA256SUMS gives for the package, None when it does not."""
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == PACKAGE_ASSET \
                and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            return parts[0].lower()
    return None


# Windows redirects these names to devices, or strips the trailing dots and
# spaces, so a member carrying one never unpacks to the path it claims.
_INVALID = '<>:"|?*'


def _unsafe_component(part):
    return (part != part.rstrip(". ")
            or any(ch in part for ch in _INVALID)
            or any(ord(ch) < 0x20 for ch in part)
            or pathlib.PureWindowsPath(part).is_reserved())


def _check_member(name):
    """One archive member: a plain relative path under the one top folder."""
    unsafe = UpdaterError("the update package contains an unsafe path")
    if "\\" in name or name.startswith("/"):
        raise unsafe
    if re.match(r"^[A-Za-z]:", name):
        raise unsafe
    parts = name.split("/")[:-1] if name.endswith("/") else name.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise unsafe
    if any(_unsafe_component(part) for part in parts):
        raise unsafe
    if parts[0] != TOP_FOLDER:
        raise UpdaterError(
            f"the update package must unpack one {TOP_FOLDER} folder")


def validate_archive(zip_path):
    """Say the package is safe to unpack, or raise.

    One top-level folder, plain relative paths under it, no links or special
    files, nothing encrypted or exotically compressed, and a size and file
    count a build of this application could plausibly have.
    """
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise UpdaterError("the update package is not a readable ZIP") from exc
    with zf:
        infos = zf.infolist()
        if len(infos) > ENTRY_LIMIT:
            raise UpdaterError("the update package has too many files")
        seen = set()
        total = 0
        has_exe = has_internal = False
        for info in infos:
            _check_member(info.filename)
            if info.flag_bits & 0x1:
                raise UpdaterError("the update package contains an encrypted "
                                   "file")
            if info.compress_type not in (zipfile.ZIP_STORED,
                                          zipfile.ZIP_DEFLATED):
                raise UpdaterError("the update package uses an unexpected "
                                   "compression")
            kind = (info.external_attr >> 16) & 0o170000
            if kind not in (0, 0o040000, 0o100000) or info.external_attr & 0x448:
                # Reject Windows reparse points, devices and volume labels too.
                raise UpdaterError("the update package contains a link or a "
                                   "special file")
            folded = info.filename.lower()
            if folded in seen:
                raise UpdaterError("the update package contains the same file "
                                   "twice")
            seen.add(folded)
            total += info.file_size
            if total > ZIP_LIMIT:
                raise UpdaterError("the update package expands to more than "
                                   "expected")
            rest = folded[len(TOP_FOLDER) + 1:]
            if folded == f"{TOP_FOLDER.lower()}/{EXECUTABLE_NAME.lower()}" \
                    and not info.is_dir():
                has_exe = True
            if rest == INTERNAL_DIR or rest.startswith(INTERNAL_DIR + "/"):
                has_internal = True
        if not has_exe or not has_internal:
            raise UpdaterError("the update package is incomplete")


def extract_package(zip_path, dest):
    """Unpack a verified package: `dest` receives the one top-level folder."""
    validate_archive(zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest)
    unpacked = dest / TOP_FOLDER
    if not (unpacked / EXECUTABLE_NAME).is_file() \
            or not (unpacked / INTERNAL_DIR).is_dir():
        raise UpdaterError("the update package is incomplete")


def download_release(release, staging):
    """Fetch both assets into `staging`, check the package against its digest
    and unpack it to `staging`/TOP_FOLDER.

    Nothing is extracted unless the archive arrived whole, as published.
    """
    sums = parse_sums(_get(release["sums_url"], SUMS_LIMIT)
                      .decode("utf-8", "replace"))
    if sums is None:
        raise UpdaterError("SHA256SUMS does not list the update package")
    zip_path = staging / PACKAGE_ASSET
    digest = hashlib.sha256()
    req = urllib.request.Request(
        release["zip_url"],
        headers={"User-Agent": f"{brand.SLUG}-updater/{brand.VERSION}"})
    try:
        with _urlopen(req, TIMEOUT_S) as resp, open(zip_path, "wb") as out:
            written = 0
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                written += len(chunk)
                if written > ZIP_LIMIT:
                    raise UpdaterError("the update package is larger than "
                                       "expected")
                digest.update(chunk)
                out.write(chunk)
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise UpdaterError(
            f"GitHub answered {code} for {release['zip_url']}", code) from None
    except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
        raise UpdaterError(f"could not download the update: {exc}") from exc
    if digest.hexdigest() != sums:
        raise UpdaterError("the downloaded update failed its checksum")
    extract_package(zip_path, staging)


# --- where an update may be applied ----------------------------------------


def install_dir():
    """The standard installation this build runs from, or None.

    Only a frozen Windows build running from %LOCALAPPDATA%\\Programs\\
    reisulkuttab may be replaced: a source checkout, or a copy someone put
    somewhere else, is not ours to touch — and neither is a folder that is a
    junction to somewhere else.
    """
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return None
    exe = pathlib.Path(sys.executable)
    if exe.name.lower() != EXECUTABLE_NAME.lower():
        return None
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    expected = pathlib.Path(local) / "Programs" / brand.SLUG
    if os.path.normcase(str(exe.parent)) != os.path.normcase(str(expected)):
        return None
    install = exe.parent
    # Match the helper: neither the installation nor its staging parent may redirect.
    for path in (install, install.parent):
        if os.path.normcase(os.path.realpath(str(path))) != os.path.normcase(
                os.path.join(os.path.realpath(str(path.parent)), path.name)):
            return None
    return install


def staging_root(install):
    """Scratch space of our own beside the installation: outside it, but on
    the same drive, so the folder swap is one atomic rename."""
    return install.parent / f"{brand.SLUG}.update-{os.getpid()}"


def powershell_path():
    """Windows PowerShell, which every supported installation has."""
    candidate = os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"), "System32",
        "WindowsPowerShell", "v1.0", "powershell.exe")
    if os.path.isfile(candidate):
        return candidate
    return shutil.which("powershell.exe") or ""


# --- the engine -------------------------------------------------------------


class Updater(QObject):
    """The engine behind "check for updates" and "update now".

    The state is ours and only ever set on the GUI thread: `state` (idle,
    checking, current, available, downloading, installing, error),
    `latest_version`, `message`, and the properties `supported` and
    `can_update`. Anything that would block happens in a worker thread and
    lands back over `_result`, a queued signal that is the only thing the
    worker touches. Nothing starts by itself.
    """

    changed = pyqtSignal()
    _result = pyqtSignal(dict)

    def __init__(self, can_install, parent=None):
        super().__init__(parent)
        self.can_install = can_install
        self.state = "idle"
        self.latest_version = ""
        self.message = ""
        self._release = None      # the metadata of the release on offer
        self._staged_tag = None   # its tag, once downloaded and verified
        self._staging = None      # and where it is waiting
        self._thread = None
        self._result.connect(self._on_result,
                             Qt.ConnectionType.QueuedConnection)

    # ---- what the UI reads -------------------------------------------------

    @property
    def supported(self):
        """Whether this build is the standard Windows installation."""
        return install_dir() is not None

    @property
    def can_update(self):
        """Whether clicking Update could go ahead right now: an update on
        offer, an installation we may replace, and nothing running."""
        return self.state == "available" and self.supported and not self._busy()

    def _busy(self):
        """What the owner of the application reports: recording, a pipeline or
        the file transcriber mid-job. A callable that fails counts as busy
        rather than taking the UI down with it."""
        try:
            return not self.can_install()
        except Exception:
            traceback.print_exc()
            return True

    # ---- what the UI calls -------------------------------------------------

    def check(self):
        """Look for a newer release. Returns at once."""
        if self.state in ("checking", "downloading", "installing"):
            return
        self._start("checking", "Looking for updates...", "check",
                    self._run_check)

    def install(self):
        """Download the release on offer and hand the application over to the
        helper that swaps the folders. Returns at once."""
        if self.state != "available":
            return
        if not self.supported:
            self._set("error", "This copy was not installed by the Windows "
                               "installer, so it cannot update itself.")
            return
        if self._busy():
            # Stay available: the button means it again as soon as the job
            # ends, and the release stays on offer.
            self._set("available",
                      "The application is busy recording or transcribing; "
                      "wait for it to finish and click Update again.")
            return
        # Already downloaded and verified: straight to the swap, which asks
        # one last time whether the application is idle.
        if (self._staged_tag == self._release.get("tag")
                and (pathlib.Path(self._staging or "") / TOP_FOLDER
                     / EXECUTABLE_NAME).is_file()):
            self._apply()
            return
        self._start("downloading",
                    f"Downloading version {self._release.get('version', '')}...",
                    "download", self._run_download)

    # ---- the worker thread, and what comes back ----------------------------

    def _start(self, state, message, kind, task):
        self._set(state, message)
        self._thread = threading.Thread(target=self._run, args=(kind, task),
                                        daemon=True)
        self._thread.start()

    def _run(self, kind, task):
        """The worker thread: one task, one result, no state touched. The kind
        rides on failure too, or a lost task would leave the state behind."""
        try:
            payload = task() or {}
        except UpdaterError as exc:
            payload = {"ok": False, "error": str(exc)}
        except Exception as exc:    # the UI must hear a failure, not see one
            traceback.print_exc()
            payload = {"ok": False,
                       "error": f"the updater failed unexpectedly: {exc}"}
        payload.setdefault("ok", True)
        payload["kind"] = kind
        self._result.emit(payload)

    def _run_check(self):
        release = fetch_release()
        newer = bool(release) and parse_tag("v" + release["version"]) > CURRENT
        return {"release": release, "newer": newer}

    def _run_download(self):
        install = install_dir()
        if install is None:
            raise UpdaterError("this copy was not installed by the Windows "
                               "installer")
        staging = staging_root(install)
        # A failed earlier try leaves its scratch behind; start clean.
        shutil.rmtree(staging, ignore_errors=True)
        try:
            staging.mkdir(parents=True)
        except OSError as exc:
            raise UpdaterError(f"could not prepare {staging}: {exc}") from exc
        download_release(self._release, staging)
        return {"staging": str(staging)}

    def _on_result(self, payload):
        """The one place the worker lands back on the GUI thread."""
        if self.state == "installing":
            return
        if payload.get("kind") == "check":
            self._finish_check(payload)
        elif payload.get("kind") == "download":
            self._finish_download(payload)

    def _finish_check(self, payload):
        if not payload["ok"]:
            self._set("error", payload["error"])
            return
        release = payload["release"]
        self._release = release
        if release and self._staged_tag != release["tag"]:
            self._staged_tag = None
            self._staging = None
        if payload["newer"]:
            self.latest_version = release["version"]
            note = ("." if self.supported else
                    ", but this copy was not installed by the Windows "
                    "installer, so it cannot update itself")
            self._set("available", f"Version {release['version']} is "
                                   f"available{note}")
        else:
            self.latest_version = release["version"] if release else ""
            self._set("current", "You are up to date.")

    def _finish_download(self, payload):
        if not payload["ok"]:
            self._set("error", f"{payload['error']} Click Check for updates "
                               "and try again.")
            return
        self._staged_tag = self._release["tag"]
        self._staging = payload["staging"]
        self._apply()

    def _apply(self):
        """The last safe moment before the swap: ask one last time whether the
        application is idle, then hand over to the helper and quit."""
        if self._busy():
            self._set("available",
                      "The update is downloaded, but the application is busy; "
                      "wait for it to finish and click Update again.")
            return
        self._set("installing",
                  f"Installing version {self.latest_version}; the application "
                  "will restart.")
        try:
            self._spawn_helper()
        except UpdaterError as exc:
            self._set("error", str(exc))
            return
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _spawn_helper(self):
        """Copy the bundled helper out of the installation and start it."""
        install = install_dir()
        if install is None:
            raise UpdaterError("this copy was not installed by the Windows "
                               "installer")
        staging = pathlib.Path(self._staging)
        if not (staging / TOP_FOLDER / EXECUTABLE_NAME).is_file():
            raise UpdaterError("the staged update package is incomplete")
        # An old backup in the way would only surface after the application
        # had quit, with the helper able to neither swap nor roll forward.
        # It is the user's to move aside, never ours to delete.
        backup = install.parent / f"{brand.SLUG}.previous"
        if backup.exists():
            raise UpdaterError(
                f"an earlier update left its backup at {backup}. Move that "
                "folder out of the way once you no longer need it, then "
                "update again")
        powershell = powershell_path()
        if not powershell:
            raise UpdaterError("Windows PowerShell was not found, so the "
                               "update cannot be applied")
        held = None
        try:
            held = tempfile.mkdtemp(prefix=f"{brand.SLUG}-helper-")
            helper = pathlib.Path(held) / "apply-update.ps1"
            shutil.copyfile(brand.asset("apply-update.ps1"), helper)
        except OSError as exc:
            if held:
                shutil.rmtree(held, ignore_errors=True)
            raise UpdaterError(f"could not prepare the update helper: {exc}") \
                from exc
        # No env= override: the helper and the app it restarts must see the
        # environment this process got — the IPC server name is derived from
        # it, and the helper probes that server to see the new version is up.
        argv = [powershell, "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-File", str(helper),
                "-AppPid", str(os.getpid()),
                "-StagedDir", str(staging), "-InstallDir", str(install),
                "-Version", self.latest_version]
        try:
            subprocess.Popen(argv, **windowless_options(),
                             stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True)
        except OSError as exc:
            shutil.rmtree(held, ignore_errors=True)
            raise UpdaterError(f"the update helper could not be started: "
                               f"{exc}") from exc

    def _set(self, state, message):
        self.state = state
        self.message = message
        self.changed.emit()

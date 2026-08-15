"""Clipboard and key injection, through whatever this machine gives us.

A Wayland session has wl-clipboard and ydotool, an X11 one has xclip and
xdotool, and macOS has pbcopy with the key press going straight to
CoreGraphics. Which of them is here gets decided in one place, and each is a
small group of functions below it: another desktop, or another operating
system, adds a group and a line to the chooser rather than a branch inside
every function here.
"""

import collections
import ctypes
import functools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

from i18n import t

# Linux input event codes (linux/input-event-codes.h), which is what ydotool
# takes. They are also the list of keys a paste shortcut may be built from, so
# xdotool is held to the same table rather than being handed the text as typed.
KEYCODES = {
    "ctrl": 29, "control": 29, "shift": 42, "alt": 56, "super": 125, "meta": 125,
    "v": 47, "insert": 110, "enter": 28, "return": 28,
}

# xdotool speaks X keysyms, which spell some of those differently.
KEYSYMS = {"control": "ctrl", "meta": "super", "insert": "Insert",
           "enter": "Return", "return": "Return"}

# Apple virtual key codes, which say where a key sits rather than what is
# printed on it: the same numbers on a Turkish and a US keyboard.
MAC_KEYCODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7,
    "c": 8, "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15,
    "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22,
    "5": 23, "=": 24, "9": 25, "7": 26, "-": 27, "8": 28, "0": 29,
    "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35, "l": 37,
    "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44,
    "n": 45, "m": 46, ".": 47, "`": 50, "enter": 36, "return": 36,
}
MAC_FLAGS = {"shift": 1 << 17, "ctrl": 1 << 18, "alt": 1 << 19, "command": 1 << 20}
# What the same modifier is called on a Mac keyboard.
MAC_ALIASES = {"cmd": "command", "meta": "command", "super": "command",
               "control": "ctrl", "option": "alt"}
HID_EVENT_TAP = 0   # kCGHIDEventTap: the event goes in where the keyboard does


# pbpaste only reads text, EPS and RTF.  In particular, an image on a Mac's
# clipboard comes back as an empty byte string and pbcopy then replaces it with
# empty plain text.  Keep every NSPasteboard representation in short-lived
# files instead.  The manifest stays small even when the clipboard holds a
# large TIFF, and no additional Python package is needed.
_MAC_SNAPSHOT = collections.namedtuple("MacClipboardSnapshot", "directory manifest")

_MAC_SNAPSHOT_SCRIPT = r'''
ObjC.import("AppKit");
const root = ObjC.unwrap(
  $.NSProcessInfo.processInfo.environment.objectForKey("REISULKUTTAB_PASTEBOARD_DIR")
);
const pasteboard = $.NSPasteboard.generalPasteboard;
const items = pasteboard.pasteboardItems;
const result = [];
for (let i = 0; i < items.count; i++) {
  const item = items.objectAtIndex(i);
  const representations = [];
  const types = item.types;
  for (let j = 0; j < types.count; j++) {
    const type = ObjC.unwrap(types.objectAtIndex(j));
    const data = item.dataForType(type);
    if (!data) continue;
    const file = `${i}-${j}.bin`;
    if (data.writeToFileAtomically(`${root}/${file}`, true)) {
      representations.push({type, file});
    }
  }
  result.push(representations);
}
JSON.stringify(result);
'''

_MAC_RESTORE_SCRIPT = r'''
ObjC.import("AppKit");
const root = ObjC.unwrap(
  $.NSProcessInfo.processInfo.environment.objectForKey("REISULKUTTAB_PASTEBOARD_DIR")
);
const input = $.NSFileHandle.fileHandleWithStandardInput.readDataToEndOfFile;
const source = $.NSString.alloc.initWithDataEncoding(input, $.NSUTF8StringEncoding);
const rows = JSON.parse(ObjC.unwrap(source));
const items = [];
for (const representations of rows) {
  const item = $.NSPasteboardItem.alloc.init;
  for (const representation of representations) {
    const data = $.NSData.dataWithContentsOfFile(
      `${root}/${representation.file}`
    );
    if (data) item.setDataForType(data, representation.type);
  }
  items.push(item);
}
const pasteboard = $.NSPasteboard.generalPasteboard;
pasteboard.clearContents;
pasteboard.writeObjects($(items));
'''


class PasteError(Exception):
    pass


# --- the key press, one group per system ----------------------------------

def _keys(shortcut):
    """'Ctrl+V' -> ['ctrl', 'v'], every one of them a key we know."""
    parts = [key.strip().lower() for key in str(shortcut).split("+") if key.strip()]
    for key in parts:
        if key not in KEYCODES:
            raise PasteError(t("Unknown key: {key}", key=key))
    return parts


def _ydotool_command(shortcut):
    """ydotool wants a press event per key, then a release in reverse."""
    codes = [KEYCODES[key] for key in _keys(shortcut)]
    return ["ydotool", "key", *[f"{code}:1" for code in codes],
            *[f"{code}:0" for code in reversed(codes)]]


def _xdotool_command(shortcut):
    """xdotool takes the whole combination as one argument."""
    keys = [KEYSYMS.get(key, key) for key in _keys(shortcut)]
    return ["xdotool", "key", "--clearmodifiers", "+".join(keys)]


def _program_keyboard(program, command, hint=""):
    """A desktop that presses keys by running another program.

    Returns the three fields an entry below is built from: the program's name,
    whether it is here at all, and the press itself.
    """

    def ready():
        return shutil.which(program) is not None

    def press(shortcut, delay):
        if not ready():
            raise PasteError(t("{tool} not found, cannot paste automatically.",
                               tool=program))
        argv = command(shortcut)
        time.sleep(delay)  # let the selection settle and focus come back
        try:
            res = subprocess.run(argv, capture_output=True, text=True, timeout=10)
        except (subprocess.SubprocessError, OSError) as exc:
            raise PasteError(t("Could not run {tool}: {error}",
                               tool=program, error=exc)) from exc
        if res.returncode != 0:
            message = t("{tool} failed: {error}", tool=program,
                        error=res.stderr.strip() or "unknown error")
            raise PasteError(f"{message}\n{t(hint)}" if hint else message)

    return {"keyboard": program, "ready": ready, "press": press}


def _macos_keys(shortcut):
    """'Cmd+V' -> (9, 0x100000): where the key sits, and the modifiers on it."""
    parts = [key.strip().lower() for key in str(shortcut).split("+") if key.strip()]
    parts = [MAC_ALIASES.get(part, part) for part in parts]
    if not parts or parts[-1] not in MAC_KEYCODES:
        raise PasteError(t("Unknown key: {key}", key=parts[-1] if parts else shortcut))
    flags = 0
    for part in parts[:-1]:
        if part not in MAC_FLAGS:
            raise PasteError(t("Unknown key: {key}", key=part))
        flags |= MAC_FLAGS[part]
    return MAC_KEYCODES[parts[-1]], flags


@functools.lru_cache(maxsize=1)
def _macos_api():
    """The bit of CoreGraphics and Accessibility a paste goes through.

    Loaded on the first paste rather than at import: this module is read on
    every system, and these two frameworks exist on one of them.
    """
    try:
        services = ctypes.CDLL(
            "/System/Library/Frameworks/ApplicationServices.framework"
            "/ApplicationServices"
        )
        core = ctypes.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
    except OSError as exc:
        raise PasteError(t("Could not run {tool}: {error}",
                           tool="CoreGraphics", error=exc)) from exc
    services.AXIsProcessTrusted.argtypes = []
    services.AXIsProcessTrusted.restype = ctypes.c_bool
    services.CGEventCreateKeyboardEvent.argtypes = [
        ctypes.c_void_p, ctypes.c_ushort, ctypes.c_bool,
    ]
    services.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
    services.CGEventSetFlags.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
    services.CGEventSetFlags.restype = None
    services.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    services.CGEventPost.restype = None
    core.CFRelease.argtypes = [ctypes.c_void_p]
    core.CFRelease.restype = None
    return services, core


def _macos_trusted():
    """Whether macOS lets this process type into another application."""
    try:
        return bool(_macos_api()[0].AXIsProcessTrusted())
    except PasteError:
        return False


_asked_for_permission = False


def _ask_for_permission():
    """Open the one settings pane that grants it, and only the first time.

    Every dictation would otherwise reopen it until the box is ticked, which is
    a window in the user's face on top of the paste that did not happen.
    """
    global _asked_for_permission
    if _asked_for_permission:
        return
    _asked_for_permission = True
    try:
        subprocess.Popen(
            ["open", ("x-apple.systempreferences:com.apple.preference.security"
                      "?Privacy_Accessibility")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
        )
    except OSError:
        pass


def _macos_press(shortcut, delay):
    """Post the key down and up straight into the window system.

    Nothing is typed anywhere until macOS has been told to trust Reisülküttab, and it
    only asks once, when the paste it was granted for is first tried.
    """
    keycode, flags = _macos_keys(shortcut)
    services, core = _macos_api()
    if not _macos_trusted():
        _ask_for_permission()
        raise PasteError(t(
            "macOS has not been told to let Reisülküttab press keys. Turn Reisülküttab on "
            "under System Settings → Privacy & Security → Accessibility."
        ))

    time.sleep(delay)  # let the selection settle and focus come back
    down = services.CGEventCreateKeyboardEvent(None, keycode, True)
    up = services.CGEventCreateKeyboardEvent(None, keycode, False)
    if not down or not up:
        for event in (down, up):
            if event:
                core.CFRelease(event)
        raise PasteError(t("Could not run {tool}: {error}", tool="CoreGraphics",
                           error="it would not make a keyboard event"))
    try:
        for event in (down, up):
            services.CGEventSetFlags(event, flags)
            services.CGEventPost(HID_EVENT_TAP, event)
            time.sleep(0.01)
    finally:
        core.CFRelease(down)
        core.CFRelease(up)


# --- Windows clipboard and key injection ----------------------------------

class _WIN_SNAPSHOT:
    """A retained OLE IDataObject, including every medium and native format."""

    __slots__ = ("data_object", "_ole32")

    def __init__(self, data_object, ole32):
        self.data_object = data_object
        self._ole32 = ole32

    def close(self):
        data_object = self.data_object
        if data_object is None:
            return
        self.data_object = None
        try:
            _release_windows_data_object(data_object)
        finally:
            self._ole32.OleUninitialize()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass




_WIN_VK = {
    "ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12,
    "super": 0x5B, "meta": 0x5B, "win": 0x5B,
    "v": 0x56, "insert": 0x2D, "enter": 0x0D, "return": 0x0D,
}


def _release_windows_data_object(data_object):
    """Release the IDataObject pointer returned by OleGetClipboard."""
    vtable = ctypes.cast(
        data_object, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
    ).contents
    release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])
    release(data_object)




def _open_windows_clipboard():
    """Open the clipboard with a real window so EmptyClipboard has an owner."""
    user32 = ctypes.windll.user32
    user32.CreateWindowExW.argtypes = [
        ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    ]
    user32.CreateWindowExW.restype = ctypes.c_void_p
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.restype = ctypes.c_bool
    user32.DestroyWindow.argtypes = [ctypes.c_void_p]
    user32.DestroyWindow.restype = ctypes.c_bool
    owner = user32.CreateWindowExW(
        0, "STATIC", "Reisülküttab clipboard owner", 0, 0, 0, 0, 0,
        ctypes.c_void_p(-3), None, None, None,  # HWND_MESSAGE
    )
    if not owner:
        return None
    for _ in range(20):
        if user32.OpenClipboard(owner):
            return owner
        time.sleep(0.01)
    user32.DestroyWindow(owner)
    return None


def _windows_snapshot():
    """Retain the clipboard's IDataObject without narrowing its data formats."""
    ole32 = ctypes.windll.ole32
    ole32.OleInitialize.argtypes = [ctypes.c_void_p]
    ole32.OleInitialize.restype = ctypes.c_int32
    ole32.OleGetClipboard.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
    ole32.OleGetClipboard.restype = ctypes.c_int32
    ole32.OleUninitialize.argtypes = []
    ole32.OleUninitialize.restype = None
    if ole32.OleInitialize(None) < 0:
        return None
    data_object = ctypes.c_void_p()
    if ole32.OleGetClipboard(ctypes.byref(data_object)) < 0 or not data_object:
        ole32.OleUninitialize()
        return None
    return _WIN_SNAPSHOT(data_object, ole32)


def _windows_restore(snapshot, expected_sequence=None):
    ole32 = snapshot._ole32
    ole32.OleSetClipboard.argtypes = [ctypes.c_void_p]
    ole32.OleSetClipboard.restype = ctypes.c_int32
    ole32.OleFlushClipboard.argtypes = []
    ole32.OleFlushClipboard.restype = ctypes.c_int32
    try:
        if snapshot.data_object is None:
            return
        for attempt in range(20):
            if (expected_sequence is not None
                    and clipboard_sequence() != expected_sequence):
                return
            if ole32.OleSetClipboard(snapshot.data_object) >= 0:
                break
            if attempt < 19:
                time.sleep(0.01)
        else:
            raise PasteError(t("Could not restore the Windows clipboard."))
        if ole32.OleFlushClipboard() < 0:
            raise PasteError(t("Could not preserve the Windows clipboard."))
    finally:
        snapshot.close()


def _windows_set_formats(formats):
    owner = _open_windows_clipboard()
    if not owner:
        raise PasteError(t("Could not open the Windows clipboard."))
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.restype = ctypes.c_bool
    installed = False
    emptied = False
    try:
        if not user32.EmptyClipboard():
            raise PasteError(t("Could not clear the Windows clipboard."))
        emptied = True
        for clipboard_format, data in formats:
            handle = kernel32.GlobalAlloc(0x0042, len(data))
            if not handle:
                continue
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                kernel32.GlobalFree(handle)
                continue
            ctypes.memmove(pointer, data, len(data))
            kernel32.GlobalUnlock(handle)
            if user32.SetClipboardData(clipboard_format, handle):
                installed = True
            else:
                kernel32.GlobalFree(handle)
        if not installed:
            raise PasteError(t("Could not write to the Windows clipboard."))
    finally:
        user32.CloseClipboard()
        user32.DestroyWindow(owner)


def _windows_copy(text):
    data = text.encode("utf-16-le") + b"\0\0"
    _windows_set_formats([(13, data)])  # CF_UNICODETEXT

def _windows_press(shortcut, delay):
    parts = [part.strip().lower() for part in shortcut.split("+") if part.strip()]
    try:
        keys = [_WIN_VK[part] for part in parts]
    except KeyError as exc:
        raise PasteError(t("Could not parse the shortcut: {shortcut}",
                           shortcut=shortcut)) from exc
    user32 = ctypes.windll.user32
    time.sleep(delay)
    for key in keys:
        user32.keybd_event(key, 0, 0, 0)
    for key in reversed(keys):
        user32.keybd_event(key, 0, 0x0002, 0)


# --- which of them is here -------------------------------------------------

Desktop = collections.namedtuple(
    "Desktop",
    # The clipboard program and the two commands it is run with, what to
    # install when it is missing, the paste combinations Settings offers, and
    # the key press: the program that does it, whether it can happen at all,
    # and the pressing itself.
    "clipboard packages read_command copy_command shortcuts keyboard ready press",
)

WAYLAND = Desktop(
    clipboard="wl-copy",
    packages="wl-clipboard and ydotool",
    read_command=["wl-paste", "--no-newline"],
    copy_command=["wl-copy"],
    shortcuts=["ctrl+v", "ctrl+shift+v", "shift+insert"],
    **_program_keyboard(
        "ydotool", _ydotool_command,
        hint="Is ydotoold running? (systemctl --user status ydotool)",
    ),
)

X11 = Desktop(
    clipboard="xclip",
    packages="xclip and xdotool",
    read_command=["xclip", "-selection", "clipboard", "-out"],
    copy_command=["xclip", "-selection", "clipboard", "-in"],
    shortcuts=["ctrl+v", "ctrl+shift+v", "shift+insert"],
    **_program_keyboard("xdotool", _xdotool_command),
)

MACOS = Desktop(
    clipboard="pbcopy",
    packages="",   # both are part of macOS; there is nothing to install
    read_command=["pbpaste"],
    copy_command=["pbcopy"],
    shortcuts=["cmd+v", "cmd+shift+v", "cmd+alt+shift+v"],
    keyboard="",   # no program: the key press is a call into the system
    ready=_macos_trusted,
    press=_macos_press,
)

WINDOWS = Desktop(
    clipboard="",
    packages="",
    read_command=[],
    copy_command=[],
    shortcuts=["ctrl+v", "ctrl+shift+v", "shift+insert"],
    keyboard="",
    ready=lambda: True,
    press=_windows_press,
)


def clipboard_sequence():
    """Windows clipboard generation, or None where no reliable guard exists."""
    if sys.platform != "win32":
        return None
    user32 = ctypes.windll.user32
    user32.GetClipboardSequenceNumber.argtypes = []
    user32.GetClipboardSequenceNumber.restype = ctypes.c_uint32
    return user32.GetClipboardSequenceNumber()


def desktop():
    """The programs this session's clipboard and key press go through.

    Read every time rather than settled at import: a session started before the
    display server was up would otherwise be stuck with the wrong answer, and a
    test would have nowhere to say which one it means.
    """
    if sys.platform == "win32":
        return WINDOWS
    if sys.platform == "darwin":
        return MACOS
    if os.environ.get("XDG_SESSION_TYPE") == "x11":
        return X11
    if os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        return X11
    return WAYLAND


# --- the clipboard ---------------------------------------------------------

def _macos_snapshot():
    """Copy every native pasteboard type to a temporary, file-backed snapshot."""
    directory = tempfile.mkdtemp(prefix="reisulkuttab-clipboard-")
    environment = dict(os.environ, REISULKUTTAB_PASTEBOARD_DIR=directory)
    try:
        result = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", _MAC_SNAPSHOT_SCRIPT],
            capture_output=True, text=True, timeout=15, env=environment,
        )
        manifest = result.stdout.strip()
        rows = json.loads(manifest) if result.returncode == 0 else None
        if not isinstance(rows, list):
            raise ValueError("the pasteboard helper returned no manifest")
        return _MAC_SNAPSHOT(directory, manifest)
    except (json.JSONDecodeError, OSError, subprocess.SubprocessError, ValueError):
        shutil.rmtree(directory, ignore_errors=True)
        return None


def _macos_restore(snapshot):
    """Put a native snapshot back, then discard its short-lived files."""
    environment = dict(os.environ, REISULKUTTAB_PASTEBOARD_DIR=snapshot.directory)
    try:
        subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", _MAC_RESTORE_SCRIPT],
            input=snapshot.manifest, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, text=True, timeout=15, env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        pass
    finally:
        shutil.rmtree(snapshot.directory, ignore_errors=True)

def read_clipboard():
    if sys.platform == "win32":
        return _windows_snapshot()
    here = desktop()
    if here is MACOS and shutil.which("osascript"):
        snapshot = _macos_snapshot()
        if snapshot is not None:
            return snapshot
    if not shutil.which(here.read_command[0]):
        return None
    try:
        res = subprocess.run(here.read_command, capture_output=True, timeout=5)
    except (subprocess.SubprocessError, OSError):
        return None
    return res.stdout if res.returncode == 0 else None
def require_clipboard():
    """Snapshot the current clipboard or refuse to overwrite what we cannot restore."""
    snapshot = read_clipboard()
    if snapshot is None:
        raise PasteError(t("Could not preserve the current clipboard."))
    return snapshot




def _run_copy(payload):
    """The clipboard owner forks to keep holding the selection; leaving its
    pipes open makes subprocess.run wait for EOF forever, hence DEVNULL."""
    return subprocess.run(
        desktop().copy_command,
        input=payload,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
    )


def copy(text):
    if sys.platform == "win32":
        _windows_copy(text)
        return
    here = desktop()
    if not shutil.which(here.clipboard):
        raise PasteError(
            t("{tool} not found. Install {packages}.",
              tool=here.clipboard, packages=here.packages) if here.packages
            else t("{tool} not found.", tool=here.clipboard)
        )
    try:
        res = _run_copy(text.encode("utf-8"))
    except (subprocess.SubprocessError, OSError) as exc:
        raise PasteError(t("Could not copy to clipboard: {error}", error=exc)) from exc
    if res.returncode != 0:
        raise PasteError(t("{tool} exited with code {code}.",
                           tool=here.clipboard, code=res.returncode))


def copy_bytes(data, expected_sequence=None):
    if isinstance(data, _WIN_SNAPSHOT):
        _windows_restore(data, expected_sequence)
        return
    if isinstance(data, _MAC_SNAPSHOT):
        _macos_restore(data)
        return
    if data is None or not shutil.which(desktop().clipboard):
        return
    try:
        result = _run_copy(data)
    except (subprocess.SubprocessError, OSError) as exc:
        raise PasteError(
            t("Could not restore the clipboard: {error}", error=exc)) from exc
    if result.returncode:
        raise PasteError(t("Could not restore the clipboard."))

# --- the key press ---------------------------------------------------------

def paste_ready():
    """Whether a paste can be sent: the program is here, or macOS trusts us."""
    return desktop().ready()


def press(shortcut="", delay=0.12):
    """Press a paste combination, e.g. 'ctrl+v', or this desktop's own."""
    here = desktop()
    here.press(shortcut or here.shortcuts[0], delay)

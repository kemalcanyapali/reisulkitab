"""The updater engine: which release, which doors, whose say-so.

The network is faked at updater._urlopen — every request is recorded and
answered from the test — so nothing here reaches GitHub. The threads are
faked where the machinery under test is the state machine; the two failures
that must reach the GUI go the real way instead, through a queued Qt signal
and a turn of the event loop. The helper script and a real end-to-end
install are exercised by the parent's smoke, not here.
"""

import hashlib
import io
import json
import os
import pathlib
import unittest
import urllib.error
import zipfile
from unittest import mock

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QApplication

import updater
from tests.support import AppTest

ZIP_NAME = "Reisulkuttab-windows-x64.zip"
GOOD_TAG = "v9.9.9"
_app = QApplication.instance() or QApplication([])


def body(data):
    """A response whose read() hands its body over and then ends."""
    resp = mock.MagicMock()
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False

    def read(size=-1):
        nonlocal data
        if size is None or size < 0:
            size = len(data)
        chunk, data = data[:size], data[size:]
        return chunk

    resp.read.side_effect = read
    return resp


def json_reply(payload):
    return body(json.dumps(payload).encode("utf-8"))


def refuse(code, url="https://api.github.com/x"):
    return urllib.error.HTTPError(url, code, "boom", {}, io.BytesIO(b"{}"))


def release_json(tag=GOOD_TAG, zip_url=None, sums_url=None, **extra):
    """A /releases/latest answer, publishing the way this repository does."""
    def url(name):
        return (f"https://github.com/kemalcanyapali/reisulkitab/releases/"
                f"download/{tag}/{name}")

    payload = {
        "tag_name": tag, "draft": False, "prerelease": False,
        "assets": [
            {"name": ZIP_NAME,
             "browser_download_url": zip_url or url(ZIP_NAME)},
            {"name": "SHA256SUMS",
             "browser_download_url": sums_url or url("SHA256SUMS")},
        ],
    }
    payload.update(extra)
    return payload


def a_release(tag=GOOD_TAG):
    """The dict fetch_release() hands the engine, tag and all."""
    def url(name):
        return (f"https://github.com/kemalcanyapali/reisulkitab/releases/"
                f"download/{tag}/{name}")

    return {"tag": tag, "version": tag[1:],
            "zip_url": url(ZIP_NAME), "sums_url": url("SHA256SUMS")}


def build_zip(path, entries=None, attrs=None):
    """A plausible package: the one folder, the executable, _internal."""
    entries = entries if entries is not None else {
        "Reisulkuttab/Reisulkuttab.exe": b"MZ the application",
        "Reisulkuttab/_internal/base_library.zip": b"payload",
    }
    attrs = attrs or {}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = attrs.get(name, 0o100644 << 16)
            zf.writestr(info, data)
    return path


def build_raw_zip(path, members):
    """A package with the archive fields set by hand: (name, data, attr,
    create_system), for the tricks a hostile archive plays."""
    with zipfile.ZipFile(path, "w") as zf:
        for name, data, attr, system in members:
            info = zipfile.ZipInfo(name)
            info.external_attr = attr
            info.create_system = system
            zf.writestr(info, data)
    return path


class UpdaterTest(AppTest):
    """A test where the updater cannot reach the network or start a thread."""

    def setUp(self):
        super().setUp()

        def no_network(req, timeout=None):
            raise AssertionError(
                "a test reached the network; patch updater._urlopen")

        self.patch_attr(updater, "_urlopen", no_network)
        self.threads = []
        started = self.threads

        class FakeThread:
            def __init__(self, target=None, args=(), daemon=None):
                self.target, self.args = target, args
                started.append(self)

            def start(self):
                pass

        self.patch_attr(updater.threading, "Thread", FakeThread)

    def serve(self, *responses):
        """Answer each request from the list (the last repeats); recorded."""
        calls = []

        def open_(req, timeout=None):
            calls.append(req)
            reply = responses[min(len(calls) - 1, len(responses) - 1)]
            if isinstance(reply, Exception):
                raise reply
            return reply

        patcher = mock.patch.object(updater, "_urlopen", side_effect=open_)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def engine(self, can_install=None):
        up = updater.Updater(
            can_install if can_install is not None else (lambda: True))
        return up

    def offer(self, up, tag=GOOD_TAG, supported=True):
        """Drive one finished check that found `tag` newer, and mean it."""
        self.patch_attr(updater, "install_dir",
                        (lambda: pathlib.Path(self.root) / "here")
                        if supported else (lambda: None))
        up._on_result({"kind": "check", "ok": True,
                       "release": a_release(tag), "newer": True})

    def stage(self, up, tag=GOOD_TAG):
        """The state a finished download leaves behind."""
        root = self.path("staging")
        (root / "Reisulkuttab").mkdir(parents=True)
        (root / "Reisulkuttab" / "Reisulkuttab.exe").write_bytes(b"MZ")
        up._release = a_release(tag)
        up._staged_tag = tag
        up._staging = str(root)
        return root


class Tags(unittest.TestCase):
    def test_a_stable_tag_parses_into_comparable_numbers(self):
        self.assertEqual(updater.parse_tag("v1.2.3"), (1, 2, 3))

    def test_anything_else_is_not_a_stable_tag(self):
        for tag in ("1.2.3", "v1.2", "v1.2.3-rc1", "v1.2.3.4", "latest", "",
                    "v1.2.3\n", "v１.2.3", None):
            with self.subTest(tag=tag):
                self.assertIsNone(updater.parse_tag(tag))


class Redirects(unittest.TestCase):
    def allowed(self, url):
        return updater._allowed_redirect(url)

    def test_github_and_its_asset_hosts_on_the_https_port(self):
        for url in ("https://github.com/kemalcanyapali/reisulkitab",
                    "https://github.com:443/x",
                    "https://objects.githubusercontent.com/release-assets",
                    "https://release-assets.githubusercontent.com/x",
                    "https://githubusercontent.com/a"):
            with self.subTest(url=url):
                self.assertTrue(self.allowed(url))

    def test_everything_else_is_shut(self):
        for url in ("http://github.com/x",           # plaintext
                    "https://github.com:8443/x",     # a port other than 443
                    "https://evil.com/github",       # not GitHub
                    "https://github.com.evil.com",   # GitHub as a suffix
                    "https://evil-githubusercontent.com",
                    "https://githubusercontent.com.evil.com",
                    "https://objects.githubusercontent.com.evil.com",
                    "https://user:pw@github.com",    # credentials in it
                    "not a url at all"):
            with self.subTest(url=url):
                self.assertFalse(self.allowed(url))

    def test_a_bad_port_is_not_looked_at(self):
        self.assertFalse(self.allowed("https://github.com:notaport/x"))


class FetchRelease(UpdaterTest):
    def test_the_latest_release_comes_back_as_the_updater_needs_it(self):
        calls = self.serve(json_reply(release_json("v1.2.0")))
        release = updater.fetch_release()
        self.assertEqual(release["version"], "1.2.0")
        self.assertEqual(
            release["zip_url"],
            "https://github.com/kemalcanyapali/reisulkitab/releases/"
            "download/v1.2.0/Reisulkuttab-windows-x64.zip")
        self.assertEqual(calls[0].full_url, updater.API_URL)
        self.assertIsNone(calls[0].get_header("Authorization"))

    def test_no_release_published_yet_is_no_error(self):
        self.serve(refuse(404))
        self.assertIsNone(updater.fetch_release())

    def test_github_being_unhappy_is_an_error(self):
        for code in (403, 500):
            with self.subTest(code=code):
                self.serve(refuse(code))
                with self.assertRaises(updater.UpdaterError) as raised:
                    updater.fetch_release()
                self.assertIn(str(code), str(raised.exception))

    def test_no_route_to_github_is_an_error(self):
        self.serve(urllib.error.URLError("no route to host"))
        with self.assertRaises(updater.UpdaterError):
            updater.fetch_release()

    def test_an_answer_that_is_not_json_is_an_error(self):
        self.serve(body(b"gateway maintenance"))
        with self.assertRaises(updater.UpdaterError):
            updater.fetch_release()

    def test_an_answer_too_big_to_be_the_api_is_an_error(self):
        self.serve(body(b"x" * (updater.METADATA_LIMIT + 1)))
        with self.assertRaises(updater.UpdaterError):
            updater.fetch_release()

    def test_a_prerelease_or_draft_is_offered_to_nobody(self):
        for extra in ({"prerelease": True}, {"draft": True}):
            with self.subTest(**extra):
                self.serve(json_reply(release_json(**extra)))
                self.assertIsNone(updater.fetch_release())

    def test_a_tag_that_is_not_a_stable_version_is_offered_to_nobody(self):
        self.serve(json_reply(release_json("1.2.0")))
        self.assertIsNone(updater.fetch_release())

    def test_an_asset_from_another_repository_is_refused(self):
        hijacked = ("https://github.com/evil/repo/releases/download/"
                    f"v1.2.0/{ZIP_NAME}")
        self.serve(json_reply(release_json(zip_url=hijacked)))
        with self.assertRaises(updater.UpdaterError):
            updater.fetch_release()

    def test_a_release_without_its_sums_is_refused(self):
        payload = release_json()
        payload["assets"] = [payload["assets"][0]]
        self.serve(json_reply(payload))
        with self.assertRaises(updater.UpdaterError):
            updater.fetch_release()


class Sums(unittest.TestCase):
    def test_the_standard_line_names_the_digest_for_our_package(self):
        digest = "a" * 64
        self.assertEqual(updater.parse_sums(f"{digest}  {ZIP_NAME}\n"),
                         digest)

    def test_the_binary_marker_is_tolerated(self):
        digest = "b" * 64
        self.assertEqual(updater.parse_sums(f"{digest} *{ZIP_NAME}"), digest)

    def test_our_package_may_not_be_the_only_line(self):
        digest = "c" * 64
        text = (f"{'d' * 64}  other-package.zip\n"
                f"{digest}  {ZIP_NAME}\n"
                f"{'e' * 64}  third.zip\n")
        self.assertEqual(updater.parse_sums(text), digest)

    def test_no_line_for_our_package_no_digest(self):
        for text in ("f" * 64 + "  other.zip\n",
                     "short  " + ZIP_NAME + "\n",
                     "not a sums file"):
            with self.subTest(text=text[:30]):
                self.assertIsNone(updater.parse_sums(text))

    def test_uppercase_hex_is_folded_not_rejected(self):
        digest = "ABCDEF0123456789" * 4
        self.assertEqual(
            updater.parse_sums(f"{digest}  {ZIP_NAME}"), digest.lower())


class Archive(UpdaterTest):
    def test_a_plain_package_passes_and_unpacks_to_one_folder(self):
        zip_path = build_zip(self.path("package.zip"))
        updater.validate_archive(zip_path)
        dest = self.path("unpacked")
        updater.extract_package(zip_path, dest)
        self.assertTrue(
            (dest / "Reisulkuttab" / "Reisulkuttab.exe").is_file())

    def test_a_package_without_the_executable_is_incomplete(self):
        zip_path = build_zip(self.path("x.zip"), entries={
            "Reisulkuttab/_internal/base_library.zip": b"payload"})
        with self.assertRaises(updater.UpdaterError):
            updater.validate_archive(zip_path)

    def test_a_package_without_internal_is_incomplete(self):
        zip_path = build_zip(self.path("x.zip"), entries={
            "Reisulkuttab/Reisulkuttab.exe": b"MZ"})
        with self.assertRaises(updater.UpdaterError):
            updater.validate_archive(zip_path)

    def test_internal_must_be_the_directory_tree_not_a_lookalike(self):
        zip_path = build_zip(self.path("x.zip"), entries={
            "Reisulkuttab/Reisulkuttab.exe": b"MZ",
            "Reisulkuttab/_internal_evil/x": b"x"})
        with self.assertRaises(updater.UpdaterError):
            updater.validate_archive(zip_path)

    def test_internal_as_a_file_does_not_unpack_as_internal(self):
        zip_path = build_zip(self.path("x.zip"), entries={
            "Reisulkuttab/Reisulkuttab.exe": b"MZ",
            "Reisulkuttab/_internal": b"not a directory"})
        updater.validate_archive(zip_path)      # the archive itself is plain
        with self.assertRaises(updater.UpdaterError):
            updater.extract_package(zip_path, self.path("out"))

    def test_paths_that_escape_the_package_are_refused(self):
        for name in ("Reisulkuttab/../evil.txt",
                     "Reisulkuttab/..\\evil.txt",
                     "C:/evil.txt",
                     "/evil.txt",
                     "Reisulkuttab//evil.txt",
                     "Reisulkuttab/./evil.txt",
                     "Other/Reisulkuttab.exe"):
            with self.subTest(name=name):
                zip_path = build_zip(self.path("x.zip"), entries={
                    "Reisulkuttab/Reisulkuttab.exe": b"MZ",
                    "Reisulkuttab/_internal/x": b"x",
                    name: b"evil"})
                with self.assertRaises(updater.UpdaterError):
                    updater.validate_archive(zip_path)

    def test_names_windows_would_redirect_or_mangle_are_refused(self):
        for name in ("Reisulkuttab/CON", "Reisulkuttab/con.txt",
                     "Reisulkuttab/NUL.dat", "Reisulkuttab/com1",
                     "Reisulkuttab/lpt9.log", "Reisulkuttab/file:stream",
                     "Reisulkuttab/trailing.", "Reisulkuttab/trailing ",
                     "Reisulkuttab/bad<name"):
            with self.subTest(name=name):
                zip_path = build_zip(self.path("x.zip"), entries={
                    "Reisulkuttab/Reisulkuttab.exe": b"MZ",
                    "Reisulkuttab/_internal/x": b"x",
                    name: b"evil"})
                with self.assertRaises(updater.UpdaterError):
                    updater.validate_archive(zip_path)

    def test_a_link_inside_the_package_is_refused(self):
        zip_path = build_zip(self.path("x.zip"), entries={
            "Reisulkuttab/Reisulkuttab.exe": b"MZ",
            "Reisulkuttab/_internal/x": b"x",
            "Reisulkuttab/link": b"target"},
            attrs={"Reisulkuttab/link": 0o120777 << 16})
        with self.assertRaises(updater.UpdaterError):
            updater.validate_archive(zip_path)

    def test_a_fat_device_or_volume_bit_is_refused_as_a_reparse(self):
        exe = ("Reisulkuttab/Reisulkuttab.exe", b"MZ",
               (0o100644 << 16) | 0x40, 0)
        internal = ("Reisulkuttab/_internal/x", b"x", 0o100644 << 16, 0)
        zip_path = build_raw_zip(self.path("x.zip"), [exe, internal])
        with self.assertRaises(updater.UpdaterError):
            updater.validate_archive(zip_path)

    def test_the_same_file_twice_under_different_case_is_refused(self):
        zip_path = build_zip(self.path("x.zip"), entries={
            "Reisulkuttab/Reisulkuttab.exe": b"MZ",
            "Reisulkuttab/_internal/X.pyd": b"x",
            "Reisulkuttab/_internal/x.pyd": b"x"})
        with self.assertRaises(updater.UpdaterError):
            updater.validate_archive(zip_path)

    def test_a_package_bigger_than_a_build_could_be_is_refused(self):
        zip_path = build_zip(self.path("x.zip"))
        with mock.patch.object(updater, "ZIP_LIMIT", 8):
            with self.assertRaises(updater.UpdaterError):
                updater.validate_archive(zip_path)

    def test_a_package_with_more_files_than_a_build_could_have(self):
        zip_path = build_zip(self.path("x.zip"), entries={
            "Reisulkuttab/Reisulkuttab.exe": b"MZ",
            "Reisulkuttab/_internal/a": b"x",
            "Reisulkuttab/_internal/b": b"x"})
        with mock.patch.object(updater, "ENTRY_LIMIT", 2):
            with self.assertRaises(updater.UpdaterError):
                updater.validate_archive(zip_path)

    def test_something_that_is_not_a_zip_is_refused(self):
        self.path("not.zip").write_bytes(b"definitely not a zip")
        with self.assertRaises(updater.UpdaterError):
            updater.validate_archive(self.path("not.zip"))


class Download(UpdaterTest):
    def sums(self, zip_bytes, name=ZIP_NAME):
        return f"{hashlib.sha256(zip_bytes).hexdigest()}  {name}\n"

    def test_the_package_is_downloaded_checked_and_unpacked(self):
        zip_path = build_zip(self.path("pkg.zip"))
        zip_bytes = zip_path.read_bytes()
        calls = self.serve(body(self.sums(zip_bytes).encode()),
                           body(zip_bytes))
        staging = self.path("staging")
        staging.mkdir()
        updater.download_release(a_release(), staging)
        self.assertTrue(
            (staging / "Reisulkuttab" / "Reisulkuttab.exe").is_file())
        self.assertEqual(calls[1].full_url, a_release()["zip_url"])

    def test_a_corrupted_package_is_never_unpacked(self):
        zip_path = build_zip(self.path("pkg.zip"))
        self.serve(body(f"{'0' * 64}  {ZIP_NAME}\n".encode()),
                   body(zip_path.read_bytes()))
        staging = self.path("staging")
        staging.mkdir()
        with self.assertRaises(updater.UpdaterError):
            updater.download_release(a_release(), staging)
        self.assertFalse((staging / "Reisulkuttab").exists())

    def test_sums_that_do_not_mention_the_package_are_refused(self):
        calls = self.serve(body(b"deadbeef  other.zip\n"))
        staging = self.path("staging")
        staging.mkdir()
        with self.assertRaises(updater.UpdaterError):
            updater.download_release(a_release(), staging)
        self.assertEqual(len(calls), 1)   # the package was never asked for

    def test_a_download_that_outgrows_the_limit_is_cut_off(self):
        self.serve(body(b"pad"), body(b"x" * 100000))
        staging = self.path("staging")
        staging.mkdir()
        with mock.patch.object(updater, "ZIP_LIMIT", 1000):
            with self.assertRaises(updater.UpdaterError):
                updater.download_release(a_release(), staging)

    def test_github_refusing_the_package_is_an_error(self):
        self.serve(body(b"irrelevant sums"), refuse(403))
        staging = self.path("staging")
        staging.mkdir()
        with self.assertRaises(updater.UpdaterError):
            updater.download_release(a_release(), staging)


class Engine(UpdaterTest):
    def quit_patch(self):
        app = mock.Mock()
        patcher = mock.patch.object(updater.QApplication, "instance",
                                    return_value=app)
        patcher.start()
        self.addCleanup(patcher.stop)
        return app

    def test_a_fresh_updater_is_idle_and_offers_nothing(self):
        up = self.engine()
        self.assertEqual(up.state, "idle")
        self.assertFalse(up.can_update)
        self.assertEqual(up.message, "")

    def test_checking_is_visible_and_starts_the_worker(self):
        up = self.engine()
        up.check()
        self.assertEqual(up.state, "checking")
        self.assertEqual(len(self.threads), 1)
        self.assertIs(self.threads[0].target.__self__, up)

    def test_a_second_check_while_busy_is_ignored(self):
        up = self.engine()
        up.check()
        up.check()
        self.assertEqual(len(self.threads), 1)

    def test_a_newer_release_is_available(self):
        up = self.engine()
        self.offer(up, "v9.0.0")
        self.assertEqual(up.state, "available")
        self.assertEqual(up.latest_version, "9.0.0")
        self.assertTrue(up.can_update)

    def test_an_unsupported_copy_sees_the_release_but_cannot_take_it(self):
        up = self.engine()
        self.offer(up, "v9.0.0", supported=False)
        self.assertEqual(up.state, "available")
        self.assertFalse(up.can_update)
        self.assertIn("not installed by the Windows installer", up.message)

    def test_no_newer_release_means_up_to_date(self):
        up = self.engine()
        for tag in ("1.0.0", "1.1.0"):      # older, then exactly this one
            with mock.patch.object(updater, "install_dir",
                                   return_value=pathlib.Path(self.root)):
                up._on_result({"kind": "check", "ok": True,
                               "release": a_release(f"v{tag}"),
                               "newer": False})
            self.assertEqual(up.state, "current")
        self.assertFalse(up.can_update)

    def test_no_release_at_all_is_up_to_date_not_a_crash(self):
        up = self.engine()
        up._on_result({"kind": "check", "ok": True,
                       "release": None, "newer": False})
        self.assertEqual(up.state, "current")
        self.assertEqual(up.latest_version, "")
        self.assertIn("up to date", up.message)

    def test_a_failed_check_is_an_error_with_something_to_say(self):
        up = self.engine()
        up._on_result({"kind": "check", "ok": False,
                       "error": "could not reach GitHub"})
        self.assertEqual(up.state, "error")
        self.assertIn("could not reach GitHub", up.message)

    def test_a_fetch_failure_reaches_the_error_state_through_the_queue(self):
        app = QCoreApplication.instance()
        up = self.engine()
        up.check()
        self.assertEqual(up.state, "checking")
        with mock.patch.object(updater, "fetch_release",
                               side_effect=updater.UpdaterError(
                                   "could not reach GitHub")):
            up._run("check", up._run_check)     # what the worker thread runs
        self.assertEqual(up.state, "checking")   # nothing yet: it is queued
        app.processEvents()
        self.assertEqual(up.state, "error")
        self.assertIn("could not reach GitHub", up.message)

    def test_a_download_failure_reaches_retry_advice_through_the_queue(self):
        app = QCoreApplication.instance()
        up = self.engine()
        self.offer(up)
        up.install()
        self.assertEqual(up.state, "downloading")
        install = self.path("Programs", "reisulkuttab")
        install.mkdir(parents=True)
        with mock.patch.object(updater, "install_dir", return_value=install), \
                mock.patch.object(updater, "download_release",
                                  side_effect=updater.UpdaterError(
                                      "the downloaded update failed its "
                                      "checksum")):
            up._run("download", up._run_download)
        app.processEvents()
        self.assertEqual(up.state, "error")
        self.assertIn("Check for updates", up.message)

    def test_install_is_ignored_when_nothing_is_on_offer(self):
        up = self.engine()
        up.install()
        self.assertEqual(up.state, "idle")
        self.assertEqual(self.threads, [])

    def test_install_refuses_an_unsupported_copy(self):
        up = self.engine()
        self.offer(up, supported=False)
        up.install()
        self.assertEqual(up.state, "error")
        self.assertIn("not installed by the Windows installer", up.message)
        self.assertEqual(self.threads, [])

    def test_install_waits_when_busy_instead_of_erroring_out(self):
        up = self.engine(can_install=lambda: False)
        self.offer(up)
        up.install()
        self.assertEqual(up.state, "available")
        self.assertIn("busy", up.message)
        self.assertFalse(up.can_update)
        self.assertEqual(self.threads, [])

    def test_an_owner_that_cannot_answer_counts_as_busy(self):
        def broken():
            raise RuntimeError("settings window is gone")

        up = self.engine(can_install=broken)
        self.offer(up)
        up.install()
        self.assertEqual(up.state, "available")
        self.assertEqual(self.threads, [])

    def test_install_downloads_and_then_installs(self):
        up = self.engine()
        self.offer(up)
        up.install()
        self.assertEqual(up.state, "downloading")
        app = self.quit_patch()
        with mock.patch.object(updater.Updater, "_spawn_helper") as spawn:
            up._on_result({"kind": "download", "ok": True,
                           "staging": str(self.path("staging"))})
        spawn.assert_called_once_with()
        self.assertEqual(up.state, "installing")
        app.quit.assert_called_once_with()

    def test_busy_before_the_apply_is_the_owners_call_not_a_quit(self):
        idle = {"now": True}
        up = self.engine(can_install=lambda: idle["now"])
        self.offer(up)
        up.install()                       # idle at the start: download
        idle["now"] = False                # a job started during it
        app = self.quit_patch()
        root = self.stage(up)
        with mock.patch.object(updater.Updater, "_spawn_helper") as spawn:
            up._on_result({"kind": "download", "ok": True,
                           "staging": str(root)})
        spawn.assert_not_called()
        app.quit.assert_not_called()
        self.assertEqual(up.state, "available")
        self.assertIn("busy", up.message)
        # The download is kept, so the retry goes straight to the swap.
        idle["now"] = True
        with mock.patch.object(updater.Updater, "_spawn_helper") as spawn:
            up.install()
        spawn.assert_called_once_with()

    def test_a_failed_download_is_an_error_that_points_at_retry(self):
        up = self.engine()
        self.offer(up)
        up.install()
        up._on_result({"kind": "download", "ok": False,
                       "error": "the downloaded update failed its checksum"})
        self.assertEqual(up.state, "error")
        self.assertIn("Check for updates", up.message)

    def test_a_spawn_failure_leaves_the_application_running(self):
        up = self.engine()
        self.offer(up)
        app = self.quit_patch()
        with mock.patch.object(
                updater.Updater, "_spawn_helper",
                side_effect=updater.UpdaterError("helper went missing")):
            up._on_result({"kind": "download", "ok": True,
                           "staging": str(self.path("staging"))})
        self.assertEqual(up.state, "error")
        self.assertIn("helper went missing", up.message)
        app.quit.assert_not_called()

    def test_a_result_that_lands_after_the_handover_is_ignored(self):
        up = self.engine()
        self.offer(up)
        up.state = "installing"
        up._on_result({"kind": "download", "ok": False, "error": "late"})
        self.assertEqual(up.state, "installing")

    def test_the_download_prepares_its_own_scratch_beside_the_install(self):
        up = self.engine()
        up._release = a_release()
        install = self.path("Programs", "reisulkuttab")
        install.mkdir(parents=True)
        stale = updater.staging_root(install) / "junk"
        stale.mkdir(parents=True)
        with mock.patch.object(updater, "install_dir", return_value=install), \
                mock.patch.object(updater, "download_release"):
            payload = up._run_download()
        self.assertTrue(pathlib.Path(payload["staging"]).is_dir())
        self.assertFalse(stale.exists())    # the failed try went with it


class SpawnHelper(UpdaterTest):
    def ready(self):
        up = self.engine()
        self.stage(up)
        install = self.path("Programs", "reisulkuttab")
        install.mkdir(parents=True)
        self.patch_attr(updater, "install_dir", lambda: install)
        self.patch_attr(updater, "powershell_path", lambda: "powershell.exe")
        return up, install

    def popen(self):
        return self.enterContext(mock.patch.object(updater.subprocess, "Popen"))

    def test_a_backup_left_behind_blocks_the_install_before_the_quit(self):
        up, install = self.ready()
        backup = install.parent / "reisulkuttab.previous"
        backup.mkdir()
        popen = self.popen()
        with self.assertRaises(updater.UpdaterError) as raised:
            up._spawn_helper()
        self.assertIn("reisulkuttab.previous", str(raised.exception))
        self.assertTrue(backup.is_dir())    # never ours to delete
        popen.assert_not_called()

    def test_an_incomplete_staged_package_never_starts_a_helper(self):
        up, _ = self.ready()
        (pathlib.Path(up._staging) / "Reisulkuttab" /
         "Reisulkuttab.exe").unlink()
        popen = self.popen()
        with self.assertRaises(updater.UpdaterError):
            up._spawn_helper()
        popen.assert_not_called()

    def test_no_powershell_no_helper(self):
        up, _ = self.ready()
        self.patch_attr(updater, "powershell_path", lambda: "")
        popen = self.popen()
        with self.assertRaises(updater.UpdaterError):
            up._spawn_helper()
        popen.assert_not_called()

    def test_a_helper_that_cannot_start_is_an_error(self):
        up, _ = self.ready()
        patcher = mock.patch.object(
            updater.subprocess, "Popen",
            side_effect=OSError("the pipe is gone"))
        patcher.start()
        self.addCleanup(patcher.stop)
        with self.assertRaises(updater.UpdaterError):
            up._spawn_helper()


class InstallDir(UpdaterTest):
    def frozen_at(self, exe):
        for patcher in (
                mock.patch.object(updater.sys, "platform", "win32"),
                mock.patch.object(updater.sys, "frozen", True, create=True),
                mock.patch.object(updater.sys, "executable", str(exe))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_this_test_run_is_not_an_installation(self):
        self.assertIsNone(updater.install_dir())

    def test_the_installed_windows_build_is_recognised(self):
        local = pathlib.Path(os.environ["LOCALAPPDATA"])
        exe = local / "Programs" / "reisulkuttab" / "Reisulkuttab.exe"
        self.frozen_at(exe)
        self.assertEqual(updater.install_dir(), exe.parent)

    def test_an_installation_that_is_a_junction_is_refused(self):
        local = pathlib.Path(os.environ["LOCALAPPDATA"])
        exe = local / "Programs" / "reisulkuttab" / "Reisulkuttab.exe"
        self.frozen_at(exe)
        elsewhere = str(exe.parent) + "-elsewhere"

        def redirects_everything(path):
            return elsewhere

        with mock.patch.object(updater.os.path, "realpath",
                               side_effect=redirects_everything):
            self.assertIsNone(updater.install_dir())

    def test_a_copy_elsewhere_is_not_ours_to_replace(self):
        local = pathlib.Path(os.environ["LOCALAPPDATA"])
        self.frozen_at(local / "Programs" / "elsewhere" / "Reisulkuttab.exe")
        self.assertIsNone(updater.install_dir())

    def test_a_renamed_executable_is_not_ours_to_replace(self):
        local = pathlib.Path(os.environ["LOCALAPPDATA"])
        self.frozen_at(local / "Programs" / "reisulkuttab" / "clone.exe")
        self.assertIsNone(updater.install_dir())

    def test_a_non_windows_build_never_reports_an_installation(self):
        with mock.patch.object(updater.sys, "platform", "linux"):
            self.assertIsNone(updater.install_dir())


if __name__ == "__main__":
    unittest.main()

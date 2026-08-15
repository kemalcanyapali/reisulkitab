"""The request a terminal sends to the running instance, and the reply it reads.

The wire format has to stay backwards compatible in both directions: a stale KDE
shortcut still sends a bare verb, and an instance from before replies existed
answers by saying nothing at all.
"""

import json
import os
import sys
import unittest
from unittest import mock

import reisulkuttab
import ipc


class FakeSocket:
    """QLocalSocket, as much of it as ipc.send() touches."""

    def __init__(self, connected=True, reply=b""):
        self.connected = connected
        self.reply = reply
        self.written = b""
        self.server = ""
        self.disconnected = False
        self.read_limits = []
        self._served = False

    def connectToServer(self, name):
        self.server = name

    def waitForConnected(self, ms):
        return self.connected

    def write(self, data):
        self.written += bytes(data)

    def flush(self):
        pass

    def waitForBytesWritten(self, ms):
        return True

    def waitForReadyRead(self, ms):
        self.read_limits.append(ms)
        if self._served or not self.reply:
            return False
        self._served = True
        return True

    def readAll(self):
        return self.reply

    def disconnectFromServer(self):
        self.disconnected = True


class FakeServer:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = 0

    def listen(self, _name):
        self.calls += 1
        return self.results.pop(0)


class FakeLock:
    def __init__(self, claimed=True):
        self.claimed = claimed
        self.unlocked = False

    def tryLock(self, _timeout):
        return self.claimed

    def unlock(self):
        self.unlocked = True

class ServerOwnership(unittest.TestCase):
    def test_first_listener_owns_the_name(self):
        server = FakeServer(True)
        lock = FakeLock()
        probe = FakeSocket(connected=False)
        with mock.patch.object(reisulkuttab, "QLocalSocket", return_value=probe), \
                mock.patch.object(reisulkuttab.QLocalServer, "removeServer") as remove:
            self.assertEqual(reisulkuttab._claim_server(server, lock), "listening")
        remove.assert_called_once_with(ipc.SERVER_NAME)
        self.assertFalse(lock.unlocked)

    def test_process_lock_rejects_a_second_new_instance(self):
        server = FakeServer(True)
        lock = FakeLock(claimed=False)
        with mock.patch.object(reisulkuttab, "QLocalSocket") as socket_type, \
                mock.patch.object(reisulkuttab.QLocalServer, "removeServer") as remove:
            self.assertEqual(reisulkuttab._claim_server(server, lock), "running")
        socket_type.assert_not_called()
        remove.assert_not_called()
        self.assertEqual(server.calls, 0)

    def test_live_older_listener_is_not_removed(self):
        server = FakeServer(True)
        lock = FakeLock()
        probe = FakeSocket(connected=True)
        with mock.patch.object(reisulkuttab, "QLocalSocket", return_value=probe), \
                mock.patch.object(reisulkuttab.QLocalServer, "removeServer") as remove:
            self.assertEqual(reisulkuttab._claim_server(server, lock), "running")
        remove.assert_not_called()
        self.assertEqual(server.calls, 0)
        self.assertTrue(lock.unlocked)

    def test_stale_name_is_removed_before_listening(self):
        server = FakeServer(True)
        lock = FakeLock()
        probe = FakeSocket(connected=False)
        with mock.patch.object(reisulkuttab, "QLocalSocket", return_value=probe), \
                mock.patch.object(reisulkuttab.QLocalServer, "removeServer") as remove:
            self.assertEqual(reisulkuttab._claim_server(server, lock), "listening")
        remove.assert_called_once_with(ipc.SERVER_NAME)
        self.assertEqual(server.calls, 1)

class RestartOwnership(unittest.TestCase):
    def test_windows_restart_releases_then_launches_replacement(self):
        controller = object.__new__(reisulkuttab.Reisulkuttab)
        controller.app = mock.Mock()
        controller.settings_window = None
        controller.shutdown = mock.Mock()
        controller._release_instance = mock.Mock()

        def start_detached(*_args):
            self.assertFalse(controller.shutdown.called)
            self.assertFalse(controller._release_instance.called)
            return True, 9876

        with mock.patch.object(reisulkuttab.sys, "frozen", True, create=True), \
                mock.patch.object(reisulkuttab.os, "name", "nt"), \
                mock.patch.object(reisulkuttab.os, "getpid", return_value=4321), \
                mock.patch.object(
                    reisulkuttab.QProcess, "startDetached",
                    side_effect=start_detached) as start_detached:
            controller.restart()
        controller.shutdown.assert_called_once_with()
        controller._release_instance.assert_called_once_with()
        powershell, arguments = start_detached.call_args.args
        self.assertTrue(powershell.endswith("powershell.exe"))
        self.assertIn("Wait-Process -Id 4321", arguments[-1])
        self.assertIn(
            "PYINSTALLER_RESET_ENVIRONMENT='1'", arguments[-1])
        self.assertIn(reisulkuttab.sys.executable, arguments[-1])
        controller.app.quit.assert_called_once_with()


class RequestFraming(unittest.TestCase):
    def test_split_json_waits_for_the_newline(self):
        buffer = bytearray(b'{"cmd":"tog')
        self.assertIsNone(reisulkuttab._take_request(buffer))
        buffer.extend(b'gle"}\n')
        self.assertEqual(
            reisulkuttab._parse_request(reisulkuttab._take_request(buffer)),
            {"cmd": "toggle"},
        )

    def test_legacy_request_is_dispatched_on_disconnect(self):
        buffer = bytearray(b"restart")
        self.assertIsNone(reisulkuttab._take_request(buffer))
        self.assertEqual(reisulkuttab._take_request(buffer, final=True), b"restart")

    def test_oversized_request_is_rejected(self):
        with self.assertRaises(OverflowError):
            reisulkuttab._take_request(bytearray(reisulkuttab.MAX_IPC_REQUEST + 1))

class Paths(unittest.TestCase):
    def test_script_path_points_at_reisulkuttab(self):
        self.assertTrue(ipc.script_path().endswith("reisulkuttab.py"))
        self.assertTrue(os.path.exists(ipc.script_path()))

    def test_the_shortcut_command_runs_it_with_this_interpreter(self):
        command = ipc.command_for("toggle")
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(command[-1], "toggle")

    @unittest.skipUnless(hasattr(os, "getuid"),
                         "the socket is named after a user id, which Windows "
                         "has no equivalent of")
    def test_the_socket_is_per_user(self):
        self.assertEqual(ipc.SERVER_NAME, f"reisulkuttab-{os.getuid()}")


class Send(unittest.TestCase):
    def send(self, socket, *args, **kwargs):
        with mock.patch.object(ipc, "QLocalSocket", return_value=socket):
            return ipc.send(*args, **kwargs)

    def written_line(self, socket):
        return socket.written.decode("utf-8").strip()

    def test_nothing_running(self):
        sock = FakeSocket(connected=False)
        self.assertIsNone(self.send(sock, "toggle"))
        self.assertEqual(sock.written, b"")

    def test_a_verb_on_its_own_goes_as_the_bare_word(self):
        """An older instance only understands this, and it is how updates land."""
        sock = FakeSocket(reply=b'{"ok": true}\n')
        self.send(sock, "restart")
        self.assertEqual(self.written_line(sock), "restart")

    def test_a_verb_with_arguments_goes_as_json(self):
        sock = FakeSocket(reply=b'{"ok": true}\n')
        self.send(sock, "ask", text="what time is it")
        self.assertEqual(json.loads(self.written_line(sock)),
                         {"cmd": "ask", "text": "what time is it"})

    def test_arguments_that_are_none_are_left_out(self):
        sock = FakeSocket(reply=b'{"ok": true}\n')
        self.send(sock, "record", seconds=None, paste=False)
        self.assertEqual(json.loads(self.written_line(sock)),
                         {"cmd": "record", "paste": False})

    def test_asking_to_be_waited_for_says_so(self):
        sock = FakeSocket(reply=b'{"ok": true, "text": "hello"}\n')
        reply = self.send(sock, "toggle", wait=True)
        self.assertTrue(json.loads(self.written_line(sock))["wait"])
        self.assertEqual(reply["text"], "hello")

    def test_a_wait_with_no_timeout_reads_without_a_deadline(self):
        sock = FakeSocket(reply=b'{"ok": true}\n')
        self.send(sock, "toggle", wait=True)
        self.assertEqual(sock.read_limits[0], -1)

    def test_a_timeout_is_passed_on_in_milliseconds(self):
        sock = FakeSocket(reply=b'{"ok": true}\n')
        self.send(sock, "toggle", wait=True, timeout=2.5)
        self.assertEqual(sock.read_limits[0], 2500)

    def test_a_fire_and_forget_verb_does_not_wait_around(self):
        sock = FakeSocket(reply=b'{"ok": true}\n')
        self.send(sock, "cancel")
        self.assertEqual(sock.read_limits[0], ipc.CONNECT_MS)

    def test_the_reply_comes_back_as_it_was_sent(self):
        sock = FakeSocket(reply=b'{"ok": false, "error": "no microphone"}\n')
        self.assertEqual(self.send(sock, "toggle"),
                         {"ok": False, "error": "no microphone"})

    def test_silence_from_an_old_instance_means_the_verb_went_through(self):
        sock = FakeSocket(reply=b"")
        reply = self.send(sock, "cancel")
        self.assertTrue(reply["ok"])
        self.assertTrue(reply["legacy"])

    def test_silence_during_a_wait_is_a_failure_with_a_way_out(self):
        sock = FakeSocket(reply=b"")
        reply = self.send(sock, "toggle", wait=True)
        self.assertFalse(reply["ok"])
        self.assertIn("reisulkuttab restart", reply["error"])

    def test_a_reply_that_is_not_json(self):
        sock = FakeSocket(reply=b"ok\n")
        self.assertEqual(self.send(sock, "toggle"), {"ok": True, "legacy": True})

    def test_a_reply_that_is_json_but_not_an_object(self):
        sock = FakeSocket(reply=b"[1, 2, 3]\n")
        self.assertEqual(self.send(sock, "toggle"), {"ok": True, "legacy": True})

    def test_the_socket_is_always_let_go_of(self):
        sock = FakeSocket(reply=b'{"ok": true}\n')
        self.send(sock, "toggle")
        self.assertTrue(sock.disconnected)


if __name__ == "__main__":
    unittest.main()

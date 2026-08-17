"""Platform-specific subprocess creation options."""

import unittest
from unittest import mock

import process


class WindowlessSubprocesses(unittest.TestCase):
    def test_windows_children_are_created_without_a_console(self):
        with mock.patch.object(process.os, "name", "nt"), \
                mock.patch.object(process.subprocess, "CREATE_NO_WINDOW", 8,
                                  create=True):
            self.assertEqual(process.windowless_options(), {"creationflags": 8})

    def test_other_platforms_leave_subprocess_creation_unchanged(self):
        with mock.patch.object(process.os, "name", "posix"):
            self.assertEqual(process.windowless_options(), {})

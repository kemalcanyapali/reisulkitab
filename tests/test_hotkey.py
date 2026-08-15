"""Parsing a shortcut, and the entry the desktop is asked to register it as."""

import ctypes
import contextlib
import os
import subprocess
import threading
import unittest
from unittest import mock

import config as cfg
import hotkey
from tests.support import AppTest, FakeCompleted, linux_only

SHORTCUTS_RC = """[services][reisulkuttab-toggle.desktop]
_launch=Ctrl+Space

[services][org.kde.spectacle.desktop]
RectangularRegionScreenShot=Meta+Shift+Print\tMeta+Shift+Print\t

[kwin]
Overview=Meta+W,Meta+W,Toggle Overview
Switch Window Down=Meta+Alt+Down,Meta+Alt+Down,Switch to Window Below
"""


class ParseShortcut(unittest.TestCase):
    def test_the_default(self):
        self.assertEqual(hotkey.parse_shortcut("Ctrl+Space"), ({"ctrl"}, 57))

    def test_case_and_spacing_do_not_matter(self):
        self.assertEqual(hotkey.parse_shortcut(" ctrl + SPACE "), ({"ctrl"}, 57))

    def test_several_modifiers(self):
        mods, key = hotkey.parse_shortcut("Ctrl+Alt+Shift+D")
        self.assertEqual(mods, {"ctrl", "alt", "shift"})
        self.assertEqual(key, 32)

    def test_the_synonyms_land_on_one_name(self):
        self.assertEqual(hotkey.parse_shortcut("Control+Space"),
                         hotkey.parse_shortcut("Ctrl+Space"))
        self.assertEqual(hotkey.parse_shortcut("Meta+Space"),
                         hotkey.parse_shortcut("Super+Space"))

    def test_a_key_on_its_own(self):
        self.assertEqual(hotkey.parse_shortcut("F9"), (set(), 67))

    def test_modifiers_with_no_key(self):
        self.assertEqual(hotkey.parse_shortcut("Ctrl+Alt"), (None, None))

    def test_a_key_nobody_mapped(self):
        self.assertEqual(hotkey.parse_shortcut("Ctrl+F13"), (None, None))

    def test_nothing(self):
        self.assertEqual(hotkey.parse_shortcut(""), (None, None))
        self.assertEqual(hotkey.parse_shortcut("+++"), (None, None))

    def test_something_that_is_not_even_a_string(self):
        self.assertEqual(hotkey.parse_shortcut(None), (None, None))


class Table(unittest.TestCase):
    """The one list of global shortcuts. The command line, the settings window
    and install.sh read it instead of keeping a copy each, so what it has to
    hold together is checked here rather than in three places."""

    def test_every_shortcut_remembers_itself_in_a_real_setting(self):
        for name, spec in hotkey.SHORTCUTS.items():
            with self.subTest(name=name):
                self.assertIn(spec.setting, cfg.DEFAULTS)

    def test_no_two_share_a_desktop_entry(self):
        ids = [spec.desktop_id for spec in hotkey.SHORTCUTS.values()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_only_the_toggle_falls_back_to_a_key_of_its_own(self):
        """The rest are off until you pick one, and emptying the box is how you
        turn them off again."""
        self.assertEqual(hotkey.SHORTCUTS["toggle"].fallback, "Ctrl+Space")
        self.assertEqual([name for name, spec in hotkey.SHORTCUTS.items()
                          if spec.fallback], ["toggle"])


class ModsMatch(unittest.TestCase):
    """The combination has to be exact, or Ctrl+Space fires on Ctrl+Shift+Space."""

    def match(self, held, wanted):
        return hotkey.EvdevHotkey._mods_match(set(held), set(wanted))

    def test_the_wanted_modifier_is_down(self):
        self.assertTrue(self.match({29}, {"ctrl"}))

    def test_either_side_of_the_keyboard_counts(self):
        self.assertTrue(self.match({97}, {"ctrl"}))

    def test_nothing_held_and_nothing_wanted(self):
        self.assertTrue(self.match(set(), set()))

    def test_a_modifier_too_many(self):
        self.assertFalse(self.match({29, 42}, {"ctrl"}))

    def test_a_modifier_missing(self):
        self.assertFalse(self.match(set(), {"ctrl"}))

    def test_the_wrong_modifier(self):
        self.assertFalse(self.match({56}, {"ctrl"}))


@linux_only
class Bindings(AppTest):
    """start() before it reaches /dev/input, which a test may not read."""

    def test_a_binding_with_no_shortcut_is_skipped(self):
        listener = hotkey.EvdevHotkey()
        with mock.patch.object(listener, "_open_devices", return_value=[]):
            self.assertFalse(listener.start({"toggle": "", "ask": ""}))

    def test_an_unparsable_shortcut_is_reported_and_the_rest_go_on(self):
        listener = hotkey.EvdevHotkey()
        self.addCleanup(listener.stop)
        failures = []
        listener.failed.connect(failures.append)
        with mock.patch.object(listener, "_open_devices", return_value=[99]), \
                mock.patch.object(hotkey.threading, "Thread"):
            self.assertTrue(listener.start({"toggle": "Ctrl+F13",
                                            "ask": "Ctrl+Space"}))
        self.assertEqual(len(failures), 1)
        self.assertIn("Ctrl+F13", failures[0])
        self.assertEqual(list(listener._bindings), [57])

    def test_no_readable_devices_says_what_to_do_about_it(self):
        listener = hotkey.EvdevHotkey()
        failures = []
        listener.failed.connect(failures.append)
        with mock.patch.object(listener, "_open_devices", return_value=[]):
            self.assertFalse(listener.start({"toggle": "Ctrl+Space"}))
        self.assertIn("input", failures[0])

    def test_two_shortcuts_on_one_key_are_both_kept(self):
        listener = hotkey.EvdevHotkey()
        self.addCleanup(listener.stop)
        with mock.patch.object(listener, "_open_devices", return_value=[]), \
                mock.patch.object(hotkey.threading, "Thread") as thread:
            listener._open_devices.return_value = [99]
            self.assertTrue(listener.start({"toggle": "Ctrl+Space",
                                            "ask": "Ctrl+Alt+Space"}))
            thread.assert_called_once()
        self.assertEqual(len(listener._bindings[57]), 2)

    def test_starting_and_discarding_do_not_fire_on_each_other(self):
        """The two defaults are one modifier apart on the same key code, so the
        modifier set is the only thing keeping them apart."""
        listener = hotkey.EvdevHotkey()
        self.addCleanup(listener.stop)
        with mock.patch.object(listener, "_open_devices", return_value=[99]), \
                mock.patch.object(hotkey.threading, "Thread"):
            listener.start({"toggle": "Ctrl+Space", "cancel": "Ctrl+Alt+Space"})

        def fired(held):
            return [name for mods, name in listener._bindings[57]
                    if hotkey.EvdevHotkey._mods_match(held, mods)]

        self.assertEqual(fired({29}), ["toggle"])          # ctrl
        self.assertEqual(fired({29, 56}), ["cancel"])      # ctrl + alt
        self.assertEqual(fired({29, 42}), [])              # ctrl + shift


class Chooser(AppTest):
    """Which desktop is asked to register the shortcut."""

    def setUp(self):
        super().setUp()
        self.patch_attr(hotkey.sys, "platform", "linux")

    @contextlib.contextmanager
    def under(self, desktop, has_gsettings=True):
        """A session that says it is this desktop, with or without gsettings."""
        with mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": desktop}), \
                mock.patch.object(hotkey.shutil, "which",
                                  return_value="/usr/bin/gsettings"
                                  if has_gsettings else None):
            yield

    def test_gnome_when_the_session_says_so_and_gsettings_is_there(self):
        with self.under("GNOME"):
            self.assertEqual(hotkey.desktop_name(), "GNOME")

    def test_kde_otherwise(self):
        with self.under("KDE"):
            self.assertEqual(hotkey.desktop_name(), "KDE")

    def test_a_gnome_session_with_no_gsettings_falls_back(self):
        """Nothing to write the binding with, so KDE's file is the only try."""
        with self.under("GNOME", has_gsettings=False):
            self.assertEqual(hotkey.desktop_name(), "KDE")

    def test_the_desktop_is_matched_loosely(self):
        for desktop in ("GNOME", "ubuntu:GNOME", "gnome"):
            with self.subTest(desktop=desktop), self.under(desktop):
                self.assertEqual(hotkey.desktop_name(), "GNOME")

    def test_installing_goes_to_whichever_it_is(self):
        with self.under("GNOME"), \
                mock.patch.object(hotkey, "install_gnome_shortcut",
                                  return_value=(True, "ok")) as gnome:
            hotkey.install_shortcut("Ctrl+Space", "reisulkuttab toggle")
        gnome.assert_called_once()

        with self.under("KDE"), \
                mock.patch.object(hotkey, "install_kde_shortcut",
                                  return_value=(True, "ok")) as kde:
            hotkey.install_shortcut("Ctrl+Space", "reisulkuttab toggle")
        kde.assert_called_once()

    def test_removing_and_reading_back_go_to_the_same_one(self):
        with self.under("GNOME"), \
                mock.patch.object(hotkey, "remove_gnome_shortcut") as remove, \
                mock.patch.object(hotkey, "gnome_shortcut_status",
                                  return_value="Ctrl+Space") as status:
            hotkey.remove_shortcut()
            self.assertEqual(hotkey.shortcut_status(), "Ctrl+Space")
        remove.assert_called_once()
        status.assert_called_once()


@linux_only
class GnomeAccelerator(AppTest):
    """Qt spells a combination one way, GNOME another."""

    def test_the_default_shortcut(self):
        self.assertEqual(hotkey.gnome_accelerator("Ctrl+Space"), "<Primary>Space")

    def test_several_modifiers_keep_their_order(self):
        self.assertEqual(hotkey.gnome_accelerator("Ctrl+Alt+A"), "<Primary><Alt>a")

    def test_the_synonyms(self):
        self.assertEqual(hotkey.gnome_accelerator("Meta+A"),
                         hotkey.gnome_accelerator("Super+A"))
        self.assertEqual(hotkey.gnome_accelerator("Control+A"),
                         hotkey.gnome_accelerator("Ctrl+A"))

    def test_a_modifier_repeated_is_written_once(self):
        self.assertEqual(hotkey.gnome_accelerator("Ctrl+Control+A"), "<Primary>a")

    def test_modifiers_with_no_key_are_not_a_shortcut(self):
        self.assertEqual(hotkey.gnome_accelerator("Ctrl+Alt"), "")
        self.assertEqual(hotkey.gnome_accelerator(""), "")

    def test_what_goes_out_comes_back_the_way_reisulkuttab_writes_it(self):
        for shortcut in ("Ctrl+Space", "Ctrl+Alt+A", "Shift+F9", "Super+M"):
            with self.subTest(shortcut=shortcut):
                accelerator = hotkey.gnome_accelerator(shortcut)
                self.assertEqual(hotkey.display_accelerator(accelerator), shortcut)

    def test_the_control_spelling_gnome_also_uses(self):
        self.assertEqual(hotkey.display_accelerator("<Control>a"), "Ctrl+A")

    def test_an_empty_binding(self):
        self.assertEqual(hotkey.display_accelerator(""), "")


@linux_only
class GsettingsArray(AppTest):
    def test_a_list_of_paths(self):
        self.assertEqual(
            hotkey._gsettings_array("['/org/gnome/one/', '/org/gnome/two/']"),
            ["/org/gnome/one/", "/org/gnome/two/"])

    def test_the_empty_form_gsettings_prints(self):
        self.assertEqual(hotkey._gsettings_array("@as []"), [])

    def test_nothing_at_all(self):
        self.assertEqual(hotkey._gsettings_array(""), [])

    def test_something_that_is_not_an_array(self):
        with self.assertRaises(ValueError):
            hotkey._gsettings_array("'just a string'")


@linux_only
class GnomeShortcut(AppTest):
    """The gsettings calls, without a session bus to make them against."""

    def setUp(self):
        super().setUp()
        self.enterContext(mock.patch.dict(os.environ,
                                          {"XDG_CURRENT_DESKTOP": "GNOME"}))
        self.enterContext(mock.patch.object(hotkey.shutil, "which",
                                            return_value="/usr/bin/gsettings"))

    def gsettings(self, listed="@as []", binding="'<Primary>Space'"):
        def run(cmd, **kwargs):
            if cmd[1] == "get" and cmd[3] == "custom-keybindings":
                return FakeCompleted(stdout=listed)
            return FakeCompleted(stdout=binding)
        return mock.patch.object(subprocess, "run", side_effect=run)

    def written(self, run, key):
        """The value the last `gsettings set ... <key>` was given."""
        for call in reversed(run.mock_calls):
            cmd = call.args[0] if call.args else []
            if len(cmd) > 4 and cmd[1] == "set" and cmd[3] == key:
                return cmd[4]
        return None

    def test_installing_registers_the_path_the_name_and_the_binding(self):
        with self.gsettings() as run:
            ok, message = hotkey.install_shortcut("Ctrl+Space", "reisulkuttab toggle")
        self.assertTrue(ok)
        self.assertIn("Ctrl+Space", message)
        self.assertIn(hotkey.DESKTOP_ID.removesuffix(".desktop"),
                      self.written(run, "custom-keybindings"))
        self.assertEqual(self.written(run, "command"), repr("reisulkuttab toggle"))
        self.assertEqual(self.written(run, "binding"), repr("<Primary>Space"))

    def test_installing_twice_does_not_list_the_path_twice(self):
        path = hotkey._gnome_path(hotkey.DESKTOP_ID)
        with self.gsettings(listed=repr([path])) as run:
            hotkey.install_shortcut("Ctrl+Space", "reisulkuttab toggle")
        self.assertIsNone(self.written(run, "custom-keybindings"))

    def test_each_verb_gets_its_own_path(self):
        self.assertNotEqual(hotkey._gnome_path(hotkey.DESKTOP_ID),
                            hotkey._gnome_path(hotkey.ASK_DESKTOP_ID))

    def test_a_shortcut_gnome_cannot_express(self):
        with self.gsettings():
            ok, message = hotkey.install_shortcut("Ctrl+Alt", "reisulkuttab toggle")
        self.assertFalse(ok)
        self.assertIn("Ctrl+Alt", message)

    def test_no_session_bus_to_talk_to(self):
        with mock.patch.object(subprocess, "run", side_effect=OSError("no bus")):
            ok, _ = hotkey.install_shortcut("Ctrl+Space", "reisulkuttab toggle")
        self.assertFalse(ok)

    def test_reading_back_a_shortcut_that_is_registered(self):
        path = hotkey._gnome_path(hotkey.DESKTOP_ID)
        with self.gsettings(listed=repr([path])):
            self.assertEqual(hotkey.shortcut_status(), "Ctrl+Space")

    def test_reading_back_one_that_is_not(self):
        with self.gsettings(listed="@as []"):
            self.assertIsNone(hotkey.shortcut_status())

    def test_removing_takes_the_path_off_the_list(self):
        path = hotkey._gnome_path(hotkey.DESKTOP_ID)
        with self.gsettings(listed=repr([path, "/org/gnome/other/"])) as run:
            hotkey.remove_shortcut()
        self.assertEqual(self.written(run, "custom-keybindings"),
                         repr(["/org/gnome/other/"]))

    def test_removing_one_that_was_never_installed(self):
        with self.gsettings(listed="@as []"):
            hotkey.remove_shortcut()   # must not raise


@linux_only
class KdeShortcut(AppTest):
    def setUp(self):
        super().setUp()
        self.apps = self.path("applications")
        self.apps.mkdir(parents=True)
        self.rc = self.path("kglobalshortcutsrc")
        self.patch_attr(hotkey, "APPLICATIONS_DIR", self.apps)
        self.patch_attr(hotkey, "SHORTCUTS_FILE", self.rc)

    def test_installing_writes_a_desktop_file_kwin_will_launch(self):
        with mock.patch.object(subprocess, "run", return_value=FakeCompleted()):
            ok, message = hotkey.install_kde_shortcut("Ctrl+Space", "reisulkuttab toggle")
        self.assertTrue(ok)
        text = (self.apps / hotkey.DESKTOP_ID).read_text(encoding="utf-8")
        self.assertIn("Exec=reisulkuttab toggle", text)
        self.assertIn("X-KDE-GlobalAccel-CommandShortcut=true", text)
        self.assertIn("log out", message)

    def test_the_shortcut_is_registered_under_the_desktop_id(self):
        with mock.patch.object(subprocess, "run",
                               return_value=FakeCompleted()) as run:
            hotkey.install_kde_shortcut("Meta+D", "reisulkuttab ask",
                                        desktop_id=hotkey.ASK_DESKTOP_ID)
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[0], "kwriteconfig6")
        self.assertIn(hotkey.ASK_DESKTOP_ID, cmd)
        self.assertEqual(cmd[-1], "Meta+D")

    def test_each_verb_gets_its_own_entry(self):
        with mock.patch.object(subprocess, "run", return_value=FakeCompleted()):
            hotkey.install_kde_shortcut("Ctrl+Space", "reisulkuttab toggle")
            hotkey.install_kde_shortcut("Meta+M", "reisulkuttab meeting",
                                        desktop_id=hotkey.MEETING_DESKTOP_ID)
        self.assertTrue((self.apps / hotkey.DESKTOP_ID).exists())
        self.assertTrue((self.apps / hotkey.MEETING_DESKTOP_ID).exists())

    def test_no_kwriteconfig_installed(self):
        with mock.patch.object(subprocess, "run", side_effect=OSError("nope")):
            ok, message = hotkey.install_kde_shortcut("Ctrl+Space", "reisulkuttab toggle")
        self.assertFalse(ok)
        self.assertIn("kglobalshortcutsrc", message)

    def test_removing_takes_the_desktop_file_with_it(self):
        (self.apps / hotkey.DESKTOP_ID).write_text("[Desktop Entry]", encoding="utf-8")
        with mock.patch.object(subprocess, "run", return_value=FakeCompleted()) as run:
            hotkey.remove_kde_shortcut()
        self.assertFalse((self.apps / hotkey.DESKTOP_ID).exists())
        self.assertIn("--delete", run.call_args.args[0])

    def test_removing_one_that_was_never_installed(self):
        with mock.patch.object(subprocess, "run", side_effect=OSError("nope")):
            hotkey.remove_kde_shortcut()   # must not raise

    def test_the_registered_shortcut_is_read_back(self):
        (self.apps / hotkey.DESKTOP_ID).write_text("[Desktop Entry]", encoding="utf-8")
        self.rc.write_text(SHORTCUTS_RC, encoding="utf-8")
        self.assertEqual(hotkey.kde_shortcut_status(), "Ctrl+Space")

    def test_no_desktop_file_means_nothing_is_installed(self):
        self.rc.write_text(SHORTCUTS_RC, encoding="utf-8")
        self.assertIsNone(hotkey.kde_shortcut_status())

    def test_a_desktop_file_with_no_entry_beside_it(self):
        (self.apps / hotkey.DESKTOP_ID).write_text("[Desktop Entry]", encoding="utf-8")
        self.rc.write_text("[kwin]\nOverview=Meta+W\n", encoding="utf-8")
        self.assertIsNone(hotkey.kde_shortcut_status())

    def test_no_shortcuts_file_at_all(self):
        (self.apps / hotkey.DESKTOP_ID).write_text("[Desktop Entry]", encoding="utf-8")
        self.assertIsNone(hotkey.kde_shortcut_status())

    def test_a_combination_somebody_else_already_took(self):
        self.rc.write_text(SHORTCUTS_RC, encoding="utf-8")
        hits = hotkey.conflicting_shortcuts("Meta+W")
        self.assertEqual(len(hits), 1)
        self.assertIn("kwin", hits[0])
        self.assertIn("Overview", hits[0])

    def test_our_own_entry_is_not_a_conflict(self):
        self.rc.write_text(SHORTCUTS_RC, encoding="utf-8")
        self.assertEqual(hotkey.conflicting_shortcuts("Ctrl+Space"), [])

    def test_a_free_combination(self):
        self.rc.write_text(SHORTCUTS_RC, encoding="utf-8")
        self.assertEqual(hotkey.conflicting_shortcuts("Ctrl+Alt+J"), [])

    def test_a_tab_separated_entry_is_read_too(self):
        self.rc.write_text(SHORTCUTS_RC, encoding="utf-8")
        hits = hotkey.conflicting_shortcuts("Meta+Shift+Print")
        self.assertEqual(len(hits), 1)
        self.assertIn("spectacle", hits[0])

    def test_no_shortcuts_file_means_no_conflicts(self):
        self.assertEqual(hotkey.conflicting_shortcuts("Ctrl+Space"), [])


# --- macOS ----------------------------------------------------------------

class ParseMacShortcut(unittest.TestCase):
    def test_a_combination_a_mac_would_use(self):
        self.assertEqual(hotkey.parse_macos_shortcut("Cmd+Space"),
                         (hotkey.MAC_MODS["cmd"], 49))

    def test_case_and_spacing_do_not_matter(self):
        self.assertEqual(hotkey.parse_macos_shortcut(" ctrl + option + a "),
                         hotkey.parse_macos_shortcut("Ctrl+Option+A"))

    def test_several_modifiers_are_one_number(self):
        modifiers, key = hotkey.parse_macos_shortcut("Cmd+Shift+M")
        self.assertEqual(modifiers,
                         hotkey.MAC_MODS["cmd"] | hotkey.MAC_MODS["shift"])
        self.assertEqual(key, hotkey.MAC_KEYS["m"])

    def test_the_names_a_mac_keyboard_uses(self):
        for name in ("cmd", "command", "meta", "super"):
            with self.subTest(name=name):
                self.assertEqual(hotkey.parse_macos_shortcut(f"{name}+space"),
                                 hotkey.parse_macos_shortcut("cmd+space"))
        self.assertEqual(hotkey.parse_macos_shortcut("option+a"),
                         hotkey.parse_macos_shortcut("alt+a"))

    def test_a_key_on_its_own(self):
        self.assertEqual(hotkey.parse_macos_shortcut("F5"), (0, 96))

    def test_modifiers_with_no_key(self):
        self.assertEqual(hotkey.parse_macos_shortcut("Cmd+Shift"), (None, None))

    def test_a_key_nobody_mapped(self):
        self.assertEqual(hotkey.parse_macos_shortcut("Cmd+F13"), (None, None))

    def test_two_keys_are_not_a_shortcut(self):
        self.assertEqual(hotkey.parse_macos_shortcut("A+B"), (None, None))

    def test_nothing(self):
        self.assertEqual(hotkey.parse_macos_shortcut(""), (None, None))
        self.assertEqual(hotkey.parse_macos_shortcut(None), (None, None))


class FakeCarbon:
    """Enough of Carbon to watch what the listener asks it for.

    The references are numbers standing in for pointers, which is all the code
    does with them: it collects them and hands them back to be unregistered.
    """

    def __init__(self, install=0, register=0):
        self.install, self.register = install, register   # what they return
        self.registered = []     # (key, modifiers, identifier)
        self.unregistered = []
        self.handlers_removed = 0
        self.pressed_id = 0
        self.parameter_result = 0

    def GetApplicationEventTarget(self):
        return 7000

    def InstallEventHandler(self, target, callback, count, spec, data, out):
        if self.install == 0:
            out._obj.value = 8000
        return self.install

    def RegisterEventHotKey(self, key, modifiers, identifier, target, options, out):
        if self.register != 0:
            return self.register
        self.registered.append((key, modifiers, identifier.id))
        out._obj.value = 9000 + len(self.registered)
        return 0

    def UnregisterEventHotKey(self, reference):
        self.unregistered.append(reference.value)
        return 0

    def RemoveEventHandler(self, handler):
        self.handlers_removed += 1
        return 0

    def GetEventParameter(self, event, kind, name, wanted_type, size, out_size, out):
        out._obj.id = self.pressed_id
        return self.parameter_result


class CarbonListener(AppTest):
    """What the listener asks macOS for, without a Mac to ask."""

    def setUp(self):
        super().setUp()
        self.carbon = FakeCarbon()
        self.patch_attr(hotkey, "_carbon", lambda: self.carbon)
        self.addCleanup(hotkey._REGISTERED.clear)
        self.listener = hotkey.CarbonHotkey()
        self.addCleanup(self.listener.stop)
        self.failures = []
        self.listener.failed.connect(self.failures.append)

    def test_every_binding_is_asked_for_by_position_and_modifier(self):
        self.assertTrue(self.listener.start({"toggle": "Ctrl+Option+Space"}))
        self.assertEqual(self.carbon.registered,
                         [(49, hotkey.MAC_MODS["ctrl"] | hotkey.MAC_MODS["option"], 1)])
        self.assertEqual(self.failures, [])
        self.assertTrue(self.listener.running)

    def test_a_binding_with_no_shortcut_is_skipped(self):
        self.assertFalse(self.listener.start({"toggle": "", "ask": ""}))
        self.assertEqual(self.carbon.registered, [])
        self.assertFalse(self.listener.running)

    def test_an_unparsable_shortcut_is_reported_and_the_rest_go_on(self):
        self.assertTrue(self.listener.start({"toggle": "Cmd+F13",
                                             "ask": "Cmd+Shift+Space"}))
        self.assertEqual(len(self.failures), 1)
        self.assertIn("Cmd+F13", self.failures[0])
        self.assertEqual(len(self.carbon.registered), 1)

    def test_a_combination_another_application_already_holds(self):
        """The conflict warning macOS has: it is the answer to asking."""
        self.carbon.register = -9878        # eventHotKeyExistsErr
        self.assertFalse(self.listener.start({"toggle": "Cmd+Shift+Space"}))
        self.assertIn("Cmd+Shift+Space", self.failures[0])
        self.assertFalse(self.listener.running)

    def test_a_handler_that_will_not_install(self):
        self.carbon.install = -50
        self.assertFalse(self.listener.start({"toggle": "Cmd+Shift+Space"}))
        self.assertEqual(self.carbon.registered, [])
        self.assertEqual(len(self.failures), 1)

    def test_no_carbon_to_talk_to(self):
        self.patch_attr(hotkey, "_carbon",
                        mock.Mock(side_effect=OSError("no such library")))
        self.assertFalse(self.listener.start({"toggle": "Cmd+Shift+Space"}))
        self.assertIn("no such library", self.failures[0])

    def test_the_key_press_arrives_under_the_name_it_was_registered_with(self):
        self.listener.start({"toggle": "Cmd+Shift+Space", "ask": "Cmd+Shift+A"})
        heard = []
        self.listener.triggered.connect(heard.append)
        self.carbon.pressed_id = 2          # the second binding, which is "ask"
        self.listener._callback(None, 0, None)
        self.assertEqual(heard, ["ask"])

    def test_a_press_carbon_could_not_identify_is_dropped(self):
        self.listener.start({"toggle": "Cmd+Shift+Space"})
        heard = []
        self.listener.triggered.connect(heard.append)
        self.carbon.parameter_result = -50
        self.listener._callback(None, 0, None)
        self.assertEqual(heard, [])

    def test_stopping_hands_every_registration_back(self):
        self.listener.start({"toggle": "Cmd+Shift+Space", "ask": "Cmd+Shift+A"})
        self.listener.stop()
        self.assertEqual(self.carbon.unregistered, [9001, 9002])
        self.assertEqual(self.carbon.handlers_removed, 1)
        self.assertFalse(self.listener.running)

    def test_starting_twice_does_not_leave_the_first_set_behind(self):
        self.listener.start({"toggle": "Cmd+Shift+Space"})
        self.listener.start({"toggle": "Cmd+Shift+A"})
        self.assertEqual(self.carbon.unregistered, [9001])
        self.assertEqual(len(self.listener._registrations), 1)

    def test_what_it_registered_is_what_the_status_line_reads_back(self):
        with mock.patch.object(hotkey.sys, "platform", "darwin"):
            self.listener.start({"toggle": "Ctrl+Option+Space",
                                 "meeting": "Ctrl+Option+M"})
            self.assertEqual(hotkey.shortcut_status(), "Ctrl+Option+Space")
            self.assertEqual(hotkey.shortcut_status(hotkey.MEETING_DESKTOP_ID),
                             "Ctrl+Option+M")
            self.listener.stop()
            self.assertIsNone(hotkey.shortcut_status())


class MacChooser(AppTest):
    """What the shortcut verbs mean where there is nothing to write them into."""

    def setUp(self):
        super().setUp()
        self.patch_attr(hotkey.sys, "platform", "darwin")
        self.addCleanup(hotkey._REGISTERED.clear)

    def test_the_listener_is_the_one_macos_has(self):
        self.assertIsInstance(hotkey.listener(), hotkey.CarbonHotkey)

    def test_everywhere_else_reads_the_keyboard_itself(self):
        with mock.patch.object(hotkey.sys, "platform", "linux"):
            self.assertIsInstance(hotkey.listener(), hotkey.EvdevHotkey)

    def test_there_is_nothing_to_install_into(self):
        self.assertFalse(hotkey.installs_shortcuts())
        self.assertFalse(hotkey.shortcut_needs_restart())
        self.assertEqual(hotkey.desktop_name(), "macOS")

    def test_installing_records_it_rather_than_writing_anything(self):
        with mock.patch.object(hotkey.subprocess, "run") as run:
            ok, message = hotkey.install_shortcut("Cmd+Shift+Space", "reisulkuttab toggle")
        run.assert_not_called()
        self.assertTrue(ok)
        self.assertIn("Cmd+Shift+Space", message)
        self.assertEqual(hotkey.shortcut_status(), "Cmd+Shift+Space")

    def test_removing_takes_it_back_out(self):
        hotkey.install_shortcut("Cmd+Shift+Space", "reisulkuttab toggle")
        hotkey.remove_shortcut()
        self.assertIsNone(hotkey.shortcut_status())

    def test_each_verb_is_kept_apart(self):
        hotkey.install_shortcut("Cmd+Shift+Space", "reisulkuttab toggle")
        hotkey.install_shortcut("Cmd+Shift+M", "reisulkuttab meeting",
                                desktop_id=hotkey.MEETING_DESKTOP_ID)
        self.assertEqual(hotkey.shortcut_status(), "Cmd+Shift+Space")
        self.assertEqual(hotkey.shortcut_status(hotkey.MEETING_DESKTOP_ID),
                         "Cmd+Shift+M")

    def test_no_list_of_conflicts_to_read(self):
        """Not even KDE's file, which a Mac could well have a copy of."""
        self.assertEqual(hotkey.conflicting_shortcuts("Ctrl+Space"), [])

    def test_a_combination_is_checked_against_the_mac_table(self):
        self.assertTrue(hotkey.valid_shortcut("Cmd+Shift+Space"))
        self.assertFalse(hotkey.valid_shortcut("Ctrl+F13"))

    def test_the_other_table_is_the_one_used_elsewhere(self):
        with mock.patch.object(hotkey.sys, "platform", "linux"):
            self.assertTrue(hotkey.valid_shortcut("Ctrl+F1"))
            self.assertFalse(hotkey.valid_shortcut("Cmd+Space"))


class WindowsHook(AppTest):
    """The Windows API is faked, but the hook callback and lifecycle are real."""

    def setUp(self):
        super().setUp()
        self.user32 = mock.Mock()
        self.kernel32 = mock.Mock()
        self.user32.SetWindowsHookExW.return_value = 0x12345678
        self.user32.UnhookWindowsHookEx.return_value = 1
        self.user32.PeekMessageW.return_value = 1
        self.user32.GetMessageW.return_value = 0
        self.user32.CallNextHookEx.return_value = 99
        self.kernel32.GetCurrentThreadId.return_value = 123
        self.kernel32.GetModuleHandleW.return_value = 0x87654321
        windll = mock.Mock(user32=self.user32, kernel32=self.kernel32)
        self.patch = mock.patch.object(
            hotkey.ctypes, "windll", windll, create=True)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.callback_factory = mock.Mock(side_effect=ctypes.CFUNCTYPE)
        callback = mock.patch.object(
            hotkey.ctypes, "WINFUNCTYPE", self.callback_factory, create=True)
        callback.start()
        self.addCleanup(callback.stop)
        self.listener = hotkey.WindowsHotkey()

    def test_timed_out_start_cancels_before_installing_a_late_hook(self):
        thread = mock.Mock()
        thread.is_alive.return_value = True
        with mock.patch.object(
                hotkey.threading, "Thread", return_value=thread), \
                mock.patch.object(
                    self.listener._ready, "wait", return_value=False):
            self.assertFalse(
                self.listener.start({"toggle": "Ctrl+Space"}))

        thread.start.assert_called_once()
        thread.join.assert_called_once_with(timeout=2)
        self.assertTrue(self.listener._cancel.is_set())
        self.assertTrue(self.listener._activate.is_set())
        self.listener._loop()
        self.user32.SetWindowsHookExW.assert_not_called()

    def test_hook_install_failure_reports_the_native_error(self):
        self.user32.SetWindowsHookExW.return_value = 0
        self.kernel32.GetLastError.return_value = 1428
        failures = []
        self.listener.failed.connect(failures.append)

        self.listener._loop()

        self.assertEqual(len(failures), 1)
        self.assertIn("1428", failures[0])
        self.kernel32.GetLastError.assert_called_once_with()
        self.user32.GetMessageW.assert_not_called()
        self.user32.UnhookWindowsHookEx.assert_not_called()



    def test_repeat_keydowns_stay_with_the_active_hold(self):
        self.listener._bindings = {
            hotkey.WIN_KEYS["space"]: [
                ({"ctrl"}, "toggle"),
                ({"alt"}, "ask"),
            ],
        }
        held = set(hotkey.WIN_MODS["alt"])
        self.user32.GetAsyncKeyState.side_effect = (
            lambda code: 0x8000 if code in held else 0)
        event = hotkey._KbdLlHookStruct(vkCode=hotkey.WIN_KEYS["space"])
        pointer = ctypes.addressof(event)
        callback_results = []

        def deliver_events(*_args):
            callback_results.append(
                self.listener._callback(0, 0x0100, pointer))
            held.clear()
            held.update(hotkey.WIN_MODS["ctrl"])
            callback_results.append(
                self.listener._callback(0, 0x0100, pointer))
            callback_results.append(
                self.listener._callback(0, 0x0101, pointer))
            return 0

        self.user32.GetMessageW.side_effect = deliver_events
        triggered, released = [], []
        self.listener.triggered.connect(triggered.append)
        self.listener.released.connect(released.append)

        self.listener._activate.set()
        self.listener._loop()

        self.assertEqual(callback_results, [1, 1, 1])
        self.assertEqual(triggered, ["ask"])
        self.assertEqual(released, ["ask"])
        self.assertEqual(self.listener._down, set())

    def test_hook_signatures_keep_pointer_sized_values(self):
        self.listener._activate.set()
        self.listener._loop()

        self.callback_factory.assert_called_once_with(
            ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t)
        self.assertIs(self.kernel32.GetModuleHandleW.restype, ctypes.c_void_p)
        self.assertIs(self.user32.SetWindowsHookExW.restype, ctypes.c_void_p)
        self.assertEqual(self.user32.SetWindowsHookExW.argtypes, [
            ctypes.c_int, self.listener._callback.__class__,
            ctypes.c_void_p, hotkey.ctypes.wintypes.DWORD,
        ])
        self.assertEqual(self.user32.CallNextHookEx.argtypes, [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t,
        ])
        self.assertIs(self.user32.CallNextHookEx.restype, ctypes.c_ssize_t)
        self.assertEqual(
            self.user32.UnhookWindowsHookEx.argtypes, [ctypes.c_void_p])
        self.assertEqual(self.user32.PostThreadMessageW.argtypes, [
            hotkey.ctypes.wintypes.DWORD, hotkey.ctypes.wintypes.UINT,
            ctypes.c_size_t, ctypes.c_ssize_t,
        ])

    def test_cancel_is_rechecked_between_messages(self):
        results = iter((1, 0))

        def cancel_after_message(*_args):
            result = next(results)
            if result:
                self.listener._cancel.set()
            return result

        self.user32.GetMessageW.side_effect = cancel_after_message
        self.listener._activate.set()
        self.listener._loop()

        self.user32.GetMessageW.assert_called_once()
        self.user32.TranslateMessage.assert_called_once()
        self.user32.DispatchMessageW.assert_called_once()

    def test_live_loop_and_failed_unhook_keep_references_until_retry(self):
        entered = threading.Event()
        release = threading.Event()

        def wait_for_release(*_args):
            entered.set()
            release.wait(timeout=2)
            return 0

        self.user32.GetMessageW.side_effect = wait_for_release
        self.user32.UnhookWindowsHookEx.side_effect = [0, 1]
        thread = threading.Thread(target=self.listener._loop)
        self.listener._thread = thread
        self.addCleanup(release.set)
        self.addCleanup(thread.join, 1)
        thread.start()

        self.assertTrue(self.listener._ready.wait(timeout=1))
        self.assertTrue(thread.is_alive())
        self.assertEqual(self.listener._hook, 0x12345678)
        self.assertIsNotNone(self.listener._callback)
        self.listener._activate.set()
        self.assertTrue(entered.wait(timeout=1))

        release.set()
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertFalse(self.listener._unhooked)
        self.assertEqual(self.listener._hook, 0x12345678)
        self.assertIsNotNone(self.listener._callback)
        self.assertTrue(self.listener.stop())
        self.assertIsNone(self.listener._hook)
        self.assertIsNone(self.listener._callback)

    def test_failed_quit_message_keeps_live_hook_references(self):
        thread = mock.Mock()
        thread.is_alive.return_value = True
        callback = object()
        self.listener._thread = thread
        self.listener._thread_id = 123
        self.listener._hook = 0x12345678
        self.listener._callback = callback
        self.listener._unhooked = False
        self.user32.PostThreadMessageW.return_value = 0

        self.assertFalse(self.listener.stop())
        self.user32.PostThreadMessageW.assert_called_once_with(
            123, 0x0012, 0, 0)
        thread.join.assert_called_once_with(timeout=2)
        self.assertIs(self.listener._thread, thread)
        self.assertEqual(self.listener._hook, 0x12345678)
        self.assertIs(self.listener._callback, callback)



class WindowsChooser(AppTest):
    def setUp(self):
        super().setUp()
        self.patch_attr(hotkey.sys, "platform", "win32")

    def test_the_listener_is_the_windows_hook(self):
        self.assertIsInstance(hotkey.listener(), hotkey.WindowsHotkey)

    def test_the_shortcut_uses_windows_virtual_keys(self):
        self.assertTrue(hotkey.valid_shortcut("Ctrl+Alt+D"))
        self.assertFalse(hotkey.valid_shortcut("Ctrl+F25"))

    def test_windows_needs_no_registered_desktop_shortcut(self):
        self.assertFalse(hotkey.installs_shortcuts())
        self.assertEqual(hotkey.desktop_name(), "Windows")


if __name__ == "__main__":
    unittest.main()

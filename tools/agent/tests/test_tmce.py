"""tmce.py: finding Dolphin, applying TM-CE's patch to your disc, and launching it (a stand-in Dolphin)."""

import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
AGENT = HERE.parent
sys.path.insert(0, str(AGENT))

import play  # noqa: E402
import tmce  # noqa: E402

WINDOWS = os.name == "nt"


def touch(p, text="x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def script(path, body):
    """An executable stand-in (POSIX)."""
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class FindDolphinTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.no_run = mock.Mock(side_effect=AssertionError("flatpak isn't installed"))

    def test_the_saved_one_first_while_it_exists(self):
        saved = touch(self.tmp / "my" / "Dolphin.exe")
        self.assertEqual(tmce.find_dolphin({"dolphin": str(saved)}, environ={"PATH": ""}, home=self.tmp,
                                           windows=True), [str(saved)])
        saved.unlink()
        self.assertIsNone(tmce.find_dolphin({"dolphin": str(saved)}, environ={"PATH": ""}, home=self.tmp,
                                            windows=True), "gone: not used, and nothing else is there")

    def test_windows_places(self):
        env = {"ProgramFiles": str(self.tmp / "PF"), "APPDATA": str(self.tmp / "AppData")}
        slippi = touch(self.tmp / "AppData" / "Slippi Launcher" / "netplay" / "Slippi Dolphin.exe")
        self.assertEqual(tmce.find_dolphin({}, environ=env, windows=True), [str(slippi)])
        plain = touch(self.tmp / "PF" / "Dolphin" / "Dolphin.exe")
        self.assertEqual(tmce.find_dolphin({}, environ=env, windows=True), [str(plain)], "plain Dolphin first")

    @unittest.skipIf(WINDOWS, "POSIX stand-ins")
    def test_linux_places(self):
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        env = {"PATH": str(bin_dir), "XDG_CONFIG_HOME": str(self.tmp / "config")}
        self.assertIsNone(tmce.find_dolphin({}, environ=env, home=self.tmp, windows=False))
        appimage = touch(self.tmp / "config" / "Slippi Launcher" / "netplay" / "Slippi_Online-x86_64.AppImage")
        self.assertEqual(tmce.find_dolphin({}, environ=env, home=self.tmp, windows=False), [str(appimage)])
        script(bin_dir / "flatpak", "exit 0\n")
        self.assertEqual(tmce.find_dolphin({}, environ=env, home=self.tmp, windows=False),
                         [str(bin_dir / "flatpak"), "run", tmce.FLATPAK_ID], "the flatpak before Slippi's")
        script(bin_dir / "flatpak", "exit 1\n")          # flatpak, but no Dolphin in it
        self.assertEqual(tmce.find_dolphin({}, environ=env, home=self.tmp, windows=False), [str(appimage)])
        script(bin_dir / "dolphin-emu", "exit 0\n")
        self.assertEqual(tmce.find_dolphin({}, environ=env, home=self.tmp, windows=False),
                         [str(bin_dir / "dolphin-emu")])


class CommandsTest(unittest.TestCase):
    def test_commands(self):
        self.assertEqual(tmce.launch_command(["flatpak", "run", tmce.FLATPAK_ID], "/x/TM-CE.iso"),
                         ["flatpak", "run", tmce.FLATPAK_ID, "-b", "-e", "/x/TM-CE.iso"])
        self.assertEqual(tmce.patch_command("xdelta3", "/d/tmce.xdelta", "/g/GALE01.iso", "/d/tmce.iso"),
                         ["xdelta3", "-d", "-f", "-s", "/g/GALE01.iso", "/d/tmce.xdelta", "/d/tmce.iso"])
        self.assertTrue(tmce.is_patch("TM-CE v1.2.xdelta"))
        self.assertFalse(tmce.is_patch("TM-CE.iso"))
        self.assertEqual(tmce.patched_path("/d/TM-CE v1.2.xdelta"), Path("/d/TM-CE v1.2.iso"))


@unittest.skipIf(WINDOWS, "POSIX stand-ins")
class RunTest(unittest.TestCase):
    """main() end to end: settings in a temporary settings.json, a stand-in Dolphin that records how it
    was started."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        patcher = mock.patch.object(play, "SETTINGS", self.tmp / "settings.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.args_file = self.tmp / "dolphin-args"
        self.dolphin = script(self.tmp / "dolphin", f'printf "%s\\n" "$@" > "{self.args_file}"\nexit 0\n')
        self.disc = self.tmp / "GALE01.iso"
        self.disc.write_bytes(os.urandom(200_000))

    def tm_image(self, path):
        """Your disc with a few changes, as TM-CE's image is."""
        data = bytearray(self.disc.read_bytes())
        data[1000:1100] = b"TM-CE" * 20
        data += b"events" * 1000
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bytes(data))
        return path

    def main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = tmce.main(list(argv))
            except SystemExit as e:
                code = e.code
        return code, out.getvalue() + err.getvalue()

    def test_an_iso_is_remembered_and_launched(self):
        iso = touch(self.tmp / "TM-CE.iso")
        code, said = self.main("--dolphin", str(self.dolphin), "--iso", str(iso))
        self.assertEqual(code, 0, said)
        self.assertIn(f"tmce: TM-CE in {self.dolphin}: {iso}", said)
        self.assertEqual(self.args_file.read_text().splitlines(), ["-b", "-e", str(iso)])
        saved = json.loads((self.tmp / "settings.json").read_text())["tmce"]
        self.assertEqual(saved, {"dolphin": str(self.dolphin), "iso": str(iso)})
        self.args_file.unlink()
        code, _ = self.main()                       # next time: nothing to say
        self.assertEqual(code, 0)
        self.assertTrue(self.args_file.exists())

    def test_nothing_set_up_yet(self):
        code, said = self.main("--dolphin", str(self.dolphin))
        self.assertEqual(code, 1)
        self.assertIn("no TM-CE disc image yet", said)
        self.assertIn(tmce.RELEASES, said)

    @unittest.skipUnless(shutil.which("xdelta3"), "needs xdelta3")
    def test_the_patch_applied_to_your_disc(self):
        tm = self.tm_image(self.tmp / "made" / "tmce-source.iso")
        patch = self.tmp / "dl" / "TM-CE v1.0.xdelta"
        patch.parent.mkdir()
        subprocess.run(["xdelta3", "-e", "-s", str(self.disc), str(tm), str(patch)], check=True)
        (self.tmp / "settings.json").write_text(json.dumps({"disc": str(self.disc)}))   # AI-Melee's disc
        code, said = self.main("--dolphin", str(self.dolphin), "--patch", str(patch))
        self.assertEqual(code, 0, said)
        out = self.tmp / "dl" / "TM-CE v1.0.iso"
        self.assertEqual(out.read_bytes(), tm.read_bytes())
        self.assertIn("tmce: applying the TM-CE patch", said)
        self.assertEqual(json.loads((self.tmp / "settings.json").read_text())["tmce"]["iso"], str(out))
        self.assertEqual(self.args_file.read_text().splitlines(), ["-b", "-e", str(out)])

    @unittest.skipUnless(shutil.which("xdelta3"), "needs xdelta3")
    def test_the_wrong_disc(self):
        tm = self.tm_image(self.tmp / "tm.iso")
        patch = self.tmp / "TM-CE.xdelta"
        subprocess.run(["xdelta3", "-e", "-s", str(self.disc), str(tm), str(patch)], check=True)
        other = self.tmp / "PAL.iso"
        other.write_bytes(os.urandom(200_000))
        code, said = self.main("--dolphin", str(self.dolphin), "--patch", str(patch), "--disc", str(other))
        self.assertEqual(code, 1)
        self.assertIn("the TM-CE patch didn't apply to your disc", said)
        self.assertFalse((self.tmp / "TM-CE.iso").exists(), "no half-made image left behind")

    def test_a_patch_without_xdelta3(self):
        patch = touch(self.tmp / "TM-CE.xdelta")
        with mock.patch.object(tmce.shutil, "which", return_value=None):
            code, said = self.main("--dolphin", str(self.dolphin), "--iso", str(patch), "--disc", str(self.disc))
        self.assertEqual(code, 1)
        self.assertIn("needs xdelta3", said)

    def test_a_compressed_disc(self):
        patch = touch(self.tmp / "TM-CE.xdelta")
        rvz = touch(self.tmp / "GALE01.rvz")
        code, said = self.main("--dolphin", str(self.dolphin), "--patch", str(patch), "--disc", str(rvz))
        self.assertEqual(code, 1)
        self.assertIn("is compressed", said)


if __name__ == "__main__":
    unittest.main()

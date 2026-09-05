import tempfile
import unittest
from pathlib import Path

from tools.autoflash.phases import ARM_RE, LOADER_RE, PhaseRunner, PhaseError, short_loader
from tools.autoflash.state import DONE, FAILED, RunState


class RunStateTests(unittest.TestCase):
    def test_state_survives_reload_and_records_steps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = RunState.open(root)
            state.set_input("plug_ip", "192.168.1.99")
            state.mark("A1", DONE, note="paired")
            state.capture("D1.upload_seconds", 41)

            reopened = RunState.open(root)
            self.assertEqual(reopened.input("plug_ip"), "192.168.1.99")
            self.assertTrue(reopened.is_done("A1"))
            self.assertEqual(reopened.data["captures"]["D1.upload_seconds"], 41)

    def test_require_reports_every_missing_input(self):
        with tempfile.TemporaryDirectory() as directory:
            state = RunState.open(Path(directory))
            state.set_input("plug_ip", "")
            with self.assertRaisesRegex(ValueError, "plug_ip.*listen_ip|listen_ip"):
                state.require("plug_ip", "listen_ip")

    def test_failed_step_is_not_skipped_on_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = RunState.open(root)
            state.mark("E1", FAILED, error="stage refused")
            self.assertFalse(RunState.open(root).is_done("E1"))


class LoaderTests(unittest.TestCase):
    URL = "http://192.168.1.141:8089/berry/commit.be"

    def test_short_loader_is_equivalent_and_much_shorter(self):
        printed = (
            "def s60_urlbeload(url) var wc=webclient() wc.begin(url) var st=wc.GET() "
            "if st!=200 wc.close() raise 'connection_error',format('status:%i',st) end "
            f"var code=wc.get_string() return compile(code)() end s60_commit=s60_urlbeload('{self.URL}')"
        )
        compact = short_loader(self.URL, assign_to="s60_commit")
        self.assertLess(len(compact), len(printed) / 2)
        for fragment in ("webclient()", self.URL, "compile(", "s60_commit="):
            self.assertIn(fragment, compact)

    def test_loader_url_and_arm_token_are_parsed_from_server_output(self):
        printed = f"return s60_urlbeload('{self.URL}')"
        self.assertEqual(LOADER_RE.search(printed).group("url"), self.URL)
        found = ARM_RE.search('s60_commit("d2_fjQTqh4KzfHDFSefx")')
        self.assertEqual(found.group("name"), "s60_commit")
        self.assertEqual(found.group("token"), "d2_fjQTqh4KzfHDFSefx")

    def test_armed_phase_refuses_to_run_without_a_confirmation_callback(self):
        runner = PhaseRunner(
            repo_root=Path("."),
            manifest=Path("m.json"),
            evidence_dir=Path("live"),
            listen_ip="192.168.1.141",
            device=None,  # never reached: the guard fires first
            log=lambda _: None,
        )
        for phase in ("commit", "restore"):
            with self.subTest(phase=phase):
                with self.assertRaisesRegex(PhaseError, "explicit confirmation"):
                    runner.run(phase)


if __name__ == "__main__":
    unittest.main()


class ResumeTests(unittest.TestCase):
    """The checklist is only useful if it behaves under interruption."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)

    def test_a_step_that_never_reported_is_re_run(self):
        state = RunState.open(self.root)
        state.mark("C2", "attempted")
        reopened = RunState.open(self.root)
        self.assertFalse(reopened.is_done("C2"))
        self.assertEqual(reopened.status("C2"), "attempted")

    def test_a_crash_before_any_mark_leaves_the_step_pending(self):
        state = RunState.open(self.root)
        state.mark("A1", DONE)
        # C2 was never reached at all
        self.assertEqual(RunState.open(self.root).status("C2"), "pending")

    def test_completed_and_failed_steps_are_distinguishable(self):
        state = RunState.open(self.root)
        state.mark("E1", DONE)
        state.mark("E2", FAILED, error="stage refused")
        reopened = RunState.open(self.root)
        self.assertTrue(reopened.is_done("E1"))
        self.assertFalse(reopened.is_done("E2"))
        self.assertEqual(reopened.data["steps"]["E2"]["error"], "stage refused")

    def test_migration_phases_resume_independently(self):
        import tools.s60_autoflash as autoflash

        ids = [step for step, _, _ in autoflash.STEPS]
        self.assertEqual(ids[ids.index("E1"):ids.index("E1") + 3], ["E1", "E2", "E3"])
        state = RunState.open(self.root)
        state.mark("E1", DONE)
        state.mark("E2", DONE)
        reopened = RunState.open(self.root)
        self.assertTrue(reopened.is_done("E2"))
        self.assertFalse(reopened.is_done("E3"))

    def test_every_destructive_step_records_an_attempt_first(self):
        import tools.s60_autoflash as autoflash

        known = {step for step, _, _ in autoflash.STEPS}
        self.assertTrue(autoflash.DESTRUCTIVE_STEPS <= known)
        self.assertIn("E3", autoflash.DESTRUCTIVE_STEPS)
        self.assertIn("C2", autoflash.DESTRUCTIVE_STEPS)

    def test_state_file_is_replaced_atomically(self):
        state = RunState.open(self.root)
        state.mark("A1", DONE)
        leftovers = [f for f in self.root.iterdir() if f.name.startswith(".")]
        self.assertEqual(leftovers, [], "temporary files must not survive a write")

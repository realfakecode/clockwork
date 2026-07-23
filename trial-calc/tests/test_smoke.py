"""Confirms the test runner is wired up. Feature tests live beside this file, added
test-first alongside the code they cover."""

import unittest


class FrameworkSmokeTest(unittest.TestCase):
    def test_runner_works(self):
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()

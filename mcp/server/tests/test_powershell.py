import os
import shutil
import subprocess
import unittest

from rustdesk_mcp.framing import Parser, wrapper


@unittest.skipUnless(os.name == "nt" and shutil.which("powershell"), "Windows PowerShell required")
class PowerShellTest(unittest.TestCase):
    def test_real_powershell_unicode_errors_and_native_exit(self):
        cases = [
            ("Write-Output '中文😀'; [Console]::Error.WriteLine('native-like stderr')", True, 0, "中文😀"),
            ("Write-Error 'reported failure'", False, 0, "reported failure"),
            ("& $env:ComSpec /c exit 7", True, 7, ""),
            ("Write-Output ('中文😀' * 300)", True, 0, "中文😀" * 300),
        ]
        for script, success, exit_code, expected in cases:
            with self.subTest(script=script):
                process = subprocess.run(["powershell", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
                                          wrapper(script, "test").rstrip("\r")],
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
                output, metadata = Parser("test").feed(process.stdout)
                self.assertIsNotNone(metadata, process.stdout.decode("utf-8", errors="replace"))
                self.assertIn(expected, output)
                self.assertEqual(metadata["powershell_success"], success)
                self.assertEqual(metadata["native_exit_code"], exit_code)

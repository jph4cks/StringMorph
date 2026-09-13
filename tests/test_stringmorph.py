"""CLI regressions using synthetic data files; no fixture is executed."""

import csv
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "StringMorph.py"
ORIGINAL = b"\x00Ab12!?.\x00\xffKeep-9\x00"
VALID_CSV = "Location,String\n0x1,Ab12!?.\n"


class StringMorphCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.directory = Path(self.temporary_directory.name)
        self.original = self.directory / "fixture.bin"
        self.original.write_bytes(ORIGINAL)
        self.modified = self.directory / "fixture_modified.bin"

    def run_cli(self, *arguments, filename=None, cwd=None):
        try:
            return subprocess.run(
                [sys.executable, str(SCRIPT), str(filename or self.original), *arguments],
                cwd=cwd or self.directory,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                check=False,
            )
        except subprocess.TimeoutExpired:
            self.fail("CLI did not finish within 15 seconds")

    def write_source(self, contents=VALID_CSV, directory=None):
        source = (directory or self.directory) / "source.csv"
        source.write_text(contents, encoding="utf-8")
        return source

    def snapshot(self, directory=None):
        directory = directory or self.directory
        return {
            path.relative_to(directory).as_posix(): path.read_bytes()
            for path in directory.rglob("*")
            if path.is_file()
        }

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stdout + result.stderr)

    def assert_rejected_without_writes(self, result, before, directory=None):
        # Check preservation first so failures identify the data-loss regression.
        self.assertEqual(self.snapshot(directory), before)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stdout + result.stderr)

    def test_extraction_writes_exact_csv_records(self):
        result = self.run_cli("-l", "4", "-o", "strings.csv")

        self.assert_success(result)
        with (self.directory / "strings.csv").open(newline="", encoding="utf-8") as stream:
            self.assertEqual(
                list(csv.reader(stream)),
                [["Location", "String"], ["0x1", "Ab12!?."], ["0xa", "Keep-9"]],
            )
        self.assertEqual(self.original.read_bytes(), ORIGINAL)
        self.assertFalse(self.modified.exists())

    def test_no_space_splits_tokens_and_keeps_final_eof_token(self):
        binary_data = b"\x00Alpha beta  GO\xffOmega"
        self.original.write_bytes(binary_data)

        result = self.run_cli("--no-space", "--length", "4")

        self.assert_success(result)
        with (self.directory / "output.csv").open(newline="", encoding="utf-8") as stream:
            self.assertEqual(
                list(csv.reader(stream)),
                [["Location", "String"], ["0x1", "Alpha"], ["0x7", "beta"], ["0x10", "Omega"]],
            )
        self.assertEqual(self.original.read_bytes(), binary_data)
        self.assertFalse(self.modified.exists())

    def test_keywords_ignore_case_and_override_length_with_shortest_keyword(self):
        binary_data = b"\x00go\x00xxALPHAxx\x00ordinary\x00Go!"
        self.original.write_bytes(binary_data)

        result = self.run_cli("--length", "20", "--keywords", "ALPHA,GO")

        self.assert_success(result)
        with (self.directory / "output.csv").open(newline="", encoding="utf-8") as stream:
            self.assertEqual(
                list(csv.reader(stream)),
                [["Location", "String"], ["0x1", "go"], ["0x4", "xxALPHAxx"], ["0x17", "Go!"]],
            )
        self.assertEqual(self.original.read_bytes(), binary_data)
        self.assertFalse(self.modified.exists())

    def test_source_modification_only_changes_selected_bytes(self):
        source = self.write_source()
        before_source = source.read_bytes()

        result = self.run_cli("-s", str(source), "-e", "--single-char", "X")

        self.assert_success(result)
        self.assertEqual(self.modified.read_bytes(), b"\x00XX44!?.\x00\xffKeep-9\x00")
        self.assertEqual(self.original.read_bytes(), ORIGINAL)
        self.assertEqual(source.read_bytes(), before_source)

    def test_roundtrip_csv_extraction_then_modification(self):
        self.assert_success(self.run_cli("-l", "4", "-o", "strings.csv"))
        source = self.directory / "strings.csv"
        before_source = source.read_bytes()

        result = self.run_cli("-s", str(source), "-e", "--single-char", "X")

        self.assert_success(result)
        self.assertEqual(self.modified.read_bytes(), b"\x00XX44!?.\x00\xffXXXX-4\x00")
        self.assertEqual(self.original.read_bytes(), ORIGINAL)
        self.assertEqual(source.read_bytes(), before_source)

    def test_empty_single_char_preserves_random_mode_and_byte_layout(self):
        source = self.write_source()

        result = self.run_cli("-s", str(source), "-e", "--single-char", "")

        self.assert_success(result)
        modified = self.modified.read_bytes()
        self.assertEqual(len(modified), len(ORIGINAL))
        self.assertEqual(modified[:1], b"\x00")
        self.assertTrue(all(byte in b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ" for byte in modified[1:3]))
        self.assertTrue(all(byte in b"0123456789" for byte in modified[3:5]))
        self.assertEqual(modified[5:], b"!?.\x00\xffKeep-9\x00")
        self.assertEqual(self.original.read_bytes(), ORIGINAL)

    def test_printable_single_char_boundaries_are_accepted(self):
        source = self.write_source()
        for index, (character, expected) in enumerate([
            (" ", b"\x00  44!?.\x00\xffKeep-9\x00"),
            ("~", b"\x00~~44!?.\x00\xffKeep-9\x00"),
        ]):
            with self.subTest(character=character):
                original = self.directory / f"case{index}.bin"
                original.write_bytes(ORIGINAL)
                result = self.run_cli("-s", str(source), "-e", "--single-char", character, filename=original)

                self.assert_success(result)
                self.assertEqual((self.directory / f"case{index}_modified.bin").read_bytes(), expected)
                self.assertEqual(original.read_bytes(), ORIGINAL)

    def test_extraction_preview_does_not_create_any_files(self):
        before = self.snapshot()

        result = self.run_cli("-t", "-l", "4", "-o", "preview.csv", "--single-char", "X")

        self.assertEqual(self.snapshot(), before)
        self.assert_success(result)
        self.assertIn("XX44!?.", result.stdout)

    def test_extraction_preview_preserves_existing_outputs(self):
        (self.directory / "output.csv").write_bytes(b"existing CSV\n")
        (self.directory / ".tmpfile").write_bytes(b"existing temporary file\x00")
        self.modified.write_bytes(b"existing result\xff")
        before = self.snapshot()

        result = self.run_cli("-t", "-l", "4", "--single-char", "X")

        self.assertEqual(self.snapshot(), before)
        self.assert_success(result)

    def test_source_preview_preserves_existing_outputs(self):
        source = self.write_source()
        (self.directory / "output.csv").write_bytes(b"existing CSV\n")
        (self.directory / ".tmpfile").write_bytes(b"existing temporary file\x00")
        self.modified.write_bytes(b"existing result\xff")
        before = self.snapshot()

        result = self.run_cli("-s", str(source), "-t", "--single-char", "X")

        self.assertEqual(self.snapshot(), before)
        self.assert_success(result)
        self.assertIn("XX44!?.", result.stdout)

    def test_execute_and_preview_are_mutually_exclusive(self):
        before = self.snapshot()

        result = self.run_cli("-e", "-t", "--single-char", "X")

        self.assert_rejected_without_writes(result, before)

    def test_nonpositive_length_is_rejected_before_writes(self):
        for index, length in enumerate(("0", "-1")):
            with self.subTest(length=length):
                directory = self.directory / f"case{index}"
                directory.mkdir()
                original = directory / "fixture.bin"
                original.write_bytes(ORIGINAL)
                before = self.snapshot(directory)

                result = self.run_cli("--length", length, "-e", filename=original, cwd=directory)

                self.assert_rejected_without_writes(result, before, directory)

    def test_empty_keyword_elements_are_rejected_before_writes(self):
        for index, keywords in enumerate(("", ",Ab", "Ab,", "Ab,,Keep", "Ab,  ,Keep", "  ")):
            with self.subTest(keywords=keywords):
                directory = self.directory / f"case{index}"
                directory.mkdir()
                original = directory / "fixture.bin"
                original.write_bytes(ORIGINAL)
                before = self.snapshot(directory)

                result = self.run_cli("--keywords", keywords, "-e", filename=original, cwd=directory)

                self.assert_rejected_without_writes(result, before, directory)

    def test_sourcefile_requires_execute_or_preview(self):
        source = self.write_source()
        before = self.snapshot()

        result = self.run_cli("--sourcefile", str(source))

        self.assert_rejected_without_writes(result, before)

    def test_extraction_refuses_existing_csv_output(self):
        (self.directory / "output.csv").write_bytes(b"do not overwrite\x00\xff")
        before = self.snapshot()

        result = self.run_cli("-e", "--single-char", "X")

        self.assert_rejected_without_writes(result, before)

    def test_extraction_refuses_output_aliasing_input(self):
        for output in (str(self.original), "./fixture.bin"):
            with self.subTest(output=output):
                self.original.write_bytes(ORIGINAL)
                before = self.snapshot()

                result = self.run_cli("-o", output)

                self.assert_rejected_without_writes(result, before)

    def test_extraction_refuses_hardlink_output_aliasing_input(self):
        alias = self.directory / "alias.csv"
        try:
            os.link(self.original, alias)
        except OSError as error:
            self.skipTest(f"Hard links unavailable: {error}")
        before = self.snapshot()

        result = self.run_cli("-o", str(alias))

        self.assert_rejected_without_writes(result, before)

    def test_source_modification_refuses_existing_generated_output(self):
        source = self.write_source()
        self.modified.write_bytes(b"existing result\x00\xff")
        before = self.snapshot()

        result = self.run_cli("-s", str(source), "-e", "--single-char", "X")

        self.assert_rejected_without_writes(result, before)

    def test_extraction_modification_preflights_existing_generated_output(self):
        self.modified.write_bytes(b"existing result\x00\xff")
        before = self.snapshot()

        result = self.run_cli("-e", "--single-char", "X")

        self.assert_rejected_without_writes(result, before)

    def test_csv_and_binary_cannot_share_planned_destination(self):
        before = self.snapshot()

        result = self.run_cli("-e", "-o", str(self.modified), "--single-char", "X")

        self.assert_rejected_without_writes(result, before)

    def test_invalid_single_char_is_rejected_before_writes(self):
        for index, character in enumerate(("XY", "é", "\t", "\n", "\x7f")):
            with self.subTest(character=repr(character)):
                directory = self.directory / f"case{index}"
                directory.mkdir()
                original = directory / "fixture.bin"
                original.write_bytes(ORIGINAL)
                before = self.snapshot(directory)

                result = self.run_cli("-e", "--single-char", character, filename=original, cwd=directory)

                self.assert_rejected_without_writes(result, before, directory)

    def test_malformed_csv_is_rejected_without_partial_output(self):
        malformed = {
            "empty file": "",
            "wrong header": "Offset,String\n0x1,Ab12!?.\n",
            "reversed header": "String,Location\nAb12!?.,0x1\n",
            "header missing column": "Location\n0x1,Ab12!?.\n",
            "header extra column": "Location,String,Other\n0x1,Ab12!?.,unused\n",
            "header wrong case": "location,String\n0x1,Ab12!?.\n",
            "missing column": "Location,String\n0x1\n",
            "extra column": "Location,String\n0x1,Ab12!?.,unused\n",
            "invalid hex": "Location,String\n0xZZ,Ab12!?.\n",
            "negative offset": "Location,String\n-0x1,Ab12!?.\n",
            "noninteger offset": "Location,String\n1.5,Ab12!?.\n",
            "empty string": "Location,String\n0x1,\n",
            "non-ASCII string": "Location,String\n0x1,é\n",
            "duplicate offset": "Location,String\n0x1,Ab\n0x1,Ab\n",
            "overlapping ranges": "Location,String\n0x1,Ab12!?.\n0x2,b12\n",
            "offset at end": "Location,String\n0x11,A\n",
            "range past end": "Location,String\n0xf,9long\n",
            "original bytes mismatch": "Location,String\n0x1,Wrong!?\n",
            "valid row then mismatch": "Location,String\n0x1,Ab12!?.\n0xa,Wrong!\n",
            "blank row": "Location,String\n\n",
            "unterminated quote": "Location,String\n0x1,\"Ab12!?.\n",
        }
        for index, (case, contents) in enumerate(malformed.items()):
            with self.subTest(case=case):
                directory = self.directory / f"case{index}"
                directory.mkdir()
                original = directory / "fixture.bin"
                original.write_bytes(ORIGINAL)
                source = self.write_source(contents, directory)
                before = self.snapshot(directory)

                result = self.run_cli("-s", str(source), "-e", "--single-char", "X", filename=original, cwd=directory)

                self.assert_rejected_without_writes(result, before, directory)

    def test_invalid_csv_preserves_existing_generated_output(self):
        source = self.write_source("Location,String\n0x1,Wrong!?\n")
        self.modified.write_bytes(b"existing result\x00\xff")
        before = self.snapshot()

        result = self.run_cli("-s", str(source), "-e", "--single-char", "X")

        self.assert_rejected_without_writes(result, before)

    def test_invalid_csv_preview_is_rejected_without_writes(self):
        source = self.write_source("Location,String\n0x1,Wrong!?\n")
        (self.directory / ".tmpfile").write_bytes(b"existing temporary file\x00")
        (self.directory / "output.csv").write_bytes(b"existing CSV\n")
        before = self.snapshot()

        result = self.run_cli("-s", str(source), "-t", "--single-char", "X")

        self.assert_rejected_without_writes(result, before)

    def test_missing_input_is_a_clean_error(self):
        before = self.snapshot()

        result = self.run_cli("-e", filename=self.directory / "missing.bin")

        self.assert_rejected_without_writes(result, before)

    def test_missing_source_is_a_clean_error(self):
        before = self.snapshot()

        result = self.run_cli("-s", "missing.csv", "-e")

        self.assert_rejected_without_writes(result, before)

    def test_missing_output_directory_is_a_clean_error(self):
        before = self.snapshot()

        result = self.run_cli("-o", "missing/strings.csv")

        self.assert_rejected_without_writes(result, before)

    def test_empty_binary_extracts_header_only(self):
        self.original.write_bytes(b"")

        result = self.run_cli()

        self.assert_success(result)
        with (self.directory / "output.csv").open(newline="", encoding="utf-8") as stream:
            self.assertEqual(list(csv.reader(stream)), [["Location", "String"]])
        self.assertEqual(self.original.read_bytes(), b"")

    def test_header_only_csv_copies_binary_unchanged(self):
        source = self.write_source("Location,String\n")

        result = self.run_cli("-s", str(source), "-e")

        self.assert_success(result)
        self.assertEqual(self.modified.read_bytes(), ORIGINAL)
        self.assertEqual(self.original.read_bytes(), ORIGINAL)


if __name__ == "__main__":
    unittest.main()

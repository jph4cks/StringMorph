"""Exercise publication failures with inert data and real temporary files."""

import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import StringMorph


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.sentinel = self.directory / 'original.bin'
        self.sentinel.write_bytes(b'unchanged input')
        self.first = self.directory / 'first.bin'
        self.second = self.directory / 'second.csv'
        self.outputs = [(self.first, b'new data', None), (self.second, b'report', None)]

    def snapshot(self):
        return {path.name: path.read_bytes() for path in self.directory.iterdir()}

    def test_flush_failure_removes_staging_files_and_publishes_nothing(self):
        before = self.snapshot()
        with patch.object(StringMorph.os, 'fsync', side_effect=OSError('simulated flush failure')):
            with self.assertRaises(OSError):
                StringMorph.publish_new_files(self.outputs)
        self.assertEqual(self.snapshot(), before)

    def test_second_publication_failure_rolls_back_first_output(self):
        before = self.snapshot()
        actual_link = os.link

        def link_or_fail(source, destination):
            if destination == self.second:
                raise OSError('simulated publication failure')
            actual_link(source, destination)

        with patch.object(StringMorph.os, 'link', side_effect=link_or_fail):
            with self.assertRaises(OSError):
                StringMorph.publish_new_files(self.outputs)
        self.assertEqual(self.snapshot(), before)

    def test_destination_created_after_preflight_is_preserved(self):
        actual_link = os.link

        def link_after_competing_write(source, destination):
            destination.write_bytes(b'other operation output')
            actual_link(source, destination)

        with patch.object(StringMorph.os, 'link', side_effect=link_after_competing_write):
            with self.assertRaises(FileExistsError):
                StringMorph.publish_new_files(self.outputs[:1])
        self.assertEqual(self.snapshot(), {
            'original.bin': b'unchanged input',
            'first.bin': b'other operation output',
        })

    def test_identical_planned_outputs_are_rejected_without_writes(self):
        before = self.snapshot()
        outputs = [(self.first, b'binary data', None), (self.first, b'CSV data', None)]
        with self.assertRaises(ValueError):
            StringMorph.publish_new_files(outputs)
        self.assertEqual(self.snapshot(), before)

    def test_temporary_cleanup_failure_reports_saved_outputs_and_continues(self):
        actual_unlink = os.unlink
        retained = []

        def fail_first_temporary_unlink(path, *args, **kwargs):
            if Path(path).name.startswith('.stringmorph-') and not retained:
                retained.append(Path(path))
                raise PermissionError('simulated temporary cleanup failure')
            return actual_unlink(path, *args, **kwargs)

        diagnostics = io.StringIO()
        with contextlib.redirect_stderr(diagnostics):
            with patch.object(StringMorph.os, 'unlink', side_effect=fail_first_temporary_unlink):
                StringMorph.publish_new_files(self.outputs)
        self.assertEqual(self.first.read_bytes(), b'new data')
        self.assertEqual(self.second.read_bytes(), b'report')
        self.assertEqual(list(self.directory.glob('.stringmorph-*.tmp')), retained)
        self.assertIn(str(retained[0]), diagnostics.getvalue())
        self.assertIn('saved', diagnostics.getvalue().lower())

    def test_rollback_cleanup_failure_keeps_primary_error_and_continues(self):
        third = self.directory / 'third.csv'
        outputs = self.outputs + [(third, b'third report', None)]
        actual_link, actual_unlink = os.link, os.unlink
        publication_error = OSError('simulated primary publication failure')

        def fail_last_publication(source, destination):
            if destination == third:
                raise publication_error
            actual_link(source, destination)

        def fail_one_rollback(path, *args, **kwargs):
            if path == self.second:
                raise PermissionError('simulated rollback failure')
            return actual_unlink(path, *args, **kwargs)

        diagnostics = io.StringIO()
        with contextlib.redirect_stderr(diagnostics):
            with patch.object(StringMorph.os, 'link', side_effect=fail_last_publication):
                with patch.object(StringMorph.os, 'unlink', side_effect=fail_one_rollback):
                    with self.assertRaises(OSError) as raised:
                        StringMorph.publish_new_files(outputs)
        self.assertIs(raised.exception, publication_error)
        self.assertEqual(self.snapshot(), {
            'original.bin': b'unchanged input',
            'second.csv': b'report',
        })
        self.assertIn(str(self.second), diagnostics.getvalue())

    def test_print_hash_reports_real_hash_and_propagates_missing_file(self):
        self.sentinel.write_bytes(b'abc')
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            StringMorph.print_hash(self.sentinel)
        self.assertIn('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad', output.getvalue())
        with self.assertRaises(FileNotFoundError):
            StringMorph.get_sha256_hash(self.directory / 'missing.bin')


if __name__ == '__main__':
    unittest.main()

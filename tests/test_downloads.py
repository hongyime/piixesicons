"""Offline regression cases; only disposable output directories are modified."""
import builtins
from contextlib import redirect_stdout
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import io
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests
import scrape_images as scraper


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.completed = self.root / "completed_downloads.txt"
        self.payload = b"synthetic image bytes for offline filesystem tests"
        self.seen = set()
        self.get = self.enterContext(patch.object(
            scraper.session, "get", return_value=Mock(status_code=200, content=self.payload)
        ))
        self.enterContext(patch.object(
            requests.sessions.Session, "request", side_effect=AssertionError("Network is forbidden")
        ))
        self.enterContext(patch.object(scraper.time, "sleep"))

    def image(self, slug):
        return self.root / "piixes.com" / "api" / "icon" / "512" / (slug + ".png")

    def download(self, slug="sample"):
        return scraper.download(slug, str(self.root), self.seen, str(self.completed))

    def test_write_failure_retries_without_false_deduplication(self):
        real_open = builtins.open
        failed = False

        def fail_first_binary_write(path, mode="r", *args, **kwargs):
            nonlocal failed
            if "b" in mode and ("w" in mode or "x" in mode) and not failed:
                failed = True
                raise OSError("simulated full disk")
            return real_open(path, mode, *args, **kwargs)

        with patch("builtins.open", side_effect=fail_first_binary_write):
            result = self.download()
        self.assertTrue(failed)
        self.assertEqual(result, "ok")
        self.assertEqual(self.image("sample").read_bytes(), self.payload)
        self.assertEqual(self.completed.read_text().splitlines(), ["sample"])
        self.assertEqual(self.seen, {scraper.sha256(self.payload)})
        self.assertEqual(self.get.call_count, 1)

    def test_persistent_write_failure_records_no_success(self):
        real_open = builtins.open

        def fail_binary_write(path, mode="r", *args, **kwargs):
            if "b" in mode and ("w" in mode or "x" in mode):
                raise OSError("simulated full disk")
            return real_open(path, mode, *args, **kwargs)

        with patch("builtins.open", side_effect=fail_binary_write):
            self.assertEqual(self.download(), "fail")
        self.assertFalse(self.image("sample").exists())
        self.assertFalse(self.completed.exists())
        self.assertEqual(self.seen, set())

    def test_duplicate_content_keeps_both_slug_files_and_completions(self):
        self.assertEqual(self.download("first"), "ok")
        self.assertEqual(self.download("second"), "dedup")
        self.assertEqual(self.image("first").read_bytes(), self.payload)
        self.assertEqual(self.image("second").read_bytes(), self.payload)
        self.assertEqual(set(self.completed.read_text().splitlines()), {"first", "second"})
        self.assertEqual(len(self.seen), 1)

    def test_checkpoint_failure_preserves_image_and_retries_without_refetch(self):
        previous = b"older\nolder\n\n"
        self.completed.write_bytes(previous)
        real_replace = scraper.os.replace
        failed = False

        def fail_first_checkpoint(source, destination):
            nonlocal failed
            if Path(destination) == self.completed and not failed:
                failed = True
                self.assertEqual(self.image("sample").read_bytes(), self.payload)
                self.assertEqual(self.seen, set())
                raise OSError("simulated checkpoint publish failure")
            return real_replace(source, destination)

        with patch.object(scraper.os, "replace", side_effect=fail_first_checkpoint):
            self.assertEqual(self.download(), "ok")
        self.assertTrue(failed)
        self.assertEqual(self.completed.read_bytes(), previous + b"sample\n")
        self.assertEqual(self.get.call_count, 1)

    def test_persistent_checkpoint_failure_keeps_existing_checkpoint_bytes(self):
        previous = b"older\nolder\n"
        self.completed.write_bytes(previous)
        real_replace = scraper.os.replace

        def reject_checkpoint(source, destination):
            if Path(destination) == self.completed:
                raise OSError("checkpoint unavailable")
            return real_replace(source, destination)

        with patch.object(scraper.os, "replace", side_effect=reject_checkpoint):
            self.assertEqual(self.download(), "fail")
        self.assertEqual(self.completed.read_bytes(), previous)
        self.assertEqual(self.image("sample").read_bytes(), self.payload)
        self.assertEqual(self.seen, set())
        self.assertEqual(self.get.call_count, 1)

    def test_existing_matching_file_repairs_missing_completion(self):
        path = self.image("sample")
        path.parent.mkdir(parents=True)
        path.write_bytes(self.payload)
        self.assertEqual(self.download(), "skip")
        self.assertEqual(self.completed.read_text(), "sample\n")
        self.assertEqual(path.read_bytes(), self.payload)

    def test_existing_different_bytes_are_never_overwritten_or_marked_complete(self):
        path = self.image("sample")
        path.parent.mkdir(parents=True)
        path.write_bytes(b"preserve this earlier or interrupted version")
        self.assertEqual(self.download(), "fail")
        self.assertEqual(path.read_bytes(), b"preserve this earlier or interrupted version")
        self.assertFalse(self.completed.exists())
        self.assertEqual(self.seen, set())
        self.assertEqual(self.get.call_count, 1)

    def test_concurrent_identical_responses_retain_every_slug(self):
        slugs = ["icon-" + str(index) for index in range(16)]
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(self.download, slugs))
        self.assertEqual(results.count("ok"), 1)
        self.assertEqual(results.count("dedup"), 15)
        self.assertEqual(set(self.completed.read_text().splitlines()), set(slugs))
        self.assertTrue(all(self.image(slug).read_bytes() == self.payload for slug in slugs))

    def test_http_failure_retries_with_existing_request_limit(self):
        self.get.side_effect = [Mock(status_code=503), Mock(status_code=200, content=self.payload)]
        self.assertEqual(self.download(), "ok")
        self.assertEqual(self.get.call_count, 2)

    def test_persistent_http_failure_creates_no_success(self):
        self.get.return_value = Mock(status_code=503)
        self.assertEqual(self.download(), "fail")
        self.assertEqual(self.get.call_count, scraper.MAX_RETRIES)
        self.assertEqual(self.seen, set())
        self.assertFalse(self.completed.exists())

    def test_duplicate_call_does_not_duplicate_checkpoint_line(self):
        self.assertEqual(self.download(), "ok")
        self.assertEqual(self.download(), "skip")
        self.assertEqual(self.completed.read_text(), "sample\n")

    def test_invalid_slug_cannot_escape_output_directory(self):
        self.assertEqual(self.download("../../escape"), "fail")
        self.get.assert_not_called()
        self.assertFalse(self.completed.exists())

    def test_image_publish_failure_leaves_no_final_image_or_completion(self):
        with patch.object(scraper.os, "replace", side_effect=OSError("rename failed")):
            self.assertEqual(self.download(), "fail")
        self.assertFalse(self.image("sample").exists())
        self.assertFalse(self.completed.exists())
        self.assertEqual(self.seen, set())

    def test_flush_failure_cannot_publish_an_image(self):
        with patch.object(scraper.os, "fsync", side_effect=OSError("flush failed")):
            self.assertEqual(self.download(), "fail")
        self.assertFalse(self.image("sample").exists())
        self.assertFalse(self.completed.exists())
        self.assertEqual(self.seen, set())

    def test_checkpoint_without_trailing_newline_keeps_its_original_bytes(self):
        self.completed.write_bytes(b"older-entry")
        self.assertEqual(self.download(), "ok")
        self.assertEqual(self.completed.read_bytes(), b"older-entry\nsample\n")

    def run_main(self):
        with patch.object(scraper, "scrape_slugs", return_value=["sample"]), \
                patch("builtins.input", side_effect=[str(self.root), "offline-fixture"]), \
                patch.object(scraper, "tqdm", side_effect=lambda values, **kwargs: values), \
                redirect_stdout(io.StringIO()):
            scraper.main()

    def test_main_recovers_a_completed_entry_whose_image_is_missing(self):
        self.completed.write_bytes(b"sample\n")
        self.run_main()
        self.assertEqual(self.image("sample").read_bytes(), self.payload)
        self.assertEqual(self.completed.read_bytes(), b"sample\n")
        self.assertEqual(self.get.call_count, 1)

    def test_main_leaves_existing_completed_outputs_untouched(self):
        path = self.image("sample")
        path.parent.mkdir(parents=True)
        path.write_bytes(self.payload)
        self.completed.write_bytes(b"sample\n")
        self.run_main()
        self.assertEqual(path.read_bytes(), self.payload)
        self.assertEqual(self.completed.read_bytes(), b"sample\n")
        self.get.assert_not_called()


if __name__ == "__main__":
    unittest.main()

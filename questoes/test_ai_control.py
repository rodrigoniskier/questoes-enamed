"""Quota/idempotency tests use a private temporary ledger and a mocked provider."""

import multiprocessing
import sqlite3
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from .ai_control import AIControlError, admit, finish, generate_text, subject_key


def process_admission(args):
    try:
        admit(*args)
        return 200
    except AIControlError as error:
        return error.status


class AIControlTests(SimpleTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = str(Path(self.directory.name) / "ledger.sqlite3")
        self.config = override_settings(
            AI_CONTROL_DATABASE=self.path,
            GEMINI_API_KEY="synthetic-only",
            AI_GLOBAL_PER_MINUTE=100,
            AI_GLOBAL_PER_DAY=100,
            AI_SUBJECT_PER_MINUTE=100,
            AI_MAX_CONCURRENT=2,
        )
        self.config.enable()
        self.addCleanup(self.config.disable)

    def test_replay_recovers_response_without_new_charge(self):
        key = str(uuid.uuid4())
        self.assertIsNone(admit(key, "test", {"prompt": "synthetic"}))
        finish(key, "draft")
        self.assertEqual(admit(key, "test", {"prompt": "synthetic"}), "draft")
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("select count(*) from attempts").fetchone()[0], 1)
        self.assertEqual(Path(self.path).stat().st_mode & 0o777, 0o600)

    def test_pending_failed_and_changed_requests_never_repeat(self):
        key = str(uuid.uuid4())
        admit(key, "test", {})
        for subject, payload in [("test", {}), ("other", {}), ("test", {"changed": True})]:
            with self.assertRaises(AIControlError) as error:
                admit(key, subject, payload)
            self.assertEqual(error.exception.status, 409)
        finish(key)
        with self.assertRaises(AIControlError):
            admit(key, "test", {})

    @override_settings(AI_GLOBAL_PER_DAY=1)
    def test_global_daily_limit_survives_new_connection_and_subject(self):
        key = str(uuid.uuid4())
        admit(key, "a", {})
        finish(key)
        with self.assertRaises(AIControlError) as error:
            admit(str(uuid.uuid4()), "b", {})
        self.assertEqual(error.exception.status, 429)
        self.assertGreater(error.exception.retry_after, 0)

    @override_settings(AI_GLOBAL_PER_MINUTE=1)
    def test_rolling_global_minute(self):
        admit(str(uuid.uuid4()), "a", {})
        with self.assertRaises(AIControlError) as error:
            admit(str(uuid.uuid4()), "b", {})
        self.assertEqual(error.exception.status, 429)

    @override_settings(AI_SUBJECT_PER_MINUTE=1)
    def test_subject_limit(self):
        key = str(uuid.uuid4())
        admit(key, "a", {})
        finish(key)
        with self.assertRaises(AIControlError):
            admit(str(uuid.uuid4()), "a", {})
        self.assertIsNone(admit(str(uuid.uuid4()), "b", {}))

    def test_concurrent_threads_share_limit(self):
        with ThreadPoolExecutor(max_workers=5) as pool:
            statuses = list(pool.map(process_admission, [(str(uuid.uuid4()), str(i), {}) for i in range(5)]))
        self.assertEqual(statuses.count(200), 2)
        self.assertEqual(statuses.count(429), 3)

    def test_multiple_processes_share_limit(self):
        # Independent worker processes access one ledger, matching PythonAnywhere's topology.
        with multiprocessing.get_context("fork").Pool(4) as pool:
            statuses = pool.map(process_admission, [(str(uuid.uuid4()), str(i), {}) for i in range(4)])
        self.assertEqual(statuses.count(200), 2)
        self.assertEqual(statuses.count(429), 2)

    def test_unavailable_control_fails_closed(self):
        with patch("questoes.ai_control.connect", side_effect=sqlite3.OperationalError("synthetic")):
            with self.assertRaises(AIControlError) as error:
                admit("test", "a", {})
        self.assertEqual(error.exception.status, 503)

    @patch("google.genai.Client")
    def test_sdk_timeout_no_retries_output_limit_and_replay(self, client):
        client.return_value.__enter__.return_value.models.generate_content.return_value = Mock(
            text="synthetic"
        )
        key = str(uuid.uuid4())
        self.assertEqual(generate_text("test", request_id=key), "synthetic")
        self.assertEqual(generate_text("test", request_id=key), "synthetic")
        client.assert_called_once()
        options = client.call_args.kwargs["http_options"]
        self.assertEqual(options.retry_options.attempts, 1)
        self.assertEqual(options.timeout, 60000)
        generator = client.return_value.__enter__.return_value.models.generate_content
        self.assertEqual(generator.call_args.kwargs["config"].max_output_tokens, 4096)

    @patch("google.genai.Client")
    def test_provider_quota_is_429_without_retry(self, client):
        from google.genai.errors import ClientError

        client.return_value.__enter__.return_value.models.generate_content.side_effect = ClientError(
            429, {"error": {"message": "synthetic"}}
        )
        key = str(uuid.uuid4())
        with self.assertRaises(AIControlError) as error:
            generate_text("test", request_id=key)
        self.assertEqual(error.exception.status, 429)
        with self.assertRaises(AIControlError) as replay:
            generate_text("test", request_id=key)
        self.assertEqual(replay.exception.status, 409)
        client.assert_called_once()

    def test_subject_hash_contains_no_raw_identifier(self):
        self.assertNotIn("192.0.2.1", subject_key("192.0.2.1"))

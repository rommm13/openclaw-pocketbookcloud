"""Public release-boundary tests. No live API calls."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_pocketbook import pb

SPEC = importlib.util.spec_from_file_location(
    "release_check", Path(__file__).resolve().parents[1] / "tools" / "release_check.py"
)
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


class PublicAuthenticationTests(unittest.TestCase):
    def test_cli_login_uses_email_and_hidden_password(self):
        output = io.StringIO()
        with patch("builtins.input", return_value="user@example.invalid") as email_prompt, \
                patch.object(pb.getpass, "getpass", return_value="test-password") as password_prompt, \
                patch.object(pb.PocketBookClient, "login",
                             return_value={"authenticated": True, "email": "user@example.invalid"}) as login, \
                contextlib.redirect_stdout(output):
            self.assertEqual(pb.main(["auth", "login"]), 0)

        email_prompt.assert_called_once()
        password_prompt.assert_called_once()
        login.assert_called_once_with("user@example.invalid", "test-password", None)
        rendered = output.getvalue()
        self.assertNotIn("test-password", rendered)
        self.assertNotIn(pb.PUBLIC_CLIENT_SECRET, rendered)

    def test_vendor_browser_values_are_explicitly_allowlisted_by_fingerprint(self):
        self.assertTrue(release.safe_credential_literal("public_client_id", pb.PUBLIC_CLIENT_ID))
        self.assertTrue(release.safe_credential_literal("public_client_secret", pb.PUBLIC_CLIENT_SECRET))
        self.assertFalse(release.safe_credential_literal("client_secret", "unexpected-private-value"))


class ReleaseGateTests(unittest.TestCase):
    def test_private_key_and_real_token_artifacts_are_rejected(self):
        key = b"-----BEGIN RSA " + b"PRIVATE KEY-----"
        self.assertIn("private key", release.content_issues(key))
        token_key = "access_" + "token"
        token_value = "unexpected-" + "private-value"
        token = json.dumps({token_key: token_value}).encode()
        self.assertIn("credential-like JSON", release.content_issues(token))

    def test_runtime_state_is_rejected(self):
        pending = json.dumps({
            "account_fingerprint": "synthetic",
            "expires_at": 1,
            "provider_alias": "pocketbook",
            "shop_id": "1",
        }).encode()
        self.assertIn("PocketBook runtime state", release.content_issues(pending))

    def test_synthetic_fixture_values_are_allowed(self):
        data = json.dumps({
            "access_token": "fixture-access-token",
            "refresh_token": "fixture-refresh-token",
        }).encode()
        self.assertFalse(release.content_issues(data))

    def test_signed_url_is_rejected(self):
        data = ("https://cdn." + "example.net/book?" + "signature=" + "unexpected-" + "private-value").encode()
        self.assertIn("signed URL", release.content_issues(data))

    def test_ignored_runtime_and_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            subprocess.run(["git", "init", "-q", td], check=True)
            (root / ".gitignore").write_text("session.json\n", encoding="utf-8")
            (root / "session.json").write_text("{}", encoding="utf-8")
            (root / "README.md").symlink_to(root / "session.json")
            self.assertTrue(release.check(root))

    def test_unexpected_tracked_bytecode_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            subprocess.run(["git", "init", "-q", td], check=True)
            cache = root / "__pycache__"
            cache.mkdir()
            (cache / "bad.pyc").write_bytes(b"bad")
            subprocess.run(["git", "-C", td, "add", "-f", "__pycache__/bad.pyc"], check=True)
            self.assertTrue(release.check(root))


if __name__ == "__main__":
    unittest.main()

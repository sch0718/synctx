import base64
import os

import pytest

from synctx_common.auth import basic_auth_header, git_token_auth_header, read_secret


class TestReadSecret:
    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("TEST_SECRET", "my-secret")
        assert read_secret(credential_env="TEST_SECRET") == "my-secret"

    def test_from_env_stripped(self, monkeypatch):
        monkeypatch.setenv("TEST_SECRET", "  spaced  \n")
        assert read_secret(credential_env="TEST_SECRET") == "spaced"

    def test_from_file(self, tmp_path):
        secret_file = tmp_path / "secret.txt"
        secret_file.write_text("file-secret\n")
        assert read_secret(credential_file=str(secret_file)) == "file-secret"

    def test_env_takes_precedence(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TEST_SECRET", "env-value")
        secret_file = tmp_path / "secret.txt"
        secret_file.write_text("file-value")
        assert read_secret(credential_env="TEST_SECRET", credential_file=str(secret_file)) == "env-value"

    def test_empty_env_falls_through_to_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TEST_SECRET", "")
        secret_file = tmp_path / "secret.txt"
        secret_file.write_text("file-value")
        assert read_secret(credential_env="TEST_SECRET", credential_file=str(secret_file)) == "file-value"

    def test_missing_both_returns_empty(self, monkeypatch):
        monkeypatch.delenv("NONEXISTENT_SECRET", raising=False)
        assert read_secret() == ""

    def test_missing_env_returns_empty(self, monkeypatch):
        monkeypatch.delenv("NONEXISTENT_SECRET", raising=False)
        assert read_secret(credential_env="NONEXISTENT_SECRET") == ""


class TestBasicAuthHeader:
    def test_format(self):
        header = basic_auth_header("user", "pass")
        token = base64.b64encode(b"user:pass").decode("ascii")
        assert header == f"Authorization: Basic {token}"

    def test_special_characters(self):
        header = basic_auth_header("user@domain", "p@ss:word!")
        decoded = base64.b64decode(header.split(" ")[2]).decode("utf-8")
        assert decoded == "user@domain:p@ss:word!"


class TestGitTokenAuthHeader:
    def test_uses_x_access_token(self):
        header = git_token_auth_header("ghp_abc123")
        decoded = base64.b64decode(header.split(" ")[2]).decode("utf-8")
        assert decoded == "x-access-token:ghp_abc123"

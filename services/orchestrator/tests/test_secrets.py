import pytest

from orchestrator.secrets import EnvSecretsProvider, SecretNotFoundError


def test_env_secrets_provider_reads_existing_var(monkeypatch):
    monkeypatch.setenv("DEVOPS_AGENT_TEST_SECRET", "shh")
    assert EnvSecretsProvider().get("DEVOPS_AGENT_TEST_SECRET") == "shh"


def test_env_secrets_provider_raises_on_missing_var(monkeypatch):
    monkeypatch.delenv("DEVOPS_AGENT_TEST_SECRET_MISSING", raising=False)
    with pytest.raises(SecretNotFoundError):
        EnvSecretsProvider().get("DEVOPS_AGENT_TEST_SECRET_MISSING")


def test_env_secrets_provider_raises_on_empty_var(monkeypatch):
    monkeypatch.setenv("DEVOPS_AGENT_TEST_SECRET_EMPTY", "")
    with pytest.raises(SecretNotFoundError):
        EnvSecretsProvider().get("DEVOPS_AGENT_TEST_SECRET_EMPTY")

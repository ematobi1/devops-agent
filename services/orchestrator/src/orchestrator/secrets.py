import os
from typing import Protocol


class SecretNotFoundError(KeyError):
    pass


class SecretsProvider(Protocol):
    def get(self, name: str) -> str: ...


class EnvSecretsProvider:
    """Local-dev secrets provider. Reads from environment variables only —
    never logs a value, never writes one back. Production deployments swap
    this for a provider backed by Vault/AWS Secrets Manager/GCP Secret
    Manager behind the same SecretsProvider interface.
    """

    def get(self, name: str) -> str:
        value = os.environ.get(name)
        if not value:
            raise SecretNotFoundError(name)
        return value

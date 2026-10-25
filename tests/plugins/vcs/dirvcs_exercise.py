import tempfile
from pathlib import Path

from kora.plugins.vcs.fake import DirVcs
from kora.protocols.common import ProtocolContext
from kora.protocols.vcs import CredentialRequest
from tests.plugins.vcs.dirvcs_support import configuration, empty_store, seeded_store


async def dirvcs_exercise(ctx: ProtocolContext, marker: str) -> object:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        remote, _ = seeded_store(root)
        gone = root / "gone.dirvcs"
        empty_store(gone)
        provider = DirVcs(configuration(remote, gone))
        request = CredentialRequest(
            binding="local-binding",
            binding_revision=1,
            credential_mode="local_credential_free",
            repo=f"/srv/{marker}",
            purpose="checkout",
            attempt_id="attempt",
            operation_id=None,
            prepared_digest=None,
            destination_digest=None,
        )
        return await provider.acquire_credential(request, ctx)

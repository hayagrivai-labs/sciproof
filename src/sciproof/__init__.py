"""SciProof: record computational experiments and create verifiable evidence for them.

SciProof checks *integrity* and *execution reproducibility* of recorded runs. It does not,

and cannot, establish that a scientific result is correct. See docs/CONCEPTS.md.

The names in ``__all__`` are the supported Python API (docs/PYTHON_API.md). Everything else in

the package is internal and may change in any release.

"""

__version__ = "0.7.0"

# The imports follow ``__version__`` on purpose: ``capture`` reads it while it is being imported.

from sciproof.capture import CaptureResult  # noqa: E402
from sciproof.capture import capture as capture_experiment  # noqa: E402
from sciproof.diffmanifests import DiffReport, diff_manifests  # noqa: E402
from sciproof.errors import (  # noqa: E402
    CaptureError,
    ConfirmationRequired,
    ManifestError,
    SciProofError,
    UsageError,
)
from sciproof.export import to_dsse as export_dsse  # noqa: E402
from sciproof.export import to_in_toto as export_in_toto  # noqa: E402
from sciproof.export import to_openlineage as export_openlineage  # noqa: E402
from sciproof.export import to_ro_crate as export_ro_crate  # noqa: E402
from sciproof.ledger import list_manifests  # noqa: E402
from sciproof.manifest import load_manifest  # noqa: E402
from sciproof.pipeline import verify_pipeline  # noqa: E402
from sciproof.policy import PolicyError  # noqa: E402
from sciproof.signing import SignatureError, sign_manifest  # noqa: E402
from sciproof.typst import to_typst as export_typst  # noqa: E402
from sciproof.update import UpdateResult, update_baseline  # noqa: E402
from sciproof.verify import Check, Report, Status, report_to_dict  # noqa: E402
from sciproof.verify import verify as verify_manifest  # noqa: E402

__all__ = [
    "CaptureError",
    "CaptureResult",
    "Check",
    "ConfirmationRequired",
    "DiffReport",
    "ManifestError",
    "PolicyError",
    "Report",
    "SciProofError",
    "SignatureError",
    "Status",
    "UpdateResult",
    "UsageError",
    "__version__",
    "capture_experiment",
    "diff_manifests",
    "export_dsse",
    "export_in_toto",
    "export_openlineage",
    "export_ro_crate",
    "export_typst",
    "list_manifests",
    "load_manifest",
    "report_to_dict",
    "sign_manifest",
    "update_baseline",
    "verify_manifest",
    "verify_pipeline",
]

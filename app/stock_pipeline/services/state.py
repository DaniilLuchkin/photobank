from collections.abc import Iterable


TRANSITIONS: dict[str, set[str]] = {
    "DISCOVERED": {"HASHING", "SKIPPED", "FAILED"},
    "HASHING": {"DUPLICATE_CHECKED", "SKIPPED", "FAILED"},
    "DUPLICATE_CHECKED": {"ANALYZING", "NEEDS_REVIEW", "SKIPPED", "FAILED"},
    "ANALYZING": {"METADATA_GENERATION", "NEEDS_REVIEW", "FAILED"},
    "METADATA_GENERATION": {"QUALITY_CHECKED", "NEEDS_REVIEW", "FAILED"},
    "QUALITY_CHECKED": {"READY", "NEEDS_REVIEW", "FAILED"},
    "READY": {"QUEUED", "NEEDS_REVIEW", "SKIPPED"},
    "QUEUED": {"UPLOADING", "NEEDS_REVIEW", "FAILED"},
    "UPLOADING": {"SUBMITTED", "MANUAL_REQUIRED", "RETRY", "FAILED"},
    "SUBMITTED": {"PROCESSING", "APPROVED", "REJECTED", "MANUAL_REQUIRED"},
    "PROCESSING": {"APPROVED", "REJECTED", "MANUAL_REQUIRED", "RETRY"},
    "NEEDS_REVIEW": {"READY", "QUEUED", "SKIPPED", "FAILED"},
    "RETRY": {"QUEUED", "FAILED", "MANUAL_REQUIRED"},
    "MANUAL_REQUIRED": {"QUEUED", "SUBMITTED", "APPROVED", "SKIPPED"},
    "FAILED": {"QUEUED", "NEEDS_REVIEW", "SKIPPED"},
    "APPROVED": set(),
    "REJECTED": {"QUEUED", "NEEDS_REVIEW", "SKIPPED"},
    "SKIPPED": {"QUEUED"},
}


def can_transition(current: str, target: str) -> bool:
    return target in TRANSITIONS.get(current, set())


def assert_transition(current: str, target: str) -> None:
    if not can_transition(current, target):
        raise ValueError(f"invalid state transition: {current} -> {target}")


def provider_ids() -> Iterable[str]:
    return ("mock", "shutterstock", "pond5", "adobe", "alamy", "storyblocks")


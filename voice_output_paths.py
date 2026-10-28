"""Resolved output roots shared by the IndexTTS worker and directed checks."""
from mac_paths import path as _portable_path
from pathlib import Path

FORMAL_JOB_ROOT = Path(__file__).resolve().parent / 'data' / 'job_temp'
OUTPUT_ROOTS = (
    FORMAL_JOB_ROOT,
    Path(_portable_path(r'D:\GPTData\temp\assembler_voice_jobs')),
    Path(_portable_path(r'D:\GPTData\temp\assembler_voice_previews')),
    Path(_portable_path(r'D:\DY video\本地配音\diagnostics')),
)
PATH_POLICY_VERSION = 'formal_job_temp_v1'


def validate_output_path(value: str | Path) -> Path:
    output = Path(value).expanduser().resolve()
    if output.suffix.lower() != '.wav' or not any(
            output.is_relative_to(root.resolve()) for root in OUTPUT_ROOTS):
        raise ValueError('Output must be in a permitted job/preview/diagnostic directory: ' + str(output))
    if output.exists():
        raise ValueError('Refusing to overwrite existing audio: ' + str(output))
    return output

# Re-export from root-level atomic_json for backward compatibility.
# The canonical implementation lives at atomic_json.py (project root)
# to avoid CWD-dependent import issues when Streamlit or scripts run
# from a different working directory.
from atomic_json import atomic_write_json, atomic_read_json  # noqa: F401

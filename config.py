HISTORY_FILE = "data/processed_jobs_history.json"
DEFAULT_OUTPUT_CSV = "data/outputs/job_matches.csv"
LOGS_DIR = "data/logs"

# Batching and resilience settings
BATCH_SIZE = 40  # Reduced batch size to prevent long response delays
CLIENT_TIMEOUT = 60_000  # 1 minute socket timeout for Gemini API client
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 10

# ---------------------------------------------------------------------------
# CANDIDATE PROFILE & PREFERENCES
# ---------------------------------------------------------------------------
CANDIDATE_PROFILE = """
"""

CANDIDATE_PREFERENCES = """
"""
import json
import logging
import os
import re
import time
import traceback
import pandas as pd
from dotenv import load_dotenv
from typing import Tuple
from google import genai
from google.genai import types
from google.genai.types import HttpOptions

# Import configuration settings and schemas
from config import (
    CANDIDATE_PROFILE,
    CANDIDATE_PREFERENCES,
    HISTORY_FILE,
    DEFAULT_OUTPUT_CSV,
    LOGS_DIR,
    BATCH_SIZE,
    CLIENT_TIMEOUT,
    MAX_RETRIES,
    RETRY_DELAY_SECONDS,
)
from schemas import BatchEvaluationResponse

# ---------------------------------------------------------------------------
# LOGGING SETUP
# ---------------------------------------------------------------------------
os.makedirs(LOGS_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(LOGS_DIR, "match_jobs.log"), encoding="utf-8")
    ]
)

load_dotenv()

if not os.getenv("GEMINI_API_KEY"):
    raise ValueError("GEMINI_API_KEY not found. Please check your .env file.")


# ---------------------------------------------------------------------------
# HELPERS TO NORMALIZE AND EXTRACT UNIQUE JOB IDENTIFIERS
# ---------------------------------------------------------------------------
def get_clean_job_id(job_dict: dict) -> str | None:
    """Extract clean numerical Job ID or fallback to cleaned canonical URL."""
    job_id = job_dict.get("jobId")
    if job_id:
        return str(job_id).strip()

    raw_url = job_dict.get("url")
    if raw_url and raw_url != "N/A":
        match = re.search(r"/view/(\d+)", raw_url)
        if match:
            return match.group(1)
        
        clean_url = raw_url.split("?")[0].rstrip("/").lower().strip()
        if clean_url:
            return clean_url

    return None


def get_composite_key(job_dict: dict) -> str | None:
    """Generate a composite key combining normalized Title, Company, and Location."""
    title = job_dict.get("title", "")
    company = job_dict.get("company", "")
    location = job_dict.get("location", "")

    if not title or not company:
        return None

    def _normalize(text: str) -> str:
        text = text.lower().strip()
        return re.sub(r"\s+", " ", text)

    return f"{_normalize(title)}|{_normalize(company)}|{_normalize(location)}"


def get_job_identifiers(job_dict: dict) -> Tuple[str | None, str | None]:
    """Return both primary ID and composite string key for duplication filtering."""
    return get_clean_job_id(job_dict), get_composite_key(job_dict)


# ---------------------------------------------------------------------------
# FUNCTIONS TO MANAGE HISTORY
# ---------------------------------------------------------------------------
def load_history() -> Tuple[set, set]:
    """Load previously processed job IDs and composite keys from storage."""
    ids_set = set()
    keys_set = set()

    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    ids_set = set(data)
                elif isinstance(data, dict):
                    ids_set = set(data.get("job_ids", []))
                    keys_set = set(data.get("composite_keys", []))
        except Exception as e:
            logging.warning(f"Could not read history file ({e}). A new one will be created.")

    return ids_set, keys_set


def save_history(ids_set: set, keys_set: set):
    """Persist processed job IDs and composite keys to the history file."""
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    payload = {
        "job_ids": sorted(list(ids_set)),
        "composite_keys": sorted(list(keys_set))
    }
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# MAIN EVALUATION FUNCTION
# ---------------------------------------------------------------------------
def evaluate_jobs(jobs_json_path: str, output_csv_path: str = DEFAULT_OUTPUT_CSV):
    with open(jobs_json_path, "r", encoding="utf-8") as f:
        jobs_data = json.load(f)

    history_ids, history_keys = load_history()
    
    # Deduplicate against stored history
    new_jobs_data = {}
    for key, job in jobs_data.items():
        job_id, composite_key = get_job_identifiers(job)
        
        is_duplicate_by_id = job_id is not None and job_id in history_ids
        is_duplicate_by_key = composite_key is not None and composite_key in history_keys

        if not is_duplicate_by_id and not is_duplicate_by_key:
            new_jobs_data[key] = job

    if not new_jobs_data:
        logging.info("No new jobs to analyze. All jobs were processed in previous runs.")
        return

    logging.info(f"Found {len(jobs_data)} total jobs.")
    logging.info(f"• {len(jobs_data) - len(new_jobs_data)} were already in history (skipped).")
    logging.info(f"• {len(new_jobs_data)} NEW jobs to process with Gemini.")

    # Instantiate Gemini client with extended HTTP timeout to prevent socket read timeouts
    client = genai.Client(
        http_options=types.HttpOptions(
            timeout=CLIENT_TIMEOUT
        )
    )

    job_items = list(new_jobs_data.items())
    all_evaluations = []

    logging.info(f"Analyzing new jobs in batches of {BATCH_SIZE} with Gemini API...")

    for i in range(0, len(job_items), BATCH_SIZE):
        batch = dict(job_items[i : i + BATCH_SIZE])
        batch_num = (i // BATCH_SIZE) + 1
        total_batches = (len(job_items) + BATCH_SIZE - 1) // BATCH_SIZE

        logging.info(f"  ──► Processing batch {batch_num}/{total_batches} ({len(batch)} jobs)...")

        prompt = f"""
You are an expert tech recruiter and career advisor.
Your task is to analyze a list of scraped job postings and evaluate each job against the candidate's profile and preferences.

### CANDIDATE PROFILE:
{CANDIDATE_PROFILE}

### CANDIDATE PREFERENCES:
{CANDIDATE_PREFERENCES}

### JOB POSTINGS TO EVALUATE:
{json.dumps(batch, indent=2, ensure_ascii=False)}

### INSTRUCTIONS:
1. For items that include full 'jd' text: perform a deep technical and location alignment match.
2. For items that ONLY contain title, company, and location (no 'jd'): evaluate based on title relevance, location feasibility, and company reputation, but flag that the detailed job description is missing.
3. Assign a match_score (0 to 100), interview_score (0 to 100), salary estimate/real identification, company type, modality, and recommendation to every single job entry in the input JSON.
4. If 'url' is present in the raw job JSON, use it; otherwise, construct it using 'https://www.linkedin.com/jobs/view/<jobId>/' if 'jobId' exists, or set as 'N/A'.
"""

        batch_success = False
        
        # Retry loop to handle temporary HTTP/socket timeouts and 500/503 API errors
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = client.models.generate_content(
                    model="gemini-3.5-flash-lite",
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=BatchEvaluationResponse,
                        temperature=0.2,
                    )
                )

                if response.parsed is None:
                    logging.warning(
                        f"Attempt {attempt}/{MAX_RETRIES} - Batch {batch_num} returned parsed=None. "
                        f"Raw text response snippet: {getattr(response, 'text', '')[:300]}"
                    )
                    time.sleep(RETRY_DELAY_SECONDS * attempt)
                    continue

                result: BatchEvaluationResponse = response.parsed
                all_evaluations.extend([e.model_dump() for e in result.evaluations])
                batch_success = True
                break  # Successful response, exit retry loop

            except Exception as e:
                logging.error(f"Attempt {attempt}/{MAX_RETRIES} - Exception in Batch {batch_num}: {type(e).__name__} - {e}")
                
                # Output full exception stack trace to debug log level
                logging.debug(f"Full traceback:\n{traceback.format_exc()}")

                if attempt < MAX_RETRIES:
                    sleep_time = RETRY_DELAY_SECONDS * attempt
                    logging.info(f"Retrying batch {batch_num} in {sleep_time} seconds...")
                    time.sleep(sleep_time)

        if not batch_success:
            logging.error(f"❌ Batch {batch_num} FAILED after {MAX_RETRIES} attempts. Skipping this batch.")
            
            # Dump the failed batch payload to disk for post-mortem analysis
            failed_batch_path = os.path.join(LOGS_DIR, f"failed_batch_{batch_num}.json")
            with open(failed_batch_path, "w", encoding="utf-8") as f:
                json.dump(batch, f, indent=2, ensure_ascii=False)
            logging.info(f"Saved failed batch payloads to '{failed_batch_path}'")

    if not all_evaluations:
        logging.error("No valid evaluations could be retrieved from Gemini API.")
        return

    evals = all_evaluations

    merged_records = []
    new_processed_ids = set()
    new_processed_keys = set()

    # Map evaluation results back to raw job records
    for ev in evals:
        key = ev["temp_id"]
        raw_job = new_jobs_data.get(key, {})
        
        resolved_url = raw_job.get("url") or ev.get("url")
        if not resolved_url or resolved_url == "N/A":
            job_id = raw_job.get("jobId")
            resolved_url = f"https://www.linkedin.com/jobs/view/{job_id}/" if job_id else "N/A"
        
        job_id, composite_key = get_job_identifiers(raw_job)
        if job_id:
            new_processed_ids.add(job_id)
        if composite_key:
            new_processed_keys.add(composite_key)

        merged_records.append({
            "Match Score (%)": ev["match_score"],
            "Recommendation": ev["interest_recommendation"],
            "Title": ev["title"],
            "Company": ev["company"],
            "Company Type": ev["type"],
            "Modality": ev["modality"] if ev["modality"] != "Unknown" else raw_job.get("modality", "N/A"),
            "Salary": ev["salary"],
            "Salary Type": ev["salary_type"],
            "Interview Difficulty (0-100)": ev["interview_score"],
            "Location": raw_job.get("location", "N/A"),
            "Has Full JD": "Yes" if bool(raw_job.get("jd")) else "No",
            "Pros": "\n".join(f"• {p}" for p in ev["key_pros"]),
            "Cons / Risks": "\n".join(f"• {c}" for c in ev["key_cons_or_risks"]),
            "AI Reasoning": ev["summary_reasoning"],
            "URL": resolved_url,
            "Posted": raw_job.get("postedRaw", "N/A"),
            "Scanned At": raw_job.get("scannedAt", "N/A"),
        })

    df = pd.DataFrame(merged_records)
    df = df.sort_values(by="Match Score (%)", ascending=False)

    file_exists = os.path.exists(output_csv_path)

    # Append results to destination CSV file
    df.to_csv(
        output_csv_path,
        mode="a" if file_exists else "w",
        index=False,
        header=not file_exists,
        encoding="utf-8-sig"
    )

    # Persist updated history
    history_ids.update(new_processed_ids)
    history_keys.update(new_processed_keys)
    save_history(history_ids, history_keys)

    logging.info(f"Done! {len(df)} new jobs analyzed and added to '{output_csv_path}'.")
    logging.info(f"History updated in '{HISTORY_FILE}'. Accumulated: {len(history_ids)} IDs, {len(history_keys)} composite keys.")


if __name__ == "__main__":
    evaluate_jobs("jobs/linkedin_jobs.json")
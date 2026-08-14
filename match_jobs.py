import json
import os
import re
import pandas as pd
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from typing import List, Tuple
from google import genai
from google.genai import types

load_dotenv()

if not os.getenv("GEMINI_API_KEY"):
    raise ValueError("GEMINI_API_KEY not found. Please check your .env file.")

HISTORY_FILE = "data/processed_jobs_history.json"

# ---------------------------------------------------------------------------
# HELPERS TO NORMALIZE AND EXTRACT UNIQUE JOB IDENTIFIERS
# ---------------------------------------------------------------------------
def get_clean_job_id(job_dict: dict) -> str | None:
    """
    Extracts a unique numeric Job ID or clean URL-based ID.
    Returns None if no ID or valid URL is available.
    """
    job_id = job_dict.get("jobId")
    if job_id:
        return str(job_id).strip()

    raw_url = job_dict.get("url")
    if raw_url and raw_url != "N/A":
        # 1. Try to extract numeric LinkedIn ID if present in the URL
        match = re.search(r"/view/(\d+)", raw_url)
        if match:
            return match.group(1)
        
        # 2. Strip query parameters and trailing slashes for other URLs
        clean_url = raw_url.split("?")[0].rstrip("/").lower().strip()
        if clean_url:
            return clean_url

    return None


def get_composite_key(job_dict: dict) -> str | None:
    """
    Generates a normalized composite key: 'title|company|location'.
    Returns None if title or company is missing.
    """
    title = job_dict.get("title", "")
    company = job_dict.get("company", "")
    location = job_dict.get("location", "")

    if not title or not company:
        return None

    def _normalize(text: str) -> str:
        text = text.lower().strip()
        # Clean multiple spaces/tabs/newlines into a single space
        return re.sub(r"\s+", " ", text)

    return f"{_normalize(title)}|{_normalize(company)}|{_normalize(location)}"


def get_job_identifiers(job_dict: dict) -> Tuple[str | None, str | None]:
    """
    Returns a tuple of (job_id, composite_key).
    """
    return get_clean_job_id(job_dict), get_composite_key(job_dict)


# ---------------------------------------------------------------------------
# FUNCTIONS TO MANAGE HISTORY (Retrocompatible structure)
# ---------------------------------------------------------------------------
def load_history() -> Tuple[set, set]:
    """
    Loads sets of previously processed job IDs and composite keys.
    Supports both legacy list format and new dictionary structure.
    Returns: (ids_set, composite_keys_set)
    """
    ids_set = set()
    keys_set = set()

    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                
                # Backward compatibility: legacy list of IDs
                if isinstance(data, list):
                    ids_set = set(data)
                # New format: dict with separate sets for ids and composite fingerprints
                elif isinstance(data, dict):
                    ids_set = set(data.get("job_ids", []))
                    keys_set = set(data.get("composite_keys", []))
        except Exception as e:
            print(f"Warning: Could not read history ({e}). A new one will be created.")

    return ids_set, keys_set


def save_history(ids_set: set, keys_set: set):
    """Saves updated sets of IDs and composite keys to disk."""
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    payload = {
        "job_ids": sorted(list(ids_set)),
        "composite_keys": sorted(list(keys_set))
    }
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 1. PROFILE & PREFERENCES
# ---------------------------------------------------------------------------
CANDIDATE_PROFILE = """
"""

CANDIDATE_PREFERENCES = """
"""

# ---------------------------------------------------------------------------
# 2. PYDANTIC SCHEMAS FOR GEMINI STRUCTURED OUTPUT
# ---------------------------------------------------------------------------
class JobEvaluation(BaseModel):
    temp_id: str = Field(description="The unique key from the input JSON")
    title: str
    company: str
    type: str = Field(description="Type of company: 'Startup', 'Scaleup', 'SME', 'Consulting', or 'Unknown'")
    modality: str = Field(description="Remote, Hybrid, Onsite, or Unknown")
    salary: str = Field(description="Expected salary range, if available, otherwise do an estimate based on market data and seniority level")
    salary_type: str = Field(description="Type of salary: 'Estimated' or 'Real'")
    match_score: int = Field(description="Score from 0 to 100 based on profile and preference alignment")
    interview_score: int = Field(description="Score from 0 to 100 based on estimated interview difficulty")
    interest_recommendation: str = Field(description="One of: 'HIGHLY INTERESTED', 'MAYBE / REVIEW', 'REJECT'")
    key_pros: List[str] = Field(description="Main reasons why this offer is attractive")
    key_cons_or_risks: List[str] = Field(description="Missing info, location issues, or stack mismatches")
    summary_reasoning: str = Field(description="A 2-sentence summary of why this score was assigned")
    url: str = Field(description="URL of the job posting")


class BatchEvaluationResponse(BaseModel):
    evaluations: List[JobEvaluation]


# ---------------------------------------------------------------------------
# 3. LLM EVALUATION FUNCTION
# ---------------------------------------------------------------------------
def evaluate_jobs(jobs_json_path: str, output_csv_path: str = "data/outputs/job_matches.csv"):
    with open(jobs_json_path, "r", encoding="utf-8") as f:
        jobs_data = json.load(f)

    history_ids, history_keys = load_history()
    
    # Filter jobs: skip if MATCHED by Job ID OR by Composite Key (title | company | location)
    new_jobs_data = {}
    for key, job in jobs_data.items():
        job_id, composite_key = get_job_identifiers(job)
        
        is_duplicate_by_id = job_id is not None and job_id in history_ids
        is_duplicate_by_key = composite_key is not None and composite_key in history_keys

        if not is_duplicate_by_id and not is_duplicate_by_key:
            new_jobs_data[key] = job

    if not new_jobs_data:
        print("No new jobs to analyze. All jobs were processed in previous runs.")
        return

    print(f"Found {len(jobs_data)} total jobs.")
    print(f"• {len(jobs_data) - len(new_jobs_data)} were already in history (skipped by ID or Title+Company+Location).")
    print(f"• {len(new_jobs_data)} NEW jobs to process with Gemini.")

    client = genai.Client()

    prompt = f"""
You are an expert tech recruiter and career advisor.
Your task is to analyze a list of scraped job postings and evaluate each job against the candidate's profile and preferences.

### CANDIDATE PROFILE:
{CANDIDATE_PROFILE}

### CANDIDATE PREFERENCES:
{CANDIDATE_PREFERENCES}

### JOB POSTINGS TO EVALUATE:
{json.dumps(new_jobs_data, indent=2, ensure_ascii=False)}

### INSTRUCTIONS:
1. For items that include full 'jd' text: perform a deep technical and location alignment match.
2. For items that ONLY contain title, company, and location (no 'jd'): evaluate based on title relevance, location feasibility, and company reputation, but flag that the detailed job description is missing.
3. Assign a match_score (0 to 100), interview_score (0 to 100), salary estimate/real identification, company type, modality, and recommendation to every single job entry in the input JSON.
4. If 'url' is present in the raw job JSON, use it; otherwise, construct it using 'https://www.linkedin.com/jobs/view/<jobId>/' if 'jobId' exists, or set as 'N/A'.
"""

    print("Analyzing new jobs with Gemini API...")
    
    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=BatchEvaluationResponse,
            temperature=0.2,
        )
    )

    result: BatchEvaluationResponse = response.parsed
    evals = [e.model_dump() for e in result.evaluations]

    merged_records = []
    new_processed_ids = set()
    new_processed_keys = set()

    for ev in evals:
        key = ev["temp_id"]
        raw_job = new_jobs_data.get(key, {})
        
        resolved_url = raw_job.get("url") or ev.get("url")
        if not resolved_url or resolved_url == "N/A":
            job_id = raw_job.get("jobId")
            resolved_url = f"https://www.linkedin.com/jobs/view/{job_id}/" if job_id else "N/A"
        
        # Extract identifiers for updating history
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

    # Create DataFrame and sort by Match Score
    df = pd.DataFrame(merged_records)
    df = df.sort_values(by="Match Score (%)", ascending=False)

    # Check if CSV output exists to handle header mode
    file_exists = os.path.exists(output_csv_path)

    df.to_csv(
        output_csv_path,
        mode="a" if file_exists else "w",
        index=False,
        header=not file_exists,
        encoding="utf-8-sig"
    )

    # Update history sets and persist to disk
    history_ids.update(new_processed_ids)
    history_keys.update(new_processed_keys)
    save_history(history_ids, history_keys)

    print(f"Done! {len(df)} new jobs analyzed and added to '{output_csv_path}'.")
    print(f"History updated in '{HISTORY_FILE}'. Accumulated: {len(history_ids)} IDs, {len(history_keys)} composite keys.")


if __name__ == "__main__":
    evaluate_jobs("jobs/linkedin_jobs.json")
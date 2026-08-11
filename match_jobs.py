import json
import os
import re
import pandas as pd
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from typing import List
from google import genai
from google.genai import types

load_dotenv()

if not os.getenv("GEMINI_API_KEY"):
    raise ValueError("GEMINI_API_KEY not found. Please check your .env file.")

HISTORY_FILE = "data/processed_jobs_history.json"

# ---------------------------------------------------------------------------
# HELPER TO NORMALIZE AND EXTRACT UNIQUE JOB IDENTIFIER
# ---------------------------------------------------------------------------
def get_clean_identifier(job_dict: dict) -> str | None:
    """
    Extracts a unique, normalized identifier.
    Returns None if the job posting has NO jobId nor a valid URL.
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
        
        # 2. If it's another valid URL, strip query strings (?refId=...) and trailing slashes
        clean_url = raw_url.split("?")[0].rstrip("/").lower().strip()
        if clean_url:
            return clean_url

    # If there is no valid ID or URL, return None to avoid saving it in the history
    return None

# ---------------------------------------------------------------------------
# FUNCTIONS TO MANAGE HISTORY
# ---------------------------------------------------------------------------
def load_history() -> set:
    """Loads the set of previously processed URLs/IDs."""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data)
        except Exception as e:
            print(f"Warning: Could not read history ({e}). A new one will be created.")
    return set()

def save_history(processed_set: set):
    """Saves the updated history to disk."""
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(list(processed_set), f, indent=2, ensure_ascii=False)

# ---------------------------------------------------------------------------
# 1. PROFILE & PREFERENCES
# ---------------------------------------------------------------------------
CANDIDATE_PROFILE = """
- Title: 
- Core Stack: 
- Education: 
- Languages: 
- Recent Experience: 
"""

CANDIDATE_PREFERENCES = """
- Work Modality: 
- Desired Tech 
- Desired Salary: 
- Role Seniority: 
- Red Flags: 
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

    history = load_history()
    
    # 2. Filter jobs to keep ONLY those NOT present in the history
    new_jobs_data = {}
    for key, job in jobs_data.items():
        job_identifier = get_clean_identifier(job)
        
        # If it DOES NOT have an identifier (None), ALWAYS treat it as a new job.
        # If it DOES have an identifier, only pass if it is NOT in history.
        if job_identifier is None or job_identifier not in history:
            new_jobs_data[key] = job

    if not new_jobs_data:
        print("No new jobs to analyze. All jobs were processed in previous runs.")
        return

    print(f"Found {len(jobs_data)} total jobs.")
    print(f"• {len(jobs_data) - len(new_jobs_data)} were already in history (skipped).")
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
    processed_identifiers = set()

    for ev in evals:
        key = ev["temp_id"]
        raw_job = new_jobs_data.get(key, {})
        
        resolved_url = raw_job.get("url") or ev.get("url")
        if not resolved_url or resolved_url == "N/A":
            job_id = raw_job.get("jobId")
            resolved_url = f"https://www.linkedin.com/jobs/view/{job_id}/" if job_id else "N/A"
        
        # Only add to the set if the job HAS a valid ID/URL
        identifier = get_clean_identifier(raw_job)
        if identifier is not None:
            processed_identifiers.add(identifier)

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

    # 5. Create DataFrame
    df = pd.DataFrame(merged_records)
    df = df.sort_values(by="Match Score (%)", ascending=False)

    # Check if the output CSV file already exists
    file_exists = os.path.exists(output_csv_path)

    # If it DOES NOT exist, create the file AND write headers (header=True)
    # If it DOES exist, append (mode='a') AND DO NOT write headers (header=False)
    df.to_csv(
        output_csv_path,
        mode="a" if file_exists else "w",
        index=False,
        header=not file_exists,
        encoding="utf-8-sig"
    )

    # 6. Update history ONLY with valid identifiers
    history.update(processed_identifiers)
    save_history(history)

    print(f"Done! {len(df)} new jobs analyzed and added to '{output_csv_path}'.")
    print(f"History updated in '{HISTORY_FILE}'. Total accumulated in history: {len(history)} jobs.")

if __name__ == "__main__":
    evaluate_jobs("jobs/linkedin_jobs.json")
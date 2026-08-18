from pydantic import BaseModel, Field
from typing import List

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
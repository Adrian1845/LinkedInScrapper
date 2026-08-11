# LinkedIn Job Matcher & Analyzer AI

Automated system for collecting, filtering, analyzing, and evaluating developer job postings using a Manifest V3 browser extension, the Google Gemini API, and Python.

The project scrapes job data from LinkedIn via a Chrome/Firefox extension, filters out duplicates using a local history file to optimize API token usage, evaluates candidate technical and salary alignment via structured AI (Pydantic), and appends the results to a cumulative CSV file ready for Excel import.

---

## Key Features

* **Manifest V3 Browser Extension:** Injects content scripts directly into LinkedIn to capture job cards and full job details in real-time, backed by an async storage queue (`chrome.storage.local` / `browser.storage.local`) to avoid write race conditions during fast scrolling.
* **Extension Popup Interface:** Lightweight Manifest V3 popup allowing users to check live metrics (total scraped jobs stored in memory), export the current database directly to `linkedin_jobs.json`, or clear storage with a single click.
* **Smart Duplicate Filtering (Token Optimization):** Maintains a local registry (`processed_jobs_history.json`) based on job IDs (`jobId`) or normalized URLs. Previously evaluated listings are automatically skipped on subsequent runs.
* **Graceful Handling of ID-less Listings:** Postings missing a numeric ID or valid URL are analyzed normally but excluded from the duplicate history to prevent false-positive blocks.
* **Structured JSON Output:** Powered by the official Google GenAI SDK (`google-genai`) using Pydantic schemas (`BatchEvaluationResponse`) to guarantee deterministic responses with scores (0–100), pros, cons, work modality, and salary estimates.
* **Cumulative CSV Export:** New evaluations are appended to `job_matches.csv` encoded in `UTF-8 with BOM` (`utf-8-sig`) for seamless display of accented characters and special symbols in Microsoft Excel.

---

## Architecture & Workflow

```bash
[ LinkedIn Web Page ] ──(Content Script)──► [ chrome.storage.local ]
                                                   │
                                         (Extension Popup)
                                                   │ Export JSON
                                                   ▼
[ linkedin_jobs.json ] (Scraper Output)
│
▼
[ Normalization & Filtering ] ──► Compares with [ processed_jobs_history.json ]
│                                                 (Skips processed listings)
▼ (New listings only)
[ Google Gemini API ] (gemini-3.6-flash + Pydantic Schema)
│
▼
[ Pandas DataFrame ] ──► Cumulative Export (Append)
│
▼
[ data/outputs/job_matches.csv ] ──► Import / Sync with [ Excel Template ]

```

---

## Prerequisites

* **Google Chrome, Brave, or Firefox** (Manifest V3 compatible browser)
* **Python 3.10+**
* **Google Gemini API Key:** Obtainable from [Google AI Studio](https://aistudio.google.com/).
* **Microsoft Excel**

---

## Installation & Setup

### 1. Clone the repository and install Python dependencies

```bash
git clone <your-repository-url>
cd <your-repository>

pip install pandas pydantic python-dotenv google-genai

```

### 2. Load the Browser Extension (Manifest V3)

1. Open your browser and navigate to `chrome://extensions/` (or `about:debugging` in Firefox).
2. Enable **Developer mode** (toggle switch in the top right).
3. Click **Load unpacked** and select the `extension/` folder inside this repository.


### 3. Configure environment variables

Create a `.env` file in the root directory with the following variable:

```env
GEMINI_API_KEY=your_api_key_here

```

### 4. Recommended Directory Structure

Ensure the following folder structure exists at the project root:

```text
.
├── .env
├── main.py
├── extension/                     # Manifest V3 Extension
│   ├── manifest.json
│   ├── content.js                 # DOM scanner & mutations observer
│   ├── popup.html                 # Extension UI popup
│   └── popup.js                   # JSON Export & Clear memory handlers
├── jobs/
│   └── linkedin_jobs.json         # Exported file from the extension popup
└── data/
    ├── processed_jobs_history.json # Automatic duplicate tracking history
    └── outputs/
        └── job_matches.csv        # Cumulative generated CSV output

```

---

## Usage Guide

### Step 1: Scrape Jobs using the Browser Extension

1. Go to [LinkedIn Jobs](https://www.linkedin.com/jobs/) and perform your desired job search.
2. Scroll through the job results list. The content script automatically detects job cards and detailed panels in real-time.
3. You need to click on the offers you want the JD, as LinkedIn charges the JD in real time and cannot be retrieved earlier.
4. Click the **Extension Icon** in your browser toolbar to open the popup interface:
* **Job Counter:** View how many total job listings are stored in local memory.
* **Export JSON:** Click to download `linkedin_jobs.json` directly into your `jobs/` directory.
* **Clear Memory:** Click to reset the extension storage when starting a completely new search batch.

Sometimes you need to recharge the LinkedIn page if the extension is not extracting data correctly. 
This is due to how extensions work on browser.

<img width="352" height="576" alt="imagen" src="https://github.com/user-attachments/assets/4cae4dc1-07a0-4964-a97a-b050861c74a4" />

### Step 2: Run the Gemini Analyzer

Once `linkedin_jobs.json` is saved in the `jobs/` folder, run the Python pipeline:

```bash
python main.py

```

**Expected console output:**

```text
Found 35 total jobs.
• 20 were already in history (skipped).
• 15 NEW jobs to process with Gemini.
Analyzing new jobs with Gemini API...
Done! Analyzed 15 new jobs and appended them to 'data/outputs/job_matches.csv'.
History updated in 'data/processed_jobs_history.json'. Total cumulative history: 35 jobs.

```

---

## Microsoft Excel Template Setup

To display data with conditional formatting, filtering, and text wrapping without mess on imports:

### Initial Template Setup (One-Time):

1. Apply **Conditional Formatting** to column B (*Green – Yellow – Red color scale*).
2. Enable **Wrap Text** on long text columns (`Pros`, `Cons`, `AI Reasoning`).
3. Select Row 1 and press `Ctrl + Shift + L` to enable **Filter** dropdowns.
4. Save the workbook as `Template.xlsx`.

### Importing Cumulative Data (Power Query):

1. Navigate to **Data > Get Data > From Folder**.
2. Select `data/outputs`.
3. Click **Load To...**, choose **Existing worksheet**, and target cell **`A2`**.
4. Right-click the newly inserted table > **Table Layout**.

> **Refresh Workflow:** Every time you run the Python script, simply open Excel and press `Ctrl + Alt + F5` (*Refresh All*) to load new job entries automatically.
<img width="1899" height="360" alt="imagen" src="https://github.com/user-attachments/assets/bdfff286-eeef-40c5-ad0b-674f356c588a" />

---

## Frequently Asked Questions

* **How do I re-analyze all listings from scratch?**
Simply delete or empty the `data/processed_jobs_history.json` file.
* **What happens to job listings without an ID or URL?**
They are fully processed and saved to the CSV, but excluded from duplicate tracking history to avoid accidentally blocking future listings that might use temporary generic keys.
* **Does clearing the Extension Popup memory clear my Python history?**
No. **Clear Memory** in the browser popup only clears the currently scraped buffer stored in `chrome.storage.local`. Your processed duplicate history (`processed_jobs_history.json`) and output CSV remain safe in the Python environment.
* **Would I get banned for using this automation?**
Could be. LinkedIn does not allow scrapping at all. This method is the cleanest way to scrap LinkedIn with the minimum risk, as it reads the DOM, does not perform any injection or automation.

## Disclaimer
**This will not work forever, as LinkedIn changes HTML structure often**

# Evaluation Results

This file documents actual outputs of the categorization agent against the held-out sample transactions in [categorizer/few_shot/few_shot.py](categorizer/few_shot/few_shot.py).

Everything below is **real output**, not hand-written examples — reproducible with:

```bash
python Testing_Script/Testing_Script.py
```

- **Provider:** Groq (`GROQ_MODEL` from `.env`; run below used the configured Groq chat model)
- **Date captured:** 2026-06-03
- **Sample size:** 13 transactions (2 per core category + 3 `Other`)
- **Chart of accounts:** 6 categories (5 core + `Other`)
- **Historical pool:** 50 transactions (10 per core category)

---

## Summary metrics

| Metric | Result |
|--------|--------|
| Overall accuracy | 13/13 (100%) |
| Average confidence | 0.96 |
| Invalid categories | 0/13 |
| Min / max confidence | 0.85 / 1.00 |

**Per-category accuracy**

| Category | Accuracy |
|----------|----------|
| Software Subscriptions | 2/2 |
| Travel & Meals | 2/2 |
| Marketing & Advertising | 2/2 |
| Professional Services | 2/2 |
| Bank Fees | 2/2 |
| Other | 3/3 |

All predictions were valid chart categories (`is_valid_category: true`), including `Other`.

---

## Full run

| # | Transaction | Predicted | Expected | Confidence | Match |
|---|-------------|-----------|----------|------------|-------|
| 1 | Figma Professional - monthly | Software Subscriptions | Software Subscriptions | 0.97 | ✅ |
| 2 | Salesforce CRM license renewal | Software Subscriptions | Software Subscriptions | 0.95 | ✅ |
| 3 | Lunch meeting with prospect | Travel & Meals | Travel & Meals | 0.95 | ✅ |
| 4 | Rental car for Austin conference | Travel & Meals | Travel & Meals | 0.90 | ✅ |
| 5 | Facebook Ads - November campaign | Marketing & Advertising | Marketing & Advertising | 1.00 | ✅ |
| 6 | Sponsored webinar production | Marketing & Advertising | Marketing & Advertising | 0.85 | ✅ |
| 7 | Legal consultation on contract | Professional Services | Professional Services | 1.00 | ✅ |
| 8 | Annual financial audit | Professional Services | Professional Services | 0.95 | ✅ |
| 9 | International wire transfer fee | Bank Fees | Bank Fees | 1.00 | ✅ |
| 10 | Payment gateway monthly fee | Bank Fees | Bank Fees | 0.95 | ✅ |
| 11 | Office rent - monthly lease payment | Other | Other | 1.00 | ✅ |
| 12 | Company gym membership - annual | Other | Other | 1.00 | ✅ |
| 13 | Charitable donation - annual giving | Other | Other | 1.00 | ✅ |

---

## Confidence behavior

Confidence comes from the LLM's self-reported score, then adjusted in [categorizer/services/confidence.py](categorizer/services/confidence.py):

- **Invalid (not on chart):** capped at `0.3` (`is_valid_category: false`)
- **Chart `Other`:** `+0.1` boost when selected (valid category)
- **Exact payee match in history under same category** (including `Other`): `+0.1` boost; stacks with the `Other` boost when both apply
- **Final value:** clamped to `[0.0, 1.0]`

In this run, **1.00** confidence appeared on exact payee matches (Meta Platforms, Smith & Partners LLP, Chase Bank) and on all three `Other` cases (`+0.1` Other boost; payee boost applies when history has the same payee under `Other`). Semantic matches without an exact payee landed at **0.85–0.97**. Average confidence **0.96** reflects strong performance across both core categories and `Other`.

Aggregate metrics are computed in [Testing_Script/Testing_Script.py](Testing_Script/Testing_Script.py), not in `confidence.py`.

---

## Example JSON outputs

### 1. Happy path — valid chart category

**Request**

```json
POST /api/categorize/
{
  "transaction": {
    "description": "Figma Professional - monthly",
    "payee": "Figma Inc"
  },
  "company_context": {
    "company_id": "acme-001",
    "industry": "SaaS / Software",
    "chart_of_accounts": [
      "Software Subscriptions",
      "Travel & Meals",
      "Marketing & Advertising",
      "Professional Services",
      "Bank Fees",
      "Other"
    ],
    "historical_transactions": [
      {"description": "Google Workspace monthly", "payee": "Google", "category": "Software Subscriptions"},
      {"description": "Slack Pro monthly billing", "payee": "Slack Technologies", "category": "Software Subscriptions"}
    ]
  }
}
```

**Response (`200 OK`)** — from eval run #1

```json
{
  "category": "Software Subscriptions",
  "confidence": 0.97,
  "reasoning": "The description and payee indicate a SaaS product subscription, matching the Software Subscriptions examples.",
  "is_valid_category": true,
  "model": "openai/gpt-oss-120b"
}
```

### 2. Catch-all — model returns chart `Other`

When nothing in the core categories fits, the model returns `"Other"` (listed on the chart of accounts). A suggested label lives in `reasoning` (2 sentences, max 30 words). Confidence receives a `+0.1` Other boost (and `+0.1` more if payee matches history under `Other`); eval runs #11–13 reached **1.00**.

**Response (`200 OK`)** — from eval run #11 (WeWork rent)

```json
{
  "category": "Other",
  "confidence": 1.0,
  "reasoning": "The provided description does not match the existing descriptions of the listed categories. Its category should be \"Rent\".",
  "is_valid_category": true,
  "model": "openai/gpt-oss-120b"
}
```

### 3. Guardrail case — LLM returns a category outside the chart

If the model hallucinates a category name not in the chart (and it is not `"Other"`), the API maps it to `"Uncategorized"`.

**Response (`200 OK`)** — reproduced with stub/mock LLM in unit tests

```json
{
  "category": "Uncategorized",
  "confidence": 0.3,
  "reasoning": "This looked like a crypto transaction to me.",
  "is_valid_category": false,
  "model": "stub-llm"
}
```

What happens here:

- `category` becomes `"Uncategorized"` for downstream safety.
- `is_valid_category` is `false`.
- `confidence` is capped at `0.3`.
- The model's original suggestion is not returned as a separate field; it may appear in `reasoning` if the model included it.

### 4. Validation error

**Request** (missing required `chart_of_accounts`)

```json
POST /api/categorize/
{
  "transaction": {"description": "x"},
  "company_context": {"company_id": "c1", "industry": "SaaS"}
}
```

**Response (`400 Bad Request`)**

```json
{
  "error": "invalid_request",
  "details": {
    "company_context": {
      "chart_of_accounts": ["This field is required."]
    }
  }
}
```

---

## How to reproduce

```bash
# Windows
.venv\Scripts\activate
python Testing_Script/Testing_Script.py

# macOS / Linux
source .venv/bin/activate
python Testing_Script/Testing_Script.py
```

The script sleeps 2s between calls to stay under Groq rate limits. Set `LLM_PROVIDER=mock` in `.env` to run without an API key (keyword-based mock; accuracy will differ).

For the full unit + API test suite:

```bash
python manage.py test categorizer
```

29 tests, none of them hit the network.

# Transaction Categorization Agent

A Django REST service that takes a single transaction (description + optional payee) plus a company's context (industry, chart of accounts, recent history) and returns a categorization suggestion produced by an LLM.

## Key design decisions

- **Retrieval-augmented few-shot selection** — picks the most similar historical examples and shortlists candidate categories so the LLM gets focused, relevant context as history grows.
- **Provider-agnostic LLM interface** — three implementations ship out of the box (Gemini, Groq, Mock); adding OpenAI or any other provider is a single new file.
- **Deterministic JSON contract** — the response shape is identical regardless of which provider produced it.
- **Confidence calibration** — heuristics adjust the LLM's self-reported confidence based on payee history and chart-of-accounts validation.

No database, no auth, no async, no UI.

---

## Architecture — how a request flows

`POST /api/categorize/` → JSON response with `category`, `confidence`, `reasoning`, `is_valid_category`, and `model`.

1. **Request validation** (`views.py`, `serializers.py`) — validate the JSON body and build a `Transaction` plus `CompanyContext`.
2. **Few-shot retrieval** (`services/retrieval.py`) — score historical rows against the new transaction; shortlist categories and pick up to two examples each.
3. **Prompt build** (`services/context_builder.py`) — assemble numbered categories, grouped examples, the new transaction, and reasoning rules.
4. **LLM call** (`llm_provider/`) — invoke Groq, Gemini, or Mock at temperature 0; expect structured JSON back.
5. **Parse and validate** (`services/response_parser.py`) — parse JSON; map the result to a chart category, `Other`, or `Uncategorized`.
6. **Confidence adjustment** (`services/confidence.py`) — cap confidence for invalid results; boost when payee matches history under the same category or when the model selects chart `Other`.
7. **Response** — return the final `CategorizationResult` to the client.

---

## Running it locally

You'll need Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# open .env and add your API key

python manage.py runserver
```

The server listens on `http://127.0.0.1:8000/`.

### Configuring the LLM provider

Everything lives in `.env`:

| Variable          | Default                     | Notes                                                |
|-------------------|-----------------------------|------------------------------------------------------|
| `LLM_PROVIDER`    | `gemini`                    | `groq`, `gemini`, or `mock`                          |
| `GROQ_API_KEY`    | —                           | Required for `groq`. Get one at https://console.groq.com/keys |
| `GROQ_MODEL`      | `openai/gpt-oss-120b`   | Any Groq-hosted chat model.                          |
| `GEMINI_API_KEY`  | —                           | Required for `gemini`. https://aistudio.google.com/apikey |
| `GEMINI_MODEL`    | `gemini-2.0-flash`          | Any Gemini model.                                    |

If the selected provider's API key is missing, the factory falls back to the mock client and logs a warning — handy for running tests without burning quota.

---

## API

### `POST /api/categorize/`

**Request**

```json
{
  "transaction": {
    "description": "Figma Professional monthly",
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
      {"description": "AWS Cloud Services", "payee": "Amazon Web Services", "category": "Software Subscriptions"},
      {"description": "Uber ride to client meeting", "payee": "Uber", "category": "Travel & Meals"}
    ]
  }
}
```

`payee` is optional on both the transaction and any historical entry. `historical_transactions` may be an empty list.

**Response (200)**

```json
{
  "category": "Software Subscriptions",
  "confidence": 0.92,
  "reasoning": "Figma is a SaaS design tool, similar to other software subscriptions in history.",
  "is_valid_category": true,
  "model": "openai/gpt-oss-120b"
}
```

The response shape is identical regardless of which provider produced it. Only the `model` field changes.

If the model picks **Other** (a catch-all on the chart when nothing else fits), the response keeps that label. The suggested label lives only inside `reasoning` (2 sentences, max 30 words). Confidence receives a small boost like other high-signal matches:

```json
{
  "category": "Other",
  "confidence": 1.0,
  "reasoning": "The provided description does not match the existing descriptions of the listed categories. Its category should be \"Rent\".",
  "is_valid_category": true,
  "model": "openai/gpt-oss-120b"
}
```

Normal categories use a single-sentence `reasoning` (max 20 words).

If the model returns a **made-up** category not in the chart (e.g. `"Spaceship Fuel"`), the service falls back to `"Uncategorized"`:

```json
{
  "category": "Uncategorized",
  "confidence": 0.3,
  "reasoning": "...",
  "is_valid_category": false,
  "model": "openai/gpt-oss-120b"
}
```

**Error responses**

| Status | `error`             | When                                                    |
|--------|---------------------|---------------------------------------------------------|
| 400    | `invalid_request`   | Request body fails validation. `details` has the field errors. |
| 502    | `llm_unavailable`   | Upstream LLM call failed (network, quota, auth).        |
| 502    | `llm_bad_response`  | LLM responded but the JSON couldn't be parsed.          |

### `GET /api/health/`

Returns `{"status": "ok"}`. Useful for liveness checks.

---

## Project structure

```
config/                           Django project (settings/urls/wsgi)
categorizer/
├── llm_provider/
│   ├── base.py                   LLMClient abstract interface + LLMResponse / LLMError
│   ├── gemini_client.py          Google AI Studio implementation
│   ├── groq_client.py            Groq implementation (JSON mode)
│   ├── mock_client.py            Deterministic fake for tests & no-key runs
│   └── factory.py                Picks the client from settings
├── services/
│   ├── retrieval.py              Similarity-based few-shot selection
│   ├── context_builder.py        Builds structured prompt from retrieval results
│   ├── response_parser.py        Parses LLM JSON + chart-of-accounts validation
│   ├── confidence.py             Adjusts LLM confidence with heuristics
│   └── categorizer.py            Orchestrates: retrieve → build → invoke → parse → validate
├── few_shot/
│   └── few_shot.py               50 historical transactions (10 per core category) + 13 eval samples
├── schemas.py                    Pydantic models shared across the service layer
├── serializers.py                DRF serializers for the API request shape
├── views.py                      CategorizeView + HealthView
├── urls.py
└── tests/                        Unit tests (retrieval, parser, confidence, prompt, service, API)
Testing_Script/Testing_Script.py  Runs eval samples and reports accuracy metrics
```

### Why the split?

- **`retrieval.py`** owns *which examples we show*. Similarity-scored few-shot selection, not random.
- **`context_builder`** owns *what we tell the model*. Structured prompt with numbered categories.
- **`llm_provider/`** owns *how we talk to the model*. Swap providers without touching prompts.
- **`response_parser`** owns *what we trust*. JSON parsing, schema check, chart validation.
- **`confidence`** owns *what we believe*. Small, isolated, easy to extend.
- **`categorizer.py`** is the only place these pieces meet. The view just calls it.

### Retrieval-based few-shot selection

The retrieval layer scores each historical transaction against the new one using description and payee similarity, then selects the most relevant few-shot examples and up to five candidate categories (two examples per category). Exact payee matches in history are prioritized so familiar vendors map quickly. For small charts or weak similarity scores, the full category list is used with the best available examples. This keeps the prompt focused and gives the model clear evidence per option, which improves accuracy without sending the entire history on every request.

### Confidence calibration

We take the model's self-reported confidence, then:

1. **If the category isn't in the chart of accounts**, cap confidence at `0.3`.
2. **If the payee exactly matches a historical transaction with the same category** (including `Other`), bump confidence by `+0.1` (capped at `1.0`).
3. **If the model selects chart `Other`**, bump confidence by another `+0.1` (stacks with payee match when both apply).

### Logging

We log shape, never content. Descriptions and payees can carry PII, so the categorizer logs only counts and the company id.

---

## Tests

```bash
python manage.py test categorizer
```

Tests cover: retrieval logic, response parsing, confidence heuristics, prompt construction, service orchestration, and the HTTP API. All use the `MockLLMClient` or inline stubs — no network, no API key required.

## Evaluation

A 13-sample test set lives in `categorizer/few_shot/few_shot.py` (2 per core category plus 3 `Other` edge cases). The chart includes `Other` as a valid catch-all category. To run it against whichever provider you've configured:

```bash
python Testing_Script/Testing_Script.py
```

Output includes overall accuracy, average confidence, invalid-category rate, and per-category accuracy. The script sleeps 2s between calls to stay within free-tier quotas. Captured Groq results: [RESULTS.md](RESULTS.md).

---

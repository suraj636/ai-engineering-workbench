"""Historical transactions (few-shot pool) and evaluation samples.

COMPANY_CONTEXT contains 50 labelled historical transactions across 5 core
categories (10 each), plus ``Other`` on the chart of accounts.  The retrieval layer picks the most relevant subset
for each new transaction rather than sending all of them to the LLM.

SAMPLE_TRANSACTIONS: held-out test cases for the evaluation script.  These are
*never* included in the few-shot pool — they are the ground truth for scoring.
"""
from __future__ import annotations

COMPANY_CONTEXT = {
    "company_id": "acme-001",
    "industry": "SaaS / Software",
    "chart_of_accounts": [
        "Software Subscriptions",
        "Travel & Meals",
        "Marketing & Advertising",
        "Professional Services",
        "Bank Fees",
        "Other",
    ],
    "historical_transactions": [
        # ── Software Subscriptions (10) ──────────────────────────────
        {"description": "AWS Cloud Services - October",       "payee": "Amazon Web Services",  "category": "Software Subscriptions"},
        {"description": "Google Workspace monthly",           "payee": "Google",                "category": "Software Subscriptions"},
        {"description": "GitHub Team plan",                   "payee": "GitHub",                "category": "Software Subscriptions"},
        {"description": "Notion Team - annual renewal",       "payee": "Notion Labs",           "category": "Software Subscriptions"},
        {"description": "Slack Pro monthly billing",          "payee": "Slack Technologies",    "category": "Software Subscriptions"},
        {"description": "Jira Software Cloud subscription",   "payee": "Atlassian",             "category": "Software Subscriptions"},
        {"description": "Zoom Business monthly plan",         "payee": "Zoom Video",            "category": "Software Subscriptions"},
        {"description": "Dropbox Business Advanced",          "payee": "Dropbox Inc",           "category": "Software Subscriptions"},
        {"description": "1Password Teams annual",             "payee": "AgileBits Inc",         "category": "Software Subscriptions"},
        {"description": "Datadog Pro plan - monitoring",      "payee": "Datadog Inc",           "category": "Software Subscriptions"},

        # ── Travel & Meals (10) ──────────────────────────────────────
        {"description": "Uber ride to client meeting",        "payee": "Uber",                  "category": "Travel & Meals"},
        {"description": "Team lunch - Q3 review",             "payee": "Chipotle",              "category": "Travel & Meals"},
        {"description": "Flight to SF conference",            "payee": "United Airlines",        "category": "Travel & Meals"},
        {"description": "Hotel stay - NYC sales trip",        "payee": "Marriott International", "category": "Travel & Meals"},
        {"description": "Lyft to airport - business travel",  "payee": "Lyft",                  "category": "Travel & Meals"},
        {"description": "Client dinner - partnership deal",   "payee": "The Capital Grille",    "category": "Travel & Meals"},
        {"description": "Team coffee meeting",                "payee": "Starbucks",             "category": "Travel & Meals"},
        {"description": "Parking at conference venue",        "payee": "ParkWhiz",              "category": "Travel & Meals"},
        {"description": "Train ticket Boston to NYC",         "payee": "Amtrak",                "category": "Travel & Meals"},
        {"description": "Lunch with investor at steakhouse",  "payee": "Ruth's Chris",          "category": "Travel & Meals"},

        # ── Marketing & Advertising (10) ─────────────────────────────
        {"description": "LinkedIn Ads - Sept campaign",       "payee": "LinkedIn",              "category": "Marketing & Advertising"},
        {"description": "Google Ads - brand keywords",        "payee": "Google",                "category": "Marketing & Advertising"},
        {"description": "Facebook Ads - retargeting",         "payee": "Meta Platforms",        "category": "Marketing & Advertising"},
        {"description": "SEO tools subscription - Ahrefs",    "payee": "Ahrefs",                "category": "Marketing & Advertising"},
        {"description": "Trade show booth rental - SaaStr",   "payee": "SaaStr Events",         "category": "Marketing & Advertising"},
        {"description": "Promotional swag - branded t-shirts","payee": "CustomInk",             "category": "Marketing & Advertising"},
        {"description": "Email marketing platform - monthly", "payee": "Mailchimp",             "category": "Marketing & Advertising"},
        {"description": "Content writer - blog posts",        "payee": "Contently",             "category": "Marketing & Advertising"},
        {"description": "Podcast sponsorship - tech audience","payee": "Podcast Network Inc",   "category": "Marketing & Advertising"},
        {"description": "PR agency retainer - monthly",       "payee": "Edelman PR",            "category": "Marketing & Advertising"},

        # ── Professional Services (10) ───────────────────────────────
        {"description": "Quarterly legal retainer",           "payee": "Smith & Partners LLP",  "category": "Professional Services"},
        {"description": "Bookkeeping services - Sept",        "payee": "BrightBooks Inc",       "category": "Professional Services"},
        {"description": "Tax preparation - annual filing",    "payee": "Deloitte",              "category": "Professional Services"},
        {"description": "HR consulting - benefits review",    "payee": "Mercer Consulting",     "category": "Professional Services"},
        {"description": "Patent filing legal fees",           "payee": "Baker McKenzie",        "category": "Professional Services"},
        {"description": "Security audit - SOC2 prep",        "payee": "CrowdStrike Services",  "category": "Professional Services"},
        {"description": "IT consulting - cloud migration",    "payee": "Accenture",             "category": "Professional Services"},
        {"description": "Recruitment agency fee - engineer",  "payee": "Robert Half",           "category": "Professional Services"},
        {"description": "Corporate insurance broker fee",     "payee": "Marsh McLennan",        "category": "Professional Services"},
        {"description": "Accounting advisory - Q4 close",     "payee": "PricewaterhouseCoopers", "category": "Professional Services"},

        # ── Bank Fees (10) ───────────────────────────────────────────
        {"description": "Wire transfer fee - vendor",         "payee": "Chase Bank",            "category": "Bank Fees"},
        {"description": "Monthly account maintenance fee",    "payee": "Chase Bank",            "category": "Bank Fees"},
        {"description": "International wire fee - EUR",       "payee": "Wells Fargo",           "category": "Bank Fees"},
        {"description": "ACH processing fee",                 "payee": "Mercury Bank",          "category": "Bank Fees"},
        {"description": "Credit card processing fees",        "payee": "Stripe",                "category": "Bank Fees"},
        {"description": "Overdraft protection fee",           "payee": "Bank of America",       "category": "Bank Fees"},
        {"description": "Foreign currency conversion fee",    "payee": "Chase Bank",            "category": "Bank Fees"},
        {"description": "Check ordering fee",                 "payee": "Deluxe Business Checks","category": "Bank Fees"},
        {"description": "Returned payment fee",               "payee": "Chase Bank",            "category": "Bank Fees"},
        {"description": "Merchant services monthly fee",      "payee": "Square",                "category": "Bank Fees"},
    ],
}


# Held-out test transactions — never in the few-shot pool.
# Used by Testing_Script/Testing_Script.py (13 samples: 2 per core category + 3 Other).
SAMPLE_TRANSACTIONS = [
    # Software Subscriptions
    {
        "transaction": {"description": "Figma Professional - monthly",       "payee": "Figma Inc"},
        "expected_category": "Software Subscriptions",
    },
    {
        "transaction": {"description": "Salesforce CRM license renewal",     "payee": "Salesforce"},
        "expected_category": "Software Subscriptions",
    },
    # Travel & Meals
    {
        "transaction": {"description": "Lunch meeting with prospect",        "payee": "The Olive Garden"},
        "expected_category": "Travel & Meals",
    },
    {
        "transaction": {"description": "Rental car for Austin conference",   "payee": "Enterprise Rent-A-Car"},
        "expected_category": "Travel & Meals",
    },
    # Marketing & Advertising
    {
        "transaction": {"description": "Facebook Ads - November campaign",   "payee": "Meta Platforms"},
        "expected_category": "Marketing & Advertising",
    },
    {
        "transaction": {"description": "Sponsored webinar production",       "payee": "BrightTalk"},
        "expected_category": "Marketing & Advertising",
    },
    # Professional Services
    {
        "transaction": {"description": "Legal consultation on contract",     "payee": "Smith & Partners LLP"},
        "expected_category": "Professional Services",
    },
    {
        "transaction": {"description": "Annual financial audit",             "payee": "KPMG"},
        "expected_category": "Professional Services",
    },
    # Bank Fees
    {
        "transaction": {"description": "International wire transfer fee",    "payee": "Chase Bank"},
        "expected_category": "Bank Fees",
    },
    {
        "transaction": {"description": "Payment gateway monthly fee",        "payee": "PayPal"},
        "expected_category": "Bank Fees",
    },
    # Other — transactions that don't fit any of the 5 core categories
    {
        "transaction": {"description": "Office rent - monthly lease payment", "payee": "WeWork"},
        "expected_category": "Other",
    },
    {
        "transaction": {"description": "Company gym membership - annual",    "payee": "Equinox"},
        "expected_category": "Other",
    },
    {
        "transaction": {"description": "Charitable donation - annual giving","payee": "Red Cross"},
        "expected_category": "Other",
    },
]

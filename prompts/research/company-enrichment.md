# Company Research Enrichment Guide

When filling in a company research record, follow these guidelines:

## Data Sources (in order of preference)
1. Company website (About, Careers, Blog)
2. Crunchbase Basic (funding, stage, size)
3. GitHub organization (tech stack, engineering culture)
4. Glassdoor (rating, reviews — use aggregate only)
5. LinkedIn company page (size estimate, industry)

## Content Policy
- **Never** store named individuals' compensation data
- **Never** store data obtained in violation of third-party Terms of Service
- **Never** embed recruiter email addresses in research files
- Attribute claims to sources using `source_urls`
- Set `confidence` based on source quality: `high` (manual/official), `medium` (scraped/inferred), `low` (guessed)

## Fields Guide
- `size_estimate`: Use ranges like "50-200", "1000-5000", not exact headcount
- `stage`: One of startup, growth, public, enterprise, unknown
- `tech_stack`: Only include technologies with evidence (job posts, GitHub, blog)
- `remote_policy`: "remote", "hybrid", "onsite", or free text describing the policy
- `recent_news`: 2-3 bullet points of recent relevant news (funding, product launches, leadership changes)

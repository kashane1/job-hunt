# Resume Variant Generation Guide

When generating resume variants, follow these guidelines:

## Variant Styles

- **technical_depth**: Emphasize system design, architecture, migrations, data models, optimization. Best for roles that focus on technical complexity.
- **impact_focused**: Emphasize business impact, revenue, cost reduction, user adoption, measurable outcomes. Best for roles that emphasize outcomes.
- **breadth**: Emphasize cross-functional experience spanning frontend, backend, infrastructure, leadership. Best for generalist or leadership roles.

## Selection Rules

1. Each variant selects accomplishments using a weighted score: 70% lead keyword overlap (Jaccard) + 30% style phrase boost
2. Variants should produce noticeably different accomplishment orderings
3. Skills are ordered by relevance to the lead, not alphabetically
4. The professional summary paragraph is the main differentiation point

## Content Rules

- **Never fabricate** accomplishments, skills, or metrics not in the candidate profile
- All content must be traceable to `source_document_ids`
- Set `provenance` to "grounded" when assembling from profile data
- Contact information comes directly from the profile — do not modify

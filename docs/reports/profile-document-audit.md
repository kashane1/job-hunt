# Profile Document Audit

- Generated at: 2026-04-16T03:23:27+00:00
- Raw documents scanned: 14
- Supported documents normalized: 14
- Average quality: 62.1
- Average quantity: 46.6
- Average value: 59.1

## Highest-Value Documents
### question-examples
- Path: profile/raw/question-examples.txt
- Type: question_bank (inferred)
- Scores: quality 75, quantity 93, value 90
- Skill hits: ai, api, automation, aws, browser, css, data, frontend, html, javascript, kysely, mysql
- Suggestions: Add YAML frontmatter so the pipeline can classify and reuse this document more reliably.

### Kashane Sakhakorn Resume
- Path: profile/raw/Kashane Sakhakorn Resume.txt
- Type: resume (inferred)
- Scores: quality 60, quantity 38, value 84
- Skill hits: api, aws, data, docker, ecs, git, javascript, jest, kafka, mysql, php, postgres
- Suggestions: Add YAML frontmatter so the pipeline can classify and reuse this document more reliably.

### Accomplishment and Impact Bank
- Path: profile/raw/accomplishments.md
- Type: question_bank (explicit)
- Scores: quality 81, quantity 50, value 78
- Skill hits: ai, api, automation, aws, data, javascript, kysely, mysql, php, platform, postgres, react
- Suggestions: none

### Work Notes 2025
- Path: profile/raw/Work Notes 2025.txt
- Type: work_note (inferred)
- Scores: quality 86, quantity 80, value 72
- Skill hits: ai, api, automation, aws, backend, data, docker, ecs, frontend, git, infrastructure, javascript
- Suggestions: Add YAML frontmatter so the pipeline can classify and reuse this document more reliably.; Split this large work-note file into smaller monthly or project-specific docs to improve retrieval quality.

### All Recent Work
- Path: profile/raw/All Recent Work.txt
- Type: work_note (fallback)
- Scores: quality 80, quantity 73, value 62
- Skill hits: api, automation, data, html, platform, sql
- Suggestions: Add YAML frontmatter so the pipeline can classify and reuse this document more reliably.

## All Documents

### question-examples
- Path: profile/raw/question-examples.txt
- Type: question_bank
- Metrics: 2992 words, 10 bullets, 4 quantified phrases, 18 Q/A pairs
- Scores: quality 75 (medium), quantity 93 (high), value 90 (high)
- Highlights: I’m a tech enthusiast and natural born software engineer. Ready to make the world a better place, one line of code at a time. | I’m a tech enthusiast and a natural born software engineer with a passion for building solutions that matter. I make the world a better place, one line of code at a time. My journey in tech is fueled by my enthusiasm for learning new things, whether it’s mastering a new programming language or tool, diving into complex algorithms, or exploring the latest advancements in artificial intelligence. I’m not just a coder; I’m a builder, a thinker, and a lifelong learner who sees every challenge as an opportunity to grow. | AI Chatbot Project with Amazon Q Business
- Suggestions: Add YAML frontmatter so the pipeline can classify and reuse this document more reliably.

### Kashane Sakhakorn Resume
- Path: profile/raw/Kashane Sakhakorn Resume.txt
- Type: resume
- Metrics: 334 words, 9 bullets, 4 quantified phrases, 0 Q/A pairs
- Scores: quality 60 (medium), quantity 38 (low), value 84 (high)
- Highlights: Led data synchronization initiative, migrating 10+ years of legacy MySQL data to Postgres, ensuring 100% data integrity and system interoperability | Collaborated on migrating a legacy PHP monolith to a modern TypeScript, Postgres, and AWS stack, accelerating transaction processing and enabling seamless onboarding of new users | Engineered Ticketmaster API integration with internal systems using PHP, JavaScript, and MySQL, boosting annual revenue to over $10M
- Suggestions: Add YAML frontmatter so the pipeline can classify and reuse this document more reliably.

### Accomplishment and Impact Bank
- Path: profile/raw/accomplishments.md
- Type: question_bank
- Metrics: 658 words, 3 bullets, 4 quantified phrases, 10 Q/A pairs
- Scores: quality 81 (high), quantity 50 (low), value 78 (medium)
- Highlights: [Fill in any additional quantified achievements] | [Fill in any open-source contributions or side projects] | [Fill in any leadership or initiative examples]
- Suggestions: none

### Work Notes 2025
- Path: profile/raw/Work Notes 2025.txt
- Type: work_note
- Metrics: 51373 words, 10 bullets, 8 quantified phrases, 0 Q/A pairs
- Scores: quality 86 (high), quantity 80 (high), value 72 (medium)
- Highlights: turn off sync-sale and sync-purchases and syncLegacyTickets | Run backfill on sync-purchases.ts | Run the backfill of the sync-ticket-to-sale. (run the backfill x amount of times !!! ) it finishes in less then a couple minutes. So run it every 5 mins? Atleast 4 times? Read the grafana logs for numSalesMatchedToTicket = 0, need to double check how long the first backfill run takes.
- Suggestions: Add YAML frontmatter so the pipeline can classify and reuse this document more reliably.; Split this large work-note file into smaller monthly or project-specific docs to improve retrieval quality.

### All Recent Work
- Path: profile/raw/All Recent Work.txt
- Type: work_note
- Metrics: 2634 words, 10 bullets, 8 quantified phrases, 0 Q/A pairs
- Scores: quality 80 (high), quantity 73 (medium), value 62 (medium)
- Highlights: Use existing `seat_map`, `price_level`, and `price_level_block` tables | Create template events (dummy events) with `event.tags` containing "template" tag | Store template tags in `seat_map.tags[]` column (genre, category, and sub-categories)
- Suggestions: Add YAML frontmatter so the pipeline can classify and reuse this document more reliably.

### Seat Map Templates
- Path: profile/raw/Seat Map Templates.txt
- Type: project_note
- Metrics: 1608 words, 10 bullets, 1 quantified phrases, 0 Q/A pairs
- Scores: quality 55 (low), quantity 44 (low), value 58 (low)
- Highlights: What Are Seat Map Templates? | Create a canonical seat map once (from an existing event’s seat map) | Store it on a synthetic “template event”
- Suggestions: Add YAML frontmatter so the pipeline can classify and reuse this document more reliably.

### Work Notes 2026
- Path: profile/raw/Work Notes 2026.txt
- Type: work_note
- Metrics: 4780 words, 10 bullets, 8 quantified phrases, 0 Q/A pairs
- Scores: quality 68 (medium), quantity 80 (high), value 58 (low)
- Highlights: PLAT-798 cleaner inventory initial price slack alert | AO-438 ticketmaster offer allowed alerts | AC-249 fix purchased_by tagging
- Suggestions: Add YAML frontmatter so the pipeline can classify and reuse this document more reliably.

### Offers Metrics
- Path: profile/raw/Offers Metrics.txt
- Type: work_note
- Metrics: 1433 words, 5 bullets, 4 quantified phrases, 0 Q/A pairs
- Scores: quality 65 (medium), quantity 44 (low), value 56 (low)
- Highlights: Past events only, or can we look at future events / current offers in metrics form? | If available for all events, should we add something to the event details page? Link to Offer Metrics or a small table? | What counts as “offer metrics”? What questions will this project answer?
- Suggestions: Add YAML frontmatter so the pipeline can classify and reuse this document more reliably.

### Candidate Identity Card
- Path: profile/raw/candidate-identity.md
- Type: resume
- Metrics: 127 words, 10 bullets, 0 quantified phrases, 0 Q/A pairs
- Scores: quality 53 (low), quantity 22 (low), value 50 (low)
- Highlights: Email: ksakhakorn@gmail.com | Phone: (818) 282-3532 | Location: [current city, state — fill in]
- Suggestions: Add more quantified outcomes so achievements are easier to ground in applications.; Mention key technologies explicitly to make skill extraction less lossy.

### Candidate Preferences
- Path: profile/raw/preferences.md
- Type: preferences
- Metrics: 160 words, 10 bullets, 2 quantified phrases, 0 Q/A pairs
- Scores: quality 62 (medium), quantity 30 (low), value 48 (low)
- Highlights: US Citizen / Authorized to work in the United States: [YES/NO — fill in] | Requires sponsorship: [YES/NO — fill in] | Minimum base salary: $140,000
- Suggestions: none

### cover-letter2
- Path: profile/raw/cover-letter2.txt
- Type: cover_letter
- Metrics: 239 words, 4 bullets, 0 quantified phrases, 0 Q/A pairs
- Scores: quality 39 (low), quantity 11 (low), value 48 (low)
- Highlights: In 2022, I completed a Full-Stack Software Developer Bootcamp at Georgia Tech, which led to an internal promotion to Junior Software Engineer. As the only engineer with prior experience as a user of our system, I brought a unique perspective to the role. | Over the following years, I honed my engineering skills both on the job and in my free time. I played a pivotal role in helping my company exceed eight figures in annual revenue, earning performance bonuses in addition to my annual bonus. Initially, I enhanced our legacy PHP and JavaScript monolith, which had been evolving since the company’s founding in 2007. Later, I contributed to a critical project migrating our codebase to TypeScript and Postgres, leveraging AWS and modern tools and frameworks. | To stay at the forefront of technology, I actively integrate state-of-the-art AI tools into my development and research workflows, using platforms like Cursor and Claude to streamline coding and problem-solving. I consistently follow AI trends and best practices, keeping up with advancements through daily engagement with tech communities and podcasts. This commitment ensures I apply cutting-edge techniques to deliver efficient, innovative solutions.
- Suggestions: Add YAML frontmatter so the pipeline can classify and reuse this document more reliably.; Add more quantified outcomes so achievements are easier to ground in applications.

### cover-letter
- Path: profile/raw/cover-letter.txt
- Type: cover_letter
- Metrics: 192 words, 3 bullets, 0 quantified phrases, 0 Q/A pairs
- Scores: quality 36 (low), quantity 8 (low), value 42 (low)
- Highlights: In 2022, I took a Full-Stack Software Developer Bootcamp at Georgia Tech, which earned me a lateral promotion to Junior Software Engineer. I was in a unique position to become one of the only engineers who knew our system as a user first. | In the following years I would evolve my engineering skills on the job as well in my free time. I played a key role in helping my company surpass 8 figures of annual revenue, and received additional performance bonuses on top of my annual bonus. After learning and improving the monolith of php code that had been evolving since the company started in 2007, I became a part of a project where we migrated our codebase into Typescript and Postgres using AWS with more innovative tools and frameworks. | I am now looking to expand my skill set outside of the ticket broker industry, and when I saw the opportunity to work for SpaceX, I knew I had to apply right away. If given the opportunity, I know I’ll make significant contributions as your next Software Engineer.
- Suggestions: Add YAML frontmatter so the pipeline can classify and reuse this document more reliably.; Add more quantified outcomes so achievements are easier to ground in applications.

### Metrics Tables_Pages
- Path: profile/raw/Metrics Tables_Pages.txt
- Type: work_note
- Metrics: 1872 words, 10 bullets, 0 quantified phrases, 0 Q/A pairs
- Scores: quality 50 (low), quantity 43 (low), value 42 (low)
- Highlights: Kashane Sakhakorn Edit | How it gets its info: Reads from purchase joined to ticket (aggregated CTE), event, inventory_listing, ticketmaster_offer, buy_attempt. Filters and sorting are applied via QueryBuilderUtil.applyPurchaseSearchFilters. | Calculations involved: On the backend, computes per-row derived fields like isMatched (based on joined ticket saleId) and aggregates in the response summary (counts, totals, etc.).
- Suggestions: Add YAML frontmatter so the pipeline can classify and reuse this document more reliably.; Add more quantified outcomes so achievements are easier to ground in applications.

### Weather Forecast System
- Path: profile/raw/Weather Forecast System.txt
- Type: work_note
- Metrics: 632 words, 10 bullets, 2 quantified phrases, 0 Q/A pairs
- Scores: quality 60 (medium), quantity 36 (low), value 40 (low)
- Highlights: Forecast ingestion jobs (hourly and daily) | City resolution and city seeding/sync workflows | Weather API endpoint for event detail views
- Suggestions: Add YAML frontmatter so the pipeline can classify and reuse this document more reliably.

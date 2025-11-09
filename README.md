# Case Closed

As part of the AI Hackathon, we built **Case Closed** — an AI-powered precedent recommendation engine that blends multi-agent reasoning, modern LLMs, and the CourtListener API to make legal research faster and more contextual. The project aims to move beyond keyword search and toward legal-similarity-driven retrieval, automated memo/brief drafting, and an iterative, chat-style research workflow.

Contributors:

- Sai Yadavalli — AI Engineer
- Sedat Unal — Full-stack Developer
- Jason Pereira — Frontend Developer & UI/UX Designer
- Saksham Anand — Backend Developer

---

## Inspiration — Make legal research feel like conversation

Legal research often requires sifting through thousands of opinions to find a single meaningful precedent. We wanted to reduce that friction. Instead of treating legal documents as strings to be keyword-matched, Case Closed reasons about facts, issues, and legal posture — more like a junior associate guided by a senior. Our goal was to create a pipeline that could:

- extract structured legal elements from uploads or prompts,
- query live case databases intelligently, and
- produce human-readable explanations and legal drafts.

The result is a research assistant that helps you find relevant cases, explains _why_ they matter, and helps convert findings into memos and briefs — all within a single session.

---

## Pitch

Case Closed is a conversational legal research assistant. Users upload a court opinion (PDF / text) or describe their fact pattern; the system extracts facts, identifies legal issues and jurisdiction, constructs targeted queries to CourtListener, ranks results by contextual similarity (not just lexical overlap), and produces explainers and draft legal memos.

This approach accelerates legal workflows by combining: multi-agent reasoning (fact extraction, query generation, relevance scoring, drafting), semantic retrieval (embeddings + Gemini), and direct integration with CourtListener for authoritative case text.

---

## Overview

The repository contains the full-stack app, agent pipeline, and demo frontend.

- `app.py` — Flask server and agent orchestrator
- `app.py/` — modular agent definitions (extractor, retriever, evaluator, drafter)
- `static/` — vanilla JavaScript/CSS chat-style UI and upload interface
- `templates/` — main HTML files

### Features

- **Contextual Precedent Retrieval:** Retrieves cases from CourtListener using structured queries derived from extracted facts and issues.
- **Multi-Agent Pipeline:** Separate agents for extraction, search-query construction, retrieval, evaluation, and drafting.
- **Draft Generation:** Generate memos and briefs based on retrieved cases and session context.
- **Session Persistence:** Each user session stores context, enabling iterative refinement and progressive drafting.
- **Filterable Results:** Narrow results by jurisdiction, court level, and issue similarity.
- **Easy Uploads:** Upload PDFs or paste text — the system extracts and analyzes content automatically.

---

## Example walk-through

1. Upload a case PDF or paste a fact pattern.
2. The **Extraction Agent** pulls parties, claims, dates, and core issues.
3. The **Query Agent** turns those structured facts into optimized CourtListener queries.
4. The **Retrieval Agent** fetches candidate opinions and embeddings are used to compute semantic similarity.
5. The **Evaluation Agent** ranks and explains why each result is relevant.
6. Ask the **Drafting Agent** to produce a memo or brief section; iterate until satisfied.

---

## Stack Overview

![Case Closed High Level Architecture](assets/case_closed_architecture.png)

---

## Demo

[![Watch our skit and demo here!](https://img.youtube.com/vi/-iNLur6breI/maxresdefault.jpg)](https://youtu.be/-iNLur6breI)


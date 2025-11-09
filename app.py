import os
import tempfile
import requests
import uuid
import re
import json
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, Response
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from google import genai
from pdfminer.high_level import extract_text

# =====================================================
# INITIALIZATION
# =====================================================
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret")
app.config['UPLOAD_FOLDER'] = tempfile.gettempdir()
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024
ALLOWED_EXTENSIONS = {'pdf'}

PROJECT_ID = os.getenv("PROJECT_ID")
GOOGLE_CLOUD_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
COURTLISTENER_TOKEN = os.getenv("COURTLISTENER_TOKEN")
# -----------------------------------------------------
# Vertex AI Client Setup
# -----------------------------------------------------
client = genai.Client(
    vertexai=True,
    project=PROJECT_ID,
    location=GOOGLE_CLOUD_LOCATION,
)

# Independent agents
clarifier_agent = client.chats.create(model="gemini-2.5-flash")
summarizer_agent = client.chats.create(model="gemini-2.5-flash")
scorer_agent = client.chats.create(model="gemini-2.5-flash")
analyzer_agent = client.chats.create(model="gemini-2.5-pro")
draft_agent = client.chats.create(model="gemini-2.5-pro")
query_agent = client.chats.create(model="gemini-2.5-pro")

# -----------------------------------------------------
# Enhanced user context with structured analysis
# -----------------------------------------------------
user_contexts = {}  # {session_id: {
#   "description": str,
#   "clarify_attempts": int,
#   "pending_questions": list,
#   "analysis": {
#     "facts": list,
#     "jurisdictions": list,
#     "parties": list,
#     "legal_issues": list,
#     "causes_of_action": list
#   },
#   "summary": str,
#   "search_query": str,
#   "cases": list
# }}

def get_context_id():
    if "context_id" not in session:
        session["context_id"] = str(uuid.uuid4())
    return session["context_id"]

# =====================================================
# AGENT FUNCTIONS
# =====================================================
def ask_clarifying_questions(user_input: str, existing_analysis: dict = None) -> list:
    context = ""
    if existing_analysis:
        context = f"Already known: {json.dumps(existing_analysis, indent=2)}\n\n"
    
    prompt = (
        f"You are a legal paralegal assistant conducting fact-finding. "
        f"Given this input: '{user_input}'\n\n{context}"
        "Ask up to 3 specific, fact-finding legal questions such as: "
        "'What damages were suffered?', 'Was a contract signed?', 'What is the jurisdiction?', "
        "'Who are the parties involved?', 'What is the timeline of events?', "
        "'Are there any witnesses?', 'What evidence exists?'. "
        "Focus on gathering concrete facts needed for legal analysis. "
        "Return each question on a new line."
    )
    try:
        response = clarifier_agent.send_message(prompt)
        return [q.strip() for q in response.text.splitlines() if q.strip()]
    except Exception as e:
        return [f"[Error asking clarifications: {e}]"]

def extract_answers_from_message(user_message: str, questions: list) -> dict:
    """Extract answers to questions from user's message."""
    prompt = (
        f"Given these questions:\n" + "\n".join([f"{i+1}. {q}" for i, q in enumerate(questions)]) + "\n\n"
        f"And this user response: '{user_message}'\n\n"
        "Extract the answers to each question from the user's response. "
        "Respond strictly in JSON format:\n"
        "{\n"
        '  "answers": {"1": "answer to question 1 or empty string if not answered", "2": "...", "3": "..."},\n'
        '  "has_sufficient_info": true/false\n'
        "}\n"
        "If the user's message doesn't answer a question, use an empty string for that answer. "
        "Set has_sufficient_info to false if critical information is still missing."
    )
    try:
        response = clarifier_agent.send_message(prompt)
        text = response.text.strip()
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            parsed = json.loads(match.group(0))
            return {
                "answers": parsed.get("answers", {}),
                "has_sufficient_info": parsed.get("has_sufficient_info", False)
            }
    except Exception as e:
        pass
    return {"answers": {}, "has_sufficient_info": False}

def check_if_more_info_needed(user_message: str, existing_context: str, analysis: dict = None) -> tuple:
    """Check if more information is needed and return questions if needed."""
    combined = (existing_context + " " + user_message).strip()
    context_str = ""
    if analysis:
        context_str = f"Current analysis: {json.dumps(analysis, indent=2)}\n\n"
    
    prompt = (
        f"You are a legal paralegal assistant. Review this case information:\n\n"
        f"{context_str}Case description: {combined}\n\n"
        "Determine if you have sufficient information to conduct a legal analysis and case law search. "
        "You need: facts, jurisdiction, parties, legal issues, and causes of action. "
        "Respond strictly in JSON format:\n"
        "{\n"
        '  "needs_more_info": true/false,\n'
        '  "questions": ["question 1", "question 2", ...] or [] if no questions needed\n'
        "}\n"
        "Only ask questions if critical information is missing. Maximum 3 questions."
    )
    try:
        response = clarifier_agent.send_message(prompt)
        text = response.text.strip()
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            parsed = json.loads(match.group(0))
            needs_more = parsed.get("needs_more_info", False)
            questions = parsed.get("questions", [])
            return needs_more, questions if isinstance(questions, list) else []
    except Exception as e:
        pass
    return False, []

def summarize_case(text: str) -> str:
    prompt = f"Summarize this legal situation clearly and factually for use in a case law search:\n\n{text}"
    try:
        response = summarizer_agent.send_message(prompt)
        return response.text.strip()
    except Exception as e:
        return f"[Error summarizing: {e}]"

def extract_structured_analysis(text: str, cases: list = None) -> dict:
    """Extract structured facts, jurisdictions, parties, issues, and causes of action.
    Can incorporate insights from relevant cases if provided."""
    cases_context = ""
    if cases:
        # Add relevant case insights to help with analysis
        case_insights = []
        for c in cases[:3]:  # Use top 3 cases
            case_analysis = c.get('case_analysis', {})
            if case_analysis:
                if case_analysis.get('legal_principles'):
                    case_insights.append(f"Legal principles from {c.get('title', 'case')}: {', '.join(case_analysis['legal_principles'][:2])}")
                if case_analysis.get('similarities'):
                    case_insights.append(f"Similarities: {', '.join(case_analysis['similarities'][:1])}")
        
        if case_insights:
            cases_context = f"\n\nRelevant case law insights:\n" + "\n".join([f"- {insight}" for insight in case_insights])
    
    prompt = (
        f"Analyze this legal text and extract structured information:\n\n{text}\n"
        f"{cases_context}\n\n"
        "Respond strictly in JSON format with the following structure:\n"
        "{\n"
        '  "facts": ["fact1", "fact2", ...],\n'
        '  "jurisdictions": ["jurisdiction1", ...],\n'
        '  "parties": [{"name": "party1", "role": "plaintiff/defendant/other"}, ...],\n'
        '  "legal_issues": ["issue1", "issue2", ...],\n'
        '  "causes_of_action": ["cause1", "cause2", ...]\n'
        "}\n"
        "Be thorough and extract all relevant information. If information is not available, use empty arrays. "
        "Consider the case law insights provided when identifying legal issues and causes of action."
    )
    try:
        response = analyzer_agent.send_message(prompt)
        text = response.text.strip()
        # Extract JSON from response
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            parsed = json.loads(match.group(0))
            return {
                "facts": parsed.get("facts", []),
                "jurisdictions": parsed.get("jurisdictions", []),
                "parties": parsed.get("parties", []),
                "legal_issues": parsed.get("legal_issues", []),
                "causes_of_action": parsed.get("causes_of_action", [])
            }
    except Exception as e:
        pass
    return {
        "facts": [],
        "jurisdictions": [],
        "parties": [],
        "legal_issues": [],
        "causes_of_action": []
    }

def generate_query(summary: str, analysis: dict = None) -> str:
    """Generate search query using summary and structured analysis."""
    context = ""
    if analysis:
        issues = ", ".join(analysis.get("legal_issues", []))
        causes = ", ".join(analysis.get("causes_of_action", []))
        jurisdictions = ", ".join(analysis.get("jurisdictions", []))
        context = f"\n\nExtracted Legal Issues: {issues}\nCauses of Action: {causes}\nJurisdictions: {jurisdictions}"
    
    prompt = (
        f"Generate exactly 5 words of short keyword-style legal search terms for CourtListener "+
        f"querying purposes based on the following summary and analysis. Output ONLY the 5 keywords with " +
        f"no numbering or explanation: \n\nSummary:\n{summary},\n\nAnalysis{context}"
    )
    try:
        query_agent = client.chats.create(model="gemini-2.5-pro")
        response = query_agent.send_message(prompt)
        return response.text.strip() or summary
    except Exception as e:
        return summary

def extract_case_information(case_data: dict, user_analysis: dict = None) -> dict:
    """Extract structured information from a case for agent access."""
    case_text = case_data.get('full_text', '') or case_data.get('snippet', '')
    if not case_text or len(case_text) < 50:
        return {
            "key_facts": [],
            "legal_principles": [],
            "holdings": [],
            "reasoning": "",
            "relevant_statutes": [],
            "similarities": []
        }
    
    # Truncate if too long (keep first 8000 chars for analysis)
    text_for_analysis = case_text[:8000] if len(case_text) > 8000 else case_text
    
    user_context = ""
    if user_analysis:
        issues = ", ".join(user_analysis.get("legal_issues", []))
        causes = ", ".join(user_analysis.get("causes_of_action", []))
        user_context = f"\n\nUser's Case Context:\nLegal Issues: {issues}\nCauses of Action: {causes}"
    
    prompt = (
        f"Analyze this legal case and extract structured information:\n\n"
        f"Case Title: {case_data.get('title', 'Unknown')}\n"
        f"Citation: {case_data.get('citation', 'N/A')}\n"
        f"Case Text:\n{text_for_analysis}\n"
        f"{user_context}\n\n"
        f"Extract the following information and respond in JSON format:\n"
        "{\n"
        '  "key_facts": ["fact 1", "fact 2", ...],\n'
        '  "legal_principles": ["principle 1", "principle 2", ...],\n'
        '  "holdings": ["holding 1", "holding 2", ...],\n'
        '  "reasoning": "summary of the court\'s reasoning",\n'
        '  "relevant_statutes": ["statute 1", "statute 2", ...],\n'
        '  "similarities": ["how this case is similar to the user\'s case", ...]\n'
        "}\n"
        "Be thorough and extract all relevant legal information that could help analyze the user's case."
    )
    
    try:
        response = analyzer_agent.send_message(prompt)
        text = response.text.strip()
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            parsed = json.loads(match.group(0))
            return {
                "key_facts": parsed.get("key_facts", []),
                "legal_principles": parsed.get("legal_principles", []),
                "holdings": parsed.get("holdings", []),
                "reasoning": parsed.get("reasoning", ""),
                "relevant_statutes": parsed.get("relevant_statutes", []),
                "similarities": parsed.get("similarities", [])
            }
    except Exception as e:
        pass
    
    return {
        "key_facts": [],
        "legal_principles": [],
        "holdings": [],
        "reasoning": "",
        "relevant_statutes": [],
        "similarities": []
    }

def grade_case(summary: str, case_title: str, snippet: str, analysis: dict = None) -> dict:
    """Grade case relevance with score range 20-100."""
    context = ""
    if analysis:
        issues = ", ".join(analysis.get("legal_issues", []))
        context = f"\n\nUser's Legal Issues: {issues}"
    
    prompt = (
        "You are a legal relevance evaluator.\n"
        f"Compare the user's issue:\n{summary}{context}\n\n"
        f"Case:\nTitle: {case_title}\nSnippet: {snippet}\n\n"
        "Respond strictly in JSON as:\n"
        "{ \"score\": <20-100>, \"reason\": \"one-sentence reason\" }\n"
        "Score must be between 20 and 100. Lower scores (20-40) for less relevant cases, "
        "higher scores (80-100) for highly relevant cases."
    )
    try:
        response = scorer_agent.send_message(prompt)
        text = response.text.strip()
        match = re.search(r"\{.*\}", text, re.S)
        parsed = json.loads(match.group(0)) if match else {}
        score = int(parsed.get("score", 50))
        reason = parsed.get("reason", "No reason given.")
        # Ensure score is between 20 and 100
        score = min(max(score, 20), 100)
    except Exception as e:
        score, reason = 50, f"[Error grading case: {e}]"
        score = min(max(score, 20), 100)
    return {"score": score, "reason": reason}

def draft_legal_document(context: dict, doc_type: str = "memo") -> str:
    """Generate professional legal memo or brief using comprehensive case information."""
    analysis = context.get("analysis", {})
    summary = context.get("summary", "")
    cases = context.get("cases", [])
    
    facts = "\n".join([f"- {f}" for f in analysis.get("facts", [])])
    issues = "\n".join([f"- {i}" for i in analysis.get("legal_issues", [])])
    parties = "\n".join([f"- {p.get('name', 'Unknown')} ({p.get('role', 'Unknown')})" for p in analysis.get("parties", [])])
    jurisdictions = ", ".join(analysis.get("jurisdictions", []))
    causes = "\n".join([f"- {c}" for c in analysis.get("causes_of_action", [])])
    
    # Build comprehensive case information using extracted case analysis
    relevant_cases = []
    for c in cases[:5]:  # Top 5 most relevant cases
        case_analysis = c.get('case_analysis', {})
        case_info = f"**{c.get('title', 'Unknown')}** ({c.get('citation', 'No citation')})\n"
        case_info += f"Relevance: {c.get('relevance_score', 0)}% - {c.get('relevance_reason', '')}\n\n"
        
        if case_analysis:
            if case_analysis.get('key_facts'):
                case_info += f"Key Facts: {', '.join(case_analysis['key_facts'][:3])}\n"
            if case_analysis.get('legal_principles'):
                case_info += f"Legal Principles: {', '.join(case_analysis['legal_principles'][:3])}\n"
            if case_analysis.get('holdings'):
                case_info += f"Holdings: {', '.join(case_analysis['holdings'][:2])}\n"
            if case_analysis.get('reasoning'):
                case_info += f"Court's Reasoning: {case_analysis['reasoning'][:300]}...\n"
            if case_analysis.get('relevant_statutes'):
                case_info += f"Relevant Statutes: {', '.join(case_analysis['relevant_statutes'])}\n"
            if case_analysis.get('similarities'):
                case_info += f"Similarities to User's Case: {', '.join(case_analysis['similarities'][:2])}\n"
        else:
            # Fallback to snippet if no analysis available
            case_info += f"Case Summary: {c.get('snippet', '')[:300]}...\n"
        
        relevant_cases.append(case_info)
    
    cases_text = "\n\n---\n\n".join(relevant_cases) if relevant_cases else "No relevant cases available."
    
    prompt = (
        f"Generate a professional legal {doc_type} with the following structure:\n\n"
        f"**FACTS**\n{facts if facts else summary}\n\n"
        f"**PARTIES**\n{parties if parties else 'To be determined'}\n\n"
        f"**JURISDICTION**\n{jurisdictions if jurisdictions else 'To be determined'}\n\n"
        f"**LEGAL ISSUES**\n{issues if issues else 'To be determined'}\n\n"
        f"**CAUSES OF ACTION**\n{causes if causes else 'To be determined'}\n\n"
        f"**APPLICABLE LAW**\nBased on the following relevant cases with detailed analysis:\n\n{cases_text}\n\n"
        f"**ANALYSIS**\nProvide a thorough legal analysis connecting the facts to the applicable law. "
        f"Use the detailed case information provided above, including legal principles, holdings, and reasoning. "
        f"Explain how these cases apply to the current situation.\n\n"
        f"**CONCLUSION**\nProvide a clear conclusion with recommendations based on the case law analysis.\n\n"
        "Use professional legal writing style, proper citations, and clear reasoning. "
        "Reference specific cases and their holdings when making legal arguments."
    )
    
    try:
        response = draft_agent.send_message(prompt)
        document_text = response.text.strip()
        return document_text
    except Exception as e:
        return f"[Error drafting document: {e}]"

# =====================================================
# COURTLISTENER SEARCH
# =====================================================
def query_courtlistener(query: str):
    base = "https://www.courtlistener.com/api/rest/v4/search/"
    headers = {}
    if COURTLISTENER_TOKEN:
        headers["Authorization"] = f"Token {COURTLISTENER_TOKEN}"
    resp = requests.get(base, params={"q": query}, headers=headers, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    results = []
    for item in data.get("results", []):
        title = item.get("caseName") or item.get("name") or "Untitled"
        citation = item.get("citation") or ""
        pdf_link = item.get("absolute_url") or item.get("url") or ""
        if pdf_link.startswith("/"):
            pdf_link = "https://www.courtlistener.com" + pdf_link
        snippet = item.get("snippet") or item.get("summary") or ""
        decision_date = item.get("decision_date") or ""
        results.append({
            "title": title,
            "citation": citation,
            "pdf_link": pdf_link,
            "snippet": snippet,
            "decision_date": decision_date,
        })
    return results[:4]

# =====================================================
# HELPERS
# =====================================================
def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# =====================================================
# ROUTES
# =====================================================
@app.route('/')
def index():
    return render_template('chat.html')

@app.route('/upload', methods=['POST'])
def upload():
    if 'pdf' not in request.files:
        return jsonify({'error': 'No PDF file provided'}), 400
    f = request.files['pdf']
    if f.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    if f and allowed_file(f.filename):
        name = secure_filename(f.filename)
        path = os.path.join(app.config['UPLOAD_FOLDER'], name)
        f.save(path)
        
        # Extract text from PDF
        try:
            pdf_text = extract_text(path)
            if not pdf_text or len(pdf_text.strip()) < 10:
                pdf_text = f"[PDF {name} uploaded but text extraction yielded minimal content]"
        except Exception as e:
            pdf_text = f"[Error extracting text from PDF: {e}]"
        
        # Store in context
        context_id = get_context_id()
        context = user_contexts.setdefault(context_id, {
            "description": "",
            "clarify_attempts": 0,
            "pending_questions": [],
            "analysis": {},
            "summary": "",
            "search_query": "",
            "cases": []
        })
        context["description"] += f"\n\n[PDF: {name}]\n{pdf_text}"
        
        # Extract structured analysis from PDF
        analysis = extract_structured_analysis(pdf_text)
        context["analysis"] = analysis
        
        return jsonify({
            'filename': name,
            'text': pdf_text[:500] + "..." if len(pdf_text) > 500 else pdf_text,
            'analysis': analysis,
            'context_id': context_id
        })
    return jsonify({'error': 'Invalid file type'}), 400

# =====================================================
# MAIN CHAT ENDPOINT
# =====================================================
@app.route('/chat', methods=['POST'])
def chat():
    payload = request.json or {}
    message = payload.get("message", "").strip()
    clarified = payload.get("clarified", False)
    clarification_answers = payload.get("clarification_answers", None)
    clarify_attempts = int(payload.get("clarify_attempts", 0) or 0)
    adding_info = payload.get("adding_info", False)

    context_id = get_context_id()
    context = user_contexts.setdefault(context_id, {
        "description": "",
        "clarify_attempts": 0,
        "pending_questions": [],
        "analysis": {},
        "summary": "",
        "search_query": "",
        "cases": []
    })

    # -------------------------------------------------
    # Add extra info if refining
    # -------------------------------------------------
    if adding_info and message:
        context["description"] += " " + message
        # Re-analyze with new info
        combined_text = context["description"].strip()
        context["analysis"] = extract_structured_analysis(combined_text)

    # -------------------------------------------------
    # Step 1: Handle clarification flow
    # -------------------------------------------------
    # Store previous questions if we're in clarification mode
    previous_questions = context.get("pending_questions", [])
    
    # If we have pending questions, extract answers from user's message
    if previous_questions and not clarified:
        extracted = extract_answers_from_message(message, previous_questions)
        # Add the user's message to context
        context["description"] += " " + message
        combined_text = context["description"].strip()
        
        # Check if we still need more info
        needs_more, new_questions = check_if_more_info_needed(message, context["description"], context.get("analysis"))
        
        if needs_more and new_questions and clarify_attempts < 2:
            context["pending_questions"] = new_questions
            context["clarify_attempts"] = clarify_attempts + 1
            return jsonify({
                "status": "clarifying",
                "questions": new_questions,
                "clarify_attempts": context["clarify_attempts"],
                "context_id": context_id,
                "analysis": context.get("analysis", {})
            })
        # If we have enough info, continue to analysis
        context["pending_questions"] = []
        context["clarify_attempts"] = 0
    elif not clarified and clarify_attempts < 2:
        # First time or no pending questions - check if we need info
        combined_text = (context["description"] + " " + message).strip()
        needs_more, questions = check_if_more_info_needed(message, combined_text, context.get("analysis"))
        
        if needs_more and questions:
            context["description"] += " " + message
            context["pending_questions"] = questions
            context["clarify_attempts"] = clarify_attempts + 1
            return jsonify({
                "status": "clarifying",
                "questions": questions,
                "clarify_attempts": context["clarify_attempts"],
                "context_id": context_id,
                "analysis": context.get("analysis", {})
            })
        # If we have enough info, continue
        context["description"] += " " + message
        context["pending_questions"] = []
    else:
        # User explicitly clarified or we're past attempts
        context["description"] += " " + message
        context["pending_questions"] = []
        context["clarify_attempts"] = 0

    # -------------------------------------------------
    # Step 2: Merge all text
    # -------------------------------------------------
    combined_text = context["description"].strip()

    # -------------------------------------------------
    # Step 3: Extract structured analysis
    # Note: We'll re-analyze with case information after cases are fetched
    # -------------------------------------------------
    analysis = extract_structured_analysis(combined_text)
    context["analysis"] = analysis

    # -------------------------------------------------
    # Step 4: Summarize & query
    # -------------------------------------------------
    summary = summarize_case(combined_text)
    context["summary"] = summary

    # -------------------------------------------------
    # Step 5: Fetch from CourtListener using multiple queries
    # -------------------------------------------------
    all_cases = []
    seen_case_ids = set()  # Track by title+citation to avoid duplicates
    
    try:
        # Generate multiple different queries for comprehensive search
        queries = []
        for i in range(5):  # Use 5 different queries
            search_query = generate_query(summary, analysis)
            if search_query not in queries:  # Avoid duplicate queries
                queries.append(search_query)
                context["search_query"] += f"Query {i+1}: {search_query}\n"
        
        # Fetch cases from all queries
        for query in queries:
            cases_for_query = query_courtlistener(query)
            for c in cases_for_query:
                # Create unique ID for deduplication
                case_id = f"{c.get('title', '')}_{c.get('citation', '')}"
                if case_id not in seen_case_ids:
                    seen_case_ids.add(case_id)
                    all_cases.append(c)
    except Exception as e:
        return jsonify({"status": "error", "message": f"CourtListener error: {e}"}), 500

    # -------------------------------------------------
    # Step 6: Grade each case and extract detailed information
    # -------------------------------------------------
    results = []
    for c in all_cases:
        grading = grade_case(summary, c['title'], c['snippet'], analysis)
        
        # Extract detailed case information for agent access
        case_info = extract_case_information(c, analysis)
        
        results.append({
            **c,
            "relevance_score": grading["score"],
            "relevance_reason": grading["reason"],
            "case_analysis": case_info  # Add structured case information
        })

    # Sort by descending relevance and take top 10
    results.sort(key=lambda x: x["relevance_score"], reverse=True)
    top_results = results[:10]  # Top 10 highest ranked
    context["cases"] = top_results

    # -------------------------------------------------
    # Step 7: Re-analyze with case insights for better understanding
    # -------------------------------------------------
    if top_results:
        enhanced_analysis = extract_structured_analysis(combined_text, top_results)
        # Merge enhanced analysis with original (prefer enhanced but keep original if enhanced is empty)
        for key in analysis:
            if enhanced_analysis.get(key):
                analysis[key] = enhanced_analysis[key]
        context["analysis"] = analysis

    # -------------------------------------------------
    # Step 8: Return results (without summary and query in response)
    # -------------------------------------------------
    return jsonify({
        "status": "results",
        "context_id": context_id,
        "analysis": analysis,
        "cases": top_results
    })

# =====================================================
# NEW ENDPOINTS
# =====================================================
@app.route('/analyze', methods=['POST'])
def analyze():
    """Extract structured analysis from text or use existing context."""
    payload = request.json or {}
    text = payload.get("text", "").strip()
    context_id = payload.get("context_id")
    
    if not text and context_id:
        context = user_contexts.get(context_id, {})
        text = context.get("description", "")
    
    if not text:
        return jsonify({"error": "No text provided"}), 400
    
    analysis = extract_structured_analysis(text)
    
    # Update context if provided
    if context_id:
        ctx = user_contexts.setdefault(context_id, {
            "description": "",
            "clarify_attempts": 0,
            "pending_questions": [],
            "analysis": {},
            "summary": "",
            "search_query": "",
            "cases": []
        })
        ctx["analysis"] = analysis
        if not ctx["description"]:
            ctx["description"] = text
    
    return jsonify({
        "status": "success",
        "analysis": analysis,
        "context_id": context_id
    })

@app.route('/draft', methods=['POST'])
def draft():
    """Generate legal memo or brief from context."""
    payload = request.json or {}
    context_id = payload.get("context_id")
    doc_type = payload.get("doc_type", "memo")  # "memo" or "brief"
    
    if not context_id:
        return jsonify({"error": "No context_id provided"}), 400
    
    context = user_contexts.get(context_id, {})
    if not context:
        return jsonify({"error": "Context not found"}), 404
    
    # Ensure we have analysis
    if not context.get("analysis"):
        if context.get("description"):
            context["analysis"] = extract_structured_analysis(context["description"])
        else:
            return jsonify({"error": "No case information available"}), 400
    
    # Generate document
    document = draft_legal_document(context, doc_type)
    
    # Store the document in context for later download
    context[f"document_{doc_type}"] = document
    
    return jsonify({
        "status": "success",
        "document": document,
        "doc_type": doc_type,
        "context_id": context_id
    })

@app.route('/download-draft', methods=['POST'])
def download_draft():
    """Generate and return PDF of legal memo or brief."""
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        import io
    except ImportError:
        return jsonify({"error": "PDF generation requires reportlab library. Please install it with: pip install reportlab"}), 500
    
    payload = request.json or {}
    context_id = payload.get("context_id")
    doc_type = payload.get("doc_type", "memo")
    
    if not context_id:
        return jsonify({"error": "No context_id provided"}), 400
    
    context = user_contexts.get(context_id, {})
    if not context:
        return jsonify({"error": "Context not found"}), 404
    
    # Ensure we have analysis
    if not context.get("analysis"):
        if context.get("description"):
            context["analysis"] = extract_structured_analysis(context["description"])
        else:
            return jsonify({"error": "No case information available"}), 400
    
    try:
        # Get document from context if it exists, otherwise generate it
        document = context.get(f"document_{doc_type}")
        if not document:
            # Document not in context, generate it
            document = draft_legal_document(context, doc_type)
            # Store it for future use
            context[f"document_{doc_type}"] = document
        
        if not document or len(document.strip()) < 10:
            return jsonify({"error": "Document is empty or invalid. Please generate the document first."}), 400
        
        # Create PDF
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter)
        styles = getSampleStyleSheet()
        story = []
        
        # Title
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=16,
            textColor='black',
            spaceAfter=12,
        )
        story.append(Paragraph(f"Legal {doc_type.capitalize()}", title_style))
        story.append(Spacer(1, 0.2*inch))
        
        # Split document into paragraphs
        paragraphs = document.split('\n\n')
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            
            # Check if it's a header (starts with **)
            if para.startswith('**') and para.endswith('**'):
                header_text = para.replace('**', '').strip()
                story.append(Spacer(1, 0.1*inch))
                story.append(Paragraph(header_text, styles['Heading2']))
                story.append(Spacer(1, 0.1*inch))
            else:
                # Regular paragraph - escape HTML and handle line breaks
                para_clean = para.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                story.append(Paragraph(para_clean.replace('\n', '<br/>'), styles['Normal']))
                story.append(Spacer(1, 0.1*inch))
        
        doc.build(story)
        buffer.seek(0)
        
        # Return PDF
        filename = f"legal_{doc_type}_{context_id[:8]}.pdf"
        pdf_data = buffer.getvalue()
        
        response = Response(
            pdf_data,
            mimetype='application/pdf',
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Content-Type': 'application/pdf',
                'Content-Length': str(len(pdf_data))
            }
        )
        return response
    except Exception as e:
        return jsonify({"error": f"Error generating PDF: {str(e)}"}), 500

@app.route('/context', methods=['GET'])
def get_context():
    """Get current context for a session."""
    context_id = get_context_id()
    context = user_contexts.get(context_id, {
        "description": "",
        "clarify_attempts": 0,
        "pending_questions": [],
        "analysis": {},
        "summary": "",
        "search_query": "",
        "cases": []
    })
    return jsonify({
        "context_id": context_id,
        "context": context
    })

@app.route('/case-info', methods=['POST'])
def get_case_info():
    """Get detailed information about a specific case."""
    payload = request.json or {}
    case_title = payload.get("case_title", "")
    case_text = payload.get("case_text", "")
    context_id = payload.get("context_id")
    
    if not case_title and not case_text:
        return jsonify({"error": "Case title or text required"}), 400
    
    # Get user's analysis context if available
    user_analysis = None
    if context_id:
        context = user_contexts.get(context_id, {})
        user_analysis = context.get("analysis")
    
    case_data = {
        "title": case_title,
        "snippet": case_text,
        "full_text": case_text
    }
    
    # Extract detailed case information
    case_info = extract_case_information(case_data, user_analysis)
    
    return jsonify({
        "status": "success",
        "case_title": case_title,
        "case_analysis": case_info
    })

# =====================================================
# RUN
# =====================================================
if __name__ == '__main__':
    print("🚀 AI Paralegal Assistant (Multi-Agent) is running...")
    app.run(host='0.0.0.0', port=5000, debug=True)

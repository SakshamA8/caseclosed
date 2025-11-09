import os
import tempfile
import requests
import uuid
import re
import json
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from google import genai
from pdfminer.high_level import extract_text

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret")
app.config['UPLOAD_FOLDER'] = tempfile.gettempdir()
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024
ALLOWED_EXTENSIONS = {'pdf'}

PROJECT_ID = os.getenv("PROJECT_ID")
GOOGLE_CLOUD_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
COURTLISTENER_TOKEN = os.getenv("COURTLISTENER_TOKEN")

# Setup Vertex AI Client and Model Endpoints
# Note: Using Gemini 2.5 for optimal performance/cost balance
# Configuration managed via environment variables for security
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
#     "causes_of_action": list,
#     "penal_codes": list
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
def ask_clarifying_questions(user_input: str, existing_analysis: dict = None, description: str = "") -> list:
    """
    Generate up to 3 clarifying legal questions relevant to building a case argument.
    Avoid repeating questions about information already in `existing_analysis` or `description`.
    """
    context_text = ""

    if existing_analysis:
        context_text += "KNOWN INFORMATION:\n"
        for key, val in existing_analysis.items():
            if val:
                context_text += f"- {key}: {json.dumps(val, indent=2)}\n"
    if description:
        context_text += f"\nFULL CASE DESCRIPTION (so far):\n{description}\n"

    prompt = (
        f"You are a professional legal paralegal assisting a lawyer in building a legal argument.\n"
        f"Given the user's most recent message:\n'{user_input}'\n\n"
        f"And the known case context below:\n{context_text}\n"
        "Ask up to 3 clarifying questions **only** about information that is missing and critical for legal analysis — "
        "such as damages, contract terms, jurisdiction, causes of action, or key facts.\n"
        "Do NOT ask about information already in the context.\n"
        "If you already have sufficient facts, respond exactly with 'NO QUESTIONS NEEDED'."
    )

    try:
        response = clarifier_agent.send_message(prompt)
        lines = [q.strip() for q in response.text.splitlines() if q.strip()]
        if any("NO QUESTIONS NEEDED" in q.upper() for q in lines):
            return []
        return lines[:3]
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

def filter_redundant_questions(questions, analysis):
    """
    Remove clarifying questions that are already covered by the analysis context.
    """
    if not analysis:
        return questions

    answered_keys = {k for k, v in analysis.items() if v}
    filtered = []
    for q in questions:
        lower_q = q.lower()
        if any(keyword in lower_q for keyword in answered_keys):
            continue
        filtered.append(q)
    return filtered

def check_if_more_info_needed(user_message: str, existing_context: str, analysis: dict = None) -> tuple:
    """
    Check if more information is needed and return up to 3 questions.
    Includes full description and structured analysis to avoid repeat questions.
    """
    context_text = ""
    if analysis:
        context_text += "CURRENT STRUCTURED ANALYSIS:\n"
        for key, val in analysis.items():
            if val:
                context_text += f"- {key}: {json.dumps(val, indent=2)}\n"

    combined = (existing_context + " " + user_message).strip()

    prompt = (
        f"You are a legal paralegal assistant reviewing a client's case.\n"
        f"Below is the full information currently known:\n{context_text}\n\n"
        f"Case description:\n{combined}\n\n"
        "Determine if critical legal information is missing for analysis "
        "(facts, jurisdiction, parties, legal issues, causes of action).\n"
        "Respond strictly in JSON format:\n"
        "{\n"
        '  "needs_more_info": true/false,\n'
        '  "questions": ["question 1", "question 2", ...]\n'
        "}\n"
        "Only ask questions if *essential* facts are missing, and avoid duplicates of already known information."
    )

    try:
        response = clarifier_agent.send_message(prompt)
        text = response.text.strip()
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            parsed = json.loads(match.group(0))
            needs_more = parsed.get("needs_more_info", False)
            questions = parsed.get("questions", [])
            if not isinstance(questions, list):
                questions = []
            # Filter redundant ones before returning
            questions = filter_redundant_questions(questions, analysis)
            return needs_more, questions
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

def extract_structured_analysis(text: str) -> dict:
    """Extract structured facts, jurisdictions, parties, issues, causes of action, and penal codes."""
    prompt = (
        f"Analyze this legal text and extract comprehensive structured information:\n\n{text}\n\n"
        "Respond strictly in JSON format with the following structure:\n"
        "{\n"
        '  "facts": ["detailed fact1", "detailed fact2", ...],\n'
        '  "jurisdictions": ["jurisdiction1", ...],\n'
        '  "parties": [{"name": "party1", "role": "plaintiff/defendant/other", "details": "additional info"}, ...],\n'
        '  "legal_issues": ["detailed issue1 with context", "detailed issue2", ...],\n'
        '  "causes_of_action": ["detailed cause1", "detailed cause2", ...],\n'
        '  "penal_codes": [{"code": "PC 123", "description": "description of the code", "relevance": "why it applies"}, ...]\n'
        "}\n"
        "Be thorough and extract all relevant information. For penal codes, include any relevant state or federal codes "
        "(e.g., 'PC 187' for California Penal Code, '18 U.S.C. § 1001' for federal). Include descriptions and why each code is relevant. "
        "If information is not available, use empty arrays."
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
                "causes_of_action": parsed.get("causes_of_action", []),
                "penal_codes": parsed.get("penal_codes", [])
            }
    except Exception as e:
        pass
    return {
        "facts": [],
        "jurisdictions": [],
        "parties": [],
        "legal_issues": [],
        "causes_of_action": [],
        "penal_codes": []
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

def grade_case(summary: str, case_title: str, snippet: str, analysis: dict = None) -> dict:
    # Build context from structured analysis if available
    context = ""
    if analysis:
        issues = ", ".join(analysis.get("legal_issues", [])) or ", ".join(analysis.get("issues", []))
        causes = ", ".join(analysis.get("causes_of_action", []))
        if issues or causes:
            context = "\n\nUser's Legal Context:\n"
            if issues:
                context += f"- Legal Issues: {issues}\n"
            if causes:
                context += f"- Causes of Action: {causes}\n"

    # Stronger, more structured grading prompt
    prompt = f"""
        You are an experienced legal research assistant. 
        Your task is to assess how relevant the following case is to the user's described legal issue.

        User's Description:
        {summary}{context}

        Case to Evaluate:
        Title: {case_title}
        Excerpt: {snippet}

        Instructions:
        - Provide a relevance score from 0 to 100.
        - Consider overlap in facts, legal issues, causes of action, and jurisdictional or procedural context.
        - Be moderately lenient: if there is any meaningful thematic or factual connection, assign at least a partial score (e.g., 30–60 range).
        - Reserve very low scores (0–20) only for completely unrelated or irrelevant cases.
        - Higher scores (70–100) indicate strong legal and factual alignment.
        - Be fair, consistent, and nuanced — reward partial matches.
        - Output ONLY valid JSON in this exact format:
        {{"score": <integer>, "reason": "<one-sentence reason>"}}
    """

    try:
        response = scorer_agent.send_message(prompt)
        text = response.text.strip()
        match = re.search(r"\{.*\}", text, re.S)
        parsed = json.loads(match.group(0)) if match else {}
        score = int(parsed.get("score", 50))
        reason = parsed.get("reason", "No reason given.")
    except Exception as e:
        score, reason = 50, f"[Error grading case: {e}]"

    # Normalize and return safe output
    return {
        "score": max(0, min(100, score)),
        "reason": reason.strip()
    }


def draft_legal_document(context: dict, doc_type: str = "memo") -> str:
    """Generate professional legal memo or brief."""
    analysis = context.get("analysis", {})
    summary = context.get("summary", "")
    cases = context.get("cases", [])
    
    facts = "\n".join([f"- {f}" for f in analysis.get("facts", [])])
    issues = "\n".join([f"- {i}" for i in analysis.get("legal_issues", [])])
    parties = "\n".join([f"- {p.get('name', 'Unknown')} ({p.get('role', 'Unknown')})" for p in analysis.get("parties", [])])
    jurisdictions = ", ".join(analysis.get("jurisdictions", []))
    causes = "\n".join([f"- {c}" for c in analysis.get("causes_of_action", [])])
    
    relevant_cases = "\n\n".join([
        f"**{c.get('title', 'Unknown')}** ({c.get('citation', 'No citation')})\n"
        f"Relevance: {c.get('relevance_score', 0)}% - {c.get('relevance_reason', '')}\n"
        f"Snippet: {c.get('snippet', '')[:200]}..."
        for c in cases[:5]
    ])
    
    prompt = (
        f"Generate a professional legal {doc_type} with the following structure:\n\n"
        f"**FACTS**\n{facts if facts else summary}\n\n"
        f"**PARTIES**\n{parties if parties else 'To be determined'}\n\n"
        f"**JURISDICTION**\n{jurisdictions if jurisdictions else 'To be determined'}\n\n"
        f"**LEGAL ISSUES**\n{issues if issues else 'To be determined'}\n\n"
        f"**CAUSES OF ACTION**\n{causes if causes else 'To be determined'}\n\n"
        f"**APPLICABLE LAW**\nBased on the following relevant cases:\n{relevant_cases}\n\n"
        f"**ANALYSIS**\nProvide a thorough legal analysis connecting the facts to the applicable law.\n\n"
        f"**CONCLUSION**\nProvide a clear conclusion with recommendations.\n\n"
        "Use professional legal writing style, proper citations, and clear reasoning."
    )
    
    try:
        response = draft_agent.send_message(prompt)
        return response.text.strip()
    except Exception as e:
        return f"[Error drafting document: {e}]"


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


def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Routes
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


    if adding_info and message:
        context["description"] += " " + message
        # Re-analyze with new info
        combined_text = context["description"].strip()
        context["analysis"] = extract_structured_analysis(combined_text)


    # Store previous questions if we're in clarification mode
    previous_questions = context.get("pending_questions", [])
    
    # If we have pending questions, extract answers from user's message
    if previous_questions and not clarified:
        extracted = extract_answers_from_message(message, previous_questions)
        # Add the user's message to context
        context["description"] += " " + message
        combined_text = context["description"].strip()
        
        # Check if we still need more info
        needs_more, questions = check_if_more_info_needed(message, combined_text, context.get("analysis"))
        
        if needs_more and questions and clarify_attempts < 2:
            context["pending_questions"] = questions
            context["clarify_attempts"] = clarify_attempts + 1
            return jsonify({
                "status": "clarifying",
                "questions": questions,
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


    combined_text = context["description"].strip()

    analysis = extract_structured_analysis(combined_text)
    context["analysis"] = analysis

    summary = summarize_case(combined_text)
    context["summary"] = summary

    cases = []
    try:
        for i in range(3):
            search_query = generate_query(summary, analysis)
            context["search_query"] += f"{i}th search query: {search_query}\n\n"
            cases_for_query = query_courtlistener(search_query)
            for c in cases_for_query:
                if c not in cases:
                    cases.append(c)
            # cases += cases_for_query
    except Exception as e:
        return jsonify({"status": "error", "message": f"CourtListener error: {e}"}), 500

    results = []
    for c in cases:
        grading = grade_case(summary, c['title'], c['snippet'], analysis)
        results.append({
            **c,
            "relevance_score": grading["score"],
            "relevance_reason": grading["reason"]
        })

    # Sort by descending relevance
    results.sort(key=lambda x: x["relevance_score"], reverse=True)
    context["cases"] = results

    # -------------------------------------------------
    # Step 7: Return results
    # -------------------------------------------------
    return jsonify({
        "status": "results",
        "context_id": context_id,
        "query": context["search_query"],
        "summary": summary,
        "analysis": analysis,
        "cases": results
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
    
    return jsonify({
        "status": "success",
        "document": document,
        "doc_type": doc_type,
        "context_id": context_id
    })

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

# =====================================================
# RUN
# =====================================================
if __name__ == '__main__':
    print("AI Paralegal Assistant (Multi-Agent) is running...")
    app.run(host='0.0.0.0', port=5000, debug=True)

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

# -----------------------------------------------------
# Simple in-memory user context
# -----------------------------------------------------
user_contexts = {}  # {session_id: {"description": str, "clarify_attempts": int}}

def get_context_id():
    if "context_id" not in session:
        session["context_id"] = str(uuid.uuid4())
    return session["context_id"]

# =====================================================
# AGENT FUNCTIONS
# =====================================================
def ask_clarifying_questions(user_input: str) -> list:
    prompt = (
        f"You are a legal assistant. Given this input: '{user_input}', "
        "ask up to 3 concise clarifying questions about the jurisdiction, "
        "main issue, and key facts. Return each question on a new line."
    )
    try:
        response = clarifier_agent.send_message(prompt)
        return [q.strip() for q in response.text.splitlines() if q.strip()]
    except Exception as e:
        return [f"[Error asking clarifications: {e}]"]

def summarize_case(text: str) -> str:
    prompt = f"Summarize this legal situation clearly and factually for use in a case law search:\n\n{text}"
    try:
        response = summarizer_agent.send_message(prompt)
        return response.text.strip()
    except Exception as e:
        return f"[Error summarizing: {e}]"

def generate_query(summary: str) -> str:
    prompt = (
        f"Generate a short keyword-style legal search query (10 main keywords) for CourtListener "
        f"based on this summary:\n\n{summary}"
    )
    try:
        response = summarizer_agent.send_message(prompt)
        return response.text.strip() or summary
    except Exception as e:
        return summary

def grade_case(summary: str, case_title: str, snippet: str) -> dict:
    prompt = (
        "You are a legal relevance evaluator.\n"
        f"Compare the user's issue:\n{summary}\n\n"
        f"Case:\nTitle: {case_title}\nSnippet: {snippet}\n\n"
        "Respond strictly in JSON as:\n"
        "{ \"score\": <0-100>, \"reason\": \"one-sentence reason\" }"
    )
    try:
        response = scorer_agent.send_message(prompt)
        text = response.text.strip()
        match = re.search(r"\{.*\}", text, re.S)
        parsed = json.loads(match.group(0)) if match else {}
        score = int(parsed.get("score", 50))
        reason = parsed.get("reason", "No reason given.")
    except Exception as e:
        score, reason = 50, f"[Error grading case: {e}]"
    return {"score": min(max(score, 0), 100), "reason": reason}

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
    return results[:10]

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
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload():
    if 'pdf' not in request.files:
        return redirect(url_for('index'))
    f = request.files['pdf']
    if f.filename == '':
        return redirect(url_for('index'))
    if f and allowed_file(f.filename):
        name = secure_filename(f.filename)
        path = os.path.join(app.config['UPLOAD_FOLDER'], name)
        f.save(path)
        return jsonify({'filename': name, 'text': f"[Extracted text from {name}]"})
    return redirect(url_for('index'))

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
    context = user_contexts.setdefault(context_id, {"description": "", "clarify_attempts": 0})

    # -------------------------------------------------
    # Add extra info if refining
    # -------------------------------------------------
    if adding_info and message:
        context["description"] += " " + message

    # -------------------------------------------------
    # Step 1: Ask clarifying questions
    # -------------------------------------------------
    if not clarified and not clarification_answers and clarify_attempts < 2:
        questions = ask_clarifying_questions(message)
        context["clarify_attempts"] = clarify_attempts + 1
        context["description"] += " " + message
        return jsonify({
            "status": "clarifying",
            "questions": questions,
            "clarify_attempts": context["clarify_attempts"],
            "context_id": context_id
        })

    # -------------------------------------------------
    # Step 2: Merge all text
    # -------------------------------------------------
    combined_text = (context["description"] + " " + message).strip()
    if clarification_answers:
        if isinstance(clarification_answers, list):
            combined_text += " " + " ".join(clarification_answers)
        else:
            combined_text += " " + str(clarification_answers)

    context["description"] = combined_text

    # -------------------------------------------------
    # Step 3: Summarize & query
    # -------------------------------------------------
    summary = summarize_case(combined_text)
    search_query = generate_query(summary)

    # -------------------------------------------------
    # Step 4: Fetch from CourtListener
    # -------------------------------------------------
    try:
        cases = query_courtlistener(search_query)
    except Exception as e:
        return jsonify({"status": "error", "message": f"CourtListener error: {e}"}), 500

    # -------------------------------------------------
    # Step 5: Grade each case
    # -------------------------------------------------
    results = []
    for c in cases:
        grading = grade_case(summary, c['title'], c['snippet'])
        results.append({
            **c,
            "relevance_score": grading["score"],
            "relevance_reason": grading["reason"]
        })

    # Sort by descending relevance
    results.sort(key=lambda x: x["relevance_score"], reverse=True)

    # -------------------------------------------------
    # Step 6: Return results
    # -------------------------------------------------
    return jsonify({
        "status": "results",
        "context_id": context_id,
        "query": search_query,
        "cases": results
    })

# =====================================================
# RUN
# =====================================================
if __name__ == '__main__':
    print("🚀 Lawyer Assistant (Multi-Agent) is running...")
    app.run(host='0.0.0.0', port=5000, debug=True)

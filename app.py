import os
import tempfile
import requests
from flask import Flask, render_template, request, jsonify, redirect, url_for
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from google import genai

# ------------------------------
# Load environment and init
# ------------------------------
load_dotenv()

PROJECT_ID = os.getenv("PROJECT_ID")
GOOGLE_CLOUD_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
COURTLISTENER_TOKEN = os.getenv("COURTLISTENER_TOKEN")

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = tempfile.gettempdir()
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024
ALLOWED_EXTENSIONS = {'pdf'}

def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ------------------------------
# Vertex AI / Gemini Setup
# ------------------------------
client = genai.Client(
    vertexai=True,
    project=PROJECT_ID,
    location=GOOGLE_CLOUD_LOCATION,
)
chat_session = client.chats.create(model="gemini-2.5-flash", history=[])

def call_genai(prompt: str) -> str:
    try:
        response = chat_session.send_message(prompt)
        return response.text
    except Exception as e:
        return f"[Gemini API error: {e}]"

# ------------------------------
# Query Generation Helper
# ------------------------------
def generate_query(text: str) -> str:
    prompt = (
        f"Generate a short keyword search query (around 10 keywords max) "
        f"for finding relevant cases on CourtListener from this text:\n\n{text}"
    )
    try:
        response = chat_session.send_message(prompt)
        return response.text.strip() or text
    except Exception as e:
        print(f"[generate_query error]: {e}")
        return text

# ------------------------------
# CourtListener API
# ------------------------------
def query_courtlistener(query: str):
    base = "https://www.courtlistener.com/api/rest/v4/search/"
    headers = {}
    if COURTLISTENER_TOKEN:
        headers["Authorization"] = f"Token {COURTLISTENER_TOKEN}"

    params = {"q": query}
    resp = requests.get(base, params=params, headers=headers, timeout=20)
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
        court = item.get("court") or {}
        decision_date = item.get("decision_date") or ""
        results.append({
            "title": title,
            "citation": citation,
            "pdf_link": pdf_link,
            "snippet": snippet,
            "court": court,
            "decision_date": decision_date,
        })
    return results[:10]

# ------------------------------
# Routes
# ------------------------------
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload():
    if 'pdf' not in request.files:
        return redirect(url_for('index'))
    file = request.files['pdf']
    if file.filename == '':
        return redirect(url_for('index'))
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        return jsonify({'filename': filename, 'text': f"[Mock text extracted from {filename}]"})
    return redirect(url_for('index'))

# ------------------------------
# Chat Route (Clarifying Flow)
# ------------------------------
@app.route('/chat', methods=['POST'])
def chat():
    payload = request.json or {}
    user_input = payload.get("message", "").strip()
    clarified = payload.get("clarified", False)
    clarification_answers = payload.get("clarification_answers", None)
    clarify_attempts = int(payload.get("clarify_attempts", 0) or 0)

    # Step 1: Ask clarifying questions (up to 2 rounds)
    if not clarified and not clarification_answers and clarify_attempts < 2:
        prompt = (
            f"You are a legal assistant. Given the user's input: \"{user_input}\", "
            f"ask up to 3 brief clarifying questions (about jurisdiction, main issue, and key facts). "
            f"Return each question on a new line."
        )
        response = call_genai(prompt)
        questions = [q.strip() for q in response.splitlines() if q.strip()]
        return jsonify({
            "status": "clarifying",
            "questions": questions,
            "clarify_attempts": clarify_attempts + 1
        })

    # Step 2: Combine user input + answers
    parts = [user_input]
    if clarification_answers:
        if isinstance(clarification_answers, list):
            parts.extend(clarification_answers)
        else:
            parts.append(str(clarification_answers))
    query_text = " ".join(parts).strip()

    # Step 3: Summarize full case description into a search query
    summary_prompt = (
        f"Summarize the following case outline into a concise, factual search description:\n\n{query_text}"
    )
    summarized_info = call_genai(summary_prompt)
    search_query = generate_query(summarized_info)

    # Step 4: Query CourtListener
    try:
        cases = query_courtlistener(search_query)
    except Exception as e:
        return jsonify({"status": "error", "message": f"Search failed: {e}"}), 500

    # Step 5: Generate rationales
    results = []
    for c in cases:
        rationale_prompt = (
            f"Explain in 1–2 sentences why the case '{c['title']}' "
            f"might be relevant to the user's issue: {summarized_info}"
        )
        rationale = call_genai(rationale_prompt)
        c["relevance"] = rationale
        results.append(c)

    return jsonify({
        "status": "results",
        "query": search_query,
        "cases": results
    })

if __name__ == '__main__':
    print("Starting Lawyer Assistant App...")
    app.run(host='0.0.0.0', port=5000, debug=True)

import os
import tempfile
import requests
from flask import Flask, render_template, request, redirect, url_for, jsonify
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

# Load env vars
load_dotenv()

PROJECT_ID = os.getenv("PROJECT_ID")          # GCP project
GOOGLE_CLOUD_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
COURTLISTENER_TOKEN = os.getenv("COURTLISTENER_TOKEN")

# PDF upload settings
app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = tempfile.gettempdir()
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB
ALLOWED_EXTENSIONS = {'pdf'}

def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ------------------------------
# Vertex AI / Gemini setup
# ------------------------------
from google import genai
from google.genai.types import HttpOptions

client = genai.Client(
    vertexai=True,
    project=PROJECT_ID,
    location=GOOGLE_CLOUD_LOCATION,
    http_options=HttpOptions(api_version="v1")
)

def call_genai(prompt: str, max_output_tokens: int = 256) -> str:
    """
    Calls Gemini 2.5-flash via Vertex AI to generate clarifying questions or rationale.
    """
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        return response.text
    except Exception as e:
        return f"[Gemini API error: {e}]"

# ------------------------------
# CourtListener search
# ------------------------------
def query_courtlistener(q: str, page_size: int = 10):
    base = "https://www.courtlistener.com/api/rest/v3/search/"
    params = {"q": q, "page_size": page_size}
    headers = {}
    if COURTLISTENER_TOKEN:
        headers["Authorization"] = f"Token {COURTLISTENER_TOKEN}"

    resp = requests.get(base, params=params, headers=headers, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    results = []
    for item in data.get("results", []):
        results.append({
            "title": item.get("caseName") or item.get("name") or "Untitled",
            "citation": item.get("citation") or "",
            "pdf_link": item.get("absolute_url") or "",
            "snippet": item.get("snippet") or ""
        })
    return results

# ------------------------------
# PDF extraction (placeholder)
# ------------------------------
def extract_pdf_text(path: str) -> str:
    return f"[Mock extracted text from {os.path.basename(path)}]"

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
        extracted_text = extract_pdf_text(filepath)
        return jsonify({'filename': filename, 'text': extracted_text})
    return redirect(url_for('index'))

@app.route('/chat', methods=['POST'])
def chat():
    payload = request.json or {}
    user_input = payload.get("message", "")
    clarified = payload.get("clarified", False)
    clarification_answers = payload.get("clarification_answers", None)

    # Stage 1: Ask clarifying questions
    if not clarified:
        try:
            q_text = call_genai(
                f"You are a legal assistant. Ask 3 short clarifying questions about this case: {user_input}"
            )
        except Exception as e:
            q_text = f"[Error generating clarifying questions: {e}]"

        questions = [q.strip() for q in q_text.splitlines() if q.strip()]
        if len(questions) == 0 and q_text:
            questions = [q_text]

        return jsonify({
            "status": "clarifying",
            "questions": questions,
            "raw": q_text
        })

    # Stage 2: Perform CourtListener search
    query_pieces = [user_input]
    if clarification_answers:
        if isinstance(clarification_answers, list):
            query_pieces.extend(clarification_answers)
        else:
            query_pieces.append(str(clarification_answers))
    query_str = " ".join([p for p in query_pieces if p])

    try:
        cases = query_courtlistener(query_str, page_size=10)
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Error querying CourtListener: {e}"
        }), 500

    # Stage 3: Generate rationale per case
    rationales = []
    for c in cases:
        snippet = c.get("snippet") or c.get("title") or ""
        prompt_for_rationale = (
            f"You are a legal research assistant. Given the user's query: '{query_str}',\n"
            f"and the following case title and snippet: '{c.get('title')} - {snippet}',\n"
            "write a 1-2 sentence explanation why this case might be relevant (focus on legal issue similarity, factual similarity, and jurisdiction authority)."
        )
        try:
            rationale_text = call_genai(prompt_for_rationale)
        except Exception as e:
            rationale_text = f"[rationale generation error: {e}]"
        rationales.append(rationale_text)

    for idx, c in enumerate(cases):
        c["relevance"] = rationales[idx] if idx < len(rationales) else ""

    return jsonify({
        "status": "results",
        "query": query_str,
        "cases": cases
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

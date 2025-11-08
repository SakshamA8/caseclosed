import os
import tempfile
from flask import Flask, render_template, request, redirect, url_for, jsonify
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
COURTLISTENER_TOKEN = os.getenv("COURTLISTENER_TOKEN")

import openai
openai.api_key = OPENAI_API_KEY

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = tempfile.gettempdir()
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB

# Allowed extensions for uploaded PDFs
ALLOWED_EXTENSIONS = {'pdf'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Home page: upload or prompt
@app.route('/')
def index():
    return render_template('index.html')

# Upload PDF route
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
        # Here you can parse PDF and extract text using PyMuPDF/pdfminer
        extracted_text = "Mock extracted text from PDF"
        return jsonify({'filename': filename, 'text': extracted_text})
    return jsonify({'filename': filename, 'text': extracted_text})


# Chat endpoint for clarifying questions and case retrieval
@app.route('/chat', methods=['POST'])
def chat():
    # user_input = request.json.get('message')

    # Stage 1: Clarifying questions (mock)
    clarifying_questions = [
        "What jurisdiction is your case in?",
        "Briefly describe the legal issue.",
        "Are there any relevant facts you want to highlight?"
    ]

    # Stage 2: Once clarified, retrieve cases (mock)
    retrieved_cases = [
        {
            'title': 'Mock Case 1',
            'citation': '123 U.S. 456',
            'pdf_link': '#',
            'relevance': 'This case shares the same legal principle regarding contract interpretation.'
        },
        {
            'title': 'Mock Case 2',
            'citation': '789 F.2d 101',
            'pdf_link': '#',
            'relevance': 'Factual similarity regarding employment law.'
        }
    ]
    
    # Generate clarifying questions or rationale
    response = openai.ChatCompletion.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are an assistant that asks clarifying questions to find relevant legal cases."},
            {"role": "user", "content": user_input}
        ]
    )
    clarifying_questions = response['choices'][0]['message']['content']
    retrieved_cases = []

    return jsonify({
        'questions': clarifying_questions,
        'cases': retrieved_cases
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

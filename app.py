# app.py

import os
from datetime import date, datetime
from io import BytesIO
import base64
import requests
import google.generativeai as genai

from flask import Flask, request, jsonify, send_file, make_response
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from dotenv import load_dotenv

# .env file se secret keys load karna (local testing ke liye)
load_dotenv()

# --- App aur Database ka Initial Setup ---
app = Flask(__name__)

# Database ka URL aur Secret Key environment variables se uthayenge
# Render par yeh settings hum dashboard mein karenge
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
migrate = Migrate(app, db) # Database changes (migrations) ko handle karne ke liye

# --- Secret Keys ko Environment se Load Karna ---
GOOGLE_TTS_API_KEY = os.environ.get('GOOGLE_TTS_API_KEY')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY') # Gemini ki key bhi add karni hogi

# --- Plan ke hisab se daily limits ---
PLAN_LIMITS = {
    'free': 3,
    'advanced': 100,
    'premium': 500
}

# --- Database Models (Tables ka Blueprint) ---

class User(db.Model):
    """User table ka structure define karta hai."""
    id = db.Column(db.Integer, primary_key=True)
    uid = db.Column(db.String(120), unique=True, nullable=False) # Firebase se aayega
    email = db.Column(db.String(120), unique=True, nullable=False)
    plan_type = db.Column(db.String(50), default='free', nullable=False) # 'free', 'advanced', 'premium'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class GenerationLog(db.Model):
    """Har user ki daily voice generation ka hisaab rakhta hai."""
    id = db.Column(db.Integer, primary_key=True)
    user_uid = db.Column(db.String(120), nullable=False)
    generation_date = db.Column(db.Date, nullable=False, default=date.today)
    count = db.Column(db.Integer, default=0, nullable=False)

# --- Helper Function: Gemini se Emotional SSML Banwana ---

def get_emotional_ssml(text_to_convert):
    """Saada text ko emotional SSML mein convert karta hai."""
    try:
        if not GEMINI_API_KEY:
            # Agar Gemini ki key set nahi hai, toh saada text hi use karo
            return f"<speak>{text_to_convert}</speak>"
            
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        prompt = f"""
        Analyze the sentiment of the following text and convert it into SSML 
        to make it sound natural and emotional. Use <prosody>, <emphasis>, 
        and <break> tags effectively. Only output the final SSML string, 
        wrapped in <speak> tags.

        TEXT: "{text_to_convert}"
        """
        
        response = model.generate_content(prompt)
        ssml_text = response.text.strip()
        
        if ssml_text.startswith("<speak>") and ssml_text.endswith("</speak>"):
            return ssml_text
        else:
            # Fallback agar Gemini sahi format na de
            return f"<speak>{text_to_convert}</speak>"

    except Exception as e:
        print(f"Error calling Gemini API: {e}")
        # Error ki soorat mein bhi saada text use karo
        return f"<speak>{text_to_convert}</speak>"

# --- API Endpoints ---

@app.route('/register', methods=['POST'])
def register_user():
    """Naye user ko database mein register karta hai."""
    data = request.json
    uid, email = data.get('uid'), data.get('email')
    if not uid or not email:
        return jsonify({"error": "UID and Email are required"}), 400
    
    if not User.query.filter_by(uid=uid).first():
        new_user = User(uid=uid, email=email, plan_type='free')
        db.session.add(new_user)
        db.session.commit()
        return jsonify({"message": "User registered successfully"}), 201
    return jsonify({"message": "User already exists"}), 200

@app.route('/get-user-status/<string:uid>', methods=['GET'])
def get_user_status(uid):
    """User ka current plan aur aaj ki usage batata hai."""
    user = User.query.filter_by(uid=uid).first_or_404(description="User not found")
    today_log = GenerationLog.query.filter_by(user_uid=uid, generation_date=date.today()).first()
    generations_today = today_log.count if today_log else 0
    
    return jsonify({
        "uid": user.uid,
        "plan_type": user.plan_type,
        "generations_today": generations_today,
        "daily_limit": PLAN_LIMITS.get(user.plan_type, 0)
    })
    
@app.route('/verify-purchase', methods=['POST'])
def verify_purchase():
    """Google Play se aayi purchase ko verify karke user ka plan upgrade karta hai."""
    data = request.json
    uid, new_plan = data.get('uid'), data.get('plan_type')

    # Yahan Google Play Developer API se token verify karne ka asal logic aayega
    is_verified = True # Abhi ke liye hum isko 'True' maan lete hain

    if is_verified:
        user = User.query.filter_by(uid=uid).first()
        if user and new_plan in PLAN_LIMITS:
            user.plan_type = new_plan
            db.session.commit()
            return jsonify({"message": f"Plan updated to {new_plan}"}), 200
    
    return jsonify({"error": "Purchase verification failed"}), 400

@app.route('/speak', methods=['POST'])
def speak():
    """Main voice generation endpoint with Gatekeeper and Gemini logic."""
    data = request.json
    uid = data.get('uid')
    ad_proof = data.get('ad_proof_token')

    if not uid:
        return jsonify({"error": "User UID is required"}), 400

    user = User.query.filter_by(uid=uid).first_or_404(description="User not found")
    
    # --- Gatekeeper Logic ---
    today = date.today()
    log = GenerationLog.query.filter_by(user_uid=uid, generation_date=today).first()
    
    if not log:
        log = GenerationLog(user_uid=uid, generation_date=today, count=0)
        db.session.add(log)

    current_limit = PLAN_LIMITS.get(user.plan_type, 0)
    remaining_credits = current_limit - log.count

    if remaining_credits <= 0 and not ad_proof:
        return jsonify({"error": "Daily limit reached. Watch an ad for more."}), 429

    if remaining_credits > 0:
        log.count += 1
    
    db.session.commit()
    # --- Gatekeeper Logic End ---

    final_ssml_text = get_emotional_ssml(data.get("text", ""))

    payload = {
        "input": {"ssml": final_ssml_text},
        "voice": {
            "languageCode": "-".join(data.get("voice", "en-US-Wavenet-D").split("-")[:2]),
            "name": data.get("voice", "en-US-Wavenet-D")
        },
        "audioConfig": {"audioEncoding": "MP3"}
    }

    try:
        url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={GOOGLE_TTS_API_KEY}"
        response = requests.post(url, json=payload)
        response.raise_for_status()

        audio_content = response.json().get("audioContent")
        audio_bytes = BytesIO(base64.b64decode(audio_content))
        
        # Audio ke sath-sath header mein credits bhi wapas bhejte hain
        final_response = make_response(send_file(audio_bytes, mimetype="audio/mpeg"))
        final_response.headers['X-Remaining-Credits'] = current_limit - log.count
        return final_response

    except requests.exceptions.HTTPError as err:
        return jsonify({"error": "Google API error", "details": str(err.response.text)}), 500
    except Exception as e:
        return jsonify({"error": "An unexpected server error occurred", "details": str(e)}), 500

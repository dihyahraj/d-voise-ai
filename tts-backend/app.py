import os
from datetime import date, datetime
from io import BytesIO
import base64
import requests

from flask import Flask, request, jsonify, send_file, make_response
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from dotenv import load_dotenv

# .env file se secret keys load karna (local testing ke liye)
load_dotenv()

# --- App aur Database ka Initial Setup ---
app = Flask(__name__)

# Database ka URL environment variable se uthayenge
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
migrate = Migrate(app, db)

# --- Secret Keys ---
GOOGLE_TTS_API_KEY = os.environ.get('GOOGLE_TTS_API_KEY')

# --- Plan Limits & Mood Presets ---
PLAN_LIMITS = {
    'free': 3,
    'advanced': 100,
    'premium': 500
}

# Rule-based "Emotional" presets
MOOD_PRESETS = {
    'sad':      {'rate': 0.85, 'pitch': -4.0},
    'angry':    {'rate': 1.1,  'pitch': -2.0},
    'excited':  {'rate': 1.15, 'pitch': 2.0},
    'default':  {'rate': 1.0,  'pitch': 0.0}
}

# --- Database Models (Tables ka Blueprint) ---

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    uid = db.Column(db.String(120), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    plan_type = db.Column(db.String(50), default='free', nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class GenerationLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_uid = db.Column(db.String(120), nullable=False)
    generation_date = db.Column(db.Date, nullable=False, default=date.today)
    count = db.Column(db.Integer, default=0, nullable=False)

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
    uid = data.get('uid')
    new_plan = data.get('plan_type')

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
    """Main voice generation endpoint with Gatekeeper and Mood logic."""
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

    # --- Mood ke Hisab se Pitch/Rate Set Karna ---
    selected_mood = data.get("mood", "default")
    preset = MOOD_PRESETS.get(selected_mood, MOOD_PRESETS['default'])
    
    speaking_rate = preset['rate']
    pitch = preset['pitch']
    
    text_to_speak = data.get("text", "")
    voice_name = data.get("voice", "en-US-Wavenet-D")

    payload = {
        "input": {"text": text_to_speak},
        "voice": {
            "languageCode": "-".join(voice_name.split("-")[:2]),
            "name": voice_name
        },
        "audioConfig": {
            "audioEncoding": "MP3",
            "speakingRate": speaking_rate,
            "pitch": pitch
        }
    }

    try:
        url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={GOOGLE_TTS_API_KEY}"
        response = requests.post(url, json=payload)
        response.raise_for_status()

        audio_content = response.json().get("audioContent")
        audio_bytes = BytesIO(base64.b64decode(audio_content))
        
        final_response = make_response(send_file(audio_bytes, mimetype="audio/mpeg"))
        final_response.headers['X-Remaining-Credits'] = current_limit - log.count
        return final_response

    except requests.exceptions.HTTPError as err:
        return jsonify({"error": "Google API error", "details": str(err.response.text)}), 500
    except Exception as e:
        return jsonify({"error": "An unexpected server error occurred", "details": str(e)}), 500

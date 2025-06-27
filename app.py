import os
from datetime import date, datetime
from io import BytesIO
import base64
import requests

from flask import Flask, request, jsonify, send_file, make_response
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from dotenv import load_dotenv

# Environment variables load karna (API Key, etc. ke liye)
load_dotenv()

# --- App aur Database ka Initial Setup ---
app = Flask(__name__)

# Database ka URL aur Secret Key environment variables se uthayenge
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
migrate = Migrate(app, db) # Database changes ko handle karne ke liye

# Plan ke hisab se daily limits
PLAN_LIMITS = {
    'free': 3,
    'advanced': 100,
    'premium': 500
}

# --- Database Models (Tables ka Blueprint) ---

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    uid = db.Column(db.String(120), unique=True, nullable=False) # Firebase se aayega
    email = db.Column(db.String(120), unique=True, nullable=False)
    plan_type = db.Column(db.String(50), default='free', nullable=False) # free, advanced, premium
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
    """User ka current plan aur status batata hai."""
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
    token = data.get('purchaseToken')
    new_plan = data.get('plan_type') # e.g., 'advanced'

    # Yahan Google Play Developer API se token verify karne ka logic aayega
    # Abhi ke liye hum isko simple rakhte hain
    is_verified = True # Maan lete hain ke verification successful hai

    if is_verified:
        user = User.query.filter_by(uid=uid).first()
        if user and new_plan in PLAN_LIMITS:
            user.plan_type = new_plan
            db.session.commit()
            return jsonify({"message": f"Plan updated to {new_plan}"}), 200
    
    return jsonify({"error": "Purchase verification failed"}), 400


@app.route('/speak', methods=['POST'])
def speak():
    """Main voice generation endpoint with Gatekeeper logic."""
    data = request.json
    uid = data.get('uid')
    ad_proof = data.get('ad_proof_token') # Ad dekhne ka proof

    if not uid:
        return jsonify({"error": "User UID is required"}), 400

    user = User.query.filter_by(uid=uid).first_or_404(description="User not found")
    
    # --- Gatekeeper Logic ---
    today = date.today()
    log = GenerationLog.query.filter_by(user_uid=uid, generation_date=today).first()
    
    if not log: # Agar aaj ki pehli request hai
        log = GenerationLog(user_uid=uid, generation_date=today, count=0)
        db.session.add(log)

    current_limit = PLAN_LIMITS.get(user.plan_type, 0)
    remaining_credits = current_limit - log.count

    if remaining_credits <= 0 and not ad_proof:
        # Credits nahi hain aur ad ka proof bhi nahi hai
        return jsonify({"error": "Daily limit reached. Watch an ad for more."}), 429

    # Agar ad ka proof hai, toh credit nahi kaateinge (kyunki 0 ahe)
    # Agar credits hain, toh 1 credit kaat lo
    if remaining_credits > 0:
        log.count += 1

    db.session.commit()
    # --- Gatekeeper Logic End ---

    # --- TTS Generation Logic ---
    text_to_speak = data.get("text")
    voice_name = data.get("voice", "en-US-Wavenet-D")
    # ... baki ka TTS logic ...
    
    GOOGLE_API_KEY = os.environ.get("GOOGLE_TTS_API_KEY")
    # ... (Baki ka Google API call wala code bilkul same rahega)
    # ...

    # --- Response Tayyar Karna ---
    # Farz karo ke audio ban gaya
    # audio_bytes = ... (Google se aaya hua audio)
    
    # Ab response tayyar karte hain
    # response = make_response(send_file(audio_bytes, mimetype="audio/mpeg"))
    # response.headers['X-Remaining-Credits'] = current_limit - log.count # Header mein credits bhejna
    # return response
    
    # Abhi ke liye testing ke liye, hum ek dummy response bhejte hain
    return jsonify({
        "message": "Voice would be generated here.",
        "remaining_credits_after_this_call": current_limit - log.count,
        "plan": user.plan_type
    })

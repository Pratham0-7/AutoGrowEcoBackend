from flask import Flask, jsonify
from flask_cors import CORS

from routes.auth import auth_bp
from routes.leads import leads_bp
from routes.ai import ai_bp
from routes.onboarding import onboarding_bp
from scheduler import start_scheduler

app = Flask(__name__)

CORS(
    app,
    resources={
        r"/*": {
            "origins": [
                "http://localhost:5173",
                "https://agefrontend.netlify.app",
                "https://ageautomation.in",
                "https://www.ageautomation.in",
            ]
        }
    },
    supports_credentials=True,
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

app.register_blueprint(auth_bp)
app.register_blueprint(leads_bp)
app.register_blueprint(ai_bp)
app.register_blueprint(onboarding_bp)

start_scheduler()


@app.route("/")
def home():
    return jsonify({"status": "ok", "message": "AGE Backend Running 🚀"}), 200


if __name__ == "__main__":
    app.run(debug=False, use_reloader=False)
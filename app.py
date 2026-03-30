from flask import Flask
from flask_cors import CORS
from routes.auth import auth_bp
from routes.leads import leads_bp
from routes.ai import ai_bp
from scheduler import start_scheduler
from routes.onboarding import onboarding_bp

app = Flask(__name__)
CORS(app)

app.register_blueprint(auth_bp)
app.register_blueprint(leads_bp)
app.register_blueprint(ai_bp)
app.register_blueprint(onboarding_bp)
start_scheduler()

@app.route("/")
def home():
    return "AGE Backend Running 🚀"

if __name__ == "__main__":
    app.run(debug=False, use_reloader=False)
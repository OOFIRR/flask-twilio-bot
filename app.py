from flask import Flask
import os 

# אתחול האפליקציה
app = Flask(__name__)

@app.route("/", methods=["GET"])
def index():
    """ראוט בדיקת חיים מינימלי."""
    return "SUCCESS: Minimal Flask server is running! (200 OK)"

if __name__ == "__main__":
    # מאפשר הרצה מקומית באמצעות flask run
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)

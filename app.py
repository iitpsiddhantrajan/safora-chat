import eventlet
eventlet.monkey_patch()

from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from flask import Flask, render_template, request, redirect, session
from flask_socketio import SocketIO, join_room, emit
import sqlite3

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret'

socketio = SocketIO(app, cors_allowed_origins="*")

# Track online users
online_users = {}

# ---------------- DATABASE ---------------- #

def get_db():
    conn = sqlite3.connect("chat.db")
    conn.row_factory = sqlite3.Row
    return conn

def create_table():
    db = get_db()

    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT,
            receiver TEXT,
            message TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            delivered INTEGER DEFAULT 0
        )
    """)

    db.commit()

create_table()

# ---------------- ROUTES ---------------- #

@app.route("/")
def home():
    if "user" not in session:
        return redirect("/login")
    return redirect("/chat")

@app.route("/chat")
def chat():
    if "user" not in session:
        return redirect("/login")

    db = get_db()
    users = db.execute(
        "SELECT username FROM users WHERE username != ?",
        (session["user"],)
    ).fetchall()

    return render_template("chat.html",
                           username=session["user"],
                           users=users)

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        password = generate_password_hash(request.form["password"])

        db = get_db()
        existing_user = db.execute(
            "SELECT * FROM users WHERE username=?",
            (username,)
        ).fetchone()

        if existing_user:
            return "Username already taken!"

        db.execute(
            "INSERT INTO users (username, password) VALUES (?, ?)",
            (username, password)
        )
        db.commit()

        return redirect("/login")

    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        db = get_db()
        user = db.execute(
            "SELECT * FROM users WHERE username=?",
            (username,)
        ).fetchone()

        if user and check_password_hash(user["password"], password):
            session["user"] = username
            return redirect("/chat")

        return "Invalid username or password!"

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# ---------------- SOCKET EVENTS ---------------- #

@socketio.on("connect")
def handle_connect():
    if "user" in session:
        username = session["user"]
        online_users[request.sid] = username
        socketio.emit("update_users", list(online_users.values()))

@socketio.on("disconnect")
def handle_disconnect():
    if request.sid in online_users:
        online_users.pop(request.sid)
        socketio.emit("update_users", list(online_users.values()))

@socketio.on("join")
def on_join(data):
    sender = data["sender"]
    receiver = data["receiver"]

    room = "_".join(sorted([sender, receiver]))
    join_room(room)

    db = get_db()
    messages = db.execute("""
        SELECT * FROM messages 
        WHERE (sender=? AND receiver=?)
        OR (sender=? AND receiver=?)
        ORDER BY id
    """, (sender, receiver, receiver, sender)).fetchall()

    for msg in messages:
        time = msg["timestamp"][11:16]

        emit("message", {
            "id": msg["id"],
            "sender": msg["sender"],
            "message": msg["message"],
            "time": time,
            "delivered": msg["delivered"]
        }, room=request.sid)

@socketio.on("private_message")
def private_message(data):
    sender = data["sender"]
    receiver = data["receiver"]
    message = data["message"]

    room = "_".join(sorted([sender, receiver]))

    db = get_db()
    cursor = db.execute(
        "INSERT INTO messages (sender, receiver, message) VALUES (?, ?, ?)",
        (sender, receiver, message)
    )
    db.commit()

    message_id = cursor.lastrowid
    time = datetime.now().strftime("%H:%M")

    emit("message", {
        "id": message_id,
        "sender": sender,
        "message": message,
        "time": time,
        "delivered": 0
    }, room=room)

@socketio.on("delivered")
def delivered(data):
    message_id = data["id"]

    db = get_db()
    db.execute(
        "UPDATE messages SET delivered=1 WHERE id=?",
        (message_id,)
    )
    db.commit()

    emit("update_tick", {"id": message_id}, broadcast=True)

@socketio.on("typing")
def typing(data):
    sender = data["sender"]
    receiver = data["receiver"]

    room = "_".join(sorted([sender, receiver]))
    emit("show_typing", sender, room=room, include_self=False)

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000)

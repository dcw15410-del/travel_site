import os
import logging
import json
from datetime import datetime
from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, session, jsonify, send_from_directory
)
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit, join_room, leave_room, disconnect
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import pytz
import eventlet

# -----------------------------
# 기본 설정
# -----------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change_this_secret_for_production")

basedir = os.path.abspath(os.path.dirname(__file__))
db_path = os.path.join(basedir, "travel_site.db")
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

UPLOAD_FOLDER = os.path.join(basedir, "static", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif"}

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

# -----------------------------
# DB 모델
# -----------------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    nickname = db.Column(db.String(50), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room = db.Column(db.String(100), nullable=False, default="한국")
    nickname = db.Column(db.String(80), nullable=False)
    text = db.Column(db.String(1000), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# -----------------------------
# 채팅 관련 변수
# -----------------------------
CHAT_ROOMS = ["한국", "일본", "베트남", "필리핀", "태국"]
TIMEZONE_MAP = {
    "한국": "Asia/Seoul",
    "일본": "Asia/Tokyo",
    "베트남": "Asia/Ho_Chi_Minh",
    "필리핀": "Asia/Manila",
    "태국": "Asia/Bangkok"
}

room_members = {room: set() for room in CHAT_ROOMS}
sid_map = {}

def build_room_state_payload():
    counts = {room: len(room_members.get(room, set())) for room in CHAT_ROOMS}
    lists = {}
    for room in CHAT_ROOMS:
        names = []
        for sid in room_members.get(room, set()):
            info = sid_map.get(sid)
            if info:
                names.append(info.get("nick", "익명"))
        lists[room] = names
    return {"counts": counts, "lists": lists}

# -----------------------------
# 라우트
# -----------------------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/chat")
def chat_rooms():
    room_user_list = {r: [sid_map.get(sid, {}).get("nick", "익명") for sid in room_members[r]] for r in CHAT_ROOMS}
    return render_template("chat_rooms.html", room_user_list=room_user_list)

@app.route("/chat/<room>")
def chat(room):
    if "user_id" not in session:
        flash("로그인 후 이용 가능합니다.")
        return redirect(url_for("login"))
    if room not in CHAT_ROOMS:
        flash("존재하지 않는 채팅방입니다.")
        return redirect(url_for("chat_rooms"))
    user = User.query.get(session["user_id"])
    return render_template("chat.html", messages=[], user=user, room=room)

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        nickname = request.form.get("nickname", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        if not all([username, nickname, email, password]):
            flash("모든 항목을 입력해주세요.")
            return redirect(url_for("register"))
        if User.query.filter((User.username==username)|(User.email==email)|(User.nickname==nickname)).first():
            flash("이미 존재하는 아이디/이메일/닉네임입니다.")
            return redirect(url_for("register"))
        pw_hash = generate_password_hash(password)
        user = User(username=username, nickname=nickname, email=email, password_hash=pw_hash)
        db.session.add(user)
        db.session.commit()
        flash("회원가입 완료! 로그인 해주세요.")
        return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            session["user_id"] = user.id
            flash(f"{user.nickname}님 환영합니다.")
            return redirect(url_for("chat_rooms"))
        flash("로그인 실패: 아이디 또는 비밀번호를 확인하세요.")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("user_id", None)
    flash("로그아웃되었습니다.")
    return redirect(url_for("index"))

# -----------------------------
# SocketIO 이벤트
# -----------------------------
@socketio.on("join")
def on_join(data):
    if "user_id" not in session:
        emit("auth_required", {"msg": "로그인이 필요합니다."})
        disconnect()
        return

    room = data.get("room", "한국")
    user = User.query.get(session["user_id"])
    nickname = user.nickname if user else "익명"
    sid = request.sid

    prev_info = sid_map.get(sid)
    prev_room = prev_info.get("room") if prev_info else None
    if prev_room and prev_room != room:
        room_members[prev_room].discard(sid)
        sid_map.pop(sid, None)

    room_members.setdefault(room, set()).add(sid)
    sid_map[sid] = {"nick": nickname, "room": room}

    join_room(room)
    ts = datetime.now().strftime("%H:%M:%S")
    emit("receive_message", {"user": "시스템", "msg": f"{nickname}님이 입장했습니다.", "time": ts}, room=room)
    socketio.emit("room_users_update", build_room_state_payload())

@socketio.on("leave")
def on_leave(data):
    room = data.get("room", "한국")
    user = User.query.get(session["user_id"]) if "user_id" in session else None
    nickname = user.nickname if user else "익명"
    sid = request.sid
    room_members[room].discard(sid)
    sid_map.pop(sid, None)
    leave_room(room)
    ts = datetime.now().strftime("%H:%M:%S")
    emit("receive_message", {"user": "시스템", "msg": f"{nickname}님이 퇴장했습니다.", "time": ts}, room=room)
    socketio.emit("room_users_update", build_room_state_payload())

@socketio.on("disconnect")
def on_disconnect():
    sid = request.sid
    info = sid_map.pop(sid, None)
    if info:
        room = info.get("room")
        if room:
            room_members[room].discard(sid)
            socketio.emit("room_users_update", build_room_state_payload())

@socketio.on("send_message")
def handle_send_message(data):
    if "user_id" not in session:
        emit("auth_required", {"msg": "로그인이 필요합니다."})
        return

    room = data.get("room", "한국")
    text = (data.get("msg", "") or "").strip()
    if not text:
        return

    user = User.query.get(session["user_id"])
    nickname = user.nickname if user else "익명"

    ts = datetime.utcnow()
    msg = Message(room=room, nickname=nickname, text=text, created_at=ts)
    db.session.add(msg)
    db.session.commit()

    emit("receive_message", {"user": nickname, "msg": text, "time": ts.strftime("%H:%M:%S")}, room=room)

# -----------------------------
# 실행
# -----------------------------
with app.app_context():
    db.create_all()
    logger.info(f"DB ensured at {db_path}")

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)

# -----------------------------
# gevent 기반 안정화 버전
# -----------------------------
from gevent import monkey
monkey.patch_all()  # 반드시 최상단에 위치

from flask import Flask, render_template, request, redirect, session, flash, url_for, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit, join_room, leave_room, disconnect
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime
import os, pytz, logging

# -----------------------------
# 기본 설정
# -----------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev_secret")

# DB 설정 (Render PostgreSQL 환경 가정)
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL",
    "sqlite:///travel_site.db"  # 로컬 테스트용 SQLite (파일명 변경)
).replace("postgres://", "postgresql://")

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# SocketIO 설정 (gevent 기반)
socketio = SocketIO(app, cors_allowed_origins="*")

# -----------------------------
# 모델 정의
# -----------------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    nickname = db.Column(db.String(150), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# -----------------------------
# 유틸 함수
# -----------------------------
def current_user():
    if "user_id" in session:
        return User.query.get(session["user_id"])
    return None

# -----------------------------
# 라우트
# -----------------------------
@app.route("/")
def index():
    posts = Post.query.order_by(Post.created_at.desc()).limit(3).all()
    return render_template("index.html", user=current_user(), posts=posts)

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()
        nickname = request.form["nickname"].strip()

        if not username or not password or not nickname:
            flash("모든 필드를 입력하세요.", "danger")
            return redirect(url_for("register"))

        if User.query.filter_by(username=username).first():
            flash("이미 존재하는 사용자입니다.", "danger")
            return redirect(url_for("register"))

        hashed_pw = generate_password_hash(password)
        user = User(username=username, password_hash=hashed_pw, nickname=nickname)
        db.session.add(user)
        db.session.commit()
        flash("회원가입 성공! 로그인 해주세요.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            session["user_id"] = user.id
            flash("로그인 성공!", "success")
            return redirect(url_for("index"))
        else:
            flash("아이디 또는 비밀번호가 잘못되었습니다.", "danger")
            return redirect(url_for("login"))

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("로그아웃 되었습니다.", "info")
    return redirect(url_for("index"))

@app.route("/posts")
def posts():
    posts = Post.query.order_by(Post.created_at.desc()).all()
    return render_template("posts.html", posts=posts, user=current_user())

@app.route("/posts/new", methods=["GET", "POST"])
def new_post():
    if not current_user():
        flash("로그인 후 작성 가능합니다.", "danger")
        return redirect(url_for("login"))

    if request.method == "POST":
        title = request.form["title"]
        content = request.form["content"]

        if not title or not content:
            flash("제목과 내용을 입력하세요.", "danger")
            return redirect(url_for("new_post"))

        post = Post(title=title, content=content)
        db.session.add(post)
        db.session.commit()
        flash("게시글이 작성되었습니다.", "success")
        return redirect(url_for("posts"))

    return render_template("new_post.html", user=current_user())

@app.route("/chat_rooms")
def chat_rooms():
    return render_template("chat_rooms.html", user=current_user())

@app.route("/chat/<room>")
def chat(room):
    if not current_user():
        flash("로그인 후 채팅방에 입장할 수 있습니다.", "danger")
        return redirect(url_for("login"))
    return render_template("chat.html", room=room, user=current_user())

@app.route("/map")
def map_page():
    return render_template("map.html", user=current_user())

# -----------------------------
# SocketIO 이벤트
# -----------------------------
rooms_users = {}  # {room: set(usernames)}

@socketio.on("join")
def handle_join(data):
    room = data["room"]
    user = data["user"]
    join_room(room)

    if room not in rooms_users:
        rooms_users[room] = set()
    rooms_users[room].add(user)

    emit("receive_message", {"msg": f"{user} 님이 입장했습니다.", "user": "SYSTEM", "time": now_time()}, room=room)
    emit("room_users_update", {"lists": {room: list(rooms_users[room])}, "counts": {room: len(rooms_users[room])}}, room=room)

@socketio.on("leave")
def handle_leave(data):
    room = data["room"]
    user = data["user"]
    leave_room(room)

    if room in rooms_users and user in rooms_users[room]:
        rooms_users[room].remove(user)

    emit("receive_message", {"msg": f"{user} 님이 퇴장했습니다.", "user": "SYSTEM", "time": now_time()}, room=room)
    emit("room_users_update", {"lists": {room: list(rooms_users.get(room, []))}, "counts": {room: len(rooms_users.get(room, []))}}, room=room)

@socketio.on("send_message")
def handle_message(data):
    room = data["room"]
    msg = data["msg"]
    user = data["user"]
    emit("receive_message", {"msg": msg, "user": user, "time": now_time()}, room=room)

# -----------------------------
# 헬퍼
# -----------------------------
def now_time():
    tz = pytz.timezone("Asia/Seoul")
    return datetime.now(tz).strftime("%H:%M:%S")

# -----------------------------
# 실행
# -----------------------------
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

import os
import logging
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

# -----------------------------
# SocketIO (eventlet 기반)
# -----------------------------
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


class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    excerpt = db.Column(db.String(300))
    content = db.Column(db.Text, nullable=False)
    image = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


class Subscriber(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room = db.Column(db.String(100), nullable=False, default="한국")
    nickname = db.Column(db.String(80), nullable=False)
    text = db.Column(db.String(1000), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# -----------------------------
# 채팅방 설정
# -----------------------------
CHAT_ROOMS = ["한국", "일본", "베트남", "필리핀", "태국"]
TIMEZONE_MAP = {
    "한국": "Asia/Seoul",
    "일본": "Asia/Tokyo",
    "베트남": "Asia/Ho_Chi_Minh",
    "필리핀": "Asia/Manila",
    "태국": "Asia/Bangkok",
}

room_members = {room: set() for room in CHAT_ROOMS}
sid_map = {}

# -----------------------------
# 템플릿 공통 변수
# -----------------------------
@app.context_processor
def inject_common():
    user = None
    subscribed = False
    if "user_id" in session:
        try:
            user = User.query.get(session["user_id"])
            if user:
                subscribed = Subscriber.query.filter_by(email=user.email).first() is not None
        except Exception:
            pass

    room_times = {}
    for room in CHAT_ROOMS:
        try:
            tz = pytz.timezone(TIMEZONE_MAP[room])
            room_times[room] = datetime.now(tz).strftime("%H:%M:%S")
        except Exception:
            room_times[room] = datetime.utcnow().strftime("%H:%M:%S")

    return dict(
        current_user=user,
        is_subscribed=subscribed,
        chat_rooms=CHAT_ROOMS,
        room_users={r: len(room_members[r]) for r in CHAT_ROOMS},
        room_times=room_times
    )

# -----------------------------
# 라우트 (게시판, 로그인, 구독 등 기존 유지)
# -----------------------------
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route("/")
def index():
    latest_posts = Post.query.order_by(Post.created_at.desc()).limit(3).all()
    return render_template("index.html", posts=latest_posts)

@app.route("/posts")
def posts():
    all_posts = Post.query.order_by(Post.created_at.desc()).all()
    return render_template("posts.html", posts=all_posts)

@app.route("/post/<int:post_id>")
def post_detail(post_id):
    post = Post.query.get_or_404(post_id)
    return render_template("post_detail.html", post=post)

@app.route("/post/new", methods=["GET", "POST"])
def new_post():
    if "user_id" not in session:
        flash("로그인이 필요합니다.")
        return redirect(url_for("login"))

    if request.method == "POST":
        title = request.form["title"]
        content = request.form["content"]
        file = request.files.get("image")

        filename = None
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))

        excerpt = (content[:280] + "...") if len(content) > 280 else content
        post = Post(title=title, content=content, excerpt=excerpt, image=filename, user_id=session["user_id"])
        db.session.add(post)
        db.session.commit()
        flash("게시글이 등록되었습니다.")
        return redirect(url_for("posts"))
    return render_template("new_post.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        nickname = request.form["nickname"].strip()
        email = request.form["email"].strip().lower()
        password = request.form["password"]

        if User.query.filter(
            (User.username == username)
            | (User.email == email)
            | (User.nickname == nickname)
        ).first():
            flash("이미 존재하는 사용자 정보입니다.")
            return redirect(url_for("register"))

        pw_hash = generate_password_hash(password)
        user = User(username=username, nickname=nickname, email=email, password_hash=pw_hash)
        db.session.add(user)
        db.session.commit()
        flash("회원가입 완료!")
        return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            session["user_id"] = user.id
            flash(f"{user.nickname}님 환영합니다!")
            return redirect(url_for("index"))
        flash("로그인 실패")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("user_id", None)
    flash("로그아웃 완료")
    return redirect(url_for("index"))

@app.route("/chat")
def chat_rooms():
    room_user_list = {r: [sid_map.get(sid, {}).get("nick", "익명") for sid in room_members[r]] for r in CHAT_ROOMS}
    return render_template("chat_rooms.html", room_user_list=room_user_list)

@app.route("/chat/<room>")
def chat(room):
    if "user_id" not in session:
        flash("로그인 후 이용해주세요.")
        return redirect(url_for("login"))
    if room not in CHAT_ROOMS:
        flash("존재하지 않는 채팅방입니다.")
        return redirect(url_for("chat_rooms"))
    user = User.query.get(session["user_id"])
    return render_template("chat.html", messages=[], user=user, room=room)

@app.route("/uploads/<filename>")
def uploaded_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

# -----------------------------
# SocketIO 이벤트
# -----------------------------
@socketio.on("join")
def handle_join(data):
    room = data.get("room", "한국")
    user = User.query.get(session.get("user_id"))
    nickname = user.nickname if user else "익명"
    sid = request.sid

    join_room(room)
    room_members[room].add(sid)
    sid_map[sid] = {"nick": nickname, "room": room}

    emit("receive_message", {"user": "시스템", "msg": f"{nickname}님이 입장했습니다."}, room=room)

@socketio.on("send_message")
def handle_message(data):
    user = User.query.get(session.get("user_id"))
    nickname = user.nickname if user else "익명"
    msg = data.get("msg", "").strip()
    room = data.get("room", "한국")
    if not msg:
        return
    message = Message(room=room, nickname=nickname, text=msg)
    db.session.add(message)
    db.session.commit()
    emit("receive_message", {"user": nickname, "msg": msg}, room=room)

@socketio.on("disconnect")
def handle_disconnect():
    sid = request.sid
    info = sid_map.pop(sid, None)
    if info:
        room = info["room"]
        if sid in room_members.get(room, set()):
            room_members[room].discard(sid)
        emit("receive_message", {"user": "시스템", "msg": f"{info['nick']}님이 퇴장했습니다."}, room=room)

# -----------------------------
# 실행
# -----------------------------
with app.app_context():
    db.create_all()

if __name__ == "__main__":
    import eventlet
    import eventlet.wsgi
    port = int(os.environ.get("PORT", 5000))
    eventlet.wsgi.server(eventlet.listen(("0.0.0.0", port)), app)

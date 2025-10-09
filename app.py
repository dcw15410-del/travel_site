import pytz
from datetime import datetime
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash
)
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, join_room, leave_room, emit
from werkzeug.security import generate_password_hash, check_password_hash
import os

# ---------------------
# Flask 기본 설정
# ---------------------
app = Flask(__name__)
app.secret_key = "secret-key"

# SQLite 설정
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///site.db"
db = SQLAlchemy(app)

# SocketIO 설정
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')

# ---------------------
# DB 모델
# ---------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(30), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)

class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100))
    content = db.Column(db.Text)
    author = db.Column(db.String(30))

# ---------------------
# 로그인 시스템
# ---------------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        password = generate_password_hash(request.form["password"])
        if User.query.filter_by(username=username).first():
            flash("이미 존재하는 사용자입니다.")
            return redirect(url_for("register"))
        user = User(username=username, password=password)
        db.session.add(user)
        db.session.commit()
        flash("회원가입 완료. 로그인해주세요.")
        return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            session["username"] = username
            flash("로그인 성공")
            return redirect(url_for("index"))
        else:
            flash("로그인 실패")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("username", None)
    flash("로그아웃되었습니다.")
    return redirect(url_for("index"))

# ---------------------
# 기본 페이지들
# ---------------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/board")
def board():
    posts = Post.query.all()
    return render_template("board.html", posts=posts)

@app.route("/write", methods=["GET", "POST"])
def write():
    if "username" not in session:
        flash("로그인이 필요합니다.")
        return redirect(url_for("login"))
    if request.method == "POST":
        title = request.form["title"]
        content = request.form["content"]
        post = Post(title=title, content=content, author=session["username"])
        db.session.add(post)
        db.session.commit()
        flash("게시글이 등록되었습니다.")
        return redirect(url_for("board"))
    return render_template("write.html")

# ---------------------
# 채팅 기능
# ---------------------
users_in_room = {}

@app.route("/chat")
def chat():
    if "username" not in session:
        flash("로그인이 필요합니다.")
        return redirect(url_for("login"))
    username = session["username"]
    rooms = [
        {"name": "한국", "timezone": "Asia/Seoul"},
        {"name": "일본", "timezone": "Asia/Tokyo"},
        {"name": "베트남", "timezone": "Asia/Ho_Chi_Minh"},
        {"name": "미국", "timezone": "America/New_York"},
        {"name": "프랑스", "timezone": "Europe/Paris"}
    ]
    return render_template("chat.html", username=username, rooms=rooms)

@app.route("/chat/<room>")
def room_chat(room):
    if "username" not in session:
        flash("로그인이 필요합니다.")
        return redirect(url_for("login"))

    timezone_map = {
        "한국": "Asia/Seoul",
        "일본": "Asia/Tokyo",
        "베트남": "Asia/Ho_Chi_Minh",
        "미국": "America/New_York",
        "프랑스": "Europe/Paris"
    }
    tz = pytz.timezone(timezone_map.get(room, "Asia/Seoul"))
    local_time = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
    users = users_in_room.get(room, [])
    return render_template("room.html", room=room, users=users, local_time=local_time)

@socketio.on("join")
def on_join(data):
    username = data["username"]
    room = data["room"]
    join_room(room)
    users_in_room.setdefault(room, []).append(username)
    emit("message", {"msg": f"{username} 님이 입장했습니다."}, to=room)
    emit("user_count", {"count": len(users_in_room[room])}, to=room)

@socketio.on("leave")
def on_leave(data):
    username = data["username"]
    room = data["room"]
    leave_room(room)
    if room in users_in_room and username in users_in_room[room]:
        users_in_room[room].remove(username)
    emit("message", {"msg": f"{username} 님이 퇴장했습니다."}, to=room)
    emit("user_count", {"count": len(users_in_room[room])}, to=room)

@socketio.on("message")
def handle_message(data):
    emit("message", {"msg": f"{data['username']}: {data['msg']}"}, to=data["room"])

# ---------------------
# 실행
# ---------------------
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    socketio.run(app, host="0.0.0.0", port=5000)

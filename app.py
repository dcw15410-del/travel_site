import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request, redirect, session, flash, url_for, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime
import os, pytz, requests, logging

# -----------------------------
# 기본 설정
# -----------------------------
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change_this_secret_for_production")

basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{os.path.join(basedir, 'travel_site.db')}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

UPLOAD_FOLDER = os.path.join(basedir, 'static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

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
    excerpt = db.Column(db.String(300), nullable=True)
    content = db.Column(db.Text, nullable=False)
    image = db.Column(db.String(300), nullable=True)
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
# 채팅방 & 타임존
# -----------------------------
CHAT_ROOMS = ["한국", "일본", "베트남", "필리핀", "태국"]
TIMEZONE_MAP = {
    "한국": "Asia/Seoul",
    "일본": "Asia/Tokyo",
    "베트남": "Asia/Ho_Chi_Minh",
    "필리핀": "Asia/Manila",
    "태국": "Asia/Bangkok"
}

room_users = {room: 0 for room in CHAT_ROOMS}
user_rooms = {}  # sid -> room

# -----------------------------
# 공통 템플릿 변수
# -----------------------------
@app.context_processor
def inject_common():
    user, subscribed = None, False
    if "user_id" in session:
        user = User.query.get(session["user_id"])
        if user:
            subscribed = Subscriber.query.filter_by(email=user.email).first() is not None

    room_times = {}
    for room in CHAT_ROOMS:
        tz = pytz.timezone(TIMEZONE_MAP[room])
        room_times[room] = datetime.now(tz).strftime("%H:%M:%S")

    return dict(
        current_user=user,
        is_subscribed=subscribed,
        chat_rooms=CHAT_ROOMS,
        room_times=room_times,
        room_users=room_users
    )

# -----------------------------
# 기본 라우트
# -----------------------------
@app.route('/')
def index():
    posts = Post.query.order_by(Post.created_at.desc()).limit(3).all()
    return render_template("index.html", posts=posts)

@app.route('/posts')
def posts():
    all_posts = Post.query.order_by(Post.created_at.desc()).all()
    return render_template("posts.html", posts=all_posts)

@app.route('/post/<int:post_id>')
def post_detail(post_id):
    post = Post.query.get_or_404(post_id)
    return render_template("post_detail.html", post=post)

@app.route('/post/new', methods=['GET','POST'])
def new_post():
    if "user_id" not in session:
        flash("로그인이 필요합니다.")
        return redirect(url_for('login'))
    if request.method == 'POST':
        title = request.form['title']
        content = request.form['content']
        file = request.files.get('image')
        filename = None
        if file and file.filename:
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        excerpt = content[:280] + "..." if len(content) > 280 else content
        post = Post(title=title, content=content, excerpt=excerpt, image=filename, user_id=session['user_id'])
        db.session.add(post)
        db.session.commit()
        return redirect(url_for('posts'))
    return render_template("new_post.html")

@app.route('/post/<int:post_id>/delete', methods=['POST'])
def delete_post(post_id):
    post = Post.query.get_or_404(post_id)
    if session.get('user_id') != post.user_id:
        flash("삭제 권한이 없습니다.")
        return redirect(url_for('post_detail', post_id=post_id))
    db.session.delete(post)
    db.session.commit()
    return redirect(url_for('posts'))

# -----------------------------
# 회원가입/로그인
# -----------------------------
@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        user = User(
            username=request.form['username'],
            nickname=request.form['nickname'],
            email=request.form['email'],
            password_hash=generate_password_hash(request.form['password'])
        )
        db.session.add(user)
        db.session.commit()
        flash("회원가입 완료!")
        return redirect(url_for('login'))
    return render_template("register.html")

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username']).first()
        if user and check_password_hash(user.password_hash, request.form['password']):
            session['user_id'] = user.id
            flash("로그인 성공")
            return redirect(url_for('index'))
        flash("로그인 실패")
    return render_template("login.html")

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    flash("로그아웃 완료")
    return redirect(url_for('index'))

# -----------------------------
# 구독
# -----------------------------
@app.route('/subscribe', methods=['POST'])
def subscribe():
    email = request.form['email']
    if not Subscriber.query.filter_by(email=email).first():
        db.session.add(Subscriber(email=email))
        db.session.commit()
    flash("구독 완료")
    return redirect(url_for('index'))

# -----------------------------
# 채팅 라우트
# -----------------------------
@app.route('/chat')
def chat_rooms():
    return render_template("chat_rooms.html")

@app.route('/chat/<room>')
def chat(room):
    if room not in CHAT_ROOMS:
        flash("없는 채팅방")
        return redirect(url_for('chat_rooms'))
    user = User.query.get(session['user_id'])
    return render_template("chat.html", room=room, user=user)

# -----------------------------
# 지도 & 환율
# -----------------------------
@app.route('/map')
def map_view():
    return render_template("map.html")

@app.route('/currency')
def currency_page():
    return render_template("currency.html")

@app.route('/convert_currency')
def convert_currency():
    from_cur = request.args.get("from")
    to_cur = request.args.get("to")
    amount = float(request.args.get("amount", 1))
    url = f"https://api.exchangerate.host/convert?from={from_cur}&to={to_cur}&amount={amount}"
    resp = requests.get(url).json()
    return jsonify({"result": resp.get("result"), "rate": resp.get("info", {}).get("rate")})

# -----------------------------
# SocketIO 이벤트
# -----------------------------
@socketio.on("join")
def on_join(data):
    room = data["room"]
    user = data["user"]
    join_room(room)
    room_users[room] += 1
    emit("receive_message", {"user": "시스템", "msg": f"{user}님이 입장했습니다.", "time": datetime.now().strftime("%H:%M:%S")}, room=room)
    socketio.emit("room_users_update", room_users)

@socketio.on("leave")
def on_leave(data):
    room = data["room"]
    user = data["user"]
    leave_room(room)
    room_users[room] = max(0, room_users[room]-1)
    emit("receive_message", {"user": "시스템", "msg": f"{user}님이 퇴장했습니다.", "time": datetime.now().strftime("%H:%M:%S")}, room=room)
    socketio.emit("room_users_update", room_users)

@socketio.on("send_message")
def handle_message(data):
    emit("receive_message", {"user": data["user"], "msg": data["msg"], "time": datetime.now().strftime("%H:%M:%S")}, room=data["room"])

# -----------------------------
# DB 생성
# -----------------------------
with app.app_context():
    db.create_all()

if __name__ == "__main__":
    socketio.run(app, debug=True)

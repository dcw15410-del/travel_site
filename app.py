import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request, redirect, session, flash, url_for, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit, join_room, leave_room, disconnect
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime
import os, pytz, eventlet.green.requests as grequests, logging

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
user_rooms = {}

# -----------------------------
# 공통 템플릿 변수
# -----------------------------
@app.context_processor
def inject_user_and_subscription_and_times():
    user, subscribed = None, False
    if "user_id" in session:
        user = User.query.get(session["user_id"])
        if user:
            subscribed = Subscriber.query.filter_by(email=user.email).first() is not None

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
        room_times=room_times,
        room_users=room_users,
        timezone_map=TIMEZONE_MAP
    )

# -----------------------------
# 메인 / 글 관련 라우트
# -----------------------------
@app.route('/')
def index():
    latest_posts = Post.query.order_by(Post.created_at.desc()).limit(3).all()
    return render_template("index.html", posts=latest_posts)

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
        flash("글 작성은 로그인 후 가능합니다.")
        return redirect(url_for('login'))
    if request.method == 'POST':
        title = request.form.get('title','').strip()
        content = request.form.get('content','').strip()
        file = request.files.get('image')

        if not title or not content:
            flash("제목과 내용을 입력하세요.")
            return redirect(url_for('new_post'))

        filename = None
        if file and file.filename:
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

        excerpt = (content[:280] + '...') if len(content) > 280 else content
        post = Post(title=title, content=content, excerpt=excerpt,
                    image=filename, user_id=session['user_id'])
        db.session.add(post)
        db.session.commit()
        flash("새 글이 등록되었습니다.")
        return redirect(url_for('posts'))
    return render_template("new_post.html")

@app.route('/post/<int:post_id>/delete', methods=['POST'])
def delete_post(post_id):
    if "user_id" not in session:
        flash("로그인 후 삭제 가능합니다.")
        return redirect(url_for('login'))

    post = Post.query.get_or_404(post_id)
    if post.user_id != session["user_id"]:
        flash("본인 글만 삭제할 수 있습니다.")
        return redirect(url_for('post_detail', post_id=post.id))

    db.session.delete(post)
    db.session.commit()
    flash("게시글이 삭제되었습니다.")
    return redirect(url_for('posts'))

# -----------------------------
# 회원가입 / 로그인 / 로그아웃
# -----------------------------
@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username','').strip()
        nickname = request.form.get('nickname','').strip()
        email = request.form.get('email','').strip().lower()
        password = request.form.get('password','').strip()
        if not all([username, nickname, email, password]):
            flash("모든 항목을 입력해주세요.")
            return redirect(url_for('register'))
        if User.query.filter((User.username==username)|(User.email==email)|(User.nickname==nickname)).first():
            flash("이미 존재하는 아이디, 이메일 또는 닉네임입니다.")
            return redirect(url_for('register'))
        pw_hash = generate_password_hash(password)
        user = User(username=username, nickname=nickname, email=email, password_hash=pw_hash)
        db.session.add(user)
        db.session.commit()
        flash("회원가입 완료. 로그인 해주세요.")
        return redirect(url_for('login'))
    return render_template("register.html")

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method=='POST':
        username = request.form.get('username','').strip()
        password = request.form.get('password','').strip()
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            session['user_id'] = user.id
            flash(f"{user.nickname}님 환영합니다.")
            return redirect(url_for('index'))
        flash("로그인 실패: 아이디 또는 비밀번호 확인")
        return redirect(url_for('login'))
    return render_template("login.html")

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    flash("로그아웃되었습니다.")
    return redirect(url_for('index'))

# -----------------------------
# 구독
# -----------------------------
@app.route('/subscribe', methods=['POST'])
def subscribe():
    email = None
    if "user_id" in session:
        user = User.query.get(session['user_id'])
        email = user.email if user else None
    else:
        email = request.form.get('email','').strip().lower()
    if not email:
        flash("이메일이 필요합니다.")
        return redirect(url_for('index'))
    if Subscriber.query.filter_by(email=email).first():
        flash("이미 구독 중입니다.")
    else:
        sub = Subscriber(email=email)
        db.session.add(sub)
        db.session.commit()
        flash("구독이 완료되었습니다.")
    return redirect(request.referrer or url_for('index'))

# -----------------------------
# 채팅 라우트
# -----------------------------
@app.route('/chat')
def chat_rooms():
    return render_template('chat_rooms.html')

@app.route('/chat/<room>')
def chat(room):
    if "user_id" not in session:
        flash("채팅은 로그인 후 이용 가능합니다.")
        return redirect(url_for('login'))
    if room not in CHAT_ROOMS:
        flash("존재하지 않는 채팅방입니다.")
        return redirect(url_for('chat_rooms'))
    user = User.query.get(session['user_id'])
    messages = Message.query.filter_by(room=room).order_by(Message.created_at.asc()).all()
    return render_template('chat.html', messages=messages, user=user, room=room)

# -----------------------------
# 지도 라우트 (Google Maps Eventlet-safe)
# -----------------------------
@app.route('/map')
def map_view():
    default_location = {'lat': 37.4602, 'lng': 126.4407}  # 인천공항
    return render_template("map.html", location=default_location)

# -----------------------------
# SocketIO 이벤트
# -----------------------------
@socketio.on('join')
def on_join(data):
    room = data.get('room','한국')
    nickname = data.get('user','익명')
    join_room(room)
    room_users[room] = room_users.get(room,0) + 1
    user_rooms[request.sid] = room
    ts = datetime.now().strftime("%H:%M:%S")
    emit('receive_message', {'user':'시스템','msg':f'{nickname}님이 입장했습니다.','time':ts}, room=room)
    socketio.emit('room_users_update', room_users, broadcast=True)

@socketio.on('leave')
def on_leave(data):
    room = data.get('room','한국')
    nickname = data.get('user','익명')
    leave_room(room)
    if room in room_users and room_users[room] > 0:
        room_users[room] -= 1
    user_rooms.pop(request.sid, None)
    ts = datetime.now().strftime("%H:%M:%S")
    emit('receive_message', {'user':'시스템','msg':f'{nickname}님이 퇴장했습니다.', 'time':ts}, room=room)
    socketio.emit('room_users_update', room_users, broadcast=True)

@socketio.on('disconnect')
def on_disconnect():
    room = user_rooms.pop(request.sid, None)
    if room and room in room_users and room_users[room] > 0:
        room_users[room] -= 1
        socketio.emit('room_users_update', room_users, broadcast=True)

@socketio.on('send_message')
def handle_send_message(data):
    room = data.get('room','한국')
    text = data.get('msg','').strip()
    nickname = data.get('user') or (session.get('user_id') and User.query.get(session['user_id']).nickname) or "익명"
    if not text:
        return
    ts = datetime.utcnow()
    try:
        m = Message(room=room, nickname=nickname, text=text, created_at=ts)
        db.session.add(m)
        db.session.commit()
    except Exception:
        db.session.rollback()
    emit('receive_message', {'user': nickname, 'msg': text, 'time': ts.strftime("%H:%M:%S")}, room=room)

# -----------------------------
# DB 생성
# -----------------------------
with app.app_context():
    db.create_all()

# -----------------------------
# 실행
# -----------------------------
if __name__ == '__main__':
    debug_mode = os.environ.get('FLASK_DEBUG', '0') == '1'
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=debug_mode)

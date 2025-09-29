# app.py
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
import requests

# -----------------------------
# 기본 설정
# -----------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change_this_secret_for_production")

# DB 파일 travel_site.db (프로젝트 루트)
basedir = os.path.abspath(os.path.dirname(__file__))
db_path = os.path.join(basedir, 'travel_site.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{db_path}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# 업로드 폴더
UPLOAD_FOLDER = os.path.join(basedir, 'static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*")  # async_mode 자동 선택

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
                names.append(info.get('nick', '익명'))
        lists[room] = names
    return {"counts": counts, "lists": lists}

# -----------------------------
# 템플릿 공통 변수
# -----------------------------
@app.context_processor
def inject_user_and_subscription_and_times():
    user = None
    subscribed = False
    if "user_id" in session:
        try:
            user = User.query.get(session["user_id"])
            if user:
                subscribed = Subscriber.query.filter_by(email=user.email).first() is not None
        except Exception:
            user = None

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
        room_users={r: len(room_members[r]) for r in CHAT_ROOMS},
        timezone_map=TIMEZONE_MAP
    )

# -----------------------------
# 유틸
# -----------------------------
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# -----------------------------
# 라우트
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
        if file and file.filename and allowed_file(file.filename):
            filename = secure_filename(f"{int(datetime.utcnow().timestamp())}_{file.filename}")
            try:
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            except Exception:
                logger.exception("파일 저장 실패")
                flash("이미지 업로드 중 오류가 발생했습니다.")

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

    if post.image:
        try:
            path = os.path.join(app.config['UPLOAD_FOLDER'], post.image)
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            logger.exception("이미지 삭제 실패")

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
    room_user_list = {r: [sid_map.get(sid, {}).get('nick','익명') for sid in room_members[r]] for r in CHAT_ROOMS}
    return render_template('chat_rooms.html', room_user_list=room_user_list)

@app.route('/chat/<room>')
def chat(room):
    # 채팅방 입장 자체는 로그인 필요 (보안)
    if "user_id" not in session:
        flash("채팅은 로그인 후 이용 가능합니다.")
        return redirect(url_for('login'))
    if room not in CHAT_ROOMS:
        flash("존재하지 않는 채팅방입니다.")
        return redirect(url_for('chat_rooms'))
    user = User.query.get(session['user_id'])
    # 최근 메시지 (예: 마지막 200개)
    messages = Message.query.filter_by(room=room).order_by(Message.created_at.asc()).limit(200).all()
    return render_template('chat.html', messages=messages, user=user, room=room)

# -----------------------------
# 지도 라우트
# -----------------------------
@app.route('/map')
def map_view():
    # map.html은 기본 장소 좌표를 자체적으로 사용하므로 별도 변수를 넘기지 않습니다.
    return render_template("map.html")

# -----------------------------
# 환율 계산
# -----------------------------
@app.route('/convert_currency')
def convert_currency_api():
    from_cur = request.args.get("from")
    to_cur = request.args.get("to")
    try:
        amount = float(request.args.get('amount', '1') or '1')
    except Exception:
        amount = 1.0

    currencies = {
        "USD":"미국 달러","KRW":"대한민국 원","JPY":"일본 엔",
        "EUR":"유로","CNY":"중국 위안","THB":"태국 바트",
        "VND":"베트남 동","PHP":"필리핀 페소"
    }

    if not from_cur or not to_cur:
        return jsonify({"error": "통화 파라미터가 필요합니다."}), 400

    if from_cur not in currencies or to_cur not in currencies:
        return jsonify({"error": "지원하지 않는 통화입니다."}), 400

    try:
        url = f"https://api.exchangerate.host/convert?from={from_cur}&to={to_cur}&amount={amount}"
        resp = requests.get(url, timeout=8)
        data = resp.json()

        if data.get("result") is not None:
            result = round(data.get("result", 0), 4)
            rate = round(data.get("info", {}).get("rate", 0), 6)
            return jsonify({"result": result, "rate": rate})

        # 백업 API 호출 (문법 오류 수정됨)
        backup_url = f"https://open.er-api.com/v6/latest/{from_cur}"
        r2 = requests.get(backup_url, timeout=8)
        data2 = r2.json()

        # open.er-api 결과 포맷은 success + rates
        if data2.get("result") == "success" and to_cur in data2.get("rates", {}):
            rate = data2["rates"][to_cur]
            return jsonify({"result": round(amount * rate, 4), "rate": round(rate, 6)})
        # 일부 API는 다른 키를 사용 -> try fallback
        if data2.get("rates") and to_cur in data2.get("rates"):
            rate = data2["rates"][to_cur]
            return jsonify({"result": round(amount * rate, 4), "rate": round(rate, 6)})

        return jsonify({"error": "환율 계산 실패"}), 500

    except Exception as e:
        logger.exception("환율 API 오류")
        return jsonify({"error": f"서버 오류: {str(e)}"}), 500

@app.route('/currency')
def currency_page():
    currencies = {
        "USD":"미국 달러","KRW":"대한민국 원","JPY":"일본 엔",
        "EUR":"유로","CNY":"중국 위안","THB":"태국 바트",
        "VND":"베트남 동","PHP":"필리핀 페소"
    }
    return render_template("currency.html", currencies=currencies)

# -----------------------------
# SocketIO 이벤트 (서버 측 로그인 검사 추가)
# -----------------------------
@socketio.on('join')
def on_join(data):
    # 서버에서 반드시 로그인 여부 확인
    if "user_id" not in session:
        # 클라이언트에게 인증 필요 신호 전달 후 연결 강제 종료
        emit('auth_required', {'msg': '로그인이 필요합니다. 로그인 후 다시 시도하세요.'})
        disconnect()
        return

    room = data.get('room','한국')
    # 사용자는 서버 세션 기반 닉네임 우선으로 사용
    user = User.query.get(session['user_id'])
    nickname = user.nickname if user else data.get('user','익명')
    sid = request.sid

    prev_info = sid_map.get(sid)
    prev_room = prev_info.get('room') if prev_info else None
    if prev_room and prev_room != room:
        if sid in room_members.get(prev_room, set()):
            room_members[prev_room].discard(sid)
        sid_map.pop(sid, None)

    room_members.setdefault(room, set()).add(sid)
    sid_map[sid] = {'nick': nickname, 'room': room}

    join_room(room)
    ts = datetime.now().strftime("%H:%M:%S")
    emit('receive_message', {'user':'시스템','msg':f'{nickname}님이 입장했습니다.','time':ts}, room=room)
    # 전체 브로드캐스트(인자 없이 emit하면 전체로 전달)
    socketio.emit('room_users_update', build_room_state_payload())

@socketio.on('leave')
def on_leave(data):
    room = data.get('room','한국')
    # 서버 세션 기반 닉네임 사용
    user = User.query.get(session['user_id']) if "user_id" in session else None
    nickname = user.nickname if user else data.get('user','익명')
    sid = request.sid

    if sid in room_members.get(room, set()):
        room_members[room].discard(sid)
    sid_map.pop(sid, None)

    leave_room(room)
    ts = datetime.now().strftime("%H:%M:%S")
    emit('receive_message', {'user':'시스템','msg':f'{nickname}님이 퇴장했습니다.', 'time':ts}, room=room)
    socketio.emit('room_users_update', build_room_state_payload())

@socketio.on('disconnect')
def on_disconnect(*args):
    # 안전하게 어떤 인자라도 받도록 처리
    sid = request.sid
    info = sid_map.pop(sid, None)
    if info:
        room = info.get('room')
        if room and sid in room_members.get(room, set()):
            room_members[room].discard(sid)
            socketio.emit('room_users_update', build_room_state_payload())

@socketio.on('send_message')
def handle_send_message(data):
    # 반드시 로그인 사용자만 전송 허용
    if "user_id" not in session:
        emit('auth_required', {'msg': '로그인이 필요합니다.'})
        return

    room = data.get('room','한국')
    text = (data.get('msg','') or '').strip()
    if not text:
        return

    user = User.query.get(session['user_id'])
    nickname = user.nickname if user else data.get('user','익명')

    ts = datetime.utcnow()
    try:
        m = Message(room=room, nickname=nickname, text=text, created_at=ts)
        db.session.add(m)
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("메시지 DB 저장 실패")

    emit('receive_message', {'user': nickname, 'msg': text, 'time': ts.strftime("%H:%M:%S")}, room=room)

# -----------------------------
# 정적 업로드 파일 제공
# -----------------------------
@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# -----------------------------
# DB 생성 및 실행
# -----------------------------
with app.app_context():
    db.create_all()
    logger.info(f"DB ensured at {db_path}")

if __name__ == '__main__':
    debug_mode = os.environ.get('FLASK_DEBUG', '0') == '1'
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=debug_mode)

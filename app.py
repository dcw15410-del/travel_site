from flask import Flask, render_template, request, redirect, session, flash, url_for, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime
import os, pytz, requests, logging, json

# -----------------------------
# 기본 설정
# -----------------------------
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change_this_secret_for_production")

basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{os.path.join(basedir, 'travel_site.db')}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# 업로드 폴더
UPLOAD_FOLDER = os.path.join(basedir, 'static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

db = SQLAlchemy(app)

# NOTE:
# - eventlet/greenlet 환경에서 종종 "do not call blocking functions from the mainloop" 오류가 발생합니다.
# - 안정적으로 작동하도록 SocketIO의 async_mode를 'threading'으로 지정합니다.
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

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

# 실시간 접속자 관리 (메모리)
# room_members: room -> set of sids
# sid_map: sid -> {"nick": nickname, "room": room}
room_members = {room: set() for room in CHAT_ROOMS}
sid_map = {}

def build_room_state_payload():
    """room counts and lists for broadcasting"""
    counts = {room: len(room_members.get(room, set())) for room in CHAT_ROOMS}
    lists = {}
    # build nickname lists (unique nicknames) for each room
    for room in CHAT_ROOMS:
        names = []
        for sid in room_members.get(room, set()):
            info = sid_map.get(sid)
            if info:
                names.append(info.get('nick', '익명'))
        lists[room] = names
    return {"counts": counts, "lists": lists}

# -----------------------------
# 공통 템플릿 변수
# -----------------------------
@app.context_processor
def inject_user_and_subscription_and_times():
    user, subscribed = None, False
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

    # 파일 삭제(선택적)
    if post.image:
        try:
            path = os.path.join(app.config['UPLOAD_FOLDER'], post.image)
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

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
    # pass current room user list names optionally (not necessary; socket will update)
    room_user_list = {r: [sid_map.get(sid, {}).get('nick','익명') for sid in room_members[r]] for r in CHAT_ROOMS}
    return render_template('chat_rooms.html', room_user_list=room_user_list)

@app.route('/chat/<room>')
def chat(room):
    if "user_id" not in session:
        flash("채팅은 로그인 후 이용 가능합니다.")
        return redirect(url_for('login'))
    if room not in CHAT_ROOMS:
        flash("존재하지 않는 채팅방입니다.")
        return redirect(url_for('chat_rooms'))
    user = User.query.get(session['user_id'])
    return render_template('chat.html', messages=[], user=user, room=room)

# -----------------------------
# 지도 라우트
# -----------------------------
@app.route('/map')
def map_view():
    return render_template("map.html")

# -----------------------------
# 환율 계산 API
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
        # 외부 요청이 블로킹이 될 수 있으므로 timeout을 짧게 두고 예외 처리 강화
        url = f"https://api.exchangerate.host/convert?from={from_cur}&to={to_cur}&amount={amount}"
        resp = requests.get(url, timeout=7)
        data = resp.json()

        if data.get("result") is not None:
            result = round(data.get("result", 0), 4)
            rate = data.get("info", {}).get("rate", 0)
            return jsonify({"result": result, "rate": rate})

        # backup
        backup_url = f"https://open.er-api.com/v6/latest/{from_cur}"
        r2 = requests.get(backup_url, timeout=7)
        data2 = r2.json()
        if data2.get("result") == "success" and to_cur in data2.get("rates", {}):
            rate = data2["rates"][to_cur]
            return jsonify({"result": round(amount * rate, 4), "rate": rate})

        return jsonify({"error": "환율 계산 실패"}), 500
    except Exception as e:
        # 문제 시 내부 로그 남기고 에러 리턴
        logging.exception("환율 API 호출 오류")
        return jsonify({"error": f"서버 오류: {str(e)}"}), 500

# -----------------------------
# SocketIO 이벤트 (안정적 인원/목록 관리)
# -----------------------------
@socketio.on('join')
def on_join(data):
    """
    data: { room: <room>, user: <nickname> }
    - 안전하게 이전 방에서 빠져나오고 새 방에 입장
    - room_members, sid_map 업데이트, 전체 브로드캐스트
    """
    room = data.get('room', '한국')
    nickname = data.get('user') or '익명'
    sid = request.sid

    # 이미 같은 방이면 nothing (하지만 counts 전송)
    current = sid_map.get(sid)
    prev_room = current.get('room') if current else None
    if prev_room == room:
        emit('room_users_update', build_room_state_payload())
        return

    # 이전 방에서 제거
    if prev_room:
        if sid in room_members.get(prev_room, set()):
            room_members[prev_room].discard(sid)
        sid_map.pop(sid, None)

    # 새 방에 추가
    room_members.setdefault(room, set()).add(sid)
    sid_map[sid] = {'nick': nickname, 'room': room}

    # 알림 메시지와 인원/목록 브로드캐스트
    ts = datetime.now().strftime("%H:%M:%S")
    emit('receive_message', {'user':'시스템', 'msg':f'{nickname}님이 입장했습니다.', 'time':ts}, room=room)
    socketio.emit('room_users_update', build_room_state_payload(), broadcast=True)

@socketio.on('leave')
def on_leave(data):
    """
    data: { room: <room optional>, user: <nickname optional> }
    - sid를 기준으로 제거(더 안전)
    """
    sid = request.sid
    nickname = data.get('user', '익명')
    # pop sid mapping
    info = sid_map.pop(sid, None)
    target_room = data.get('room') or (info.get('room') if info else None)
    if target_room:
        if sid in room_members.get(target_room, set()):
            room_members[target_room].discard(sid)

        ts = datetime.now().strftime("%H:%M:%S")
        emit('receive_message', {'user':'시스템', 'msg':f'{nickname}님이 퇴장했습니다.', 'time':ts}, room=target_room)
        socketio.emit('room_users_update', build_room_state_payload(), broadcast=True)

@socketio.on('disconnect')
def on_disconnect():
    # 브라우저 닫기 / 새로고침 처리
    sid = request.sid
    info = sid_map.pop(sid, None)
    if info:
        room = info.get('room')
        if room and sid in room_members.get(room, set()):
            room_members[room].discard(sid)
            socketio.emit('room_users_update', build_room_state_payload(), broadcast=True)

@socketio.on('send_message')
def handle_send_message(data):
    room = data.get('room','한국')
    text = data.get('msg','').strip()
    # 클라이언트에서 전달된 user 우선, 없으면 세션 기반 nickname 사용
    nickname = data.get('user') or (session.get('user_id') and User.query.get(session['user_id']).nickname) or "익명"
    if not text:
        return
    ts = datetime.utcnow()
    # DB 저장
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
# 실행 (개발용)
# -----------------------------
if __name__ == '__main__':
    debug_mode = os.environ.get('FLASK_DEBUG', '0') == '1'
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=debug_mode)

from flask import Flask, render_template, request, redirect, session, flash, url_for, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

# ----------------------------
# 앱 초기화
# ----------------------------
app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///db.sqlite3'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
socketio = SocketIO(app, async_mode='threading')

# ----------------------------
# DB 모델
# ----------------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)

class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class ChatMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), nullable=False)
    message = db.Column(db.Text, nullable=False)
    room = db.Column(db.String(50), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

# ----------------------------
# 메인 페이지
# ----------------------------
@app.route('/')
def index():
    with app.app_context():
        latest_posts = Post.query.order_by(Post.created_at.desc()).limit(3).all()
    return render_template("index.html", posts=latest_posts)

# ----------------------------
# 회원가입
# ----------------------------
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        with app.app_context():
            if User.query.filter_by(username=username).first():
                flash("이미 존재하는 사용자입니다.")
            else:
                hashed_pw = generate_password_hash(password)
                new_user = User(username=username, password=hashed_pw)
                db.session.add(new_user)
                db.session.commit()
                flash("회원가입 완료")
                return redirect(url_for('login'))
    return render_template('register.html')

# ----------------------------
# 로그인
# ----------------------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        with app.app_context():
            user = User.query.filter_by(username=username).first()
            if user and check_password_hash(user.password, password):
                session['user'] = user.username
                flash("로그인 성공")
                return redirect(url_for('index'))
            else:
                flash("아이디 또는 비밀번호가 잘못되었습니다.")
    return render_template('login.html')

# ----------------------------
# 로그아웃
# ----------------------------
@app.route('/logout')
def logout():
    session.pop('user', None)
    flash("로그아웃 되었습니다.")
    return redirect(url_for('index'))

# ----------------------------
# 글 작성
# ----------------------------
@app.route('/new_post', methods=['GET', 'POST'])
def new_post():
    if 'user' not in session:
        flash("로그인이 필요합니다.")
        return redirect(url_for('login'))

    if request.method == 'POST':
        title = request.form['title']
        content = request.form['content']
        with app.app_context():
            post = Post(title=title, content=content)
            db.session.add(post)
            db.session.commit()
        flash("글 작성 완료")
        return redirect(url_for('index'))
    return render_template('new_post.html')

# ----------------------------
# 채팅방 리스트 페이지
# ----------------------------
@app.route('/chat')
def chat_rooms():
    return render_template('chat_rooms.html')

# ----------------------------
# SocketIO 이벤트
# ----------------------------
@socketio.on('join')
def handle_join(data):
    username = data['username']
    room = data['room']
    join_room(room)
    emit('status', {'msg': f'{username}님이 입장했습니다.'}, room=room)

@socketio.on('message')
def handle_message(data):
    username = data['username']
    room = data['room']
    message = data['message']
    with app.app_context():
        chat_msg = ChatMessage(username=username, message=message, room=room)
        db.session.add(chat_msg)
        db.session.commit()
    emit('message', {'username': username, 'message': message}, room=room)

@socketio.on('leave')
def handle_leave(data):
    username = data['username']
    room = data['room']
    leave_room(room)
    emit('status', {'msg': f'{username}님이 나갔습니다.'}, room=room)

# ----------------------------
# 앱 실행
# ----------------------------
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    socketio.run(app, host='0.0.0.0', port=5000)

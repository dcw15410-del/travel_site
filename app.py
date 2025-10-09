import os
import logging
from datetime import datetime
from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, session, jsonify
)
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.security import generate_password_hash, check_password_hash

# -----------------------------
# Flask 기본 설정
# -----------------------------
app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret-key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
socketio = SocketIO(app)

# -----------------------------
# 데이터베이스 모델 정의
# -----------------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)

class ChatRoom(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey('chat_room.id'), nullable=False)
    username = db.Column(db.String(50), nullable=False)
    text = db.Column(db.String(500), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

# -----------------------------
# DB 자동 생성 (핵심 부분)
# -----------------------------
with app.app_context():
    db_path = os.path.join(os.getcwd(), "site.db")
    if not os.path.exists(db_path):
        print("✅ site.db가 존재하지 않아 새로 생성합니다...")
        db.create_all()
        print("✅ 데이터베이스 테이블 생성 완료")

# -----------------------------
# 라우트
# -----------------------------
@app.route('/')
def index():
    return render_template('index.html')

# 회원가입
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        if User.query.filter_by(username=username).first():
            flash('이미 존재하는 사용자명입니다.')
            return redirect(url_for('register'))

        hashed_pw = generate_password_hash(password)
        new_user = User(username=username, password=hashed_pw)
        db.session.add(new_user)
        db.session.commit()

        flash('회원가입이 완료되었습니다. 로그인 해주세요.')
        return redirect(url_for('login'))
    return render_template('register.html')

# 로그인
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            session['username'] = username
            flash('로그인 성공!')
            return redirect(url_for('chat'))
        else:
            flash('잘못된 사용자명 또는 비밀번호입니다.')
    return render_template('login.html')

# 로그아웃
@app.route('/logout')
def logout():
    session.pop('username', None)
    flash('로그아웃 되었습니다.')
    return redirect(url_for('login'))

# 채팅 페이지
@app.route('/chat')
def chat():
    if 'username' not in session:
        return redirect(url_for('login'))
    rooms = ChatRoom.query.all()
    return render_template('chat.html', username=session['username'], rooms=rooms)

# -----------------------------
# SocketIO 이벤트
# -----------------------------
@socketio.on('join_room')
def handle_join(data):
    room = data['room']
    join_room(room)
    emit('message', {'username': '시스템', 'text': f"{data['username']} 님이 입장했습니다."}, room=room)

@socketio.on('leave_room')
def handle_leave(data):
    room = data['room']
    leave_room(room)
    emit('message', {'username': '시스템', 'text': f"{data['username']} 님이 퇴장했습니다."}, room=room)

@socketio.on('send_message')
def handle_message(data):
    room = data['room']
    msg = Message(room_id=room, username=data['username'], text=data['text'])
    db.session.add(msg)
    db.session.commit()
    emit('message', {'username': data['username'], 'text': data['text']}, room=room)

# -----------------------------
# 서버 실행
# -----------------------------
if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000)

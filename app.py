import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from flask_socketio import SocketIO, join_room, leave_room, emit
import pytz
import requests
import eventlet

# -------------------- eventlet 초기화 --------------------
eventlet.monkey_patch()

# -------------------- Flask 설정 --------------------
app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key_here'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///chat.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# -------------------- DB 모델 --------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room = db.Column(db.String(50), nullable=False)
    username = db.Column(db.String(50), nullable=False)
    content = db.Column(db.String(500))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

# -------------------- 라우팅 --------------------
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
            flash('이미 존재하는 사용자입니다.')
            return redirect(url_for('register'))
        hashed_pw = generate_password_hash(password)
        new_user = User(username=username, password=hashed_pw)
        db.session.add(new_user)
        db.session.commit()
        flash('회원가입 완료! 로그인해주세요.')
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
            return redirect(url_for('index'))
        else:
            flash('로그인 실패! 아이디 또는 비밀번호를 확인하세요.')
    return render_template('login.html')

# 로그아웃
@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('index'))

# 환율 계산기
@app.route('/currency', methods=['GET', 'POST'])
def currency():
    rate = None
    amount = None
    result = None
    from_currency = None
    to_currency = None
    if request.method == 'POST':
        from_currency = request.form['from_currency'].upper()
        to_currency = request.form['to_currency'].upper()
        amount = float(request.form['amount'])
        url = f'https://api.exchangerate.host/latest?base={from_currency}&symbols={to_currency}'
        data = requests.get(url).json()
        if 'rates' in data and to_currency in data['rates']:
            rate = data['rates'][to_currency]
            result = round(amount * rate, 2)
    return render_template('currency.html', rate=rate, amount=amount,
                           result=result, from_currency=from_currency, to_currency=to_currency)

# 지도
@app.route('/map')
def map_view():
    return render_template('map.html')

# 채팅방 목록
@app.route('/chat_rooms')
def chat_rooms():
    if 'username' not in session:
        return redirect(url_for('login'))

    rooms = [
        {'name': '한국', 'timezone': 'Asia/Seoul'},
        {'name': '일본', 'timezone': 'Asia/Tokyo'},
        {'name': '베트남', 'timezone': 'Asia/Ho_Chi_Minh'},
        {'name': '미국', 'timezone': 'America/New_York'}
    ]
    for r in rooms:
        tz = pytz.timezone(r['timezone'])
        r['local_time'] = datetime.now(tz).strftime('%H:%M:%S')
    return render_template('chat_rooms.html', rooms=rooms)

# 개별 채팅방
@app.route('/chat/<room>')
def chat(room):
    if 'username' not in session:
        return redirect(url_for('login'))
    messages = Message.query.filter_by(room=room).order_by(Message.timestamp.asc()).all()
    return render_template('chat.html', room=room, messages=messages, username=session['username'])

# -------------------- Socket.IO 이벤트 --------------------
@socketio.on('join')
def handle_join(data):
    room = data['room']
    username = data['username']
    join_room(room)
    emit('status', {'msg': f'💬 {username}님이 입장했습니다.'}, room=room)

@socketio.on('send_message')
def handle_message(data):
    room = data['room']
    username = data['username']
    msg = data['msg']
    new_msg = Message(room=room, username=username, content=msg)
    db.session.add(new_msg)
    db.session.commit()
    emit('receive_message', {'username': username, 'msg': msg}, room=room)

@socketio.on('leave')
def handle_leave(data):
    room = data['room']
    username = data['username']
    leave_room(room)
    emit('status', {'msg': f'🚪 {username}님이 퇴장했습니다.'}, room=room)

# -------------------- 실행 --------------------
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    print("✅ Flask-SocketIO 서버 실행 중 (eventlet)...")
    socketio.run(app, host='0.0.0.0', port=5000)

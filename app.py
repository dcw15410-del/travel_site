import os
import logging
from datetime import datetime
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, send_from_directory
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from flask_socketio import SocketIO, emit
from werkzeug.utils import secure_filename

# ---------------------------
# Flask App 설정
# ---------------------------
app = Flask(__name__)
app.secret_key = "supersecretkey"
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///chat.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(os.getcwd(), 'uploads')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# ---------------------------
# DB 모델
# ---------------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)

class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    content = db.Column(db.Text, nullable=False)
    author = db.Column(db.String(50), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50))
    msg = db.Column(db.String(500))
    timestamp = db.Column(db.DateTime, default=datetime.now)

# ---------------------------
# 라우팅
# ---------------------------
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = generate_password_hash(request.form['password'])
        if User.query.filter_by(username=username).first():
            flash('이미 존재하는 아이디입니다.')
            return redirect(url_for('register'))
        db.session.add(User(username=username, password=password))
        db.session.commit()
        flash('회원가입 완료! 로그인 해주세요.')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if not user or not check_password_hash(user.password, password):
            flash('아이디 또는 비밀번호가 올바르지 않습니다.')
            return redirect(url_for('login'))
        session['username'] = username
        flash('로그인 성공!')
        return redirect(url_for('home'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('username', None)
    flash('로그아웃 되었습니다.')
    return redirect(url_for('home'))

# ---------------------------
# 게시판
# ---------------------------
@app.route('/posts')
def posts():
    posts = Post.query.order_by(Post.created_at.desc()).all()
    return render_template('posts.html', posts=posts)

@app.route('/new_post', methods=['GET', 'POST'])
def new_post():
    if 'username' not in session:
        flash('로그인이 필요합니다.')
        return redirect(url_for('login'))
    if request.method == 'POST':
        title = request.form['title']
        content = request.form['content']
        post = Post(title=title, content=content, author=session['username'])
        db.session.add(post)
        db.session.commit()
        flash('게시글이 등록되었습니다.')
        return redirect(url_for('posts'))
    return render_template('new_post.html')

@app.route('/post/<int:post_id>')
def post_detail(post_id):
    post = Post.query.get_or_404(post_id)
    return render_template('post_detail.html', post=post)

# ---------------------------
# 채팅
# ---------------------------
@app.route('/chat')
def chat():
    if 'username' not in session:
        flash('로그인이 필요합니다.')
        return redirect(url_for('login'))
    messages = Message.query.order_by(Message.timestamp.asc()).limit(50).all()
    return render_template('chat.html', messages=messages)

@socketio.on('message')
def handle_message(data):
    username = session.get('username', '익명')
    msg = data.get('msg')
    if msg:
        new_msg = Message(username=username, msg=msg)
        db.session.add(new_msg)
        db.session.commit()
        emit('message', {'username': username, 'msg': msg}, broadcast=True)

# ---------------------------
# 파일 업로드 (옵션)
# ---------------------------
@app.route('/upload', methods=['POST'])
def upload():
    if 'file' not in request.files:
        return "파일이 없습니다.", 400
    file = request.files['file']
    if file.filename == '':
        return "파일 이름이 없습니다.", 400
    filename = secure_filename(file.filename)
    file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
    return "업로드 완료!"

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# ---------------------------
# 지도, 환율 페이지
# ---------------------------
@app.route('/map')
def map_page():
    return render_template('map.html')

@app.route('/currency')
def currency_page():
    return render_template('currency.html')

# ---------------------------
# 앱 실행
# ---------------------------
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    with app.app_context():
        db.create_all()
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)

# create_db.py
from app import db, app

with app.app_context():
    db.create_all()
    print("✅ travel_site.db 새로 생성 완료!")

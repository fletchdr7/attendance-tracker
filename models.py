from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class Class(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    code = db.Column(db.String(20), unique=True, nullable=False)
    category = db.Column(db.String(50), nullable=False, default='Leadership Laboratory')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    qr_code_data = db.Column(db.String(500))
    is_active = db.Column(db.Boolean, default=True)
    
    attendance_records = db.relationship('Attendance', backref='class_record', lazy=True)

class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.String(50), unique=True, nullable=True)  # Made nullable for backward compatibility
    first_name = db.Column(db.String(50), nullable=False)
    last_name = db.Column(db.String(50), nullable=False)
    name = db.Column(db.String(100), nullable=False)  # Full name for backward compatibility
    email = db.Column(db.String(100))
    as_year = db.Column(db.String(20), nullable=True)  # AS_Year for grouping Leadership Laboratory and Physical Training
    aero_class = db.Column(db.String(50), nullable=True)  # AERO_Class for grouping Aerospace Class
    aero_class_2 = db.Column(db.String(50), nullable=True)  # AERO_Class_2 for additional Aerospace Class grouping
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    attendance_records = db.relationship('Attendance', backref='student', lazy=True)
    
    __table_args__ = (db.UniqueConstraint('first_name', 'last_name', name='unique_student_name'),)

class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    class_id = db.Column(db.Integer, db.ForeignKey('class.id'), nullable=False)
    scanned_at = db.Column(db.DateTime, default=datetime.utcnow)
    ip_address = db.Column(db.String(50))
    
    __table_args__ = (db.UniqueConstraint('student_id', 'class_id', 'scanned_at', name='unique_attendance'),)
import os
from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from sqlalchemy import func
from datetime import datetime, timedelta
from collections import defaultdict

app = Flask(__name__)
CORS(app)

# Database Configuration
current_dir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_HOST'] = 'afrotcdet320.mysql.pythonanywhere-services.com'
app.config['SQLALCHEMY_DATABASE_NAME'] = 'afrotcdet320$attendance_db'
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{os.path.join(current_dir, 'attendance.db')}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Models
class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.String(20), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100))
    as_year = db.Column(db.String(10))
    flight = db.Column(db.String(50))

class Class(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(50))
    is_active = db.Column(db.Boolean, default=True)

class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    class_id = db.Column(db.Integer, db.ForeignKey('class.id'), nullable=False)
    scanned_at = db.Column(db.DateTime, default=datetime.utcnow)
    session_type = db.Column(db.String(20), default='MANDATORY')

CLASS_CATEGORIES = ['Physical Training', 'Leadership Laboratory', 'Aerospace Class']

def _to_date(date_val):
    if isinstance(date_val, datetime): return date_val.date()
    if isinstance(date_val, str): return datetime.strptime(date_val[:10], '%Y-%m-%d').date()
    return date_val

@app.route('/api/student/my_attendance')
def get_my_attendance():
    try:
        input_sid = request.args.get('student_id')
        student = Student.query.filter_by(student_id=input_sid).first()
        if not student: return jsonify({'error': 'Cadet not found'}), 404

        all_active_classes = Class.query.filter_by(is_active=True).all()
        aero_ids = [c.id for c in all_active_classes if c.category == 'Aerospace Class']
        enrolled_aero = db.session.query(Attendance.class_id).filter(
            Attendance.student_id == student.id, Attendance.class_id.in_(aero_ids)
        ).distinct().first()
        target_aero_id = enrolled_aero[0] if enrolled_aero else None

        analytics = {}
        grouped_history = defaultdict(list)
        weekly_optional_tracker = defaultdict(bool)

        for category in CLASS_CATEGORIES:
            cat_ids = [target_aero_id] if category == 'Aerospace Class' and target_aero_id else \
                      [c.id for c in all_active_classes if c.category == category]
            if not cat_ids:
                analytics[category] = {'attended': 0, 'total': 0, 'percentage': 0, 'trend': ''}
                continue

            offered = db.session.query(Attendance.class_id, func.date(Attendance.scanned_at), Attendance.session_type, Class.name).join(Class).filter(Attendance.class_id.in_(cat_ids)).distinct().all()
            cadet_scans = db.session.query(Attendance.class_id, func.date(Attendance.scanned_at)).filter(Attendance.student_id == student.id, Attendance.class_id.in_(cat_ids)).all()
            cadet_map = {(row[0], str(row[1])) for row in cadet_scans}

            # 1. Weekly Tracker (Did they hit ANY optional this week?)
            for cid, d_raw, s_type, c_name in offered:
                if (s_type or '').upper() == 'OPTIONAL' and (cid, str(d_raw)) in cadet_map:
                    d_obj = _to_date(d_raw)
                    ws = d_obj - timedelta(days=d_obj.weekday())
                    weekly_optional_tracker[ws] = True

            cat_attended, cat_total, trend_flags = 0, 0, []

            # 2. History & Summary Calculation
            for cid, d_raw, s_type, c_name in offered:
                d_str, d_obj = str(d_raw), _to_date(d_raw)
                is_att = (cid, d_str) in cadet_map
                s_type = (s_type or 'MANDATORY').upper()
                ws = d_obj - timedelta(days=d_obj.weekday())
                
                if s_type == 'MANDATORY':
                    cat_total += 1
                    if is_att: cat_attended += 1
                
                if is_att: status = "ATTENDED"
                elif s_type == "MANDATORY": status = "MISSED"
                else: status = "OPTIONAL_OK" if weekly_optional_tracker[ws] else "OPTIONAL_MISSED"
                
                grouped_history[ws.strftime('%b %d, %Y')].append({
                    'date_raw': d_str, 'date_str': d_obj.strftime('%a, %b %d'),
                    'class_name': c_name, 'category': category, 'type': s_type, 'status': status
                })

            offered_sorted = sorted(offered, key=lambda x: str(x[1]), reverse=True)
            for cid, d_raw, s_type, c_name in offered_sorted:
                if (s_type or 'MANDATORY').upper() == 'MANDATORY' and len(trend_flags) < 8:
                    trend_flags.append("✅" if (cid, str(d_raw)) in cadet_map else "❌")

            analytics[category] = {'attended': cat_attended, 'total': cat_total, 'percentage': round((cat_attended/cat_total*100),1) if cat_total > 0 else 0, 'trend': "".join(trend_flags)}

        for week in grouped_history:
            grouped_history[week].sort(key=lambda x: x['date_raw'])

        return jsonify({'name': student.name, 'as_year': student.as_year, 'flight': student.flight, 'analytics': analytics, 'grouped_history': dict(grouped_history)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/student/submit_correction', methods=['POST'])
def submit_correction():
    try:
        data = request.json
        entry = f"--- CORRECTION REQUEST ---\n"
        entry += f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        entry += f"Cadet: {data.get('name')} (ID: {data.get('student_id')})\n"
        entry += f"Date: {data.get('date')}\n"
        entry += f"Reason: {data.get('reason')}\n\n"
        with open(os.path.join(current_dir, 'justifications.txt'), 'a', encoding='utf-8') as f:
            f.write(entry)
        return jsonify({'success': True}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)

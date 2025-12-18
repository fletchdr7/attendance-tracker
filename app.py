from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, text
from datetime import datetime, timedelta, date
import secrets
import pandas as pd
import os
import sys

# Add current directory to Python path for Render deployment
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from models import db, Class, Student, Attendance

app = Flask(__name__)
# Use environment variable for database path if available (for cloud deployment)
# Otherwise use local path
import os
db_path = os.environ.get('DATABASE_URL', 'sqlite:///attendance.db')
# Render.com provides DATABASE_URL, but for SQLite we need to adjust the path
if db_path.startswith('sqlite'):
    # Keep SQLite path as is
    app.config['SQLALCHEMY_DATABASE_URI'] = db_path
else:
    # For PostgreSQL or other databases, use the provided URL
    app.config['SQLALCHEMY_DATABASE_URI'] = db_path
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# Use environment variable for secret key in production, or generate one
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', secrets.token_hex(16))

db.init_app(app)

def migrate_database():
    """Add category column to existing Class table and new columns to Student table if they don't exist"""
    try:
        # First ensure tables exist
        db.create_all()
        
        # Check if category column exists using raw SQL
        with db.engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(class)"))
            columns = [row[1] for row in result]
            
            if 'category' not in columns:
                # Add category column
                conn.execute(text('ALTER TABLE class ADD COLUMN category VARCHAR(50) DEFAULT "Leadership Laboratory"'))
                # Update existing records
                conn.execute(text('UPDATE class SET category = "Leadership Laboratory" WHERE category IS NULL'))
                conn.commit()
                print("Database migrated: Added category column")
            
            # Check Student table columns
            result = conn.execute(text("PRAGMA table_info(student)"))
            student_columns = [row[1] for row in result]
            
            if 'as_year' not in student_columns:
                conn.execute(text('ALTER TABLE student ADD COLUMN as_year VARCHAR(20)'))
                print("Database migrated: Added as_year column to student")
            
            if 'aero_class' not in student_columns:
                conn.execute(text('ALTER TABLE student ADD COLUMN aero_class VARCHAR(50)'))
                print("Database migrated: Added aero_class column to student")
            
            if 'aero_class_2' not in student_columns:
                conn.execute(text('ALTER TABLE student ADD COLUMN aero_class_2 VARCHAR(50)'))
                print("Database migrated: Added aero_class_2 column to student")
            
            conn.commit()
    except Exception as e:
        # If there's an error, try to create all tables fresh
        print(f"Migration check: {e}")
        try:
            db.create_all()
        except:
            pass

# Class schedule dates
CLASS_DATES = [
    date(2026, 1, 15),
    date(2026, 1, 22),
    date(2026, 1, 29),
    date(2026, 2, 5),
    date(2026, 2, 12),
    date(2026, 2, 19),
    date(2026, 2, 26),
    date(2026, 3, 5),
    date(2026, 3, 12),
    date(2026, 3, 19),
    date(2026, 4, 2),
    date(2026, 4, 9),
    date(2026, 4, 16),
    date(2026, 4, 23),
    date(2026, 4, 30),
]

# Class categories
CLASS_CATEGORIES = [
    'Leadership Laboratory',
    'Physical Training',
    'Aerospace Class'
]

# Predefined classes
PREDEFINED_CLASSES = [
    # Aerospace Class
    {
        'name': 'AERO 1020 - Heritage and Values of the USAF II',
        'code': 'AERO1020',
        'category': 'Aerospace Class'
    },
    {
        'name': 'AERO 2020 - Team and Leadership Fundamentals II',
        'code': 'AERO2020',
        'category': 'Aerospace Class'
    },
    {
        'name': 'AERO 3020 - Leading People and Effective Communication II',
        'code': 'AERO3020',
        'category': 'Aerospace Class'
    },
    {
        'name': 'AERO 4020 - National Security/Commissioning Preparation II',
        'code': 'AERO4020',
        'category': 'Aerospace Class'
    },
]

# Categories that can have QR codes generated directly (without individual classes)
CATEGORY_BASED_CATEGORIES = [
    'Leadership Laboratory',
    'Physical Training'
]

# Total sessions per category for attendance percentage calculation
CATEGORY_SESSION_COUNTS = {
    'Leadership Laboratory': 14,
    'Physical Training': 27,
    'Aerospace Class': {
        'AERO1020': 14,
        'AERO2020': 14,
        'AERO3020': 45,
        'AERO4020': 14
    }
}

def ensure_predefined_classes():
    """Ensure all predefined classes exist in the database"""
    for class_info in PREDEFINED_CLASSES:
        existing = Class.query.filter_by(code=class_info['code']).first()
        if not existing:
            legacy_qr_data = f"ATTENDANCE:{class_info['code']}:{datetime.now().isoformat()}"
            
            new_class = Class(
                name=class_info['name'],
                code=class_info['code'],
                category=class_info.get('category', 'Leadership Laboratory'),
                qr_code_data=legacy_qr_data,
                is_active=True
            )
            db.session.add(new_class)
        else:
            # Update existing class category if it doesn't have one
            if not existing.category:
                existing.category = class_info.get('category', 'Leadership Laboratory')
    
    # Ensure category-based classes exist for Leadership Laboratory and Physical Training
    for category in CATEGORY_BASED_CATEGORIES:
        category_code = category.upper().replace(' ', '')
        existing = Class.query.filter_by(code=category_code).first()
        if not existing:
            legacy_qr_data = f"ATTENDANCE:{category_code}:{datetime.now().isoformat()}"
            new_class = Class(
                name=category,
                code=category_code,
                category=category,
                qr_code_data=legacy_qr_data,
                is_active=True
            )
            db.session.add(new_class)
    
    db.session.commit()

def load_students_from_excel():
    """Load students from students.xlsx file into the database"""
    # Use absolute path for cloud deployment compatibility
    excel_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'students.xlsx')
    
    if not os.path.exists(excel_path):
        print(f"Warning: {excel_path} not found. Skipping student import.")
        return
    
    try:
        # Read the Excel file
        df = pd.read_excel(excel_path)
        
        # Try to identify column names (case-insensitive)
        first_name_col = None
        last_name_col = None
        student_id_col = None
        email_col = None
        as_year_col = None
        aero_class_col = None
        aero_class_2_col = None
        
        for col in df.columns:
            col_lower = str(col).lower().strip()
            if 'first' in col_lower and 'name' in col_lower:
                first_name_col = col
            elif 'last' in col_lower and 'name' in col_lower:
                last_name_col = col
            elif 'student' in col_lower and 'id' in col_lower:
                student_id_col = col
            elif 'email' in col_lower:
                email_col = col
            elif 'as_year' in col_lower or 'as year' in col_lower:
                as_year_col = col
            elif 'aero_class' in col_lower and '2' not in col_lower:
                aero_class_col = col
            elif 'aero_class_2' in col_lower or 'aero class 2' in col_lower:
                aero_class_2_col = col
        
        # If we can't find the expected columns, try common alternatives
        if first_name_col is None:
            # Try common variations
            for col in df.columns:
                col_lower = str(col).lower().strip()
                if col_lower in ['first', 'firstname', 'fname', 'given name']:
                    first_name_col = col
                    break
        
        if last_name_col is None:
            for col in df.columns:
                col_lower = str(col).lower().strip()
                if col_lower in ['last', 'lastname', 'lname', 'surname', 'family name']:
                    last_name_col = col
                    break
        
        # If still not found, assume first two columns are first and last name
        if first_name_col is None and len(df.columns) >= 1:
            first_name_col = df.columns[0]
        if last_name_col is None and len(df.columns) >= 2:
            last_name_col = df.columns[1]
        
        if first_name_col is None or last_name_col is None:
            print(f"Error: Could not identify first name and last name columns in {excel_path}")
            print(f"Available columns: {list(df.columns)}")
            return
        
        # Load students
        students_added = 0
        students_updated = 0
        
        for _, row in df.iterrows():
            try:
                first_name = str(row[first_name_col]).strip() if pd.notna(row[first_name_col]) else ''
                last_name = str(row[last_name_col]).strip() if pd.notna(row[last_name_col]) else ''
                
                # Skip rows with missing names
                if not first_name or not last_name or first_name == 'nan' or last_name == 'nan':
                    continue
                
                # Get optional fields
                student_id = None
                if student_id_col and student_id_col in row:
                    student_id_val = row[student_id_col]
                    if pd.notna(student_id_val):
                        student_id = str(student_id_val).strip()
                
                email = None
                if email_col and email_col in row:
                    email_val = row[email_col]
                    if pd.notna(email_val):
                        email = str(email_val).strip()
                
                as_year = None
                if as_year_col and as_year_col in row:
                    as_year_val = row[as_year_col]
                    if pd.notna(as_year_val):
                        as_year = str(as_year_val).strip()
                
                aero_class = None
                if aero_class_col and aero_class_col in row:
                    aero_class_val = row[aero_class_col]
                    if pd.notna(aero_class_val):
                        aero_class = str(aero_class_val).strip()
                
                aero_class_2 = None
                if aero_class_2_col and aero_class_2_col in row:
                    aero_class_2_val = row[aero_class_2_col]
                    if pd.notna(aero_class_2_val):
                        aero_class_2 = str(aero_class_2_val).strip()
                
                full_name = f"{first_name} {last_name}"
                
                # Check if student already exists
                existing_student = Student.query.filter_by(
                    first_name=first_name,
                    last_name=last_name
                ).first()
                
                if existing_student:
                    # Update existing student if needed
                    if student_id and not existing_student.student_id:
                        existing_student.student_id = student_id
                    if email and not existing_student.email:
                        existing_student.email = email
                    if as_year:
                        existing_student.as_year = as_year
                    if aero_class:
                        existing_student.aero_class = aero_class
                    if aero_class_2:
                        existing_student.aero_class_2 = aero_class_2
                    students_updated += 1
                else:
                    # Create new student
                    new_student = Student(
                        first_name=first_name,
                        last_name=last_name,
                        name=full_name,
                        student_id=student_id,
                        email=email,
                        as_year=as_year,
                        aero_class=aero_class,
                        aero_class_2=aero_class_2
                    )
                    db.session.add(new_student)
                    students_added += 1
            except Exception as e:
                print(f"Error processing row: {e}")
                continue
        
        db.session.commit()
        print(f"Loaded students from {excel_path}: {students_added} added, {students_updated} updated")
        
    except Exception as e:
        print(f"Error loading students from {excel_path}: {e}")
        db.session.rollback()

# Run migration and load students on startup
with app.app_context():
    migrate_database()
    load_students_from_excel()

@app.route('/')
def index():
    """Redirect to attendance sign in as default page"""
    return redirect(url_for('attendance_sign_in'))

@app.route('/attendance_summary')
def attendance_summary():
    """Display all attendance records grouped by category"""
    # Ensure predefined classes exist
    ensure_predefined_classes()
    
    # Get all attendance records with student and class information
    all_attendance = db.session.query(
        Attendance,
        Student,
        Class
    ).join(
        Student, Attendance.student_id == Student.id
    ).join(
        Class, Attendance.class_id == Class.id
    ).order_by(
        Attendance.scanned_at.desc()
    ).all()
    
    # Group attendance by category, then by date
    attendance_by_category = {}
    for attendance, student, class_record in all_attendance:
        category = class_record.category
        if category not in attendance_by_category:
            attendance_by_category[category] = {}
        
        # Convert scanned_at to datetime if it's a string
        scanned_datetime = attendance.scanned_at
        if isinstance(scanned_datetime, str):
            try:
                scanned_datetime = datetime.strptime(scanned_datetime, '%Y-%m-%d %H:%M:%S.%f')
            except ValueError:
                try:
                    scanned_datetime = datetime.strptime(scanned_datetime, '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    scanned_datetime = datetime.strptime(scanned_datetime, '%Y-%m-%d')
        
        # Extract date and time components
        if hasattr(scanned_datetime, 'date'):
            date_obj = scanned_datetime.date()
            time_str = scanned_datetime.strftime('%I:%M %p')
            date_str = date_obj.strftime('%B %d, %Y')
            date_key = date_obj.isoformat()  # Use ISO format for sorting
        else:
            date_str = str(scanned_datetime)
            time_str = 'N/A'
            date_key = str(scanned_datetime)
        
        # Group by date within category
        if date_key not in attendance_by_category[category]:
            attendance_by_category[category][date_key] = []
        
        attendance_by_category[category][date_key].append({
            'attendance_id': attendance.id,
            'student_name': student.name,
            'student_first': student.first_name,
            'student_last': student.last_name,
            'class_name': class_record.name,
            'class_code': class_record.code,
            'scanned_at': scanned_datetime,
            'date_str': date_str,
            'time_str': time_str,
            'date_obj': date_obj if hasattr(scanned_datetime, 'date') else None
        })
    
    # Sort dates within each category (newest first) and sort records within each date
    # Also calculate total counts per category
    category_totals = {}
    for category in attendance_by_category:
        # Sort dates (newest first)
        sorted_dates = sorted(attendance_by_category[category].keys(), reverse=True)
        sorted_category = {}
        total_count = 0
        for date_key in sorted_dates:
            # Sort records within date by time (newest first)
            attendance_by_category[category][date_key].sort(key=lambda x: x['scanned_at'], reverse=True)
            sorted_category[date_key] = attendance_by_category[category][date_key]
            total_count += len(attendance_by_category[category][date_key])
        attendance_by_category[category] = sorted_category
        category_totals[category] = total_count
    
    # Get all classes for the template
    all_classes = Class.query.filter_by(is_active=True).all()
    classes_by_category = {}
    for cls in all_classes:
        if cls.category not in classes_by_category:
            classes_by_category[cls.category] = []
        classes_by_category[cls.category].append(cls)
    
    return render_template('index.html', 
                         attendance_by_category=attendance_by_category,
                         categories=CLASS_CATEGORIES,
                         category_totals=category_totals,
                         classes_by_category=classes_by_category,
                         category_based=CATEGORY_BASED_CATEGORIES)

@app.route('/analytics_dashboard')
def analytics_dashboard():
    """Display analytics dashboard with cadet attendance percentages"""
    # Ensure predefined classes exist
    ensure_predefined_classes()
    
    # Get all students
    all_students = Student.query.all()
    
    # Get all classes grouped by category
    all_classes = Class.query.filter_by(is_active=True).all()
    classes_by_category = {}
    for cls in all_classes:
        if cls.category not in classes_by_category:
            classes_by_category[cls.category] = []
        classes_by_category[cls.category].append(cls)
    
    # First, calculate sessions offered per category (unique dates with attendance records)
    sessions_offered = {}
    for category in CLASS_CATEGORIES:
        category_classes = [c for c in all_classes if c.category == category]
        category_class_ids = [c.id for c in category_classes]
        
        if category_class_ids:
            # Count unique dates that have ANY attendance records for this category
            offered_dates = db.session.query(
                func.date(Attendance.scanned_at).label('attendance_date')
            ).filter(
                Attendance.class_id.in_(category_class_ids)
            ).distinct().all()
            sessions_offered[category] = len(offered_dates)
        else:
            sessions_offered[category] = 0
    
    # Special handling for Physical Training: group by week and calculate 2/3 requirement
    pt_category_classes = [c for c in all_classes if c.category == 'Physical Training']
    pt_category_class_ids = [c.id for c in pt_category_classes]
    pt_weeks_data = {}
    
    if pt_category_class_ids:
        # Get all Physical Training attendance dates
        pt_dates = db.session.query(
            func.date(Attendance.scanned_at).label('attendance_date')
        ).filter(
            Attendance.class_id.in_(pt_category_class_ids)
        ).distinct().all()
        
        # Group dates by week (ISO week: year-week)
        for date_row in pt_dates:
            if date_row.attendance_date:
                # Convert string date to date object if needed
                if isinstance(date_row.attendance_date, str):
                    date_obj = datetime.strptime(date_row.attendance_date, '%Y-%m-%d').date()
                else:
                    date_obj = date_row.attendance_date
                # Get ISO week (year, week number)
                iso_year, iso_week, _ = date_obj.isocalendar()
                week_key = f"{iso_year}-W{iso_week:02d}"
                
                if week_key not in pt_weeks_data:
                    pt_weeks_data[week_key] = []
                pt_weeks_data[week_key].append(date_obj)
        
        # Count weeks with sessions
        pt_total_weeks = len(pt_weeks_data)
    else:
        pt_total_weeks = 0
    
    # Calculate attendance for each student
    student_data = []
    for student in all_students:
        student_info = {
            'id': student.id,
            'first_name': student.first_name,
            'last_name': student.last_name,
            'name': student.name,
            'as_year': student.as_year if student.as_year else None,
            'categories': {}
        }
        
        # Calculate attendance for each category
        for category in CLASS_CATEGORIES:
            category_classes = [c for c in all_classes if c.category == category]
            category_class_ids = [c.id for c in category_classes]
            
            if category == 'Physical Training':
                # Special calculation for PT: 2 of 3 per week requirement
                weeks_met_requirement = 0
                total_sessions_attended = 0
                
                if pt_category_class_ids and pt_weeks_data:
                    # Get all PT attendance dates for this student
                    student_pt_dates = db.session.query(
                        func.date(Attendance.scanned_at).label('attendance_date')
                    ).filter(
                        Attendance.student_id == student.id,
                        Attendance.class_id.in_(pt_category_class_ids)
                    ).distinct().all()
                    
                    # Convert to set for quick lookup (convert strings to date objects)
                    student_pt_date_set = set()
                    for row in student_pt_dates:
                        if row.attendance_date:
                            if isinstance(row.attendance_date, str):
                                date_obj = datetime.strptime(row.attendance_date, '%Y-%m-%d').date()
                            else:
                                date_obj = row.attendance_date
                            student_pt_date_set.add(date_obj)
                    
                    # Check each week
                    for week_key, week_dates in pt_weeks_data.items():
                        # Count how many sessions in this week the student attended
                        attended_in_week = sum(1 for d in week_dates if d in student_pt_date_set)
                        total_sessions_attended += attended_in_week
                        
                        # If student attended 2 or more out of the sessions in this week, they met the requirement
                        if attended_in_week >= 2:
                            weeks_met_requirement += 1
                
                # Percentage based on weeks meeting requirement
                percentage = (weeks_met_requirement / pt_total_weeks * 100) if pt_total_weeks > 0 else 0
                
                student_info['categories'][category] = {
                    'attended': weeks_met_requirement,
                    'total': pt_total_weeks,
                    'percentage': round(percentage, 1),
                    'total_sessions_attended': total_sessions_attended,
                    'total_sessions_offered': sessions_offered.get(category, 0)
                }
            elif category == 'Aerospace Class':
                # For Aerospace Class, only calculate for classes the student is enrolled in
                # Determine enrollment by checking which Aerospace classes the student has attendance records for
                student_aero_attendance = db.session.query(
                    Attendance.class_id
                ).filter(
                    Attendance.student_id == student.id,
                    Attendance.class_id.in_(category_class_ids)
                ).distinct().all()
                
                enrolled_class_ids = [row.class_id for row in student_aero_attendance]
                
                if enrolled_class_ids:
                    # Calculate attendance only for enrolled classes
                    attended_sessions = 0
                    total_offered = 0
                    
                    for cls in category_classes:
                        if cls.id in enrolled_class_ids:
                            # Count unique dates for this enrolled class
                            class_attendance_dates = db.session.query(
                                func.date(Attendance.scanned_at).label('attendance_date')
                            ).filter(
                                Attendance.student_id == student.id,
                                Attendance.class_id == cls.id
                            ).distinct().all()
                            attended_sessions += len(class_attendance_dates)
                            
                            # Count unique dates offered for this class
                            class_offered_dates = db.session.query(
                                func.date(Attendance.scanned_at).label('attendance_date')
                            ).filter(
                                Attendance.class_id == cls.id
                            ).distinct().all()
                            total_offered += len(class_offered_dates)
                    
                    percentage = (attended_sessions / total_offered * 100) if total_offered > 0 else 0
                    
                    student_info['categories'][category] = {
                        'attended': attended_sessions,
                        'total': total_offered,
                        'percentage': round(percentage, 1),
                        'enrolled_classes': len(enrolled_class_ids)
                    }
                else:
                    # Student not enrolled in any Aerospace classes
                    student_info['categories'][category] = {
                        'attended': 0,
                        'total': 0,
                        'percentage': 0,
                        'enrolled_classes': 0
                    }
            else:
                # Standard calculation for other categories (Leadership Laboratory)
                attended_sessions = 0
                if category_class_ids:
                    attendance_dates = db.session.query(
                        func.date(Attendance.scanned_at).label('attendance_date')
                    ).filter(
                        Attendance.student_id == student.id,
                        Attendance.class_id.in_(category_class_ids)
                    ).distinct().all()
                    attended_sessions = len(attendance_dates)
                
                # Use sessions offered (not scheduled) for percentage calculation
                total_offered = sessions_offered.get(category, 0)
                percentage = (attended_sessions / total_offered * 100) if total_offered > 0 else 0
                
                student_info['categories'][category] = {
                    'attended': attended_sessions,
                    'total': total_offered,
                    'percentage': round(percentage, 1)
                }
        
        student_data.append(student_info)
    
    # Group students by AS_Year
    students_by_as_year = {}
    for student_info in student_data:
        as_year = student_info.get('as_year') if student_info.get('as_year') else 'Ungrouped'
        if as_year not in students_by_as_year:
            students_by_as_year[as_year] = []
        students_by_as_year[as_year].append(student_info)
    
    # Sort AS_Year groups alphabetically (handle numeric years properly)
    def sort_as_year_key(year):
        """Sort AS_Year values: numeric years first (sorted numerically), then 'Ungrouped'"""
        if year == 'Ungrouped':
            return (1, year)  # Put ungrouped at the end
        # Try to extract numeric part for proper sorting
        try:
            # Extract numbers from the year string (e.g., "100", "200", "250")
            numeric_part = ''.join(filter(str.isdigit, year))
            if numeric_part:
                return (0, int(numeric_part))  # Sort numerically
        except:
            pass
        return (0, year)  # Fallback to alphabetical
    
    # Sort each group alphabetically by last name, then first name
    for as_year in students_by_as_year:
        students_by_as_year[as_year].sort(key=lambda x: (x['last_name'], x['first_name']))
    
    # Sort AS_Year groups
    sorted_as_years = sorted(students_by_as_year.keys(), key=sort_as_year_key)
    students_by_as_year_sorted = {year: students_by_as_year[year] for year in sorted_as_years}
    
    return render_template('cadre_dashboard.html', 
                         students_by_as_year=students_by_as_year_sorted,
                         students=student_data,  # Keep for backward compatibility
                         categories=CLASS_CATEGORIES,
                         sessions_offered=sessions_offered)

@app.route('/attendance_sign_in')
def attendance_sign_in():
    """Display attendance sign in with student checklist"""
    # Ensure predefined classes exist
    ensure_predefined_classes()
    
    # Get all active classes, prioritizing predefined ones
    all_classes = Class.query.filter_by(is_active=True).all()
    
    # Sort to show predefined classes first
    predefined_codes = [c['code'] for c in PREDEFINED_CLASSES]
    sorted_classes = sorted(all_classes, key=lambda x: (
        0 if x.code in predefined_codes else 1,
        x.name
    ))
    
    # Get all students, sorted by last name, then first name
    all_students = Student.query.order_by(Student.last_name, Student.first_name).all()
    
    # Group classes by category
    classes_by_category = {}
    for cls in sorted_classes:
        if cls.category not in classes_by_category:
            classes_by_category[cls.category] = []
        classes_by_category[cls.category].append(cls)
    
    # Group students for different categories
    # For Leadership Laboratory and Physical Training: group by AS_Year
    students_by_as_year = {}
    for student in all_students:
        as_year = student.as_year if student.as_year else 'Ungrouped'
        if as_year not in students_by_as_year:
            students_by_as_year[as_year] = []
        students_by_as_year[as_year].append(student)
    
    # For Aerospace Class: group by AERO_Class (including AERO_Class_2)
    students_by_aero_class = {}
    for student in all_students:
        # Add to primary AERO_Class group
        if student.aero_class:
            aero_class = student.aero_class
            if aero_class not in students_by_aero_class:
                students_by_aero_class[aero_class] = []
            students_by_aero_class[aero_class].append(student)
        
        # Also add to AERO_Class_2 group if it exists
        if student.aero_class_2:
            aero_class_2 = student.aero_class_2
            if aero_class_2 not in students_by_aero_class:
                students_by_aero_class[aero_class_2] = []
            # Only add if not already in this group (avoid duplicates)
            if student not in students_by_aero_class[aero_class_2]:
                students_by_aero_class[aero_class_2].append(student)
    
    # If no grouping, add ungrouped students
    if not students_by_aero_class:
        students_by_aero_class['Ungrouped'] = [s for s in all_students if not s.aero_class and not s.aero_class_2]
    
    return render_template('class_board.html', 
                         classes=sorted_classes, 
                         students=all_students,
                         students_by_as_year=students_by_as_year,
                         students_by_aero_class=students_by_aero_class,
                         classes_by_category=classes_by_category,
                         categories=CLASS_CATEGORIES, 
                         category_based=CATEGORY_BASED_CATEGORIES)

# QR code routes removed - using student checklist instead

@app.route('/api/attendance/<int:class_id>')
def get_attendance(class_id):
    class_record = Class.query.get_or_404(class_id)
    date_filter = request.args.get('date')
    
    query = Attendance.query.filter_by(class_id=class_id)
    
    if date_filter:
        try:
            # Parse as date only to avoid timezone issues
            filter_date = datetime.strptime(date_filter, '%Y-%m-%d').date()
            # Use date-only comparison to avoid timezone conversion issues
            query = query.filter(
                func.date(Attendance.scanned_at) == filter_date
            )
        except ValueError:
            pass
    
    records = query.order_by(Attendance.scanned_at.desc()).all()
    
    result = []
    for record in records:
        # Handle both new format (first_name, last_name) and old format (name only)
        first_name = getattr(record.student, 'first_name', '')
        last_name = getattr(record.student, 'last_name', '')
        if not first_name and not last_name:
            # Fallback for old records
            name_parts = record.student.name.split(maxsplit=1) if record.student.name else ['', '']
            first_name = name_parts[0] if name_parts else ''
            last_name = name_parts[1] if len(name_parts) > 1 else ''
        
        result.append({
            'first_name': first_name,
            'last_name': last_name,
            'student_name': record.student.name,
            'scanned_at': record.scanned_at.isoformat(),
            'ip_address': record.ip_address
        })
    
    return jsonify({
        'class_name': class_record.name,
        'class_code': class_record.code,
        'attendance': result,
        'total': len(result)
    })

@app.route('/api/attendance/add', methods=['POST'])
def add_attendance():
    """Quick add attendance record manually"""
    data = request.json
    first_name = data.get('first_name', '').strip()
    last_name = data.get('last_name', '').strip()
    category = data.get('category', '').strip()
    class_id = data.get('class_id')
    attendance_date = data.get('date', '').strip()
    
    if not first_name or not last_name:
        return jsonify({'error': 'First name and last name are required'}), 400
    
    if not category:
        return jsonify({'error': 'Category is required'}), 400
    
    if not attendance_date:
        return jsonify({'error': 'Date is required'}), 400
    
    try:
        # Parse the date
        date_obj = datetime.strptime(attendance_date, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'Invalid date format'}), 400
    
    # Get or create student
    student = Student.query.filter_by(first_name=first_name, last_name=last_name).first()
    if not student:
        full_name = f"{first_name} {last_name}"
        student = Student(
            first_name=first_name,
            last_name=last_name,
            name=full_name,
            student_id=None
        )
        db.session.add(student)
        db.session.flush()  # Get the student ID
    
    # Get the class
    if category in CATEGORY_BASED_CATEGORIES:
        # For category-based categories, use the category class
        category_code = category.upper().replace(' ', '')
        class_record = Class.query.filter_by(code=category_code, is_active=True).first()
    else:
        # For regular categories, use the provided class_id
        if not class_id:
            return jsonify({'error': 'Class is required for this category'}), 400
        class_record = Class.query.filter_by(id=class_id, is_active=True).first()
        if not class_record or class_record.category != category:
            return jsonify({'error': 'Invalid class for this category'}), 400
    
    if not class_record:
        return jsonify({'error': 'Class not found'}), 404
    
    # Check if attendance already exists for this student, class, and date
    existing = Attendance.query.filter_by(
        student_id=student.id,
        class_id=class_record.id
    ).filter(
        func.date(Attendance.scanned_at) == date_obj
    ).first()
    
    if existing:
        return jsonify({
            'message': f'Attendance already recorded for {date_obj.strftime("%B %d, %Y")}',
            'already_recorded': True
        }), 200
    
    # Create attendance record
    # Combine date with current time
    current_time = datetime.now().time()
    scanned_datetime = datetime.combine(date_obj, current_time)
    
    attendance = Attendance(
        student_id=student.id,
        class_id=class_record.id,
        scanned_at=scanned_datetime,
        ip_address=request.remote_addr or 'Manual Entry'
    )
    db.session.add(attendance)
    db.session.commit()
    
    return jsonify({
        'message': f'Attendance recorded successfully for {class_record.name} on {date_obj.strftime("%B %d, %Y")}',
        'student': {
            'id': student.id,
            'name': student.name,
            'first_name': student.first_name,
            'last_name': student.last_name
        },
        'class': {
            'id': class_record.id,
            'name': class_record.name,
            'code': class_record.code
        },
        'date': date_obj.isoformat()
    }), 201

@app.route('/api/classes')
def get_classes():
    classes = Class.query.filter_by(is_active=True).all()
    return jsonify([{
        'id': c.id,
        'name': c.name,
        'code': c.code,
        'created_at': c.created_at.isoformat()
    } for c in classes])

@app.route('/api/attendance/bulk', methods=['POST'])
def bulk_attendance():
    """Record attendance for multiple students at once"""
    try:
        data = request.json
        student_ids = data.get('student_ids', [])
        class_id = data.get('class_id')
        category = data.get('category')
        date_str = data.get('date', '').strip()
        
        if not date_str:
            return jsonify({'error': 'Date is required'}), 400
        
        try:
            session_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400
        
        # Determine class record
        class_record = None
        if category in CATEGORY_BASED_CATEGORIES:
            # For category-based categories, find the generic category class
            category_code = category.upper().replace(' ', '')
            class_record = Class.query.filter_by(code=category_code, category=category, is_active=True).first()
        elif class_id:
            # For regular categories (like Aerospace Class), use the provided class_id
            class_record = Class.query.get(class_id)
            if class_record and class_record.category != category:
                return jsonify({'error': f'Class {class_record.name} does not belong to category {category}'}), 400
        
        if not class_record:
            return jsonify({'error': f'Class not found for category {category}'}), 404
        
        # Record attendance for each selected student
        current_time = datetime.now().time()
        scanned_datetime = datetime.combine(session_date, current_time)
        
        recorded_count = 0
        skipped_count = 0
        errors = []
        
        for student_id in student_ids:
            try:
                student = Student.query.get(student_id)
                if not student:
                    errors.append(f'Student ID {student_id} not found')
                    continue
                
                # Check for duplicate attendance
                existing_attendance = Attendance.query.filter_by(
                    student_id=student.id,
                    class_id=class_record.id
                ).filter(
                    func.date(Attendance.scanned_at) == session_date
                ).first()
                
                if existing_attendance:
                    skipped_count += 1
                    continue
                
                # Record attendance
                attendance = Attendance(
                    student_id=student.id,
                    class_id=class_record.id,
                    scanned_at=scanned_datetime,
                    ip_address=request.remote_addr
                )
                db.session.add(attendance)
                recorded_count += 1
            except Exception as e:
                errors.append(f'Error processing student ID {student_id}: {str(e)}')
        
        db.session.commit()
        
        return jsonify({
            'message': f'Attendance recorded for {recorded_count} student(s)',
            'recorded': recorded_count,
            'skipped': skipped_count,
            'errors': errors if errors else None
        }), 201
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/student_management')
def student_management():
    """Display student management page for adding and removing students"""
    # Get all students, sorted by last name, then first name
    all_students = Student.query.order_by(Student.last_name, Student.first_name).all()
    
    # Group students by AS_Year for display
    students_by_as_year = {}
    for student in all_students:
        as_year = student.as_year if student.as_year else 'Ungrouped'
        if as_year not in students_by_as_year:
            students_by_as_year[as_year] = []
        students_by_as_year[as_year].append(student)
    
    # Sort AS_Year groups
    def sort_as_year_key(year):
        if year == 'Ungrouped':
            return (1, year)
        try:
            numeric_part = ''.join(filter(str.isdigit, year))
            if numeric_part:
                return (0, int(numeric_part))
        except:
            pass
        return (0, year)
    
    sorted_as_years = sorted(students_by_as_year.keys(), key=sort_as_year_key)
    students_by_as_year_sorted = {year: students_by_as_year[year] for year in sorted_as_years}
    
    return render_template('student_management.html', 
                         students_by_as_year=students_by_as_year_sorted,
                         total_students=len(all_students))

@app.route('/api/students', methods=['POST'])
def add_student():
    """Add a new student to the database"""
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Helper function to safely get and strip values
        def safe_strip(value, default=''):
            if value is None:
                return default
            try:
                if isinstance(value, str):
                    result = value.strip()
                    return result if result else default
                # Convert to string if not None
                result = str(value).strip()
                return result if result else default
            except (AttributeError, TypeError):
                return default
        
        # Helper to safely get optional fields (convert None/empty to None)
        def get_optional_field(field_name):
            value = data.get(field_name)
            if value is None:
                return None
            try:
                if isinstance(value, str):
                    stripped = value.strip()
                    return stripped if stripped else None
                # Convert to string and strip
                stripped = str(value).strip()
                return stripped if stripped else None
            except (AttributeError, TypeError):
                return None
        
        # Safely extract all fields
        first_name = safe_strip(data.get('first_name'), '')
        last_name = safe_strip(data.get('last_name'), '')
        student_id = get_optional_field('student_id')
        email = get_optional_field('email')
        as_year = get_optional_field('as_year')
        aero_class = get_optional_field('aero_class')
        aero_class_2 = get_optional_field('aero_class_2')
        
        if not first_name or not last_name:
            return jsonify({'error': 'First name and last name are required'}), 400
        
        # Check if student already exists
        existing = Student.query.filter_by(
            first_name=first_name,
            last_name=last_name
        ).first()
        
        if existing:
            return jsonify({'error': f'Student {first_name} {last_name} already exists'}), 400
        
        # Create new student
        full_name = f"{first_name} {last_name}"
        new_student = Student(
            first_name=first_name,
            last_name=last_name,
            name=full_name,
            student_id=student_id,
            email=email,
            as_year=as_year,
            aero_class=aero_class,
            aero_class_2=aero_class_2
        )
        
        db.session.add(new_student)
        db.session.commit()
        
        return jsonify({
            'message': f'Student {full_name} added successfully',
            'student': {
                'id': new_student.id,
                'first_name': new_student.first_name,
                'last_name': new_student.last_name,
                'name': new_student.name
            }
        }), 201
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/students/<int:student_id>', methods=['DELETE'])
def delete_student(student_id):
    """Delete a student and all their attendance records"""
    try:
        student = Student.query.get(student_id)
        if not student:
            return jsonify({'error': f'Student with ID {student_id} not found'}), 404
        
        student_name = student.name
        
        # Delete all attendance records for this student
        Attendance.query.filter_by(student_id=student_id).delete()
        
        # Delete the student
        db.session.delete(student)
        db.session.commit()
        
        return jsonify({
            'message': f'Student {student_name} and all attendance records deleted successfully',
            'deleted': True
        }), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/attendance/<int:attendance_id>', methods=['DELETE'])
def delete_attendance(attendance_id):
    """Delete a specific attendance record"""
    try:
        attendance = Attendance.query.get_or_404(attendance_id)
        
        # Get student and class info for the response message
        student = Student.query.get(attendance.student_id)
        class_record = Class.query.get(attendance.class_id)
        
        student_name = student.name if student else 'Unknown'
        class_name = class_record.name if class_record else 'Unknown'
        
        # Delete the attendance record
        db.session.delete(attendance)
        db.session.commit()
        
        return jsonify({
            'message': f'Attendance record deleted for {student_name} in {class_name}',
            'deleted': True
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/classes/<int:class_id>', methods=['DELETE'])
def delete_class(class_id):
    try:
        class_record = Class.query.get_or_404(class_id)
        
        # Delete all attendance records for this class
        Attendance.query.filter_by(class_id=class_id).delete()
        
        # Delete the class
        db.session.delete(class_record)
        db.session.commit()
        
        return jsonify({'message': 'Class deleted successfully'}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # For local network deployment, use host='0.0.0.0' to allow external connections
    # For production, set debug=False and use a proper WSGI server like gunicorn
    app.run(host='0.0.0.0', port=5000, debug=False)
@app.route('/api/student/my_attendance')
def get_my_attendance():
    try:
        input_sid = request.args.get('student_id')
        input_email = request.args.get('email')
        
        student = None
        if input_sid:
            student = Student.query.filter_by(student_id=input_sid).first()
        if not student and input_email:
            student = Student.query.filter_by(email=input_email).first()
        
        if not student:
            return jsonify({'error': 'Cadet not found'}), 404

        all_active_classes = Class.query.filter_by(is_active=True).all()
        aero_ids = [c.id for c in all_active_classes if c.category == 'Aerospace Class']
        enrolled_aero = db.session.query(Attendance.class_id).filter(
            Attendance.student_id == student.id, Attendance.class_id.in_(aero_ids)
        ).distinct().first()
        target_aero_id = enrolled_aero[0] if enrolled_aero else None

        analytics = {}
        grouped_history = defaultdict(list)
        
        # We need to track weekly optional attendance across categories
        # week_start -> has_attended_any_optional (bool)
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

            cat_attended, cat_total, trend_flags = 0, 0, []

            # Pre-calculate weekly optional status
            for cid, d_raw, s_type, c_name in offered:
                if (s_type or '').upper() == 'OPTIONAL' and (cid, str(d_raw)) in cadet_map:
                    d_obj = _to_date(d_raw)
                    ws = d_obj - timedelta(days=d_obj.weekday())
                    weekly_optional_tracker[ws] = True

            # Process history
            for cid, d_raw, s_type, c_name in offered:
                d_str = str(d_raw)
                is_att = (cid, d_str) in cadet_map
                s_type = (s_type or 'MANDATORY').upper()
                d_obj = _to_date(d_raw)
                ws = d_obj - timedelta(days=d_obj.weekday())
                week_key = ws.strftime('%b %d, %Y')
                
                if s_type == 'MANDATORY':
                    cat_total += 1
                    if is_att: cat_attended += 1
                
                # STATUS LOGIC
                if is_att:
                    status = "ATTENDED"
                elif s_type == "MANDATORY":
                    status = "MISSED"
                else:
                    # It's an optional miss. Check if they hit ANY other optional this week.
                    status = "OPTIONAL_OK" if weekly_optional_tracker[ws] else "OPTIONAL_MISSED"
                
                grouped_history[week_key].append({
                    'date_raw': d_str, 'date_str': d_obj.strftime('%a, %b %d'),
                    'class_name': c_name, 'category': category, 'type': s_type, 'status': status
                })

            # Trend line (Newest 8)
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

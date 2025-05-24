import os
import uuid
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
import firebase_admin
from firebase_admin import credentials, firestore
from functools import wraps
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'your_secret_key')  # Use environment variable

# Initialize Firebase Admin SDK
cred_path = os.getenv('FIREBASE_CREDENTIALS_PATH', 'test-1d627-firebase-adminsdk-fbsvc-1906bb7cf0.json')
if not firebase_admin._apps:
    cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred)
db = firestore.client()

# --- Authentication Decorators ---
def login_required(f):
    """Decorator to ensure user is logged in"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def superadmin_required(f):
    """Decorator to ensure user is a superadmin"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('role') != 'super_admin':
            flash('Super Admin access required.')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


# --- Routes ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    """Handle user login"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        # Query Firebase for admin user
        admin_query = db.collection('admin_users').where('username', '==', username).limit(1).get()
        
        if len(admin_query) > 0:
            admin_data = admin_query[0].to_dict()
            if admin_data.get('password') == password:  # In production, use proper password hashing
                session['username'] = username
                session['role'] = admin_data.get('role', 'admin')
                return redirect(url_for('index'))
        
        flash('Invalid credentials')
    return render_template('login.html')

@app.route('/logout')
def logout():
    """Handle user logout"""
    session.clear()
    return redirect(url_for('login'))

@app.route('/', methods=['GET'])
@login_required
def index():
    """Display main admin panel with user data"""
    # Get sorting and filtering parameters
    sort_field = request.args.get('sort', 'registration_date')
    sort_dir = request.args.get('dir', 'desc')
    username_filter = request.args.get('username', None)
    email_filter = request.args.get('email', None)

    # Query Firestore with sorting and filtering
    direction = firestore.Query.DESCENDING if sort_dir == 'desc' else firestore.Query.ASCENDING
    users_ref = db.collection('visual_impaired_individuals').order_by(sort_field, direction=direction)

    users = []
    for doc in users_ref.stream():
        data = doc.to_dict()
        data['uid'] = doc.id
        # Apply filters
        if username_filter and username_filter.lower() not in data.get('username', '').lower():
            continue
        if email_filter and email_filter.lower() not in data.get('email', '').lower():
            continue
        users.append(data)

    content = render_template('admin_panel.html', users=users, session=session, 
                            sort_field=sort_field, sort_dir=sort_dir, 
                            username_filter=username_filter or '', 
                            email_filter=email_filter or '')
    return render_template('sidebar_layout.html', title='Visual Impaired Individual Data', 
                         content=content, session=session, active_page='individuals')

@app.route('/edit/<uid>', methods=['POST'])
@login_required
def edit_user(uid):
    """Edit user information"""
    username = request.form.get('username')
    password = request.form.get('password')
    update_data = {'username': username}
    if password:
        update_data['password'] = password
    
    db.collection('visual_impaired_individuals').document(uid).update(update_data)
    db.collection('admin_activity_log').add({
        'admin': session['username'],
        'action': f"edit: Changed username to {username}" + (f", password updated" if password else ""),
        'target_uid': uid,
        'target_role': 'user',
        'timestamp': datetime.utcnow().isoformat()
    })
    flash(f'Updated user {uid}')
    return redirect(url_for('index'))

@app.route('/delete/<uid>', methods=['POST'])
@login_required
def delete_user(uid):
    """Delete a user"""
    db.collection('visual_impaired_individuals').document(uid).delete()
    db.collection('admin_activity_log').add({
        'admin': session['username'],
        'action': "delete: Deleted user",
        'target_uid': uid,
        'target_role': 'user',
        'timestamp': datetime.utcnow().isoformat()
    })
    flash(f'Deleted user {uid}')
    return redirect(url_for('index'))

@app.route('/activity_log')
@login_required
@superadmin_required
def activity_log():
    """Display admin activity log"""
    logs_ref = db.collection('admin_activity_log').order_by('timestamp', 
                                                          direction=firestore.Query.DESCENDING).limit(100)
    logs = [doc.to_dict() for doc in logs_ref.stream()]
    content = render_template('activity_log.html', logs=logs, session=session)
    return render_template('sidebar_layout.html', title='Admin Activity Log', 
                         content=content, session=session, active_page='activity')

@app.route('/manage_admins')
@login_required
@superadmin_required
def manage_admins():
    """Display admin management interface"""
    # Get sorting and filtering parameters
    sort_field = request.args.get('sort', 'username')
    sort_dir = request.args.get('dir', 'desc')
    username_filter = request.args.get('username', None)

    # Query Firestore with sorting and filtering
    direction = firestore.Query.DESCENDING if sort_dir == 'desc' else firestore.Query.ASCENDING
    admins_ref = db.collection('admin_users').order_by(sort_field, direction=direction)

    admins = []
    for doc in admins_ref.stream():
        data = doc.to_dict()
        data['admin_id'] = doc.id
        if username_filter and username_filter.lower() not in data.get('username', '').lower():
            continue
        admins.append(data)

    content = render_template('admin_management.html', admins=admins, session=session,
                            sort_field=sort_field, sort_dir=sort_dir,
                            username_filter=username_filter or '')
    return render_template('sidebar_layout.html', title='Admin Management',
                         content=content, session=session, active_page='admins')

@app.route('/add_admin', methods=['POST'])
@login_required
@superadmin_required
def add_admin():
    """Add a new admin user"""
    username = request.form.get('username')
    password = request.form.get('password')
    admin_id = str(uuid.uuid4())
    
    # Check for existing username
    existing_admin = db.collection('admin_users').where('username', '==', username).get()
    if len(existing_admin) > 0:
        flash('Username already exists')
        return redirect(url_for('manage_admins'))

    # Create new admin
    db.collection('admin_users').document(admin_id).set({
        'username': username,
        'password': password,
        'role': 'admin',  # Default role is admin
        'created_at': datetime.utcnow().isoformat()
    })

    # Log the action
    db.collection('admin_activity_log').add({
        'admin': session['username'],
        'action': f"add_admin: Added new admin: {username}",
        'target_uid': admin_id,
        'target_role': 'admin',
        'timestamp': datetime.utcnow().isoformat()
    })
    flash(f'Added new admin: {username}')
    return redirect(url_for('manage_admins'))

@app.route('/edit_admin/<admin_id>', methods=['POST'])
@login_required
@superadmin_required
def edit_admin(admin_id):
    """Edit admin user information"""
    username = request.form.get('username')
    password = request.form.get('password')
    
    # Check for username conflicts
    existing_admin = db.collection('admin_users').where('username', '==', username).get()
    for doc in existing_admin:
        if doc.id != admin_id:
            flash('Username already exists')
            return redirect(url_for('manage_admins'))
    
    # Get existing admin data
    admin_doc = db.collection('admin_users').document(admin_id).get()
    if not admin_doc.exists:
        flash('Admin not found')
        return redirect(url_for('manage_admins'))
    
    # Update admin data
    existing_data = admin_doc.to_dict()
    update_data = {
        'username': username,
        'role': existing_data.get('role', 'admin')  # Preserve existing role
    }
    if password:
        update_data['password'] = password
    
    db.collection('admin_users').document(admin_id).update(update_data)
    
    # Log the action
    db.collection('admin_activity_log').add({
        'admin': session['username'],
        'action': f"edit_admin: Updated admin: {username}",
        'target_uid': admin_id,
        'target_role': update_data['role'],
        'timestamp': datetime.utcnow().isoformat()
    })
    flash(f'Updated admin: {username}')
    return redirect(url_for('manage_admins'))

@app.route('/delete_admin/<admin_id>', methods=['POST'])
@login_required
@superadmin_required
def delete_admin(admin_id):
    """Delete an admin user"""
    admin_doc = db.collection('admin_users').document(admin_id).get()
    if admin_doc.exists:
        username = admin_doc.to_dict().get('username', '')
        db.collection('admin_users').document(admin_id).delete()
        db.collection('admin_activity_log').add({
            'admin': session['username'],
            'action': f"delete_admin: Deleted admin: {username}",
            'target_uid': admin_id,
            'target_role': 'admin',
            'timestamp': datetime.utcnow().isoformat()
        })
        flash(f'Deleted admin: {username}')
    return redirect(url_for('manage_admins'))

@app.route('/guidance_history')
@login_required
def guidance_history():
    """Display guidance history data"""
    # Get sorting and filtering parameters
    sort_field = request.args.get('sort', 'timestamp')
    sort_dir = request.args.get('dir', 'desc')
    user_id_filter = request.args.get('userId', None)
    object_name_filter = request.args.get('objectName', None)

    # Query Firestore with sorting and filtering
    direction = firestore.Query.DESCENDING if sort_dir == 'desc' else firestore.Query.ASCENDING
    logs_ref = db.collection('object_detection_logs').order_by(sort_field, direction=direction)

    guidance = []
    for doc in logs_ref.stream():
        data = doc.to_dict()
        data['id'] = doc.id
        if user_id_filter and data.get('userId', '') != user_id_filter:
            continue
        if object_name_filter and data.get('objectName', '') != object_name_filter:
            continue
        guidance.append(data)

    content = render_template('guidance_history.html', guidance=guidance,
                            sort_field=sort_field, sort_dir=sort_dir,
                            user_id_filter=user_id_filter or '',
                            object_name_filter=object_name_filter or '')
    return render_template('sidebar_layout.html', title='Guidance Data',
                         content=content, session=session, active_page='guidance')

if __name__ == '__main__':
    # Remove browser automation
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

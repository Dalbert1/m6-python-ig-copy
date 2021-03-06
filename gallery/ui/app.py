import os
from base64 import b64encode
from flask import Flask, flash, session, render_template, redirect, url_for, request
from gallery.tools.postgres_user_dao import PostgresUserDAO
from gallery.tools.user import User
from gallery.tools.db import connect, insertImage, deleteImage, deleteAllUserImages, get_secret
from gallery.tools.s3 import get_object, put_object, delete_object
from werkzeug.utils import secure_filename
from markupsafe import escape
from functools import wraps

app = Flask(__name__)

def get_key():
    get_secret()
    return os.getenv("flask_session_key")


app.secret_key = get_key() 

def get_bucket():
   if os.getenv("S3_IMAGE_BUCKET"):
      return os.getenv("S3_IMAGE_BUCKET")
   else:
      return "edu.au.cc.m6.python-image-gallery"
      
BUCKET = get_bucket()
connect()


def get_user_dao():
   return PostgresUserDAO()


def check_admin():
   if 'username' in session:
      is_admin = get_user_dao().get_admin_user_by_username(session['username'])
      return is_admin
   else: 
      return None


def check_auth():
   return 'username' in session and get_user_dao().get_user_by_username(session['username'])
  

def requires_admin(view):
   @wraps(view)
   def decorated(**kwargs):
      if not check_admin():
         return "Restricted to administrator"
      return view(**kwargs)
   return decorated


def requires_auth(view):
   @wraps(view)
   def decorated(**kwargs):
      if not check_auth():
         return redirect('/login')
      return view(**kwargs)
   return decorated
   
   
# Loads home page for logged user      
@app.route('/')
@requires_auth
def root_page():
   return render_template('home.html', user=get_user_dao().get_user_by_username(session['username']))


# Resets session var
@app.route('/logout')
@requires_auth
def logout():
   session['username'] = None
   return redirect('/login')


# Loads login page
@app.route('/login', methods=['GET', 'POST'])
def login():
   if request.method == 'POST':
      user = get_user_dao().get_user_by_username(request.form["username"])
      if user is None or user.password != request.form["password"]: 
         return redirect('/invalidLogin')
      else:
         session['username'] = request.form["username"]
         return redirect('/')
   else:
      return render_template('login.html')
         

# Loads admin page with all users displayed
@app.route('/admin/users', methods=['GET', 'POST'])
@requires_admin 
def users():
   return render_template('users.html', user = get_user_dao().get_user_by_username(session['username']), users=get_user_dao().get_users())


# Confirmation - Delete selected username
@app.route('/admin/deleteUser/<username>', methods=['GET', 'POST'])
@requires_admin 
def deleteUser(username):
   return render_template("confirm.html", 
                           title ="Confirm Delete", 
                           message="Are you sure you want to delete this user?",
                           on_yes="/admin/executeDeleteUser/"+username,
                           on_no="/admin/users")
                       

# Executes Delete User
@app.route('/admin/executeDeleteUser/<username>', methods=['GET', 'POST'])
@requires_admin 
def executeDeleteUser(username):
   get_user_dao().delete_user(username)
   deleteAllUserImages(username)
   return redirect('/admin/users')


# Invalid Login redirect
@app.route('/invalidLogin')
def invalidLogin():
   return "Invalid Login"
   

# Modify User page
@app.route('/admin/editUser/<username>/<full_name>', methods=['GET'])
@requires_admin 
def modify_user(username, full_name):
   user = get_user_dao().get_user_by_username(username) 
   if user is None: 
      return redirect('/admin/users')
   else:
      return render_template('editUser.html', user=user)


# Commit user modification
@app.route('/admin/commitEdit', methods=['POST'])
@requires_admin 
def commit_edit():
   if request.method == 'POST':
      get_user_dao().modify_user(request.form["username"], request.form["new_password"], request.form["new_full_name"])
   return redirect('/admin/users')
   

# Loads New User page
@app.route('/admin/newUser')
@requires_admin 
def new_user_interface():
   return render_template('new_user.html', user = get_user_dao().get_user_by_username(session['username']))


# Commits new user
@app.route('/admin/commitNewUser', methods=['POST'])
@requires_admin 
def commit_new():
   user = get_user_dao().get_user_by_username(request.form["username"])
   if user is None: 
      get_user_dao().create_user(request.form["username"], request.form["password"], request.form["full_name"])
   return redirect('/admin/users')
   

# Performs the file uploads
@app.route("/upload", methods=['POST'])
@requires_auth
def upload():
   if request.method == 'POST':
      image = request.files['file']
      if image:
         path = session['username'] + '/' + image.filename
         put_object(BUCKET, path, image)
         user = session['username']
         insertImage(user, path)
         return redirect(url_for('user_images', username=user))
   return "Invalid Request"
   
   
# Deletes specified image      
@app.route("/delete/image/<username>/<path:key>", methods=['GET'])
@requires_auth
def delete_image(username, key):
   if username != session['username']:
      return "Invalid Request"
   else:
      username = session['username']
      deleteImage(username, key)
      delete_object(BUCKET, key)
      return redirect(url_for('user_images', username=username))    
     
    
# Loads page for user to upload their new image
@app.route('/<username>/upload', methods=['GET'])
@requires_auth
def new_image(username):
   if username != session['username']:
      return "Invalid Request"
   else:
      return render_template('new_image.html', user=get_user_dao().get_user_by_username(session['username']))


# Displays all images for given user
@app.route('/<username>/all_images', methods=['GET', 'POST'])
@requires_auth
def user_images(username):
   if username != session['username']:
      return "Invalid Request"
   else:
      user_images = get_user_dao().get_images_by_username(username)
      s3_imports = {}
      if user_images:
         for img_name in user_images:
            image_object = get_object(BUCKET, img_name)
            b64_img = b64encode(image_object).decode("utf-8")
            s3_imports[img_name] = b64_img
      return render_template('all_user_images.html', contents=s3_imports, user=get_user_dao().get_user_by_username(username))         
      
      
@app.route("/<username>/viewImage/<path:key>", methods=['GET', 'POST'])
@requires_auth
def view_image(username, key):
   if username != session['username']:
      return "Invalid Request"
   else:
      image_object = get_object(BUCKET, key=key)
      b64_img = b64encode(image_object).decode("utf-8")
      return render_template('viewImage.html', image=b64_img)
  

@app.route('/debugSession')
def debugSession():
   result = ""
   for key, value in session.items():
      result += key+"->"+str(value)+"<br />"
   return result


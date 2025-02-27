from flask import Blueprint, render_template, request, redirect
from flask_login import login_required, current_user
from ..extensions import db
from ..models.post import Post
from ..models.user import User


post = Blueprint("post", __name__)

@post.route('/', methods=['POST', 'GET'])
def all():
    posts = Post.query.order_by(Post.date.desc()).all()
    return render_template('post/all.html', posts=posts), 200


@post.route("/post/create", methods=['POST', 'GET'])
@login_required
def create():
    if request.method == 'POST':
        subject = request.form.get('subject')

        post = Post(name=current_user.name, subject=subject, user_id=current_user.id)

        try:
            db.session.add(post)
            db.session.commit()
            return redirect('/')
        except Exception as e:
            db.session.rollback()
            print(str(e))
            return render_template("post/create.html", error="Произошла ошибка при создании поста.")
    
    return render_template("post/create.html")


@post.route("/post/<int:id>/update", methods=['POST', 'GET'])
@login_required
def update(id):
    post = Post.query.get(id)
    if request.method == 'POST':

        teacher_id = request.form.get('teacher')
        subject = request.form.get('subject')

        post.teacher = teacher_id
        post.subject = subject

        try:
            db.session.commit()
            return redirect('/')
        except Exception as e:
            print(str(e))
            db.session.rollback()
    else:
        return render_template("post/update.html", post=post)
    teachers = User.query.all()
    return render_template("post/update.html", post=post, teachers=teachers)


@post.route("/post/<int:id>/delete", methods=['POST', 'GET'])
@login_required
def delete(id):
    post = Post.query.get(id)
    try:
        db.session.delete(post)
        db.session.commit()
        return redirect('/')
    except Exception as e:
        print(str(e))
        return str(e)


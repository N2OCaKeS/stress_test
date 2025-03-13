from flask import Blueprint, render_template, request, redirect
from flask_login import login_required, current_user
from ..extensions import db
from ..models.post import Post
from ..models.user import User


post = Blueprint("post", __name__)

@post.route('/', methods=['GET', 'POST'])
def all():
    page = request.args.get("page", 1, type=int)  # Получаем номер страницы (по умолчанию 1)
    per_page = request.args.get("per_page", 10, type=int)  # Количество записей на странице (по умолчанию 10)

    posts = Post.query.order_by(Post.date.desc()).paginate(page=page, per_page=per_page, error_out=False)

    if request.is_json:
        return jsonify({
            "posts": [
                {"id": post.id, "content": post.content, "date": post.date.isoformat()}
                for post in posts.items
            ],
            "page": posts.page,
            "total_pages": posts.pages,
            "total_items": posts.total
        }), 200

    return render_template('post/all.html', posts=posts.items, page=posts), 200


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


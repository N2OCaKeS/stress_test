from flask import Blueprint, render_template, redirect, flash, url_for

from ..web_funcs import save_picture
from ..forms import LoginForm, RegistrationForm
from ..extensions import db, bcrypt
from ..models.user import User


user = Blueprint("user", __name__)


@user.route("/user/register", methods=['POST', 'GET'])
def register():
    form = RegistrationForm()
    if form.validate_on_submit():
        hashed_password = bcrypt.generate_password_hash(form.password.data).decode('UTF-8')
        avatar_filename = save_picture(form.avatar.data)
        user = User(name=form.name.data, login=form.login.data, password=hashed_password, avatar=avatar_filename)
        db.session.add(user)
        db.session.commit()
        flash(f"Поздравляем, {form.login.data}! Вы успешно зарегистрированы", "success")

        return redirect(url_for('user.login'))
    else:
        flash("При регистрации произошла ошибка!", "danger")
    return render_template('user/register.html', form=form)


@user.route("/user/login", methods=['POST', 'GET'])
def login():
    form = LoginForm()

    return render_template('user/login.html', form=form)
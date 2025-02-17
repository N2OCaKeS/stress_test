from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed
from wtforms import StringField, PasswordField, SubmitField, FileField, BooleanField
from wtforms.validators import DataRequired, Length, EqualTo, ValidationError
from .models.user import User


class RegistrationForm(FlaskForm):
    name = StringField('ФИО', validators=[DataRequired(), Length(min=2, max=50)])
    login = StringField('Логин', validators=[DataRequired(), Length(min=4, max=16)])
    password = PasswordField('Пароль', validators=[DataRequired()])
    confirm_password = PasswordField('Пароль', validators=[DataRequired(), EqualTo('password')])
    avatar = FileField('Аватар', validators=[FileAllowed(['jpg', 'jpeg', 'png'])])
    submit = SubmitField("Зарегистрироваться")


    def validate_login(self, login):
        user = User.query.filter_by(login=login.data).first()
        if user:
            raise ValidationError("Выберите другое имя пользователя!")


class LoginForm(FlaskForm):
    """Form to log in Users"""
    login = StringField('Логин', validators=[DataRequired(), Length(min=2, max=20)])
    password = PasswordField('Пароль', validators=[DataRequired()])
    remember = BooleanField('Запомнить')
    submit = SubmitField('Войти')
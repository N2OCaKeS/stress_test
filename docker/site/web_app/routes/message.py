from flask import Blueprint, request, jsonify, render_template, redirect, flash
from flask_login import login_required, current_user
from ..extensions import db
from ..models.message import Message
import json

message = Blueprint("message", __name__)

@message.route("/message/create", methods=['POST', 'GET'])
def create():
    if request.method == "POST":
        if request.is_json:  # Если запрос JSON
            try:
                content = request.get_json().get('content') 
                if not content:
                    return json.loads(json.dumps("Сообщение не может быть пустым")), 400
                message = Message(content=content)
            except Exception as e:
                return json.loads(json.dumps("Некоректный JSON")), 400  # Возвращаем ошибку для некорректного JSON
            
        else:  # Если запрос из формы
            content = request.form.get('content')
            if not content:
                return render_template("message/create.html", error="Сообщение не может быть пустым")
            message = Message(content=content)

        try:
            db.session.add(message)
            db.session.commit()
            if request.is_json:
                return json.loads(json.dumps("Сообщение успешно создано")), 201 
            else:
                flash("Сообщение успешно отправлено!")
                return redirect("/")

        except Exception as e:
            db.session.rollback()  # Откатываем изменения при ошибке
            if request.is_json:
                return json.loads(json.dumps(f"Произошла ошибка при сохранении сообщения: {str(e)}")), 500
            else:
                return render_template("message/create.html", error="Произошла ошибка при отправке сообщения.")
            
    elif request.method == "GET":
        return render_template("message/create.html")
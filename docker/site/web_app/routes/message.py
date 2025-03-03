from flask import Blueprint, request, jsonify, render_template, redirect, flash
from ..extensions import db
from ..models.message import Message

message = Blueprint("message", __name__)

@message.route("/message/create", methods=['POST', 'GET'])
def create():
    if request.method == "POST":
        if request.is_json:
            try:
                data = request.get_json()
                if not data or 'content' not in data:
                    return jsonify({"error": "Сообщение не может быть пустым"}), 400
                content = data["content"]
                new_message = Message(content=content)
            except Exception as e:
                return jsonify({"error": "Некорректный JSON"}), 400
            
        else:
            content = request.form.get('content')
            if not content:
                return render_template("message/create.html", error="Сообщение не может быть пустым")
            new_message = Message(content=content)

        try:
            db.session.add(new_message)
            db.session.commit()
            if request.is_json:
                return jsonify({"message": "Сообщение успешно создано"}), 201
            else:
                flash("Сообщение успешно отправлено!")
                return redirect("/")

        except Exception as e:
            db.session.rollback()
            if request.is_json:
                return jsonify({"error": f"Ошибка при сохранении сообщения: {str(e)}"}), 500
            else:
                return render_template("message/create.html", error="Ошибка при отправке сообщения.")
            
    elif request.method == "GET":
        return render_template("message/create.html")
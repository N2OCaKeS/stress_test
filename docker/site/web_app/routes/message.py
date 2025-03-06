from flask import Blueprint, request, jsonify, render_template, redirect, flash
from ..extensions import db
from ..models.message import Message

message = Blueprint("message", __name__)

@message.route("/message/create", methods=['POST', 'GET'])
def create():
    if request.method == "POST":
        messages = []
        try:
            if request.is_json:
                data = request.get_json()
                if isinstance(data, list):  # Если массив сообщений
                    messages = [Message(content=item["content"]) for item in data if "content" in item]
                elif isinstance(data, dict) and "content" in data:  # Если одно сообщение
                    messages = [Message(content=data["content"])]
                else:
                    return jsonify({"error": "Некорректные данные"}), 400
            else:
                content = request.form.get('content')
                if not content:
                    return render_template("message/create.html", error="Сообщение не может быть пустым")
                messages = [Message(content=content)]

            if not messages:
                return jsonify({"error": "Нет сообщений для сохранения"}), 400

            db.session.bulk_save_objects(messages)
            db.session.commit()

            if request.is_json:
                return jsonify({"message": "Сообщение(я) успешно создано"}), 201
            else:
                flash("Сообщение успешно отправлено!")
                return redirect("/")
        
        except Exception as e:
            db.session.rollback()
            error_message = f"Ошибка при сохранении сообщений: {str(e)}"
            if request.is_json:
                return jsonify({"error": error_message}), 500
            else:
                return render_template("message/create.html", error=error_message)

    elif request.method == "GET":
        return render_template("message/create.html")

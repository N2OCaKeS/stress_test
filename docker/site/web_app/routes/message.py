from flask import Blueprint, request, jsonify, Response, render_template, flash, redirect
from ..extensions import db, redis_client
from ..models.message import Message


message = Blueprint("message", __name__)


def batch_insert(session, messages, batch_size=500):
    """ Вставка сообщений батчами с коммитом каждые batch_size записей """
    for i in range(0, len(messages), batch_size):
        session.bulk_insert_mappings(Message, messages[i:i + batch_size])
        session.commit()

@message.route("/message/create", methods=['POST', 'GET'])
def create():
    if request.method == "GET":
        return render_template("message/create.html"), 200

    messages = []
    try:
        if request.is_json:
            data = request.get_json()
            if isinstance(data, list):  # Если массив сообщений
                messages = [{"content": item["content"]} for item in data if "content" in item]
            elif isinstance(data, dict) and "content" in data:  # Если одно сообщение
                messages = [{"content": data["content"]}]
            else:
                return jsonify({"error": "Некорректные данные"}), 400
        else:
            content = request.form.get('content')
            if not content:
                return render_template("message/create.html", error="Сообщение не может быть пустым")
            messages = [{"content": content}]

        if not messages:
            return jsonify({"error": "Нет сообщений для сохранения"}), 400

        # Проверяем дубликаты в Redis
        filtered_messages = []
        for msg in messages:
            msg_key = f"message:{msg['content']}"
            if not redis_client.exists(msg_key):  # Если сообщение ещё не кэшировано
                filtered_messages.append(msg)
                redis_client.setex(msg_key, 60, "1")  # Кэшируем сообщение на 60 секунд

        if not filtered_messages:
            return jsonify({"message": "Сообщение уже было отправлено недавно"}), 200

        # Вставляем только уникальные сообщения батчами
        batch_insert(db.session, filtered_messages, batch_size=500)

        if request.is_json:
            return jsonify({"message": "Сообщение(я) успешно создано"}), 201
        else:
            flash("Сообщение успешно отправлено!")
            return redirect("/")

    except Exception as e:
        db.session.rollback()
        error_message = f"Ошибка при сохранении сообщений: {str(e)}"
        print(error_message)  # Логируем ошибку в консоль для отладки
        if request.is_json:
            return jsonify({"error": error_message}), 500
        else:
            return render_template("message/create.html", error=error_message)

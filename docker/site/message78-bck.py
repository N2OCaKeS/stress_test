from flask import Blueprint, request, jsonify, Response, render_template, flash, redirect
from ..extensions import db, redis_client
from ..models.message import Message
import logging

# Настраиваем логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

message = Blueprint("message", __name__)

def batch_insert(session, messages, batch_size=500):
    """ Вставка сообщений батчами с коммитом каждые batch_size записей """
    try:
        for i in range(0, len(messages), batch_size):
            session.bulk_insert_mappings(Message, messages[i:i + batch_size])
            session.flush()  # Фикс ошибки IntegrityError
            session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"Ошибка при вставке в БД: {e}")
        raise

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

        # **Оптимизированная проверка дубликатов в Redis**
        msg_keys = [f"message:{msg['content']}" for msg in messages]
        cached_results = redis_client.mget(msg_keys)  # Массовая проверка существования ключей

        filtered_messages = [
            msg for i, msg in enumerate(messages) if cached_results[i] is None
        ]

        if not filtered_messages:
            return jsonify({"message": "Все сообщения уже были отправлены недавно"}), 200

        # **Оптимизированное добавление в Redis**
        pipeline = redis_client.pipeline()
        for msg in filtered_messages:
            pipeline.setex(f"message:{msg['content']}", 600, "1")  # TTL 10 минут
        pipeline.execute()

        # **Вставка в базу данных батчами**
        batch_insert(db.session, filtered_messages, batch_size=500)

        return jsonify({"message": "Сообщение(я) успешно создано"}), 201

    except Exception as e:
        db.session.rollback()
        logger.error(f"Ошибка в обработке запроса: {e}")
        return jsonify({"error": f"Ошибка при обработке сообщения: {str(e)}"}), 500


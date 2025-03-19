import traceback
from flask import Blueprint, request, jsonify, render_template
from ..extensions import db, redis_client
from ..models.message import Message
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

message = Blueprint("message", __name__)

def batch_insert(session, messages, batch_size=500):
    """ Вставка сообщений батчами с коммитом каждые batch_size записей """
    try:
        for i in range(0, len(messages), batch_size):
            session.bulk_save_objects([Message(**msg) for msg in messages[i:i + batch_size]])
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
        # Логируем всё, что приходит
        logger.info(f"Headers: {request.headers}")
        logger.info(f"Raw Data: {request.data}")
        logger.info(f"Parsed JSON: {request.get_json(silent=True)}")

        if request.is_json:
            data = request.get_json()
            logger.info(f"Received JSON data: {data}")

            if isinstance(data, list):
                messages = [{"content": item["content"]} for item in data if "content" in item]
            elif isinstance(data, dict) and "content" in data:
                messages = [{"content": data["content"]}]
            else:
                logger.error("Некорректные данные в JSON")
                return jsonify({"error": "Некорректные данные"}), 400
        else:
            content = request.form.get('content')
            logger.info(f"Received form data: {content}")

            if not content:
                return render_template("message/create.html", error="Сообщение не может быть пустым")
            messages = [{"content": content}]

        if not messages:
            logger.error("Нет сообщений для сохранения")
            return jsonify({"error": "Нет сообщений для сохранения"}), 400

        logger.info(f"Valid messages: {messages}")

        # Проверяем дубликаты в Redis
        msg_keys = [f"message:{msg['content']}" for msg in messages]
        if msg_keys:
            cached_results = redis_client.pipeline().exists(*msg_keys).execute()
        else:
            cached_results = []

        filtered_messages = [msg for i, msg in enumerate(messages) if i < len(cached_results) and not cached_results[i]]
        logger.info(f"Filtered messages (not in Redis): {filtered_messages}")

        if not filtered_messages:
            logger.info("Все сообщения уже были отправлены недавно")
            return jsonify({"message": "Все сообщения уже были отправлены недавно"}), 200

        # Кэшируем новые сообщения
        pipeline = redis_client.pipeline()
        for msg in filtered_messages:
            pipeline.setex(f"message:{msg['content']}", 30, "1")
        pipeline.execute()

        # Вставляем в БД
        batch_insert(db.session, filtered_messages, batch_size=500)

        logger.info("Сообщения успешно добавлены")
        return jsonify({"message": "Сообщение(я) успешно создано"}), 201

    except Exception as e:
        db.session.rollback()
        logger.error(f"Ошибка в обработке запроса: {e}\n{traceback.format_exc()}", exc_info=True)
        return jsonify({"error": f"Ошибка при обработке сообщения: {str(e)}"}), 500



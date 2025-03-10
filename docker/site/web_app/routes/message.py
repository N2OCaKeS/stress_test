from flask import Blueprint, request, jsonify, render_template, redirect, flash
from ..extensions import db

from ..models.message import Message

message = Blueprint("message", __name__)

def batch_insert(session, messages, batch_size=500):
    for i in range(0, len(messages), batch_size):
        session.bulk_save_objects(messages[i:i + batch_size])
        session.commit()  # 🎯 Коммитим каждые 500 записей


@message.route("/message/create", methods=['POST', 'GET'])
def create():
    messages = []
    try:
        if request.is_json:
            data = request.get_json()
            if isinstance(data, list):
                messages = [Message(content=item["content"]) for item in data if "content" in item]
            elif isinstance(data, dict) and "content" in data:
                messages = [Message(content=data["content"])]
            else:
                return jsonify({"error": "Некорректные данные"})

        if not messages:
            return jsonify({"error": "Нет сообщений для сохранения"})

        # Используем batch insert вместо обычного коммита
        batch_insert(db.session, messages, batch_size=500)

        return jsonify({"message": "Сообщение(я) успешно создано"})

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Ошибка при сохранении сообщений: {str(e)}"})
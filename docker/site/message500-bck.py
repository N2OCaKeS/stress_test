@message.route("/message/create", methods=['POST', 'GET'])
def create():
    if request.method == "GET":
        return render_template("message/create.html"), 200

    messages = []
    try:
        if request.is_json:
            data = request.get_json()
            if isinstance(data, list):  
                messages = [{"content": item["content"]} for item in data if "content" in item]
            elif isinstance(data, dict) and "content" in data:  
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

        # ✅ Оптимизация 1: Проверяем дубликаты через MGET
        msg_keys = [f"message:{msg['content']}" for msg in messages]
        existing_keys = redis_client.mget(msg_keys)

        filtered_messages = [
            msg for msg, exists in zip(messages, existing_keys) if exists is None
        ]

        if not filtered_messages:
            return jsonify({"message": "Сообщение уже было отправлено недавно"}), 200

        # ✅ Оптимизация 2: Кэшируем найденные уникальные сообщения через pipeline
        pipeline = redis_client.pipeline()
        for msg in filtered_messages:
            pipeline.setex(f"message:{msg['content']}", 300, "1")  # Увеличили TTL до 5 минут
        pipeline.execute()

        # ✅ Оптимизация 3: Вставляем только уникальные сообщения батчами
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

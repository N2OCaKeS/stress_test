document.getElementById('submitMessage').addEventListener('click', function(e) {
    e.preventDefault();  // Предотвращаем отправку формы
  
    const content = document.getElementById('content').value;
  
    // Проверка на пустое сообщение
    if (!content) {
      alert('Сообщение не может быть пустым');
      return;
    }
  
    // Отправка данных как JSON
    fetch('/message/create', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ content: content })  // Преобразуем данные в JSON
    })
    .then(response => response.json())
    .then(data => {
      if (data.message) {
        alert(data.message);
      } else {
        alert('Произошла ошибка: ' + data.error);
      }
    })
    .catch(error => {
      console.error('Ошибка:', error);
    });
  });
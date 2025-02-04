from src.aggregator.libs import check_collector
from src.aggregator.conf import full_dashboard
from flask import (Flask, 
                   redirect, 
                   url_for, 
                   jsonify,
                   send_file)




app = Flask(__name__)
app.config['SECRET_KEY'] = 'info_1381'


@app.route('/rest/api/dashboard/<stand>/<board_name>', methods=['GET'])
def get_dashboard(stand, board_name):
    if check_collector(stand) == 0:
        if board_name == 'full':
            return redirect(url_for(full_dashboard.format(stand))), 200
    else: return 'Запрос получен и обработан, но запрашиваемый сервер недоступен или вернул ошибку', 200


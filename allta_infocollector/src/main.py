from src.aggregator.libs import check_collector
from src.aggregator.conf import full_dashboard, allta_dashboard
from flask_cors import CORS
from flask import (Flask, 
                   redirect, 
                   url_for, 
                   jsonify,
                   send_file)




app = Flask(__name__)
CORS(app)
app.config['SECRET_KEY'] = 'info_1381'


@app.route('/rest/api/dashboard/<stand>/<board_name>', methods=['GET'])
def get_dashboard(stand, board_name):
    if check_collector(stand) == 0:
        if board_name == 'full':
            return redirect(full_dashboard.format(stand)), 302
        elif board_name == 'allta':
            return redirect(allta_dashboard.format(stand)), 302
    else: return 'Запрос получен и обработан, но запрашиваемый сервер недоступен или вернул ошибку', 200


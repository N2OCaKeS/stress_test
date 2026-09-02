from src.aggregator.libs import check_collector
from src.aggregator.conf import (allta_dashboard,
                                 emm_blue_dashboard,
                                 emm_dark_orange_dashboard,
                                 emm_vscode_dark_dashboard,
                                 emm_vscode_light_dashboard,
                                 full_dashboard)
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
        elif board_name == 'emm-vscode-dark':
            return redirect(emm_vscode_dark_dashboard.format(stand)), 302
        elif board_name == 'emm-vscode-light':
            return redirect(emm_vscode_light_dashboard.format(stand)), 302
        elif board_name == 'emm-dark-orange':
            return redirect(emm_dark_orange_dashboard.format(stand)), 302
        elif board_name == 'emm-blue':
            return redirect(emm_blue_dashboard.format(stand)), 302
    else: return 'Запрос получен и обработан, но запрашиваемый сервер недоступен или вернул ошибку', 200
